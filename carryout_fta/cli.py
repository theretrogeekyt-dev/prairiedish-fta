"""Command-line interface: python3 -m carryout_fta --help"""
import argparse
import csv
import json
import re
import sys
import tomllib
from pathlib import Path

from . import peak as peaking
from .dish import Carryout, DishError, SimulatedDish
from . import link
from .geo import look_angles, slant_range_km
from .meter import (RTL_MAX_MHZ, RTL_MIN_MHZ, ManualMeter, MeterError,
                    RtlSdrMeter, SimMeter, if_frequency)

HERE = Path(__file__).parent
DEFAULTS = {
    "site": {"lat": None, "lon": None},
    "dish": {"port": "/dev/ttyUSB0", "baud": 57600, "north_offset": 0.0,
             "el_offset": 0.0, "el_min": 22.0, "el_max": 73.0,
             "slew_deg_per_s": 6.0, "settle_s": 1.0,
             "diameter_m": 0.40, "efficiency": 0.55},
    "lnb": {"lo_mhz": 10750.0, "dbs_lo_mhz": 11250.0, "noise_temp_k": 120.0},
    "meter": {"type": "rtlsdr", "gain": 20.7, "averages": 4},
}
SIM_SITE = {"lat": 39.10, "lon": -94.58}    # example site used by --sim


def load_config(path, sim):
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    if path.exists():
        with open(path, "rb") as fh:
            for section, values in tomllib.load(fh).items():
                cfg.setdefault(section, {}).update(values)
    elif not sim:
        sys.exit(f"No config at {path}. Copy config.example.toml to "
                 f"config.toml and fill in your location, or try --sim.")
    if cfg["site"]["lat"] is None or cfg["site"]["lon"] is None:
        if not sim:
            sys.exit("Set site.lat and site.lon in the config file.")
        cfg["site"].update(SIM_SITE)
    return cfg


def load_satellites(path=None):
    with open(path or HERE / "satellites.json") as fh:
        return json.load(fh)["satellites"]


def parse_longitude(text):
    """'97W' -> -97.0, '30.5E' -> 30.5, '-97' -> -97.0; None if not a longitude."""
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*([EeWw]?)\s*", text)
    if not m:
        return None
    value = float(m.group(1))
    return -value if m.group(2).lower() == "w" else value


def resolve(text, sats):
    """Find a catalogue entry by name/alias, or build one from a longitude."""
    key = text.lower().replace(" ", "")
    for sat in sats:
        names = [sat["name"]] + sat.get("aliases", [])
        if key in (n.lower().replace(" ", "") for n in names):
            return sat
    lon = parse_longitude(text)
    if lon is None or not -180 <= lon <= 180:
        sys.exit(f"Unknown satellite '{text}'. Use a name from `sats` or an "
                 f"orbital slot such as 97W.")
    for sat in sats:
        if abs(sat["lon"] - lon) < 0.05:
            return sat
    return {"name": fmt_lon(lon), "lon": lon, "transponders": []}


VOLTS = {"H": "18V", "L": "18V", "V": "13V", "R": "13V"}


def lo_for(cfg, sat):
    """Local oscillator of the LNB that suits this satellite's band."""
    key = "dbs_lo_mhz" if (sat or {}).get("band") == "dbs" else "lo_mhz"
    return cfg["lnb"][key]


def tp_text(tp):
    parts = [tp.get("system"), tp.get("mod"),
             str(tp["sr"]) if "sr" in tp else None, tp.get("fec")]
    return " ".join(p for p in parts if p)


def reachable(cfg, el):
    d = cfg["dish"]
    return d["el_min"] <= el - d["el_offset"] <= d["el_max"]


def fmt_lon(lon):
    return f"{abs(lon):.1f}°{'W' if lon < 0 else 'E'}"


