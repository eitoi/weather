"""How well does the weak-layer ledger match the avalanche centers? Scores it zone by zone, day by day.

    python -m ingest.avy_verify            (also runs in every avalanche build and in the daily history update)
    python -m ingest.avy_verify --tune     (grid-search the ledger thresholds against the saved seasons)

Two versions of the ledger are compared with every avalanche.org forecast saved in history/avy.db:

  snotel  past seasons since Oct 2022 and this one: the ledger driven only by the SNOTEL stations inside each center
          zone (median snow depth, daily SWE gain and air temperature). No humidity, wind or cloud, so it cannot see
          surface hoar or wind loading; clear nights are guessed from a large day-night temperature range.
  hrrr    this season: the full ledger, from HRRR for the ranges matched to each zone.

A day counts as a persistent slab day for the center when its forecast lists a Persistent Slab or Deep Persistent
Slab problem, and for the model when its persistent-slab danger floor is Moderate or higher. Results go to the
verify_day table (one row per source, zone and forecast day, rewritten each run) and verify_summary(). Forecasts
that list no problems at all are left out. hss is the Heidke skill score: 0 is no better than chance, 1 is perfect.
"""
import datetime as dt
import json
import logging
import sys
import warnings

import numpy as np

from . import avy_history as H
from . import avy_layers as LY

log = logging.getLogger("avy-verify")
FIRST_SEASON = H.CENTER_BACKFILL_FROM
# The SNOTEL proxy's surface (air minus 3 degC) runs colder than HRRR's skin temperature, so its gradient threshold is
# set higher; fitted with tune() like the shared thresholds in avy_layers.P
SNOTEL_P = {"tg": 20.0}
TUNE_GRID = dict(tg=[10, 20], heal=[0.5, 1, 2], dormant_c=[0.5, 0.8], active_c=[0.35, 0.6], thaw_days=[7, 10, 999])

SCHEMA = """
CREATE TABLE IF NOT EXISTS center_zone (center TEXT, zone TEXT, state TEXT, polys TEXT, updated TEXT, zone_id INTEGER,
  PRIMARY KEY (center, zone)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS verify_day (source TEXT, center TEXT, zone TEXT, day TEXT, center_ps INTEGER,
  center_danger INTEGER, model_floor INTEGER, PRIMARY KEY (source, center, zone, day)) WITHOUT ROWID;
"""


def _schema(db):
    db.executescript(SCHEMA)
    if "zone_id" not in [r[1] for r in db.execute("PRAGMA table_info(center_zone)")]:
        db.execute("ALTER TABLE center_zone ADD COLUMN zone_id INTEGER")


def save_zones(db, zones):
    """Keep each center zone's outline so stations can be matched to zones without the live map layer."""
    _schema(db)
    now = dt.datetime.utcnow().isoformat(timespec="seconds")
    for z in zones:
        if z.get("center_id") and z.get("name") and z.get("polys"):
            zid = z.get("id") if isinstance(z.get("id"), int) else None
            db.execute("INSERT OR REPLACE INTO center_zone VALUES (?, ?, ?, ?, ?, ?)",
                       (z["center_id"], z["name"], z.get("state"), json.dumps(z["polys"]), now, zid))
    db.commit()


def zone_stations(db):
    """(center, zone) -> SNOTEL station ids inside the zone (or within ~15 km of its edge when fewer than 2 are)."""
    from .avalanche import _inside
    st = db.execute("SELECT sid, lat, lon FROM snotel_station").fetchall()
    if not st:
        return {}
    sid = np.array([s[0] for s in st]); lat = np.array([s[1] for s in st]); lon = np.array([s[2] for s in st])
    out = {}
    for c, z, polys in db.execute("SELECT center, zone, polys FROM center_zone").fetchall():
        inn = np.zeros(len(sid), bool)
        near = np.zeros(len(sid), bool)
        for poly in json.loads(polys):
            ring = np.asarray(poly[0], float)
            if not ((lon > ring[:, 0].min() - .3) & (lon < ring[:, 0].max() + .3)
                    & (lat > ring[:, 1].min() - .3) & (lat < ring[:, 1].max() + .3)).any():
                continue
            hit = _inside(lon, lat, ring)
            for hole in poly[1:]:
                hit &= ~_inside(lon, lat, np.asarray(hole, float))
            inn |= hit
            d = np.min(np.hypot((lon[:, None] - ring[None, :, 0]) * np.cos(np.radians(lat[:, None])),
                                lat[:, None] - ring[None, :, 1]), axis=1)
            near |= d < 0.14
        pick = inn if inn.sum() >= 2 else inn | near
        if pick.any():
            out[(c, z)] = sid[pick].tolist()
    return out


