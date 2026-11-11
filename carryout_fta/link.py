"""Rough link budget: will a given dish hold a given transponder?

This is an estimate for ranking what to try first, not a promise. It assumes
clear sky, a correctly skewed LNB, and that every carrier gets a share of the
transponder's power in proportion to its bandwidth.
"""
import math

BOLTZMANN_DB = -228.6
TRANSPONDER_BW_HZ = 36e6
MISC_LOSS_DB = 1.5        # pointing, radome, polarisation, atmosphere

# C/N needed to lock, dB (LyngSat's figures where it publishes them)
REQUIRED_CN = {
    ("DVB-S", "QPSK", "1/2"): 4.1, ("DVB-S", "QPSK", "2/3"): 5.0,
    ("DVB-S", "QPSK", "3/4"): 5.5, ("DVB-S", "QPSK", "5/6"): 6.0,
    ("DVB-S", "QPSK", "7/8"): 6.4,
    ("DVB-S2", "QPSK", "1/2"): 1.0, ("DVB-S2", "QPSK", "3/5"): 2.2,
    ("DVB-S2", "QPSK", "2/3"): 3.1, ("DVB-S2", "QPSK", "3/4"): 4.0,
    ("DVB-S2", "QPSK", "4/5"): 4.7, ("DVB-S2", "QPSK", "5/6"): 5.2,
    ("DVB-S2", "8PSK", "3/5"): 5.5, ("DVB-S2", "8PSK", "2/3"): 6.6,
    ("DVB-S2", "8PSK", "3/4"): 7.9, ("DVB-S2", "8PSK", "5/6"): 9.4,
}


def wavelength_m(mhz):
    return 299.792458 / mhz


def antenna_gain_db(diameter_m, mhz, efficiency=0.55):
    return 10 * math.log10(
        efficiency * (math.pi * diameter_m / wavelength_m(mhz)) ** 2)


def beamwidth_deg(diameter_m, mhz):
    """Approximate half-power beamwidth."""
    return 70 * wavelength_m(mhz) / diameter_m


def required_cn(tp):
    """C/N a transponder needs to lock, or None if its parameters are unknown."""
    if "cn" in tp:
        return tp["cn"]
    return REQUIRED_CN.get((tp.get("system"), tp.get("mod"), tp.get("fec")))


def estimated_cn(eirp_dbw, range_km, mhz, diameter_m, efficiency=0.55,
                 noise_temp_k=120.0):
    fsl = 20 * math.log10(4 * math.pi * range_km * 1000 / wavelength_m(mhz))
    g_over_t = (antenna_gain_db(diameter_m, mhz, efficiency)
                - 10 * math.log10(noise_temp_k))
    return (eirp_dbw - fsl - MISC_LOSS_DB + g_over_t - BOLTZMANN_DB
            - 10 * math.log10(TRANSPONDER_BW_HZ))


def margin(sat, tp, range_km, diameter_m, efficiency=0.55, noise_temp_k=120.0):
    """(estimated C/N, required C/N, margin) with None where unknown."""
    need = required_cn(tp)
    if "eirp" not in sat:
        return None, need, None
    est = estimated_cn(sum(sat["eirp"]) / 2, range_km, tp["mhz"], diameter_m,
                       efficiency, noise_temp_k)
    return est, need, (est - need if need is not None else None)
