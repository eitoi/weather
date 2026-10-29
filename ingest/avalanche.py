"""Avalanche danger for ~150 mountain ranges in ten western states (WA OR CA NV ID MT WY UT CO NM).

    python -m ingest.avalanche [--out site/data/avy]     (also runs as part of ingest.run)

Official ratings come from the avalanche.org map layer wherever a center publishes one. Everywhere else
(and in the off season) each ~2 km mountain cell gets an estimated rating from HRRR snowfall, rain, wind and
temperature over the past 7 days plus the next 24 hours, with snow depth corrected toward nearby SNOTEL
stations. Each range and elevation band then takes the 75th-percentile cell score, raised where the season-long
weak-layer ledger (avy_layers.py) has a buried persistent layer under a slab. The season record it runs on
(SNOTEL history, HRRR since 1 Oct, the centers' daily problems) is kept by avy_history.py in history/avy.db.

Static inputs (rebuilt by tools/make_avy_static.py): ingest/avy_static.npz, site/img/avy-grid.png.
Output: avy.json (ranges, official zones, SNOTEL stations, meta).
"""
import argparse
import datetime as dt
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import avy_history as H
from . import avy_layers as LY
from . import avy_verify as AV
from . import gribio as G
from .avy_ranges import RANGES, CENTERS

log = logging.getLogger("avy")
ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).with_name("avy_static.npz")
CACHE = ROOT / "cache" / "avy"
STATES = H.STATES
MAP_LAYER = "https://api.avalanche.org/v2/public/products/map-layer"
NAMES = ["Minimal snow", "Low", "Moderate", "Considerable", "High", "Extreme"]


# ---------------------------------------------------------------- HRRR

def _fetch(run, fh, wanted, hidx):
    """wanted: {key: (var, level, desc)} -> {key: float32 values at the mountain cells}."""
    return H._fetch(run, fh, wanted, {"c": hidx})["c"]


def hrrr_fields(st, workers=16):
    """Past 7 days in 6-h windows plus current snow depth and the next 24 h, at the mountain cells."""
    hidx = st["hidx"]
    C = H.latest_cycle()

    def fcst(fh):
        spec = {"t2": ("TMP", "2 m above ground", f"{fh} hour fcst"),
                "u80": ("UGRD", "80 m above ground", f"{fh} hour fcst"), "v80": ("VGRD", "80 m above ground", f"{fh} hour fcst")}
        if fh == 24:
            spec.update(fasnow=("ASNOW", "surface", "0-1 day acc fcst"), fapcp=("APCP", "surface", "0-1 day acc fcst"))
        return _fetch(C, fh, spec, hidx)

    with ThreadPoolExecutor(workers) as ex:  # windows ending C - 6k; ingest.avy_history caches their cell values
        hs = ex.map(lambda k: H.window(C - dt.timedelta(hours=6 * k + 6), st)["cells"], range(28))
        fc = ex.map(fcst, (6, 12, 18, 24))
        now = ex.submit(_fetch, C, 0, {"snod": ("SNOD", "surface", "anl")}, hidx)
        hs, fc, now = list(hs), list(fc), now.result()

    lapse = -0.0065 * (st["dem"] - st["hhgt"])  # HRRR's smoothed terrain -> real cell elevation
    t = np.stack([h["t2"] for h in hs])                 # degC, already moved to the cell's height
    asnow = np.stack([h["asnow"] for h in hs])          # cm
    apcp = np.stack([h["apcp"] for h in hs])            # mm
    wind = np.stack([h["w80"] for h in hs[:12]])
    rain = np.where(t > 1.0, np.maximum(apcp - asnow, 0), 0)  # 10:1 snow ratio
    return C, dict(
        hs=np.clip(now["snod"] * 100, 0, 400), hn24=asnow[:4].sum(0), hn72=asnow[:12].sum(0), hn7d=asnow.sum(0),
        rain72=rain[:12].sum(0), tmax24=t[:4].max(0), wmean72=wind.mean(0), wmax72=wind.max(0),
        fhn24=fc[3]["fasnow"] * 100, ftmax=np.stack([f["t2"] - 273.15 + lapse for f in fc]).max(0),
        fwmax=np.stack([np.hypot(f["u80"], f["v80"]) for f in fc]).max(0),
        model_hs=np.clip(now["snod"] * 100, 0, 400))


