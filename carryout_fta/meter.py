"""Signal-strength sources used for peaking the dish."""
import math
import random

from .geo import angular_separation

RTL_MIN_MHZ, RTL_MAX_MHZ = 950.0, 1766.0


def if_frequency(transponder_mhz, lo_mhz):
    """Frequency the LNB outputs for a given downlink frequency."""
    return abs(transponder_mhz - lo_mhz)


class MeterError(Exception):
    pass


class RtlSdrMeter:
    """Relative power from an RTL-SDR on the LNB's IF output.

    Same measurement as upstream carryout_scan.py: 10*log10(variance of IQ).
    The SDR must sit behind a DC block; the LNB needs 13/18 V from a receiver
    or power inserter, which would destroy an unprotected SDR input.
    """

    def __init__(self, if_mhz, gain=20.7, averages=4):
        if not RTL_MIN_MHZ <= if_mhz <= RTL_MAX_MHZ:
            raise MeterError(
                f"IF of {if_mhz:.0f} MHz is outside what an RTL-SDR can tune "
                f"({RTL_MIN_MHZ:.0f}-{RTL_MAX_MHZ:.0f}). Check lnb.lo_mhz.")
        try:
            from rtlsdr import RtlSdr
            import numpy as np
        except ImportError as exc:
            raise MeterError("pyrtlsdr and numpy are required for the "
                             "rtlsdr meter (pip install pyrtlsdr numpy)") from exc
        self._np = np
        self.averages = averages
        self.sdr = RtlSdr()
        self.sdr.sample_rate = 2.4e6
        self.sdr.center_freq = if_mhz * 1e6
        self.sdr.gain = gain
        self.sdr.read_samples(256 * 1024)        # flush stale samples

    def read(self):
        np = self._np
        self.sdr.read_samples(64 * 1024)         # discard data from before the move
        power = [np.var(self.sdr.read_samples(256 * 1024))
                 for _ in range(self.averages)]
        return 10 * math.log10(sum(power) / len(power))

    def close(self):
        self.sdr.close()


class ManualMeter:
    """You read a number off a receiver or satfinder and type it in."""

    def __init__(self, dish):
        self.dish = dish

    def read(self):
        az, el = self.dish.to_true(self.dish.dish_az, self.dish.dish_el)
        while True:
            raw = input(f"  az {az:6.1f}  el {el:5.1f}  signal reading: ")
            try:
                return float(raw)
            except ValueError:
                print("  enter a number")

    def close(self):
        pass


class SimMeter:
    """Fake satellites with a Gaussian beam, for use with SimulatedDish."""

    def __init__(self, dish, targets, beamwidth=4.5, peak_db=6.0,
                 noise_db=0.05, seed=1):
        self.dish = dish
        self.targets = targets       # list of (true_az, true_el)
        self.beamwidth = beamwidth   # half-power beamwidth, degrees
        self.peak_db = peak_db
        self.noise_db = noise_db
        self.rng = random.Random(seed)

    def read(self):
        az, el = self.dish.actual_pointing()
        best = 0.0
        for t_az, t_el in self.targets:
            off = angular_separation(az, el, t_az, t_el)
            best = max(best, self.peak_db * math.exp(
                -4 * math.log(2) * (off / self.beamwidth) ** 2))
        return -30.0 + best + self.rng.gauss(0, self.noise_db)

    def close(self):
        pass