def make_dish(cfg, sim):
    d = cfg["dish"]
    common = dict(north_offset=d["north_offset"], el_offset=d["el_offset"],
                  el_min=d["el_min"], el_max=d["el_max"],
                  slew_deg_per_s=d["slew_deg_per_s"], settle_s=d["settle_s"])
    if sim:
        # the simulated dish is "really" mounted 3.2° off north and 0.8° low
        return SimulatedDish(hidden_north=3.2, hidden_el=-0.8, **common)
    return Carryout(port=d["port"], baud=d["baud"], **common)


def make_meter(args, cfg, dish, sat, sats):
    site = cfg["site"]
    if args.sim:
        # only satellites in the same band as the target are "visible", as
        # with a real LNB; close neighbours blur together like the real sky
        band = (sat or {}).get("band", "ku-linear")
        targets = [look_angles(site["lat"], site["lon"], s["lon"])[:2]
                   for s in sats + ([sat] if sat else [])
                   if s.get("band", "ku-linear") == band]
        return SimMeter(dish, targets)
    kind = args.meter or cfg["meter"]["type"]
    if kind == "manual":
        return ManualMeter(dish)
    if kind != "rtlsdr":
        sys.exit(f"Unknown meter type '{kind}' (use rtlsdr or manual).")
    mhz = args.mhz or next(
        (t["mhz"] for t in (sat or {}).get("transponders", [])), None)
    if mhz is None:
        sys.exit("Give a transponder frequency with --mhz.")
    lo = lo_for(cfg, sat)
    if_mhz = if_frequency(mhz, lo)
    print(f"Meter: RTL-SDR at {if_mhz:.0f} MHz IF "
          f"({mhz:.0f} MHz downlink, LO {lo:.0f})")
    return RtlSdrMeter(if_mhz, gain=cfg["meter"]["gain"],
                       averages=cfg["meter"]["averages"])


def describe(cfg, sat):
    site, d = cfg["site"], cfg["dish"]
    az, el, skew = look_angles(site["lat"], site["lon"], sat["lon"])
    dish_el = el - d["el_offset"]
    print(f"{sat['name']} at {fmt_lon(sat['lon'])}")
    print(f"  true azimuth   {az:7.2f}°")
    print(f"  elevation      {el:7.2f}°")
    if sat.get("band") != "dbs":
        side = "clockwise" if skew >= 0 else "counter-clockwise"
        print(f"  LNB skew       {skew:+7.2f}°  ({side}, seen from behind the dish)")
    print(f"  dish command   g {(az - d['north_offset']) % 360:.1f} {dish_el:.1f}")
    if el <= 0:
        print("  NOT VISIBLE: below the horizon from this site.")
    elif not d["el_min"] <= dish_el <= d["el_max"]:
        print(f"  OUT OF REACH: the dish covers elevation "
              f"{d['el_min']:.0f}-{d['el_max']:.0f}° (see README on el_max).")
    return az, el


def cmd_sats(args, cfg, sats):
    site = cfg["site"]
    print(f"Site {site['lat']:.3f}, {site['lon']:.3f}")
    print(f"{'satellite':<21}{'slot':>8}{'az':>7}{'el':>6}{'skew':>7}"
          f"  {'reach':<6}{'type':<11}transponders")
    for sat in sorted(sats, key=lambda s: s["lon"], reverse=True):
        az, el, skew = look_angles(site["lat"], site["lon"], sat["lon"])
        dbs = sat.get("band") == "dbs"
        print(f"{sat['name']:<21}{fmt_lon(sat['lon']):>8}{az:7.1f}{el:6.1f}"
              f"{0 if dbs else skew:+7.1f}  "
              f"{'yes' if reachable(cfg, el) else 'no':<6}"
              f"{'pay beacon' if dbs else 'free':<11}"
              f"{len(sat.get('transponders', []))}")
    print("\n'pay beacon' satellites are encrypted; they are strong signals "
          "for testing\npointing with the stock LNB. Run `plan` for what to "
          "try first.")