def snotel_inputs(db, sids, d0, d1):
    """Ledger inputs for one zone from its stations' daily medians, shaped like avy_layers.inputs() for one track."""
    days = [d0 + dt.timedelta(days=i) for i in range((d1 - d0).days + 1)]
    ND = len(days)
    q = f"SELECT sid, day, snwd, wteq, tmax, tmin, tavg FROM snotel_daily WHERE sid IN ({','.join('?' * len(sids))}) AND day BETWEEN ? AND ?"
    A = np.full((len(sids), ND + 1, 5), np.nan, np.float32)
    si = {s: i for i, s in enumerate(sids)}
    base = H.dayno(d0)  # SNOTEL's midnight reading at the end of day D is dated D + 1
    for s, day, snwd, wteq, tmax, tmin, tavg in db.execute(q, (*sids, base, H.dayno(d1) + 1)):
        k = day - base
        if 0 <= k <= ND:
            A[si[s], k] = [np.nan if v is None else v for v in (snwd, wteq, tmax, tmin, tavg)]
    hs = A[..., 0] * 2.54                                   # cm
    swe = A[..., 1] * 2.54                                  # tenths of an inch -> mm
    tc = (A[..., 2:] / 10 - 32) / 1.8                       # degC: max, min, avg
    end = slice(1, ND + 1)
    dhs = np.nanmedian(hs[:, end] - hs[:, :ND], 0)
    dswe = np.nanmedian(swe[:, end] - swe[:, :ND], 0)
    tmax, tmin, tavg = (np.nanmedian(tc[:, end, j], 0) for j in range(3))
    clear = np.where(tmax - tmin >= LY.P["clear"], 10.0, 80.0)          # big day-night range ~ clear sky
    one = lambda a: np.asarray(a, np.float32)[None]
    nan = np.full((1, ND), np.nan, np.float32)
    return dict(days=days, keys=[(0, 2)], lat={(0, 2): float(np.median([r[0] for r in db.execute(
                    f"SELECT lat FROM snotel_station WHERE sid IN ({','.join('?' * len(sids))})", sids)]))},
                hs=one(np.nanmedian(hs[:, end], 0)), hn=one(np.maximum(dhs, 0)), swe=one(np.maximum(dswe, 0)),
                rain=one(np.where((tavg > 1) & (dhs <= 0), np.maximum(dswe, 0), 0)),
                t2mean=one(tavg), t2max=one(tmax), tskmean=one(tavg - 3), tskmax=one(tmax - 1),
                tsknight=np.repeat(one(tmin - 2)[..., None], 2, -1), tdnight=np.full((1, ND, 2), np.nan, np.float32),
                wnight=np.full((1, ND, 2), np.nan, np.float32), cnight=np.repeat(one(clear)[..., None], 2, -1),
                w10mean=nan, w80mean=nan, sw=nan, have=one(np.isfinite(tavg) & np.isfinite(np.nanmedian(hs[:, end], 0))))


def center_days(db, center, zone, d0, d1):
    """day -> (persistent slab listed?, highest danger that day) for one zone's saved forecasts that list problems
    (matched by name, or by zone id so renamed zones still count)."""
    zid = (db.execute("SELECT zone_id FROM center_zone WHERE center = ? AND zone = ?", (center, zone)).fetchone() or [None])[0]
    rows = db.execute("""SELECT f.day, count(p.forecast_id), max(coalesce(p.name LIKE '%Persistent%', 0)),
                         max(f.lower, f.middle, f.upper)
                         FROM center_forecast f LEFT JOIN center_problem p ON p.forecast_id = f.id
                         WHERE f.center = ? AND (f.zone = ? OR f.zone_id = ?) AND f.day BETWEEN ? AND ? GROUP BY f.day""",
                      (center, zone, zid, d0.isoformat(), d1.isoformat())).fetchall()
    # a forecast with no problems listed (UAC's feed, early-season bulletins) says nothing either way, so it is skipped
    return {d: (int(ps or 0), dg) for d, n, ps, dg in rows if n}


