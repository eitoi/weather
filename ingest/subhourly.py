"""HRRR 15-minute (subhourly) surface data for the daylight windows around sunrise and sunset.

HRRR's wrfsubhf files hold four 15-minute steps per forecast hour, surface fields only, out to 18 h.
We keep the steps that fall in [sunrise, sunrise + WINDOW] and [sunset - WINDOW, sunset] (local daylight),
for the newest run.
"""
import datetime as dt
import math

from .config import BBOX

WINDOW_H = 3
STEP_MIN = 15

# key: (idx var, idx level, units, scale, convert, kind)  kind: "inst" | "acc" | "avg5"
SUB_FIELDS = {
    "T2":     ("TMP", "2 m above ground", "C", 0.01, "K2C", "inst"),
    "D2":     ("DPT", "2 m above ground", "C", 0.01, "K2C", "inst"),
    "U10":    ("UGRD", "10 m above ground", "m/s", 0.01, None, "inst"),
    "V10":    ("VGRD", "10 m above ground", "m/s", 0.01, None, "inst"),
    "U80":    ("UGRD", "80 m above ground", "m/s", 0.01, None, "inst"),
    "V80":    ("VGRD", "80 m above ground", "m/s", 0.01, None, "inst"),
    "WAVG10": ("WIND", "10 m above ground", "m/s", 0.01, None, "avg5"),   # 5-minute mean speed
    "GUST":   ("GUST", "surface", "m/s", 0.01, None, "inst"),
    "APCP15": ("APCP", "surface", "mm", 0.01, None, "acc"),
    "PRATE":  ("PRATE", "surface", "mm/h", 0.01, "s2h", "inst"),
    "CPOFP":  ("CPOFP", "surface", "%", 0.1, None, "inst"),
    "CBASE":  ("HGT", "cloud base", "m", 1, None, "inst"),
    "CEIL":   ("HGT", "cloud ceiling", "m", 1, None, "inst"),
    "CTOP":   ("HGT", "cloud top", "m", 1, None, "inst"),
    "VIS":    ("VIS", "surface", "km", 0.01, "m2km", "inst"),
    "REFC":   ("REFC", "entire atmosphere", "dBZ", 0.01, None, "inst"),
    "DSWRF":  ("DSWRF", "surface", "W/m2", 0.1, None, "inst"),
    "PSFC":   ("PRES", "surface", "hPa", 0.1, "Pa2hPa", "inst"),
}


def sun_times(date, lat, lon):
    """NOAA solar position algorithm -> (sunrise, sunset) as naive UTC datetimes for a calendar date."""
    n = date.timetuple().tm_yday
    out = []
    for rising in (True, False):
        lng_hour = lon / 15
        t = n + ((6 if rising else 18) - lng_hour) / 24
        M = 0.9856 * t - 3.289
        L = (M + 1.916 * math.sin(math.radians(M)) + 0.020 * math.sin(math.radians(2 * M)) + 282.634) % 360
        RA = math.degrees(math.atan(0.91764 * math.tan(math.radians(L)))) % 360
        RA = (RA + (L // 90) * 90 - (RA // 90) * 90) / 15
        sin_dec = 0.39782 * math.sin(math.radians(L))
        cos_dec = math.cos(math.asin(sin_dec))
        cos_h = (math.cos(math.radians(90.833)) - sin_dec * math.sin(math.radians(lat))) / (cos_dec * math.cos(math.radians(lat)))
        H = (360 - math.degrees(math.acos(cos_h))) if rising else math.degrees(math.acos(cos_h))
        T = H / 15 + RA - 0.06571 * t - 6.622
        ut = (T - lng_hour) % 24
        out.append(dt.datetime(date.year, date.month, date.day) + dt.timedelta(hours=ut))
    return out


def windows(run, horizon_h=18):
    """Quarter-hour valid times (UTC) in the dawn/dusk daylight windows reachable by this run."""
    lat = (BBOX["north"] + BBOX["south"]) / 2
    lon = (BBOX["east"] + BBOX["west"]) / 2
    end = run + dt.timedelta(hours=horizon_h)
    times, spans = set(), []
    for d in range(-1, 3):
        day = (run - dt.timedelta(hours=7)).date() + dt.timedelta(days=d)   # local (Mountain) calendar day
        rise, sset = sun_times(day, lat, lon)
        # the UTC date of a local sunset can be the next day; recompute sunset on that UTC date
        if sset < rise:
            sset += dt.timedelta(days=1)
        for a, b, label in ((rise, rise + dt.timedelta(hours=WINDOW_H), "morning"),
                            (sset - dt.timedelta(hours=WINDOW_H), sset, "evening")):
            q0 = a.replace(second=0, microsecond=0) - dt.timedelta(minutes=a.minute % STEP_MIN)
            q = q0
            span = []
            while q <= b + dt.timedelta(minutes=STEP_MIN - 1):
                if run < q <= end:
                    times.add(q)
                    span.append(q)
                q += dt.timedelta(minutes=STEP_MIN)
            if span:
                spans.append({"label": label, "start": span[0], "end": span[-1], "sun": rise if label == "morning" else sset})
    return sorted(times), spans


def specs_for(run, times):
    """Group quarter-hour times by subhourly file: {fh: [(key@minute, 'subh', var, level, pred, conv)]}."""
    by_fh = {}
    for q in times:
        m = int((q - run).total_seconds() // 60)
        fh = math.ceil(m / 60)
        for key, (var, level, _u, _s, conv, kind) in SUB_FIELDS.items():
            if kind == "acc":
                pred = (lambda d, m=m: d.startswith(f"{m - 15}-{m} min acc"))
            elif kind == "avg5":
                pred = (lambda d, m=m: d.startswith(f"{m - 5}-{m} min ave"))
            else:
                pred = (lambda d, m=m: d == f"{m} min fcst")
            by_fh.setdefault(fh, []).append((f"{key}@{m}", "subh", var, level, pred, conv))
    return by_fh