def tp_line(cfg, sat, tp, rng):
    lo = lo_for(cfg, sat)
    if_mhz = if_frequency(tp["mhz"], lo)
    d, lnb = cfg["dish"], cfg["lnb"]
    est, need, mar = link.margin(sat, tp, rng, d["diameter_m"],
                                 d["efficiency"], lnb["noise_temp_k"])
    sdr = "" if RTL_MIN_MHZ <= if_mhz <= RTL_MAX_MHZ else " (beyond RTL-SDR)"
    if sat.get("band") == "dbs":
        verdict = "encrypted; pointing only"
    elif mar is None:
        verdict = "margin unknown"
    else:
        verdict = f"margin {mar:+.1f} dB"
    return (f"  {tp['mhz']} {tp['pol']} {VOLTS[tp['pol']]}  IF {if_mhz:.0f}{sdr}"
            f"  {tp_text(tp):<26}{verdict:<26}{tp.get('channels', '')}")


def cmd_look(args, cfg, sats):
    sat = resolve(args.satellite, sats)
    describe(cfg, sat)
    site = cfg["site"]
    rng = slant_range_km(site["lat"], site["lon"], sat["lon"])
    if sat.get("band") == "dbs":
        print("  Pay-TV satellite: circular polarisation, stock LNB, no skew.")
    for tp in sat.get("transponders", []):
        print(tp_line(cfg, sat, tp, rng))


def cmd_plan(args, cfg, sats):
    """Rank every reachable free transponder by how likely it is to lock."""
    site, d, lnb = cfg["site"], cfg["dish"], cfg["lnb"]
    rows, beacons = [], []
    for sat in sats:
        az, el, skew = look_angles(site["lat"], site["lon"], sat["lon"])
        if not reachable(cfg, el):
            continue
        if sat.get("band") == "dbs":
            beacons.append((sat, az, el))
            continue
        rng = slant_range_km(site["lat"], site["lon"], sat["lon"])
        for tp in sat.get("transponders", []):
            est, need, mar = link.margin(sat, tp, rng, d["diameter_m"],
                                         d["efficiency"], lnb["noise_temp_k"])
            rows.append((mar, sat, tp, az, el, skew, need))
    rows.sort(key=lambda r: (r[0] is None, -(r[0] or 0)))
    bw = link.beamwidth_deg(d["diameter_m"], 12000)
    print(f"Dish {d['diameter_m'] * 100:.0f} cm (beamwidth about {bw:.1f}°), "
          f"site {site['lat']:.3f}, {site['lon']:.3f}")
    print("Free-to-air transponders, most likely to lock first:\n")
    print(f"{'#':>2} {'margin':>7} {'needs':>6}  {'satellite':<20}{'slot':>8}"
          f"{'az':>7}{'el':>6}{'skew':>6}  {'transponder':<38}channels")
    for i, (mar, sat, tp, az, el, skew, need) in enumerate(rows, 1):
        m = f"{mar:+.1f}" if mar is not None else "?"
        n = f"{need:.1f}" if need is not None else "?"
        desc = f"{tp['mhz']} {tp['pol']} {VOLTS[tp['pol']]} {tp_text(tp)}"
        print(f"{i:>2} {m:>7} {n:>6}  {sat['name']:<20}{fmt_lon(sat['lon']):>8}"
              f"{az:7.1f}{el:6.1f}{skew:+6.1f}  {desc:<38}"
              f"{tp.get('channels', '')} [{tp.get('seen', '?')}]")
    print("\nmargin = estimated signal-to-noise minus what the transponder "
          "needs (dB).\nPositive is promising, negative is unlikely, '?' "
          "means parameters are missing.\nIt is a rough clear-sky estimate; "
          "set dish.diameter_m to your reflector's size.\nIt ignores "
          "interference from neighbouring satellites, which a beam this wide "
          "will\npick up where slots are 2° apart (97°W, 99°W and 101°W).")
    if beacons:
        print("\nStrong encrypted satellites for testing pointing "
              "(stock LNB, no skew):")
        for sat, az, el in sorted(beacons, key=lambda b: -b[0]["lon"]):
            print(f"   {sat['name']:<20}{fmt_lon(sat['lon']):>8}"
                  f"{az:7.1f}{el:6.1f}")