def seasons(today):
    s = H.season_start(FIRST_SEASON)
    while s <= today:
        yield s, min(dt.date(s.year + 1, 5, 31), today)
        s = dt.date(s.year + 1, 10, 1)


def snotel_cases(db, today):
    """[(center, zone, season start, ledger inputs, {day index: (ps, danger)})] for every zone-season with labels."""
    warnings.simplefilter("ignore", RuntimeWarning)
    zs = zone_stations(db)
    out = []
    for (c, z), sids in zs.items():
        for d0, d1 in seasons(today):
            cd = center_days(db, c, z, d0, d1)
            if cd:
                X = snotel_inputs(db, sids, d0, d1)
                out.append((c, z, d0, X, {i: cd[d.isoformat()] for i, d in enumerate(X["days"]) if d.isoformat() in cd}))
    return out, len(zs)


def snotel_floor(X, params=None):
    keep = dict(LY.P)
    LY.P.update(SNOTEL_P, **(params or {}))
    try:
        return np.maximum(*(LY.ledger(X, 0, a, 2)[0] for a in LY.ASPECTS))
    finally:
        LY.P.clear(); LY.P.update(keep)


def run_snotel(db, today):
    cases, nz = snotel_cases(db, today)
    rows = []
    for c, z, _d0, X, lab in cases:
        fl = snotel_floor(X)
        rows += [("snotel", c, z, X["days"][i].isoformat(), ps, dg, int(fl[i])) for i, (ps, dg) in lab.items()]
    return rows, nz


def run_hrrr(db, led, days, zones, zmap):
    """The full HRRR ledger for this season, in zones matched to ranges (max over the matched ranges and bands)."""
    by = {}
    for r, zi in zmap.items():
        z = zones[zi]
        by.setdefault((z["center_id"], z["name"]), []).append(r)
    rows = []
    for (c, z), rs in by.items():
        cd = center_days(db, c, z, days[0], days[-1])
        for i, day in enumerate(days):
            k = day.isoformat()
            if k in cd:
                f = max((led[(r, b)]["series"][i] for r in rs for b in (1, 2, 3) if (r, b) in led), default=0)
                rows.append(("hrrr", c, z, k, cd[k][0], cd[k][1], int(f)))
    return rows


def score(rows):
    t = {"both": 0, "center_only": 0, "model_only": 0, "neither": 0}
    for _s, _c, _z, _d, cps, _dg, mf in rows:
        m = mf >= 2
        t["both" if cps and m else "center_only" if cps else "model_only" if m else "neither"] += 1
    n = sum(t.values())
    hit, fa = t["both"] + t["center_only"], t["both"] + t["model_only"]
    pe = ((t["both"] + t["center_only"]) * fa + (t["model_only"] + t["neither"]) * (n - fa)) / n ** 2 if n else 1
    t.update(days=n, agree=round((t["both"] + t["neither"]) / n, 3) if n else None,
             hss=round(((t["both"] + t["neither"]) / n - pe) / (1 - pe), 3) if pe < 1 else None,  # skill over chance
             pod=round(t["both"] / hit, 3) if hit else None,           # share of the center's PS days the model caught
             far=round(t["model_only"] / fa, 3) if fa else None)       # share of the model's PS days the center didn't list
    return t


def verify_summary(db):
    """{source: {"all": score, "seasons": {...}, "zones": {"CENTER:zone": score}}} from verify_day."""
    _schema(db)
    rows = db.execute("SELECT source, center, zone, day, center_ps, center_danger, model_floor FROM verify_day").fetchall()
    out = {}
    for src in sorted({r[0] for r in rows}):
        rs = [r for r in rows if r[0] == src]
        seas = {}
        for r in rs:
            d = dt.date.fromisoformat(r[3])
            seas.setdefault(f"{H.season_start(d).year}-{(H.season_start(d).year + 1) % 100:02d}", []).append(r)
        zones = {}
        for r in rs:
            zones.setdefault(f"{r[1]}:{r[2]}", []).append(r)
        out[src] = {"all": score(rs), "seasons": {k: score(v) for k, v in sorted(seas.items())},
                    "zones": {k: score(v) for k, v in sorted(zones.items())}}
    return out


