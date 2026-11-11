"""Fine-pointing: sweep across a satellite and settle on the strongest signal."""
import math
import statistics

from .dish import DishError
from .geo import look_angles

BACKLASH_DEG = 1.5   # always arrive from the low side by at least this much


def _approach(dish, az, el):
    """Take up gear backlash by arriving from the same side every time."""
    try:
        dish.goto(az - BACKLASH_DEG, el - BACKLASH_DEG)
    except DishError:
        pass
    dish.goto(az, el)


def _interpolate(points):
    """Offset of the peak, refined with a parabola through its neighbours."""
    i = max(range(len(points)), key=lambda k: points[k][1])
    if 0 < i < len(points) - 1:
        (x0, y0), (x1, y1), (x2, y2) = points[i - 1:i + 2]
        denom = y0 - 2 * y1 + y2
        if denom < 0 and math.isclose(x1 - x0, x2 - x1, rel_tol=1e-6):
            return x1 + 0.5 * (y0 - y2) / denom * (x1 - x0)
    return points[i][0]


def sweep(dish, meter, axis, az, el, span, step, report=None):
    """Measure along one axis; return [(offset_deg, reading), ...]."""
    scale = 1 / math.cos(math.radians(el)) if axis == "az" else 1.0
    count = int(round(2 * span / step))
    points, first = [], True
    for k in range(count + 1):
        off = (-span + k * step) * scale
        p_az, p_el = (az + off, el) if axis == "az" else (az, el + off)
        try:
            if first:
                _approach(dish, p_az, p_el)
            else:
                dish.goto(p_az, p_el)
        except DishError:
            continue                      # outside the elevation limits
        first = False
        reading = meter.read()
        points.append((off, reading))
        if report:
            report(p_az % 360, p_el, reading)
    return points


def peak(dish, meter, az, el, span=4.0, step=0.5, report=None):
    """Cross-scan around (az, el). Returns (best_az, best_el, gain_db).

    gain_db is how far the peak stood above the weakest reading in the search
    area; a value near zero means nothing was found.
    """
    readings = []
    for axis in ("az", "el", "az"):
        points = sweep(dish, meter, axis, az, el, span, step, report)
        if len(points) < 3:
            raise DishError("not enough of the sweep was inside the dish's limits")
        readings += [r for _, r in points]
        best = _interpolate(points)
        if axis == "az":
            az = (az + best) % 360
        else:
            el += best
        span = max(span / 2, 2 * step)    # tighten each pass
    _approach(dish, az, el)
    final = meter.read()
    return az, el, final - min(readings)


def scan_arc(dish, meter, lat, lon, lon_from, lon_to, step=0.5, report=None):
    """Sweep along the geostationary arc, measuring at each orbital slot.

    Returns [(sat_lon, az, el, reading), ...] for slots the dish can reach.
    """
    lo, hi = sorted((lon_from, lon_to))
    results, first = [], True
    for k in range(int(round((hi - lo) / step)) + 1):
        sat_lon = lo + k * step
        az, el, _ = look_angles(lat, lon, sat_lon)
        try:
            if first:
                _approach(dish, az, el)
            else:
                dish.goto(az, el)
        except DishError:
            continue
        first = False
        reading = meter.read()
        results.append((sat_lon, az, el, reading))
        if report:
            report(sat_lon, az, el, reading)
    return results


def find_peaks(results, min_db=1.0):
    """Local maxima of an arc scan that stand min_db above the median."""
    if len(results) < 3:
        return []
    floor = statistics.median(r[3] for r in results)
    return [results[i] for i in range(1, len(results) - 1)
            if results[i][3] - floor >= min_db
            and results[i][3] >= results[i - 1][3]
            and results[i][3] > results[i + 1][3]]