def cmd_goto(args, cfg, sats):
    dish = make_dish(cfg, args.sim)
    try:
        if args.azel:
            az, el = args.azel
        else:
            if not args.satellite:
                sys.exit("Give a satellite, or --azel AZ EL.")
            az, el = describe(cfg, resolve(args.satellite, sats))
        dish.goto(az, el)
        print(f"Dish sent to true az {az:.1f}°, el {el:.1f}°.")
    finally:
        dish.close()


def _show_point(az, el, reading):
    print(f"  az {az:6.1f}  el {el:5.1f}  {reading:7.2f}")


def crowded(cfg, sat, sats, span):
    """Same-band catalogue satellites close enough to be mistaken for sat."""
    reach = span + link.beamwidth_deg(cfg["dish"]["diameter_m"], 12000) / 2
    return [o for o in sats
            if o is not sat and o.get("band") == sat.get("band")
            and 0.05 < abs(o["lon"] - sat["lon"]) <= reach]


def run_peak(args, cfg, sats):
    sat = resolve(args.satellite, sats)
    az, el = describe(cfg, sat)
    near = crowded(cfg, sat, sats, args.span)
    if near:
        names = ", ".join(f"{o['name']} ({fmt_lon(o['lon'])})" for o in near)
        print(f"NOTE: also inside the search area: {names}. With a beam "
              f"this wide the peak may be a neighbour or a blend.")
    dish = make_dish(cfg, args.sim)
    meter = None
    try:
        meter = make_meter(args, cfg, dish, sat, sats)
        show = None if isinstance(meter, ManualMeter) else _show_point
        best_az, best_el, gain = peaking.peak(
            dish, meter, az, el, span=args.span, step=args.step, report=show)
    finally:
        if meter:
            meter.close()
        dish.close()
    print(f"Peak at true az {best_az:.2f}°, el {best_el:.2f}° "
          f"({gain:+.1f} above the weakest reading nearby)")
    if gain < 1.0:
        print("WARNING: no clear peak. Widen --span, check LNB power and "
              "polarity, or try a stronger transponder.")
    return az, el, best_az, best_el, gain


def cmd_peak(args, cfg, sats):
    run_peak(args, cfg, sats)


def cmd_calibrate(args, cfg, sats):
    az, el, best_az, best_el, gain = run_peak(args, cfg, sats)
    if gain < 1.0:
        sys.exit("Calibration skipped because the peak is not trustworthy.")
    sat = resolve(args.satellite, sats)
    if crowded(cfg, sat, sats, args.span):
        print("\nWARNING: this satellite has close neighbours, so these "
              "offsets may be wrong by a degree or two.\nCalibrate on an "
              "isolated target instead: a pay beacon such as `directv` with "
              "the stock LNB, or `ses2`.")
    d = cfg["dish"]
    d_az = (az - best_az + 180) % 360 - 180
    print("\nPut these in the [dish] section of your config:")
    print(f"north_offset = {(d['north_offset'] + d_az) % 360:.2f}")
    print(f"el_offset = {d['el_offset'] + (el - best_el):.2f}")


