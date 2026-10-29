"""Season-long record for the avalanche estimate: SNOTEL history, HRRR at every range and station, center problems.

    python -m ingest.avy_history status
    python -m ingest.avy_history update [--budget SECONDS]

Kept the way the weather agent keeps data/history.db: one SQLite file (history/avy.db) that only grows, and every
update catches up whatever is missing, so a gap (nobody fetched avalanche data for a week) fills itself next time.

  snotel_station, snotel_daily   every SNOTEL station in the ten states, active or not, daily from its first record:
                                 snow depth, SWE, air temperature max / min / average, accumulated precipitation
  hrrr_track                     HRRR every 6 h since the season started (1 Oct), as the median over each range's
                                 elevation band: temperature, dew point, skin temperature, wind, cloud, radiation,
                                 snowfall, precipitation and snow depth
  hrrr_station                   the same fields at the HRRR point nearest each active SNOTEL station, so HRRR can be
                                 compared with what the station measured (the snotel_vs_hrrr view)
  center_forecast, center_problem  every avalanche.org forecast in the ten states: danger by elevation and the
                                 avalanche problems with their aspects and elevations, likelihood and size

Numbers are stored as integers x10 to keep the file small; the *_v views convert them back (cm, mm, degC, m/s).
Old SNOTEL years and past seasons' center forecasts are backfilled a slice at a time within --budget seconds.
In GitHub Actions the file is kept as an asset of the "avy-history" release (see .github/workflows/update.yml).
"""
import argparse
import datetime as dt
import json
import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import gribio as G
from . import hrrr as HR

log = logging.getLogger("avy-history")
ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "history" / "avy.db"
CACHE = ROOT / "cache" / "avy"
STATIC = Path(__file__).with_name("avy_static.npz")
STATES = ["WA", "OR", "CA", "NV", "ID", "MT", "WY", "UT", "CO", "NM"]
AWDB = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"
AVA = "https://api.avalanche.org/v2/public"
SNOTEL_ELEMENTS = ["SNWD", "WTEQ", "TMAX", "TMIN", "TAVG", "PREC"]
CENTER_BACKFILL_FROM = dt.date(2022, 10, 1)   # past seasons of center forecasts to collect, oldest first
EPOCH = dt.date(1970, 1, 1)

