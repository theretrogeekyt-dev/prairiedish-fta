"""Look angles from a ground site to a geostationary satellite.

Longitudes are degrees east (west is negative, so 97.0W is -97.0).
"""
import math

WGS84_A = 6378.137            # km, equatorial radius
WGS84_E2 = 6.69437999014e-3   # first eccentricity squared
GEO_RADIUS = 42164.17         # km, from Earth's centre


def look_angles(lat, lon, sat_lon):
    """Return (azimuth, elevation, skew) in degrees.

    azimuth:   true (not magnetic) bearing, 0 = north, clockwise
    elevation: above the horizon; negative means the satellite is not visible
    skew:      LNB polarisation tilt for linear (H/V) signals. Positive means
               rotate the LNB clockwise as seen from BEHIND the dish, looking
               toward the satellite.
    """
    phi, lam, lam_s = map(math.radians, (lat, lon, sat_lon))
    n = WGS84_A / math.sqrt(1 - WGS84_E2 * math.sin(phi) ** 2)
    site = (n * math.cos(phi) * math.cos(lam),
            n * math.cos(phi) * math.sin(lam),
            n * (1 - WGS84_E2) * math.sin(phi))
    sat = (GEO_RADIUS * math.cos(lam_s), GEO_RADIUS * math.sin(lam_s), 0.0)
    dx, dy, dz = (s - g for s, g in zip(sat, site))

    east = -math.sin(lam) * dx + math.cos(lam) * dy
    north = (-math.sin(phi) * math.cos(lam) * dx
             - math.sin(phi) * math.sin(lam) * dy + math.cos(phi) * dz)
    up = (math.cos(phi) * math.cos(lam) * dx
          + math.cos(phi) * math.sin(lam) * dy + math.sin(phi) * dz)

    az = math.degrees(math.atan2(east, north)) % 360
    el = math.degrees(math.atan2(up, math.hypot(east, north)))

    skew = math.degrees(math.atan2(math.sin(lam - lam_s), math.tan(phi)))
    if skew > 90:
        skew -= 180
    elif skew <= -90:
        skew += 180
    return az, el, skew


def slant_range_km(lat, lon, sat_lon):
    """Distance from the site to the satellite."""
    phi, dlam = math.radians(lat), math.radians(sat_lon - lon)
    cos_gamma = math.cos(phi) * math.cos(dlam)
    return math.sqrt(WGS84_A ** 2 + GEO_RADIUS ** 2
                     - 2 * WGS84_A * GEO_RADIUS * cos_gamma)


def angular_separation(az1, el1, az2, el2):
    """Great-circle angle in degrees between two az/el directions."""
    a1, e1, a2, e2 = map(math.radians, (az1, el1, az2, el2))
    c = (math.sin(e1) * math.sin(e2)
         + math.cos(e1) * math.cos(e2) * math.cos(a1 - a2))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))