def cmd_arc(args, cfg, sats):
    site = cfg["site"]
    lon_from = parse_longitude(args.start)
    lon_to = parse_longitude(args.end)
    if lon_from is None or lon_to is None:
        sys.exit("--from and --to take orbital slots such as 110W and 80W.")
    dish = make_dish(cfg, args.sim)
    meter = None
    try:
        meter = make_meter(args, cfg, dish, None, sats)
        results = peaking.scan_arc(
            dish, meter, site["lat"], site["lon"], lon_from, lon_to,
            step=args.step,
            report=lambda lon, az, el, r: print(
                f"  {fmt_lon(lon):>8}  az {az:6.1f}  el {el:5.1f}  {r:7.2f}"))
    finally:
        if meter:
            meter.close()
        dish.close()
    if not results:
        sys.exit("None of that arc is within the dish's elevation range.")
    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["sat_lon", "azimuth", "elevation", "reading"])
            writer.writerows(results)
        print(f"Saved {len(results)} points to {args.csv}")
    found = peaking.find_peaks(results)
    if found:
        print("Signal peaks:")
        for lon, az, el, reading in found:
            near = min(sats, key=lambda s: abs(s["lon"] - lon))
            label = near["name"] if abs(near["lon"] - lon) <= 1.0 else "unlisted"
            print(f"  {fmt_lon(lon):>8}  {reading:7.2f}  ({label})")
    else:
        print("No peaks stood out above the noise.")


def cmd_console(args, cfg, sats):
    dish = make_dish(cfg, args.sim)
    try:
        for command in args.commands:
            print(dish.console(command))
    finally:
        dish.close()


def cmd_rotctld(args, cfg, sats):
    from .rotctld import serve
    dish = make_dish(cfg, args.sim)
    try:
        serve(dish, port=args.port)
    finally:
        dish.close()


def build_parser():
    p = argparse.ArgumentParser(
        prog="carryout-fta",
        description="Aim a Winegard Carryout at free-to-air satellites.")
    p.add_argument("--config", type=Path, default=Path("config.toml"))
    p.add_argument("--sim", action="store_true",
                   help="use a simulated dish and signal (no hardware needed)")
    p.add_argument("--satellites", type=Path, help="alternative satellites.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sats", help="list catalogued satellites for your site"
                   ).set_defaults(func=cmd_sats)

    sub.add_parser("plan", help="rank free transponders by chance of locking"
                   ).set_defaults(func=cmd_plan)

    s = sub.add_parser("look", help="show look angles and LNB skew")
    s.add_argument("satellite")
    s.set_defaults(func=cmd_look)

    s = sub.add_parser("goto", help="point the dish")
    s.add_argument("satellite", nargs="?")
    s.add_argument("--azel", nargs=2, type=float, metavar=("AZ", "EL"))
    s.set_defaults(func=cmd_goto)

    def add_meter(s):
        s.add_argument("--mhz", type=float, help="transponder frequency to measure")
        s.add_argument("--meter", choices=["rtlsdr", "manual"])

    for name, func, text in (
            ("peak", cmd_peak, "fine-tune pointing on signal strength"),
            ("calibrate", cmd_calibrate,
             "peak on a known satellite and work out the mounting offsets")):
        s = sub.add_parser(name, help=text)
        s.add_argument("satellite")
        s.add_argument("--span", type=float, default=4.0,
                       help="degrees to search either side (default 4)")
        s.add_argument("--step", type=float, default=0.5)
        add_meter(s)
        s.set_defaults(func=func)

    s = sub.add_parser("arc", help="sweep the satellite belt looking for signals")
    s.add_argument("--from", dest="start", required=True, metavar="SLOT")
    s.add_argument("--to", dest="end", required=True, metavar="SLOT")
    s.add_argument("--step", type=float, default=0.5)
    s.add_argument("--csv", type=Path)
    add_meter(s)
    s.set_defaults(func=cmd_arc)

    s = sub.add_parser("console", help="send raw firmware commands in order")
    s.add_argument("commands", nargs="+", metavar="command")
    s.set_defaults(func=cmd_console)

    s = sub.add_parser("rotctld", help="serve Gpredict/hamlib rotator commands")
    s.add_argument("--port", type=int, default=4533)
    s.set_defaults(func=cmd_rotctld)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config, args.sim)
    sats = load_satellites(args.satellites)
    try:
        args.func(args, cfg, sats)
    except (DishError, MeterError) as exc:
        sys.exit(f"Error: {exc}")
    return 0
