# carryout-fta

Aim a Winegard Carryout portable dish at free-to-air (FTA) Ku-band satellites.

You give it your location and a satellite (or just an orbital slot such as
`97W`). It works out the azimuth, elevation and LNB skew, drives the dish
there through the firmware console, and fine-tunes on signal strength.

This is a derivative of two public-domain projects by Gabe Emerson
(saveitforparts):
[Carryout-Rotor](https://github.com/saveitforparts/Carryout-Rotor) and
[Carryout-Radio-Telescope](https://github.com/saveitforparts/Carryout-Radio-Telescope).
The serial protocol, cable pinout and firmware knowledge are his. This project
adds the geostationary pointing maths, a satellite catalogue, calibration,
peak-up, arc scanning and a simulator.

**Status: untested on hardware.** Everything passes against a simulated dish,
and the pointing maths agrees with LyngSat's published figures to within
0.05°. The firmware commands are the ones upstream uses on a 2003 Carryout
(HandEra HAL 1.00.065). Expect to adjust things on first contact with a real
dish, and keep a hand near the power plug.

## Will it actually receive FTA?

Pointing is the easy part. Reception is limited by three things:

1. **The LNB.** The stock LNB is for Dish/DirecTV-style circular-polarised
   signals. FTA Ku is linear (H/V), so fit a linear Ku LNB. A "standard" LNB
   with a 10750 MHz local oscillator is the best match: it puts 11.7-12.2 GHz
   at 950-1450 MHz, inside an RTL-SDR's range. A universal LNB's low band
   (LO 9750) lands at 1950-2450 MHz, which an RTL-SDR cannot tune.
2. **Skew.** Linear signals need the LNB rotated to match the satellite. The
   Carryout has no skew motor, so you rotate the LNB by hand to the angle
   `look` prints, and it is only right for one satellite at a time.
3. **Dish size.** Intelsat recommends a 0.75 m dish for Galaxy 19, and the
   Carryout's reflector is far smaller. Expect a weak or marginal signal.
   `plan` estimates which transponders have the best chance.

## Hardware

- Winegard Carryout with the radome off, powered from 12 V (1 A or more).
- USB-serial adapter, RS-232 to RS-485 converter, and an RJ-25 (6-wire) cable
  into the console port. Pins, looking at the contacts with the cable end up,
  left to right: 1 GND, 2 T/R-, 3 T/R+, 4 RXD-, 5 RXD+, 6 unused.
- A linear Ku LNB, powered with 13 V (vertical) or 18 V (horizontal) by an FTA
  receiver or a power inserter.
- A way to read signal strength, either:
  - an **RTL-SDR** on a splitter port **with a DC block** (LNB voltage will
    destroy an unprotected SDR), or
  - an FTA receiver or satfinder whose reading you type in (`--meter manual`).

On power-up the dish spends 10-15 minutes calibrating and searching, with
grinding noises; it has no limit switches. Let that finish before sending
commands.

## Setup

```
pip install -r requirements.txt     # pyserial; pyrtlsdr + numpy for the SDR meter
cp config.example.toml config.toml  # then set your lat/lon and serial port
```

Requires Python 3.11 or newer. Try everything without hardware by adding
`--sim`:

```
python3 -m carryout_fta --sim calibrate directv
```

## Use

```
python3 -m carryout_fta sats                  # catalogue, with angles from your site
python3 -m carryout_fta plan                  # what to try first, ranked
python3 -m carryout_fta look g19              # az / el / skew / IF frequencies
python3 -m carryout_fta look 101W             # any orbital slot works
python3 -m carryout_fta goto g19              # point the dish
python3 -m carryout_fta goto --azel 180 45    # point at a raw direction
python3 -m carryout_fta calibrate directv
python3 -m carryout_fta peak g19 --mhz 11842
python3 -m carryout_fta arc --from 110W --to 85W --mhz 11842 --csv arc.csv
python3 -m carryout_fta console "?"           # raw firmware command(s)
python3 -m carryout_fta rotctld               # Gpredict bridge on 127.0.0.1:4533
```

### First light

1. Set the dish level with its azimuth zero facing roughly true north. Find
   zero with `goto --azel 0 22` while `north_offset` is still 0.
2. **Calibrate on an isolated satellite.** With the stock LNB still fitted,
   run `calibrate directv` (or another pay beacon). It cross-scans around the
   predicted position and prints `north_offset` and `el_offset`; paste them
   into `config.toml`. If it reports no clear peak, widen the search with
   `--span 10`. The offsets describe the mount, not the LNB, so they carry
   over when you change LNBs.
3. Fit the linear LNB. Run `plan`, pick a target, and `look` it; rotate the
   LNB to the printed skew.
4. Power the LNB for the transponder's polarity (18 V = H, 13 V = V).
5. `goto` the satellite, then `peak` it. `arc` sweeps the belt and lists
   where signals appear.

Do not calibrate on Galaxy 19, Galaxy 16 or SES 1: they sit 2° apart, the
dish cannot separate them, and the tool will warn that the offsets may be a
degree or two out. Among the free satellites, SES 2 is the isolated one.

### Elevation limits

The firmware accepts 22-73° by default. Upstream raises the ceiling from the
console: `nvs`, then `e 102` to read the value, `e 102 90` to set it, `s` to
save. After that, set `el_max = 90` in `config.toml`. Satellites below 22° are
out of reach.

## Satellite catalogue and fallbacks

`carryout_fta/satellites.json` lists seven free-to-air Ku satellites between
87°W and 123°W, plus five encrypted pay-TV satellites that are useful as
pointing beacons. Any other slot still works by longitude (`look 91W`).

| Satellite | Slot | Free content (when last listed) |
|---|---|---|
| SES 2 | 87.0°W | The Florida Channel, Louisiana PBS, test streams |
| Galaxy 19 | 97.0°W | The largest set: religious and international channels |
| Galaxy 16 | 99.0°W | PBS national feeds, Montana PBS, Family Radio |
| SES 1 | 101.0°W | The Florida Channel, K-Love and AFR radio |
| SES 11 | 105.0°W | InfoWars |
| Eutelsat 117 West A | 116.8°W | BYU TV, Hope Channel |
| Galaxy 18 | 123.0°W | Daystar, Telesecundaria |

`plan` ranks every transponder you can reach by estimated margin: the signal
your dish should get minus what that transponder needs to lock.

```
python3 -m carryout_fta plan
```

Read it as an order to try things in, not a prediction:

- The estimate is a clear-sky link budget from the satellite's published
  power, your slant range and `dish.diameter_m`. The 0.40 m default is a
  guess; measure your reflector.
- Narrow, robust carriers (radio services, QPSK) rank highest. They are the
  realistic first lock on a dish this small. Wide 8PSK muxes such as the PBS
  feeds are borderline.
- A 40 cm dish has a beam about 4.4° wide, while 97°W, 99°W and 101°W sit 2°
  apart. Neighbouring satellites will leak in, and the estimate ignores that.
- Each entry carries the date its source last confirmed it. Several are a
  year or more old. Free channels come and go, so check
  [LyngSat](https://www.lyngsat.com/america.html) when something won't lock.
- BYU TV on Eutelsat 117 West A is at 12521 MHz, above the usual 11.7-12.2 GHz
  FTA band: not every "standard" LNB passes it, and its IF is beyond an
  RTL-SDR.

**Pay beacons.** DirecTV at 101°W and Dish at 61.5°W, 72.7°W, 110°W and 119°W
are encrypted, but they are the strongest Ku signals in the sky and work with
the stock LNB. Use them to prove the mount, serial link and calibration before
swapping to a linear LNB, for example `calibrate directv`. Their listed
frequencies are nominal channel centres of the 12.2-12.7 GHz band, not
verified carriers.

## Known gaps

- **Position feedback.** The firmware reports position only while moving, and
  the parser for it is ported from upstream unverified. The code waits an
  estimated time per move instead (`slew_deg_per_s`).
- **Built-in tuner.** The firmware's `dvb` menu reports signal from the
  onboard receiver. Its output format is undocumented, so it is not used yet.
  Running `console dvb "?"` (enter the menu, then list its commands) and
  saving the output would be enough to start adding it as a third meter.
- **G2 / G3 models** are untested, as they are upstream.
- **Skew offsets.** A few satellites use a non-standard polarisation tilt;
  fine-tune the LNB by hand on signal.

## Tests

```
python3 -m pytest
```

## Licence

Public domain (Unlicense), the same as the upstream projects.