# ---------------------------------------------------------------- SNOTEL

def merc_px(lon, lat, z):
    n = 256 * 2 ** z
    x = (np.asarray(lon) + 180) / 360 * n
    lr = np.radians(np.asarray(lat))
    return x, (1 - np.log(np.tan(lr) + 1 / np.cos(lr)) / np.pi) / 2 * n


def apply_snotel(st, w, stations):
    """Attach stations to nearby range cells and scale each range's modelled snow toward what they measure."""
    rid = st["rid"]
    hits = LY.attach(st, stations)
    for s in stations:
        if "_cell" in s:
            s["model_hs"] = round(float(w["model_hs"][s["_cell"]]), 1)
    for r, ss in hits.items():
        # Blend the model toward what the stations measure, scaled by each cell's height above or below them.
        m = rid == r
        st_elev = float(np.median([(s["elev_ft"] or 0) * 0.3048 for s in ss]))
        f = np.clip(1 + (st["dem"][m] - st_elev) / 1500, 0, 2)
        obs = float(np.median([s["hs"] for s in ss]))
        w["hs"][m] = np.clip(0.3 * w["hs"][m] + 0.7 * obs * f, 0, 400)
        for key, sk in (("hn24", "d24"), ("hn72", "d72")):
            d = [s[sk] for s in ss if s[sk] is not None]
            if d:
                w[key][m] = 0.4 * w[key][m] + 0.6 * max(float(np.median(d)), 0.0) * f
        w["hn72"][m] = np.maximum(w["hn72"][m], w["hn24"][m])
        w["hn7d"][m] = np.maximum(w["hn7d"][m], w["hn72"][m])
    return hits


# ---------------------------------------------------------------- official ratings