# HRRR fields per 6-h window, all from the f06 file of the run 6 h before the window ends.
WINDOW_SPEC = {
    "asnow": ("ASNOW", "surface", "0-6 hour acc fcst"), "apcp": ("APCP", "surface", "0-6 hour acc fcst"),
    "t2": ("TMP", "2 m above ground", "6 hour fcst"), "td2": ("DPT", "2 m above ground", "6 hour fcst"),
    "tsk": ("TMP", "surface", "6 hour fcst"),
    "u10": ("UGRD", "10 m above ground", "6 hour fcst"), "v10": ("VGRD", "10 m above ground", "6 hour fcst"),
    "u80": ("UGRD", "80 m above ground", "6 hour fcst"), "v80": ("VGRD", "80 m above ground", "6 hour fcst"),
    "tcdc": ("TCDC", "entire atmosphere", "6 hour fcst"),
    "dswrf": ("DSWRF", "surface", "6 hour fcst"), "dlwrf": ("DLWRF", "surface", "6 hour fcst"),
    "snod": ("SNOD", "surface", "6 hour fcst"),
}
# what hrrr_track / hrrr_station keep, in display units (x10 in the table)
FIELDS = ["asnow", "apcp", "t2", "td2", "tsk", "w10", "w80", "tcdc", "dswrf", "dlwrf", "snod"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS snotel_station (sid INTEGER PRIMARY KEY, triplet TEXT UNIQUE, name TEXT, state TEXT,
  lat REAL, lon REAL, elev_ft REAL, begin TEXT, active INTEGER, through TEXT, hrrr_idx INTEGER, hrrr_hgt REAL);
CREATE TABLE IF NOT EXISTS snotel_daily (sid INTEGER, day INTEGER, snwd INTEGER, wteq INTEGER, tmax INTEGER,
  tmin INTEGER, tavg INTEGER, prec INTEGER, PRIMARY KEY (sid, day)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS hrrr_track (valid INTEGER, range_id INTEGER, band INTEGER, asnow INTEGER, apcp INTEGER,
  t2 INTEGER, td2 INTEGER, tsk INTEGER, w10 INTEGER, w80 INTEGER, tcdc INTEGER, dswrf INTEGER, dlwrf INTEGER,
  snod INTEGER, PRIMARY KEY (valid, range_id, band)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS hrrr_station (valid INTEGER, sid INTEGER, asnow INTEGER, apcp INTEGER, t2 INTEGER,
  td2 INTEGER, tsk INTEGER, w10 INTEGER, w80 INTEGER, tcdc INTEGER, dswrf INTEGER, dlwrf INTEGER, snod INTEGER,
  PRIMARY KEY (valid, sid)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS hrrr_done (valid INTEGER PRIMARY KEY, cycle TEXT, fetched TEXT);
CREATE TABLE IF NOT EXISTS center_forecast (id INTEGER PRIMARY KEY, center TEXT, zone_id INTEGER, zone TEXT,
  day TEXT, published TEXT, expires TEXT, rating INTEGER, lower INTEGER, middle INTEGER, upper INTEGER);
CREATE INDEX IF NOT EXISTS center_forecast_day ON center_forecast (center, zone, day);
CREATE TABLE IF NOT EXISTS center_problem (forecast_id INTEGER, rank INTEGER, problem_id INTEGER, name TEXT,
  likelihood TEXT, size_min REAL, size_max REAL, location TEXT, PRIMARY KEY (forecast_id, rank)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS center_month (center TEXT, month TEXT, PRIMARY KEY (center, month)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE VIEW IF NOT EXISTS snotel_daily_v AS SELECT s.triplet, s.name, date(d.day * 86400, 'unixepoch') AS date,
  d.snwd * 2.54 AS snow_depth_cm, d.wteq * 0.254 AS swe_cm, (d.tmax / 10.0 - 32) / 1.8 AS tmax_c,
  (d.tmin / 10.0 - 32) / 1.8 AS tmin_c, (d.tavg / 10.0 - 32) / 1.8 AS tavg_c, d.prec * 0.254 AS precip_accum_cm
  FROM snotel_daily d JOIN snotel_station s USING (sid);
CREATE VIEW IF NOT EXISTS hrrr_track_v AS SELECT datetime(valid * 3600, 'unixepoch') AS valid_utc, range_id, band,
  asnow / 10.0 AS snow_cm, apcp / 10.0 AS precip_mm, t2 / 10.0 AS t2_c, td2 / 10.0 AS dewpoint_c,
  tsk / 10.0 AS skin_c, w10 / 10.0 AS wind10_ms, w80 / 10.0 AS wind80_ms, tcdc / 10.0 AS cloud_pct,
  dswrf / 10.0 AS sw_down, dlwrf / 10.0 AS lw_down, snod / 10.0 AS snow_depth_cm FROM hrrr_track;
CREATE VIEW IF NOT EXISTS snotel_vs_hrrr AS SELECT s.triplet, s.name, date(h.valid * 3600, 'unixepoch') AS date,
  avg(h.t2) / 10.0 AS hrrr_t2_c, (d.tavg / 10.0 - 32) / 1.8 AS snotel_tavg_c, sum(h.apcp) / 10.0 AS hrrr_precip_mm,
  max(h.snod) / 10.0 AS hrrr_depth_cm, d.snwd * 2.54 AS snotel_depth_cm
  FROM hrrr_station h JOIN snotel_station s USING (sid)
  LEFT JOIN snotel_daily d ON d.sid = h.sid AND d.day = h.valid / 24
  GROUP BY h.sid, h.valid / 24;
"""


def connect(path=DB):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=60)
    db.executescript(SCHEMA)
    return db


def season_start(today):
    return dt.date(today.year if today.month >= 8 else today.year - 1, 10, 1)


def dayno(d):
    return (d - EPOCH).days


def vhour(v):
    """Naive UTC datetime -> hours since 1970 (the valid key of the HRRR tables)."""
    return int((v - dt.datetime(1970, 1, 1)).total_seconds() // 3600)


def x10(v):
    return None if v is None or not np.isfinite(v) else int(round(float(v) * 10))


# ---------------------------------------------------------------- HRRR windows

def _fetch(run, fh, wanted, idx):
    """wanted: {key: (var, level, desc)}; idx: {name: flat HRRR indices} -> {name: {key: float32 values}}."""
    u = HR.url(run, fh, "sfc")
    ents = G.parse_idx(G.http_get(u + ".idx").decode())
    pick = {}
    for key, (var, level, desc) in wanted.items():
        e = G.find(ents, var, level, lambda d, desc=desc: d == desc)
        if e is None:
            raise KeyError(f"{var}:{level}:{desc} not in {u}")
        pick[e] = key
    msgs = G.fetch_ranges(u, G.merge_ranges(list(pick)))
    out = {name: {} for name in idx}
    for e, key in pick.items():
        vals = G.decode(msgs[e])[0].ravel()
        for name, ix in idx.items():
            out[name][key] = vals[ix].astype(np.float32)
    return out


def latest_cycle():
    now = dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    for k in range(1, 30):
        c = now - dt.timedelta(hours=k)
        if c.hour % 6 == 0 and G.exists(HR.url(c, 24, "sfc") + ".idx"):
            return c
    raise RuntimeError("no HRRR cycle with a 24-h forecast")


def derive(f, dem, hgt):
    """Raw window fields -> display units, with temperatures moved from HRRR's smoothed terrain to the real height."""
    dz = dem - hgt
    return {"asnow": f["asnow"] * 100, "apcp": f["apcp"], "t2": f["t2"] - 273.15 - 0.0065 * dz,
            "td2": f["td2"] - 273.15 - 0.0018 * dz, "tsk": f["tsk"] - 273.15 - 0.0065 * dz,
            "w10": np.hypot(f["u10"], f["v10"]), "w80": np.hypot(f["u80"], f["v80"]), "tcdc": f["tcdc"],
            "dswrf": f["dswrf"], "dlwrf": f["dlwrf"], "snod": np.clip(f["snod"] * 100, 0, 600)}


def window(cyc, st, stations=None, keep_cells=True):
    """One 6-h window ending cyc + 6 h. Returns {"cells": derived fields at the mountain cells, "stations": ...}.
    Cell values are cached (cache/avy/w_*.npz) because the danger score reuses the last 7 days of them."""
    p = CACHE / f"w_{cyc:%Y%m%d%H}.npz"
    idx = {}
    cells = None
    if keep_cells and p.exists():
        try:
            with np.load(p) as z:
                cells = {k: z[k].astype(np.float32) for k in z.files}
        except Exception:  # noqa
            p.unlink(missing_ok=True)
    if cells is None:
        idx["cells"] = st["hidx"]
    if stations is not None and len(stations["idx"]):
        idx["stations"] = stations["idx"]
    raw = _fetch(cyc, 6, WINDOW_SPEC, idx) if idx else {}
    if cells is None:
        cells = derive(raw["cells"], st["dem"], st["hhgt"])
        if keep_cells:
            CACHE.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(p, **{k: v.astype(np.float16) for k, v in cells.items()})
    out = {"cells": cells}
    if "stations" in raw:
        out["stations"] = derive(raw["stations"], stations["elev"], stations["hgt"])
    return out


def tracks(st):
    """(range id, band) -> indices of its mountain cells."""
    rid, band = st["rid"].astype(int), st["band"].astype(int)
    key = rid * 4 + band
    order = np.argsort(key, kind="stable")
    ks, starts = np.unique(key[order], return_index=True)
    return {(int(k) // 4, int(k) % 4): order[a:b] for k, a, b in zip(ks, starts, list(starts[1:]) + [len(key)])}


def update_hrrr(db, st, budget_end, workers=16):
    """Fill hrrr_track (and hrrr_station) for every 6-h window from the season start to the newest run."""
    C = latest_cycle()
    start = dt.datetime.combine(season_start(C.date()), dt.time(6))
    done = {r[0] for r in db.execute("SELECT valid FROM hrrr_done")}
    want = []
    v = start
    while v <= C:
        if vhour(v) not in done:
            want.append(v)
        v += dt.timedelta(hours=6)
    stn = db.execute("SELECT sid, hrrr_idx, elev_ft, hrrr_hgt FROM snotel_station WHERE active = 1 AND hrrr_idx IS NOT NULL").fetchall()
    stations = {"sid": [r[0] for r in stn], "idx": np.array([r[1] for r in stn], np.int64),
                "elev": np.array([(r[2] or 0) * 0.3048 for r in stn], np.float32),
                "hgt": np.array([r[3] or 0 for r in stn], np.float32)}
    groups = tracks(st)
    recent = C - dt.timedelta(days=9)
    log.info("HRRR: %d of %d windows since %s still to fetch", len(want), len(want) + len(done), start.date())

    def one(valid):
        cyc = valid - dt.timedelta(hours=6)
        return valid, window(cyc, st, stations, keep_cells=valid > recent)

    n = 0
    with ThreadPoolExecutor(workers) as ex:
        for b in range(0, len(want), workers * 2):
            if time.time() > budget_end and n:
                log.info("HRRR: stopping for time with %d windows left (next run continues)", len(want) - b)
                break
            for valid, w in ex.map(one, want[b:b + workers * 2]):
                vh = vhour(valid)
                rows = []
                for (r, bd), ix in groups.items():
                    rows.append((vh, r, bd, *[x10(np.nanmedian(w["cells"][k][ix])) for k in FIELDS]))
                db.executemany(f"INSERT OR REPLACE INTO hrrr_track VALUES ({','.join('?' * (3 + len(FIELDS)))})", rows)
                if "stations" in w:
                    s = w["stations"]
                    db.executemany(f"INSERT OR REPLACE INTO hrrr_station VALUES ({','.join('?' * (2 + len(FIELDS)))})",
                                   [(vh, sid, *[x10(s[k][i]) for k in FIELDS]) for i, sid in enumerate(stations["sid"])])
                db.execute("INSERT OR REPLACE INTO hrrr_done VALUES (?, ?, ?)",
                           (vh, f"{valid - dt.timedelta(hours=6):%Y%m%d%H}", dt.datetime.utcnow().isoformat(timespec="seconds")))
                n += 1
            db.commit()
    for p in CACHE.glob("[hw]_*.npz"):  # cell values only matter for the last 7 days
        try:
            if dt.datetime.strptime(p.stem[2:], "%Y%m%d%H") < recent:
                p.unlink()
        except ValueError:
            pass
    return C


# ---------------------------------------------------------------- SNOTEL

def _hrrr_points(lats, lons):
    """Flat HRRR index and model terrain height nearest each lat/lon (lat/lon grid cached in cache/avy)."""
    p = CACHE / "hrrr_ll.npz"
    if not p.exists():
        c = latest_cycle()
        u = HR.url(c, 0, "sfc")
        ents = G.parse_idx(G.http_get(u + ".idx").decode())
        e = G.find(ents, "HGT", "surface", lambda d: d == "anl")
        msg = G.fetch_ranges(u, G.merge_ranges([e]))[e]
        hgt, _i, la, lo = G.decode(msg, with_latlon=True)
        CACHE.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(p, lat=la.ravel().astype(np.float32), lon=lo.ravel().astype(np.float32), hgt=hgt.ravel())
    with np.load(p) as z:
        la, lo, hg = z["lat"], z["lon"], z["hgt"]
    out = []
    for a, b in zip(lats, lons):
        m = np.nonzero((np.abs(la - a) < 0.04) & (np.abs(lo - b) < 0.05))[0]
        if not len(m):
            out.append((None, None)); continue
        k = m[np.argmin((la[m] - a) ** 2 + ((lo[m] - b) * np.cos(np.radians(a))) ** 2)]
        out.append((int(k), float(hg[k])))
    return out


def update_stations(db):
    """Refresh the station list (active and retired SNOTEL sites in the ten states)."""
    c = G.client()
    stations = []
    for s in STATES:
        r = c.get(f"{AWDB}/stations", params={"stationTriplets": f"*:{s}:SNTL", "activeOnly": "false",
                                              "returnForecastPointMetadata": "false", "returnReservoirMetadata": "false",
                                              "returnStationElements": "false"}, timeout=120)
        r.raise_for_status()
        stations += r.json()
    have = {r[0]: r[1] for r in db.execute("SELECT triplet, hrrr_idx FROM snotel_station")}
    today = dt.date.today().isoformat()
    new = [s for s in stations if s.get("stationTriplet") and s.get("latitude") and s.get("longitude")]
    need = [s for s in new if have.get(s["stationTriplet"]) is None]
    pts = dict(zip([s["stationTriplet"] for s in need],
                   _hrrr_points([float(s["latitude"]) for s in need], [float(s["longitude"]) for s in need]))) if need else {}
    for s in new:
        t = s["stationTriplet"]
        active = 1 if not s.get("endDate") or s["endDate"][:10] >= today else 0
        db.execute("""INSERT INTO snotel_station (triplet, name, state, lat, lon, elev_ft, begin, active)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(triplet) DO UPDATE SET name = excluded.name,
                      lat = excluded.lat, lon = excluded.lon, elev_ft = excluded.elev_ft, begin = excluded.begin,
                      active = excluded.active""",
                   (t, s.get("name"), s.get("stateCode") or t.split(":")[1], float(s["latitude"]), float(s["longitude"]),
                    s.get("elevation"), (s.get("beginDate") or "1978-10-01")[:10], active))
        if t in pts and pts[t][0] is not None:
            db.execute("UPDATE snotel_station SET hrrr_idx = ?, hrrr_hgt = ? WHERE triplet = ?", (*pts[t], t))
    db.commit()
    return len(new)


def _snotel_data(db, rows, begin, end):
    """Download daily values for [(sid, triplet)] between two dates and upsert them."""
    by = {t: sid for sid, t in rows}
    r = G.client().get(f"{AWDB}/data", params={"stationTriplets": ",".join(by), "elements": ",".join(SNOTEL_ELEMENTS),
                                               "duration": "DAILY", "beginDate": begin, "endDate": end}, timeout=300)
    r.raise_for_status()
    vals = {}
    for row in r.json():
        sid = by.get(row.get("stationTriplet"))
        if sid is None:
            continue
        for el in row.get("data", []):
            code = (el.get("stationElement") or {}).get("elementCode")
            if code not in SNOTEL_ELEMENTS:
                continue
            j = SNOTEL_ELEMENTS.index(code)
            for v in el.get("values", []):
                if v.get("value") is None:
                    continue
                k = (sid, dayno(dt.date.fromisoformat(v["date"][:10])))
                vals.setdefault(k, [None] * 6)[j] = int(round(v["value"] * (1 if code == "SNWD" else 10)))
    db.executemany("""INSERT INTO snotel_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(sid, day) DO UPDATE SET
                      snwd = coalesce(excluded.snwd, snwd), wteq = coalesce(excluded.wteq, wteq),
                      tmax = coalesce(excluded.tmax, tmax), tmin = coalesce(excluded.tmin, tmin),
                      tavg = coalesce(excluded.tavg, tavg), prec = coalesce(excluded.prec, prec)""",
                   [(*k, *v) for k, v in vals.items()])
    return len(vals)


def update_snotel(db, budget_end):
    """The last 30 days for every active station (values get revised), then full histories within the budget."""
    n = update_stations(db)
    today = dt.date.today()
    recent = db.execute("SELECT sid, triplet FROM snotel_station WHERE active = 1 AND through IS NOT NULL").fetchall()
    begin = (today - dt.timedelta(days=30)).isoformat()
    got = 0
    for b in range(0, len(recent), 50):
        got += _snotel_data(db, recent[b:b + 50], begin, today.isoformat())
        db.executemany("UPDATE snotel_station SET through = ? WHERE sid = ?", [(today.isoformat(), r[0]) for r in recent[b:b + 50]])
        db.commit()
    todo = db.execute("SELECT sid, triplet, begin FROM snotel_station WHERE through IS NULL ORDER BY active DESC, sid").fetchall()
    full = 0
    for b in range(0, len(todo), 8):
        if time.time() > budget_end:
            log.info("SNOTEL: %d stations still to backfill (next run continues)", len(todo) - b)
            break
        chunk = todo[b:b + 8]
        try:
            got += _snotel_data(db, [(s, t) for s, t, _b in chunk], min(c[2] for c in chunk), today.isoformat())
            ok = chunk
        except Exception as e:  # noqa  (a very long request can fail: retry each station a decade at a time)
            log.info("SNOTEL: batch failed (%s); fetching station by station", e)
            ok = []
            for s, t, begin in chunk:
                try:
                    y = int(begin[:4])
                    while y <= today.year:
                        got += _snotel_data(db, [(s, t)], max(begin, f"{y}-01-01"), min(f"{y + 9}-12-31", today.isoformat()))
                        y += 10
                    ok.append((s, t, begin))
                except Exception as e2:  # noqa
                    log.warning("SNOTEL: %s history failed, will retry next run: %s", t, e2)
        db.executemany("UPDATE snotel_station SET through = ? WHERE sid = ?", [(today.isoformat(), c[0]) for c in ok])
        db.commit()
        full += len(ok)
    log.info("SNOTEL: %d stations listed, %d station-days written, %d full histories added", n, got, full)


def station_list(db, today, ranges_of=None):
    """Active stations reporting in the last 2 days, shaped like the map's SNOTEL markers."""
    d0 = dayno(today) - 9
    rows = db.execute("""SELECT s.sid, s.triplet, s.name, s.lat, s.lon, s.elev_ft, d.day, d.snwd, d.wteq
                         FROM snotel_station s JOIN snotel_daily d USING (sid)
                         WHERE s.active = 1 AND d.day >= ? AND d.snwd IS NOT NULL ORDER BY s.sid, d.day""", (d0,)).fetchall()
    by = {}
    for sid, t, name, lat, lon, elev, day, snwd, wteq in rows:
        by.setdefault(sid, {"meta": (t, name, lat, lon, elev), "d": {}})["d"][day] = (snwd, wteq)
    out = []
    for sid, s in by.items():
        last = max(s["d"])
        if dayno(today) - last > 2:
            continue
        hs = s["d"][last][0] * 2.54
        ch = lambda n: None if (last - n) not in s["d"] else round((s["d"][last][0] - s["d"][last - n][0]) * 2.54, 1)
        t, name, lat, lon, elev = s["meta"]
        swe = s["d"][last][1]
        out.append({"name": name, "id": t, "sid": sid, "lat": round(lat, 4), "lon": round(lon, 4), "elev_ft": elev,
                    "date": (EPOCH + dt.timedelta(days=last)).isoformat(), "hs": round(hs, 1), "d24": ch(1), "d72": ch(3),
                    "swe": None if swe is None else round(swe / 10 * 2.54, 1)})
    return out


# ---------------------------------------------------------------- avalanche center forecasts

def _get(path, params=None):
    r = G.client().get(f"{AVA}/{path}", params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def _save_product(db, p):
    z = (p.get("forecast_zone") or [{}])[0]
    pub = p.get("published_time") or p.get("start_date")
    if not pub:
        return
    day = (dt.datetime.fromisoformat(pub.replace("Z", "+00:00")) + dt.timedelta(hours=6)).date().isoformat()
    cur = next((d for d in p.get("danger") or [] if d.get("valid_day") == "current"), {})
    db.execute("INSERT OR REPLACE INTO center_forecast VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (p["id"], (p.get("avalanche_center") or {}).get("id"), z.get("id"), z.get("name"), day, pub,
                p.get("expires_time"), p.get("danger_rating"), cur.get("lower"), cur.get("middle"), cur.get("upper")))
    db.execute("DELETE FROM center_problem WHERE forecast_id = ?", (p["id"],))
    for q in p.get("forecast_avalanche_problems") or []:
        size = [float(x) for x in (q.get("size") or []) if str(x).replace(".", "", 1).isdigit()]
        db.execute("INSERT OR REPLACE INTO center_problem VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (p["id"], q.get("rank"), q.get("avalanche_problem_id"), q.get("name"), q.get("likelihood"),
                    min(size) if size else None, max(size) if size else None, json.dumps(q.get("location") or [])))


def _center_month(db, center, month):
    """List one center's forecasts for one month and fetch the ones not saved yet. Returns products fetched."""
    first = dt.date.fromisoformat(month + "-01")
    last = min((first + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1), dt.date.today())
    items = _get("products", {"avalanche_center_id": center, "date_start": first.isoformat(), "date_end": last.isoformat()})
    have = {r[0] for r in db.execute("SELECT id FROM center_forecast WHERE center = ?", (center,))}
    n = 0
    for it in items:
        if it.get("product_type") != "forecast" or it.get("id") in have:
            continue
        _save_product(db, _get(f"product/{it['id']}"))
        n += 1
    db.commit()
    return n


def update_centers(db, centers, budget_end):
    """This season's forecasts for every center, then earlier seasons a month at a time within the budget."""
    today = dt.date.today()
    s0 = season_start(today)
    months = []
    m = dt.date(CENTER_BACKFILL_FROM.year, CENTER_BACKFILL_FROM.month, 1)
    while m <= today:
        if m.month >= 10 or m.month <= 5:  # forecast season only
            months.append(m.strftime("%Y-%m"))
        m = (m + dt.timedelta(days=32)).replace(day=1)
    done = {(c, mo) for c, mo in db.execute("SELECT center, month FROM center_month")}
    n = 0
    cur = [mo for mo in months if mo >= s0.strftime("%Y-%m")]
    past = [mo for mo in reversed(months) if mo < s0.strftime("%Y-%m")]  # newest past season first
    for mo in cur + past:
        for c in centers:
            if (c, mo) in done:
                continue
            if mo in past and time.time() > budget_end:
                log.info("centers: backfill paused at %s (next run continues)", mo)
                db.commit()
                return n
            try:
                n += _center_month(db, c, mo)
            except Exception as e:  # noqa
                log.warning("centers: %s %s failed: %s", c, mo, e)
                continue
            if mo < today.strftime("%Y-%m"):
                db.execute("INSERT OR IGNORE INTO center_month VALUES (?, ?)", (c, mo))
        db.commit()
    log.info("centers: %d forecasts saved", n)
    return n


def center_problems(db, center, zone, day):
    """The newest forecast for one zone issued on day or the day before, with its problems."""
    f = db.execute("""SELECT id, day, lower, middle, upper FROM center_forecast WHERE center = ? AND zone = ? AND day <= ?
                      AND day >= ? ORDER BY day DESC, published DESC LIMIT 1""",
                   (center, zone, day.isoformat(), (day - dt.timedelta(days=1)).isoformat())).fetchone()
    if not f:
        return None
    probs = db.execute("SELECT rank, name, likelihood, size_min, size_max, location FROM center_problem WHERE forecast_id = ? ORDER BY rank",
                       (f[0],)).fetchall()
    return {"day": f[1], "danger": [f[2], f[3], f[4]],
            "problems": [{"name": n, "likelihood": lk, "size": [a, b], "location": json.loads(loc or "[]")}
                         for _r, n, lk, a, b, loc in probs]}


# ---------------------------------------------------------------- entry points

def update(st=None, budget=600, workers=16, centers=None, parts=("snotel", "hrrr", "centers")):
    """Catch everything up. Required catch-up (this season) always runs; old backfills stop after budget seconds."""
    t0 = time.time()
    end = t0 + budget
    if st is None:
        st = dict(np.load(STATIC))
        st["dem"] = st["dem"].astype(np.float32)
        st["hhgt"] = st["hhgt"].astype(np.float32)
    db = connect()
    if "snotel" in parts:
        try:
            update_snotel(db, end)
        except Exception as e:  # noqa
            log.warning("SNOTEL update failed: %s", e)
    if "hrrr" in parts:
        update_hrrr(db, st, end + 1800, workers)  # this season's HRRR is needed for the estimate itself
    if "centers" in parts:
        if centers is None:
            from .avy_ranges import CENTERS
            centers = list(CENTERS)
        try:
            update_centers(db, centers, end)
        except Exception as e:  # noqa
            log.warning("center update failed: %s", e)
    db.execute("INSERT OR REPLACE INTO meta VALUES ('updated', ?)", (dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",))
    db.commit()
    log.info("history updated in %.0fs", time.time() - t0)
    return db


def status(db):
    q = lambda s: db.execute(s).fetchone()
    print(f"file: {DB} ({DB.stat().st_size / 1e6:.1f} MB)" if DB.exists() else f"file: {DB}")
    print("updated:", (q("SELECT value FROM meta WHERE key = 'updated'") or ["never"])[0])
    a, b, n = q("SELECT count(*), sum(active), sum(through IS NOT NULL) FROM snotel_station")
    print(f"SNOTEL: {a} stations ({b or 0} active), {n or 0} with history saved")
    n, d0, d1 = q("SELECT count(*), min(day), max(day) FROM snotel_daily")
    if n:
        print(f"        {n} station-days, {EPOCH + dt.timedelta(days=d0)} to {EPOCH + dt.timedelta(days=d1)}")
    n, v0, v1 = q("SELECT count(*), min(valid), max(valid) FROM hrrr_done")
    if n:
        f = lambda v: dt.datetime.utcfromtimestamp(v * 3600).strftime("%Y-%m-%d %HZ")
        print(f"HRRR:   {n} six-hour windows, {f(v0)} to {f(v1)}")
    n, d0, d1 = q("SELECT count(*), min(day), max(day) FROM center_forecast")
    print(f"Centers: {n} forecasts" + (f", {d0} to {d1}" if n else ""),
          f"with {q('SELECT count(*) FROM center_problem')[0]} problems")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "update"])
    ap.add_argument("--budget", type=float, default=600, help="seconds to spend on old backfills")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--only", choices=["snotel", "hrrr", "centers"], action="append")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if a.cmd == "update":
        db = update(budget=a.budget, workers=a.workers, parts=a.only or ("snotel", "hrrr", "centers"))
    else:
        db = connect()
    status(db)


if __name__ == "__main__":
    main()