def run(db, today=None, st=None, zones=None, zmap=None, led=None, days=None):
    """Recompute verify_day (SNOTEL for every season; HRRR for this one when its ledger is given)."""
    today = today or dt.datetime.utcnow().date()
    _schema(db)
    if zones:
        save_zones(db, zones)
    rows, nz = run_snotel(db, today)
    if led is not None and zones and zmap:
        rows += run_hrrr(db, led, days, zones, zmap)
    srcs = {r[0] for r in rows}
    for s in srcs:
        db.execute("DELETE FROM verify_day WHERE source = ?", (s,))
    db.executemany("INSERT OR REPLACE INTO verify_day VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    db.commit()
    log.info("verify: %d zones with SNOTEL, %d zone-days compared", nz, len(rows))
    return verify_summary(db)


def report(summary):
    lines = []
    for src, s in summary.items():
        a = s["all"]
        lines.append(f"{src}: {a['days']} zone-days, agree {a['agree']}, caught {a['pod']} of the centers' persistent slab days, "
                     f"{a['far']} of its own were not listed by the center, skill {a['hss']}")
        for k, v in s["seasons"].items():
            lines.append(f"  {k}: {v['days']} days, agree {v['agree']}, POD {v['pod']}, FAR {v['far']}, HSS {v['hss']}")
        worst = sorted((v for v in s["zones"].items() if v[1]["days"] >= 20), key=lambda kv: kv[1]["agree"] or 0)
        for k, v in (worst if len(worst) <= 10 else worst[:5] + worst[-5:]):
            lines.append(f"    {k}: {v['days']} days, agree {v['agree']}, POD {v['pod']}, FAR {v['far']}")
    return "\n".join(lines) or "nothing to compare yet (no center forecasts or SNOTEL history saved)"


def _tune_one(args):
    cases, params = args
    by = {}
    for _c, _z, d0, X, lab in cases:
        fl = snotel_floor(X, params)
        by.setdefault(d0.year, []).extend(("", "", "", "", ps, dg, int(fl[i])) for i, (ps, dg) in lab.items())
    return params, {y: score(r)["hss"] for y, r in sorted(by.items())}


def tune(db, today, grid=None):
    """Score every combination in the grid season by season; rank by mean skill, and show what each season would
    have been given by the combination picked on the other seasons alone (an honest out-of-sample number)."""
    import itertools
    from multiprocessing import Pool
    cases, _ = snotel_cases(db, today)
    grid = grid or TUNE_GRID
    combos = [dict(zip(grid, v)) for v in itertools.product(*grid.values())]
    with Pool() as p:
        res = p.map(_tune_one, [(cases, c) for c in combos], chunksize=max(1, len(combos) // 16))
    mean = lambda h: sum(h.values()) / len(h)
    res.sort(key=lambda r: -mean(r[1]))
    lines = [f"now {_tune_one((cases, {}))[1]}"] + [f"{round(mean(h), 3)} {h} {p}" for p, h in res[:8]]
    for y in res[0][1]:
        p, h = max(res, key=lambda r: sum(v for k, v in r[1].items() if k != y))
        lines.append(f"held out {y}: {h[y]} with {p}")
    return "\n".join(lines)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    from .avalanche import official, match_zones, STATIC
    db = H.connect()
    today = dt.datetime.utcnow().date()
    if "--tune" in sys.argv:
        print(tune(db, today))
        return
    off = official()
    zones = off["zones"]
    led = days = zmap = None
    if zones and db.execute("SELECT count(*) FROM hrrr_done").fetchone()[0]:
        st = dict(np.load(STATIC))
        st["dem"] = st["dem"].astype(np.float32)
        zmap = match_zones(st, zones)
        led, days = LY.run(db, st, today)
    print(report(run(db, today, zones=zones, zmap=zmap, led=led, days=days)))


if __name__ == "__main__":
    main()
