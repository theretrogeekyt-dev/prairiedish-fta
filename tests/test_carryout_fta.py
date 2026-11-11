import math

import pytest

from carryout_fta import peak as peaking
from carryout_fta.cli import main, parse_longitude
from carryout_fta.dish import DishError, SimulatedDish, parse_position
from carryout_fta import link
from carryout_fta.cli import load_satellites, resolve
from carryout_fta.geo import look_angles, slant_range_km
from carryout_fta.meter import MeterError, RtlSdrMeter, SimMeter, if_frequency


def test_look_angles_match_lyngsat_columbus():
    # LyngSat lists Galaxy 19 (97.0W) from Columbus, Ohio at az 201.22, el 41.55
    az, el, skew = look_angles(39.9612, -82.9988, -97.0)
    assert az == pytest.approx(201.22, abs=0.3)
    assert el == pytest.approx(41.55, abs=0.3)
    assert skew > 0          # satellite to the west: clockwise from behind


def test_look_angles_basic_geometry():
    az, el, skew = look_angles(0.0, -97.0, -97.0)
    assert el == pytest.approx(90, abs=1e-6)
    az, el, skew = look_angles(40.0, -97.0, -97.0)
    assert az == pytest.approx(180, abs=1e-6)
    assert skew == pytest.approx(0, abs=1e-6)
    assert look_angles(40.0, -97.0, 83.0)[1] < 0      # far side of the Earth
    # satellite east of the site: negative skew (London to 28.2E is about -20)
    assert look_angles(51.5, -0.13, 28.2)[2] == pytest.approx(-20.6, abs=1.0)


def test_parse_longitude():
    assert parse_longitude("97W") == -97.0
    assert parse_longitude("30.5e") == 30.5
    assert parse_longitude("-97") == -97.0
    assert parse_longitude("galaxy") is None


def test_parse_position_upstream_layout():
    assert parse_position("az = 18025 x y el = 4150zz") == (180.25, 41.5)
    assert parse_position("garbage") is None


def test_if_frequency_and_rtl_range():
    assert if_frequency(11842, 10750) == 1092
    with pytest.raises(MeterError):
        RtlSdrMeter(if_frequency(11842, 9750))     # 2092 MHz: out of range


def test_dish_offsets_limits_and_commands():
    dish = SimulatedDish(north_offset=10.0, el_offset=1.0)
    dish.goto(200.0, 41.0)
    assert (dish.dish_az, dish.dish_el) == (190.0, 40.0)
    assert dish.log[-3:] == ["target", "g 190.0 40.0", "q"]
    with pytest.raises(DishError):
        dish.goto(200.0, 15.0)
    with pytest.raises(DishError):
        dish.goto(200.0, 80.0)


def test_peak_recovers_hidden_mounting_error():
    dish = SimulatedDish(hidden_north=3.2, hidden_el=-0.8)
    az, el, _ = look_angles(39.10, -94.58, -97.0)
    meter = SimMeter(dish, [(az, el)])
    best_az, best_el, gain = peaking.peak(dish, meter, az, el)
    # dish thinks it is at best_*, but really points 3.2 / -0.8 away from that
    assert best_az + 3.2 == pytest.approx(az, abs=0.3)
    assert best_el - 0.8 == pytest.approx(el, abs=0.3)
    assert gain > 3


def test_arc_scan_finds_satellite():
    dish = SimulatedDish()
    az, el, _ = look_angles(39.10, -94.58, -97.0)
    meter = SimMeter(dish, [(az, el)])
    results = peaking.scan_arc(dish, meter, 39.10, -94.58, -110, -85, step=0.5)
    found = peaking.find_peaks(results)
    assert len(found) == 1
    assert found[0][0] == pytest.approx(-97.0, abs=0.5)


def test_cli_sim_runs(capsys, tmp_path):
    cfg = tmp_path / "none.toml"
    assert main(["--sim", "--config", str(cfg), "sats"]) == 0
    assert main(["--sim", "--config", str(cfg), "look", "g19"]) == 0
    assert main(["--sim", "--config", str(cfg), "goto", "97W"]) == 0
    assert main(["--sim", "--config", str(cfg), "calibrate", "directv"]) == 0
    out = capsys.readouterr().out
    north = float(out.split("north_offset = ")[1].split()[0])
    el_off = float(out.split("el_offset = ")[1].split()[0])
    assert north == pytest.approx(3.2, abs=0.3)
    assert el_off == pytest.approx(-0.8, abs=0.3)
    # an arc scan only labels satellites correctly once the dish is calibrated
    good = tmp_path / "config.toml"
    good.write_text(f"[site]\nlat = 39.10\nlon = -94.58\n"
                    f"[dish]\nnorth_offset = {north}\nel_offset = {el_off}\n")
    assert main(["--sim", "--config", str(good), "arc", "--from", "110W",
                 "--to", "85W", "--csv", str(tmp_path / "arc.csv")]) == 0
    assert "(SES 2)" in capsys.readouterr().out
    # crowded part of the arc: calibration still runs but warns
    assert main(["--sim", "--config", str(cfg), "calibrate", "g19"]) == 0
    assert "close neighbours" in capsys.readouterr().out


def test_catalogue_is_well_formed():
    sats = load_satellites()
    assert len([s for s in sats if s["band"] == "ku-linear"]) >= 7
    names = set()
    for sat in sats:
        assert -180 <= sat["lon"] <= 180 and sat["band"] in ("ku-linear", "dbs")
        for name in [sat["name"]] + sat.get("aliases", []):
            assert name.lower() not in names, f"duplicate name {name}"
            names.add(name.lower())
        for tp in sat["transponders"]:
            pols = "RL" if sat["band"] == "dbs" else "HV"
            assert tp["pol"] in pols and 11700 <= tp["mhz"] <= 12750
    assert resolve("g16", sats)["name"] == "Galaxy 16"
    assert resolve("directv", sats)["band"] == "dbs"
    assert resolve("99W", sats)["name"] == "Galaxy 16"


def test_link_budget_is_plausible():
    rng = slant_range_km(37.835, -97.373, -97.0)
    assert rng == pytest.approx(37330, abs=50)
    # Intelsat recommends 0.75 m for Galaxy 19: that should clear a 5.5 dB
    # DVB-S threshold comfortably, and a 40 cm dish should not
    big = link.estimated_cn(47.5, rng, 11842, 0.75)
    small = link.estimated_cn(47.5, rng, 11842, 0.40)
    assert 3 < big - 5.5 < 7
    assert small < 5.5
    assert big - small == pytest.approx(20 * math.log10(0.75 / 0.40), abs=0.01)
    assert link.required_cn({"system": "DVB-S2", "mod": "8PSK", "fec": "3/4"}) == 7.9
    assert link.required_cn({"mhz": 1}) is None


def test_cli_plan_and_beacons(capsys, tmp_path):
    cfg = tmp_path / "none.toml"
    assert main(["--sim", "--config", str(cfg), "plan"]) == 0
    out = capsys.readouterr().out
    assert "Galaxy 16" in out and "DirecTV 101W" in out
    assert main(["--sim", "--config", str(cfg), "look", "directv"]) == 0
    out = capsys.readouterr().out
    assert "IF 974" in out and "skew" not in out.replace("no skew", "")
    assert main(["--sim", "--config", str(cfg), "peak", "ses2"]) == 0