def _simplify(ring, tol=0.004):
    a = np.asarray(ring, float)
    if len(a) < 5:
        return a
    keep = np.zeros(len(a), bool); keep[0] = keep[-1] = True
    stack = [(0, len(a) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        d = a[j] - a[i]; seg = a[i + 1:j] - a[i]; L = np.hypot(*d)
        dist = np.hypot(seg[:, 0], seg[:, 1]) if L < 1e-9 else np.abs(seg[:, 0] * d[1] - seg[:, 1] * d[0]) / L
        k = int(np.argmax(dist))
        if dist[k] > tol:
            keep[i + 1 + k] = True
            stack += [(i, i + 1 + k), (i + 1 + k, j)]
    return a[keep]


def _polys(geom):
    if not geom:
        return []
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    return []


def _inside(lon, lat, ring):
    """Ray-casting point-in-polygon for arrays of points against one ring."""
    x, y = ring[:, 0], ring[:, 1]
    xj, yj = np.roll(x, 1), np.roll(y, 1)
    res = np.zeros(len(lon), bool)
    for a, b, c, d in zip(x, y, xj, yj):
        cond = ((b > lat) != (d > lat)) & (lon < (c - a) * (lat - b) / ((d - b) or 1e-12) + a)
        res ^= cond
    return res


def official():
    """Zones from the avalanche.org map layer in the ten states. Falls back to the last good copy (48 h)."""
    keep = CACHE / "official.json"
    try:
        r = G.client().get(MAP_LAYER, timeout=60)
        r.raise_for_status()
        fc = r.json()
        zones = []
        for f in fc.get("features", []):
            p = f.get("properties", {})
            if p.get("state") not in STATES:
                continue
            polys = []
            for poly in _polys(f.get("geometry")):
                rings = [np.round(_simplify(r), 3).tolist() for r in poly]
                if len(rings[0]) >= 4:
                    polys.append(rings)
            lvl = p.get("danger_level")
            zones.append({"id": f.get("id"), "name": p.get("name"), "center": p.get("center"), "center_id": p.get("center_id"),
                          "state": p.get("state"), "level": lvl if isinstance(lvl, int) else -1, "danger": p.get("danger"),
                          "travel_advice": p.get("travel_advice"), "link": p.get("link"), "off_season": p.get("off_season"),
                          "start": p.get("start_date"), "end": p.get("end_date"), "polys": polys})
        out = {"checked": dt.datetime.utcnow().isoformat(timespec="minutes") + "Z", "source": "api.avalanche.org", "zones": zones}
        CACHE.mkdir(parents=True, exist_ok=True)
        keep.write_text(json.dumps(out))
        return out
    except Exception as e:  # noqa
        log.warning("avalanche.org map layer failed: %s", e)
        if keep.exists():
            out = json.loads(keep.read_text())
            if dt.datetime.utcnow() - dt.datetime.fromisoformat(out["checked"].rstrip("Z")) < dt.timedelta(hours=48):
                out["stale"] = True
                return out
        return {"checked": None, "source": None, "zones": []}


def match_zones(st, zones):
    """range id -> zone index, by where the range's cells fall (falls back to the zone names in avy_ranges)."""
    lat, lon, rid = st["lat"], st["lon"], st["rid"]
    rng = np.random.default_rng(0)
    out = {}
    for k in range(1, len(RANGES) + 1):
        idx = np.nonzero(rid == k)[0]
        if not len(idx):
            continue
        idx = rng.choice(idx, min(60, len(idx)), replace=False)
        best, bestf = None, 0.0
        for zi, z in enumerate(zones):
            hit = np.zeros(len(idx), bool)
            for poly in z["polys"]:
                outer = np.asarray(poly[0])
                if not (outer[:, 0].min() - .1 < lon[idx].max() and outer[:, 0].max() + .1 > lon[idx].min()
                        and outer[:, 1].min() - .1 < lat[idx].max() and outer[:, 1].max() + .1 > lat[idx].min()):
                    continue
                inn = _inside(lon[idx], lat[idx], outer)
                for hole in poly[1:]:
                    inn &= ~_inside(lon[idx], lat[idx], np.asarray(hole))
                hit |= inn
            f = hit.mean()
            if f > bestf:
                best, bestf = zi, f
        if best is not None and bestf >= 0.3:
            out[k] = best
    # name fallback for ranges no polygon claimed
    by_name = {f'{z["center_id"]}:{z["name"]}': i for i, z in enumerate(zones)}
    for k, r in enumerate(RANGES, 1):
        if k not in out and r[3] in by_name:
            out[k] = by_name[r[3]]
    return out


# ---------------------------------------------------------------- danger model

def cell_scores(w, band):
    """1-5 on the North American danger scale for each cell (0 = minimal snow) from the last 7 days and next 24 h,
    plus the reasons that fired. Buried persistent weak layers come from the season-long ledger (avy_layers)."""
    hs, hn24, hn72, hn7d = w["hs"], w["hn24"], w["hn72"], w["hn7d"]
    rain, tmax, wmean, wmax, fhn = w["rain72"], np.maximum(w["tmax24"], w["ftmax"]), w["wmean72"], w["wmax72"], w["fhn24"]
    high = band >= 2
    snow = hs >= 15  # snow that fell and already melted does not count
    load = hn24 + 0.6 * (hn72 - hn24) + 0.25 * (hn7d - hn72)  # older snow has settled and bonded more
    load_eff = load * (1 + np.where(high, 1.0, 0.3) * np.clip((wmean - 5) / 10, 0, 0.8))
    s = 1 + np.interp(load_eff, [0, 10, 30, 55, 90], [0, 1, 2, 3, 4])
    drift = high & (hs >= 30) & (hn7d >= 10) & (wmax >= 15)
    s = np.where(drift, np.maximum(s, 2), s)
    ros = (hs >= 20) & (rain >= 5)
    s = np.where(ros, np.maximum(s, np.interp(rain, [5, 15, 30], [2, 3, 4])), s)
    warm = (tmax > 2) & (hn72 >= 10)
    s = s + np.where(warm, 0.5, 0)
    s = s + 0.6 * np.interp(fhn, [0, 10, 30, 60], [0, 0.5, 1.5, 2.5])
    s = np.where(hs < 40, np.minimum(s, 2), np.where(hs < 70, np.minimum(s, 3), s))
    s = np.where(snow, np.clip(s, 1, 5), 0)
    flags = dict(load=load_eff >= 10, drift=drift, ros=ros, warm=warm, storm=fhn >= 10, shallow=snow & (hs < 70))
    return s, flags


# ---------------------------------------------------------------- build

def build(out_dir, workers=16, use_snotel=True, budget=300):
    t0 = time.time()
    st = dict(np.load(STATIC))
    st["dem"] = st["dem"].astype(np.float32)
    st["hhgt"] = st["hhgt"].astype(np.float32)
    today = dt.datetime.utcnow().date()
    # the season record first: SNOTEL, every 6-h HRRR window since 1 Oct, and the centers' forecasts
    db = H.update(st, budget=budget, workers=workers, parts=("snotel", "hrrr", "centers") if use_snotel else ("hrrr", "centers"))
    C, w = hrrr_fields(st, workers)
    log.info("HRRR %s: %d mountain cells (%.0fs)", C, len(st["rid"]), time.time() - t0)

    stations = []
    if use_snotel:
        stations = H.station_list(db, today)
        hits = apply_snotel(st, w, stations)
        log.info("SNOTEL: %d stations reporting, %d in ranges", len(stations), sum(len(v) for v in hits.values()))

    off = official()
    zones = off["zones"]
    zmap = match_zones(st, zones) if zones else {}
    log.info("official: %d zones, %d rated, %d ranges matched", len(zones), sum(z["level"] >= 1 for z in zones), len(zmap))

    led, days = LY.run(db, st, today)
    tally, per = LY.verify(db, led, days, zones, zmap)
    log.info("weak layers: %d tracks, %d with a persistent problem today; vs centers %s", len(led),
             sum(v["floor"] >= 2 for v in led.values()), tally)
    try:  # the same comparison for past seasons, with the ledger driven by each zone's SNOTEL stations
        vsum = AV.run(db, today, zones=zones, zmap=zmap, led=led, days=days)
        log.info("verify:\n%s", AV.report(vsum))
    except Exception as e:  # noqa
        log.warning("verification failed: %s", e)
        vsum = {}

    rid, band = st["rid"].astype(int), st["band"].astype(int)
    score, flags = cell_scores(w, band)
    ranges = []
    for k, (name, state, _line, zone_key) in enumerate(RANGES, 1):
        m = rid == k
        if not m.any():
            continue
        rec = {"id": k, "name": name, "state": state,
               "bbox": [round(float(st["lat"][m].min()), 3), round(float(st["lon"][m].min()), 3),
                        round(float(st["lat"][m].max()), 3), round(float(st["lon"][m].max()), 3)],
               "bands": [], "elev": [], "drivers": [], "reasons": [], "layers": [], "pwl": [],
               "snotel": [s["id"] for s in stations if s.get("range") == k]}
        reasons = set()
        for b in (1, 2, 3):
            mb = m & (band == b)
            lg = led.get((k, b))
            rec["layers"].append(lg["layers"] if lg else [])
            rec["pwl"].append("".join(str(x) for x in lg["series"]) if lg else "")
            if mb.sum() < 3:
                rec["bands"].append(None); rec["elev"].append(None); rec["drivers"].append(None)
                continue
            sc = score[mb]
            lvl = 0 if (sc > 0).mean() < 0.3 else int(np.clip(np.round(np.percentile(sc[sc > 0], 75)), 1, 5))
            if lvl and lg and lg["floor"] >= 2:
                reasons.add("persistent")
                lvl = max(lvl, lg["floor"])
            rec["bands"].append(lvl)
            rec["elev"].append([int(np.percentile(st["dem"][mb], 5)), int(np.percentile(st["dem"][mb], 95))])
            p = lambda key, q=50: round(float(np.percentile(w[key][mb], q)), 1)
            rec["drivers"].append({"hs": p("hs"), "hn24": p("hn24", 75), "hn72": p("hn72", 75), "hn7d": p("hn7d", 75),
                                   "rain72": p("rain72", 75), "tmax24": p("tmax24"), "wmean72": p("wmean72", 75),
                                   "wmax72": p("wmax72", 75), "fhn24": p("fhn24", 75), "ftmax": p("ftmax")})
            if lvl > 0:
                reasons |= {fk for fk, fv in flags.items() if fv[mb].mean() > 0.25}
        rec["reasons"] = sorted(reasons)
        if k in zmap:
            rec["zone"] = zmap[k]
            z = zones[zmap[k]]
            today_c = H.center_problems(db, z["center_id"], z["name"], today)
            if today_c:
                rec["center_problems"] = today_c
            if k in per:
                rec["verify"] = per[k]
            vz = vsum.get("snotel", {}).get("zones", {}).get(f'{z["center_id"]}:{z["name"]}')
            if vz:
                rec["verify_snotel"] = vz
        elif zone_key:  # no live zone list: still say which center covers it
            cid, zname = zone_key.split(":", 1)
            rec["center"] = {"center": CENTERS[cid][0], "zone": zname, "link": CENTERS[cid][1]}
        ranges.append(rec)

    hist = db.execute("SELECT count(*), sum(back <= begin) FROM snotel_station").fetchone()
    meta = {"generated": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z", "cycle": C.isoformat() + "Z",
            "official_checked": off["checked"], "official_stale": bool(off.get("stale")), "snotel": len(stations),
            "season_start": days[0].isoformat(), "verify": tally,
            "verify_history": {src: {"all": v["all"], "seasons": v["seasons"]} for src, v in vsum.items()},
            "history": {"snotel_stations": hist[0], "snotel_saved": hist[1] or 0,
                        "center_forecasts": db.execute("SELECT count(*) FROM center_forecast").fetchone()[0]},
            "grid": {"W": int(st["W"]), "H": int(st["H"]), "gx": float(st["gx"]), "gy": float(st["gy"]), "z": int(st["z"])}}
    db.close()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for z in zones:
        z["polys"] = [[[[round(x, 3), round(y, 3)] for x, y in ring] for ring in poly] for poly in z["polys"]]
    (out_dir / "avy.json").write_text(json.dumps({"meta": meta, "ranges": ranges, "zones": zones,
                                                  "stations": [{k: v for k, v in s.items() if not k.startswith("_")} for s in stations]},
                                                 separators=(",", ":")))
    lv = [max([b for b in r["bands"] if b is not None] or [0]) for r in ranges]
    log.info("avalanche: %d ranges %s, %.0fs", len(ranges), {NAMES[i]: lv.count(i) for i in range(6) if lv.count(i)}, time.time() - t0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "site" / "data" / "avy"))
    ap.add_argument("--no-snotel", action="store_true")
    ap.add_argument("--budget", type=float, default=300, help="seconds for backfilling old SNOTEL years and past center forecasts")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    build(a.out, use_snotel=not a.no_snotel, budget=a.budget)


if __name__ == "__main__":
    main()
