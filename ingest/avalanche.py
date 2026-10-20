"""Avalanche danger for ~150 mountain ranges in ten western states (WA OR CA NV ID MT WY UT CO NM).

    python -m ingest.avalanche [--out site/data/avy]     (also runs as part of ingest.run)

Official ratings come from the avalanche.org map layer wherever a center publishes one. Everywhere else
(and in the off season) each ~2 km mountain cell gets an estimated rating from HRRR snowfall, rain, wind and
temperature over the past 7 days plus the next 24 hours, with snow depth corrected toward nearby SNOTEL
stations. Each range and elevation band then takes the 75th-percentile cell score.

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

from . import gribio as G
from . import hrrr as HR
from .avy_ranges import RANGES, CENTERS

log = logging.getLogger("avy")
ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).with_name("avy_static.npz")
CACHE = ROOT / "cache" / "avy"
STATES = ["WA", "OR", "CA", "NV", "ID", "MT", "WY", "UT", "CO", "NM"]
MAP_LAYER = "https://api.avalanche.org/v2/public/products/map-layer"
AWDB = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"
NAMES = ["Minimal snow", "Low", "Moderate", "Considerable", "High", "Extreme"]


# ---------------------------------------------------------------- HRRR

def _fetch(run, fh, wanted, hidx):
    """wanted: {key: (var, level, desc)} -> {key: float32 values at the mountain cells}."""
    u = HR.url(run, fh, "sfc")
    ents = G.parse_idx(G.http_get(u + ".idx").decode())
    pick = {}
    for key, (var, level, desc) in wanted.items():
        e = G.find(ents, var, level, lambda d, desc=desc: d == desc)
        if e is None:
            raise KeyError(f"{var}:{level}:{desc} not in {u}")
        pick[e] = key
    msgs = G.fetch_ranges(u, G.merge_ranges(list(pick)))
    out = {}
    for e, key in pick.items():
        vals, _info, _la, _lo = G.decode(msgs[e])
        out[key] = vals.ravel()[hidx].astype(np.float32)
    return out


def latest_cycle():
    now = dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    for k in range(1, 30):
        c = now - dt.timedelta(hours=k)
        if c.hour % 6 == 0 and G.exists(HR.url(c, 24, "sfc") + ".idx"):
            return c
    raise RuntimeError("no HRRR cycle with a 24-h forecast")


def hrrr_fields(st, workers=16):
    """Past 7 days in 6-h windows plus current snow depth and the next 24 h, at the mountain cells."""
    hidx = st["hidx"]
    C = latest_cycle()
    CACHE.mkdir(parents=True, exist_ok=True)
    hist_spec = {"asnow": ("ASNOW", "surface", "0-6 hour acc fcst"), "apcp": ("APCP", "surface", "0-6 hour acc fcst"),
                 "t2": ("TMP", "2 m above ground", "6 hour fcst"),
                 "u80": ("UGRD", "80 m above ground", "6 hour fcst"), "v80": ("VGRD", "80 m above ground", "6 hour fcst")}

    def hist(k):  # window ending at C - 6k, from the run 6 h before its end
        cyc = C - dt.timedelta(hours=6 * k + 6)
        p = CACHE / f"h_{cyc:%Y%m%d%H}.npz"
        if p.exists():
            try:
                with np.load(p) as z:
                    return {key: z[key].astype(np.float32) for key in z.files}
            except Exception:  # noqa
                p.unlink(missing_ok=True)
        f = _fetch(cyc, 6, hist_spec, hidx)
        np.savez_compressed(p, **{key: v.astype(np.float16) for key, v in f.items()})
        return f

    def fcst(fh):
        spec = {"t2": ("TMP", "2 m above ground", f"{fh} hour fcst"),
                "u80": ("UGRD", "80 m above ground", f"{fh} hour fcst"), "v80": ("VGRD", "80 m above ground", f"{fh} hour fcst")}
        if fh == 24:
            spec.update(fasnow=("ASNOW", "surface", "0-1 day acc fcst"), fapcp=("APCP", "surface", "0-1 day acc fcst"))
        return _fetch(C, fh, spec, hidx)

    with ThreadPoolExecutor(workers) as ex:
        hs = ex.map(hist, range(28))
        fc = ex.map(fcst, (6, 12, 18, 24))
        now = ex.submit(_fetch, C, 0, {"snod": ("SNOD", "surface", "anl"), "hgt": ("HGT", "surface", "anl")}, hidx)
        hs, fc, now = list(hs), list(fc), now.result()
    for p in CACHE.glob("h_*.npz"):  # keep 9 days
        try:
            if dt.datetime.strptime(p.stem[2:], "%Y%m%d%H") < C - dt.timedelta(days=9):
                p.unlink()
        except ValueError:
            pass

    lapse = -0.0065 * (st["dem"] - now["hgt"])  # HRRR's smoothed terrain -> real cell elevation
    t = np.stack([h["t2"] - 273.15 + lapse for h in hs])
    asnow = np.stack([h["asnow"] for h in hs]) * 100   # m -> cm
    apcp = np.stack([h["apcp"] for h in hs])            # mm
    wind = np.stack([np.hypot(h["u80"], h["v80"]) for h in hs[:12]])
    rain = np.where(t > 1.0, np.maximum(apcp - asnow, 0), 0)  # 10:1 snow ratio
    return C, dict(
        hs=np.clip(now["snod"] * 100, 0, 400), hn24=asnow[:4].sum(0), hn72=asnow[:12].sum(0), hn7d=asnow.sum(0),
        rain72=rain[:12].sum(0), tmax24=t[:4].max(0), wmean72=wind.mean(0), wmax72=wind.max(0),
        fhn24=fc[3]["fasnow"] * 100, ftmax=np.stack([f["t2"] - 273.15 + lapse for f in fc]).max(0),
        fwmax=np.stack([np.hypot(f["u80"], f["v80"]) for f in fc]).max(0),
        model_hs=np.clip(now["snod"] * 100, 0, 400))


# ---------------------------------------------------------------- SNOTEL

def snotel(today):
    """Active SNOTEL stations in the ten states with daily snow depth for the past 8 days (cm)."""
    c = G.client()
    stations = []
    for s in STATES:
        r = c.get(f"{AWDB}/stations", params={"stationTriplets": f"*:{s}:SNTL", "activeOnly": "true",
                                              "returnForecastPointMetadata": "false", "returnReservoirMetadata": "false",
                                              "returnStationElements": "false"})
        r.raise_for_status()
        stations += r.json()
    by = {s["stationTriplet"]: s for s in stations if s.get("latitude") and s.get("longitude")}
    trip = list(by)
    begin, end = (today - dt.timedelta(days=8)).isoformat(), today.isoformat()
    for b in range(0, len(trip), 60):
        r = c.get(f"{AWDB}/data", params={"stationTriplets": ",".join(trip[b:b + 60]), "elements": "SNWD,WTEQ",
                                          "duration": "DAILY", "beginDate": begin, "endDate": end})
        r.raise_for_status()
        for row in r.json():
            s = by.get(row.get("stationTriplet"))
            if s is None:
                continue
            for el in row.get("data", []):
                code = (el.get("stationElement") or {}).get("elementCode")
                vals = {v["date"][:10]: v["value"] for v in el.get("values", []) if v.get("value") is not None}
                s[code] = vals
    out = []
    for s in by.values():
        d = s.get("SNWD") or {}
        if not d:
            continue
        days = sorted(d)
        last = days[-1]
        if (today - dt.date.fromisoformat(last)).days > 2:
            continue
        hs = d[last] * 2.54
        prev = lambda n: d.get((dt.date.fromisoformat(last) - dt.timedelta(days=n)).isoformat())
        ch = lambda n: None if prev(n) is None else round((d[last] - prev(n)) * 2.54, 1)
        swe = (s.get("WTEQ") or {}).get(last)
        out.append({"name": s.get("name"), "id": s["stationTriplet"], "lat": round(float(s["latitude"]), 4),
                    "lon": round(float(s["longitude"]), 4), "elev_ft": s.get("elevation"), "date": last,
                    "hs": round(hs, 1), "d24": ch(1), "d72": ch(3), "swe": None if swe is None else round(swe * 2.54, 1)})
    return out


def merc_px(lon, lat, z):
    n = 256 * 2 ** z
    x = (np.asarray(lon) + 180) / 360 * n
    lr = np.radians(np.asarray(lat))
    return x, (1 - np.log(np.tan(lr) + 1 / np.cos(lr)) / np.pi) / 2 * n


def apply_snotel(st, w, stations):
    """Attach stations to nearby range cells and scale each range's modelled snow toward what they measure."""
    W, H, gx, gy, z = int(st["W"]), int(st["H"]), float(st["gx"]), float(st["gy"]), int(st["z"])
    cell = np.full((H, W), -1, np.int32)
    cell[st["iy"], st["ix"]] = np.arange(len(st["iy"]))
    rid = st["rid"]
    hits = {}
    for s in stations:
        x, y = merc_px(s["lon"], s["lat"], z)
        x, y = int(x - gx), int(y - gy)
        best = None
        for r in range(0, 4):  # nearest mountain cell within ~8 km
            y0, y1, x0, x1 = max(y - r, 0), min(y + r + 1, H), max(x - r, 0), min(x + r + 1, W)
            win = cell[y0:y1, x0:x1]
            if (win >= 0).any():
                best = int(win[win >= 0][0]); break
        if best is None:
            continue
        s["range"] = int(rid[best])
        s["model_hs"] = round(float(w["model_hs"][best]), 1)
        s["_cell"] = best
        hits.setdefault(int(rid[best]), []).append(s)
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

def cell_scores(w, band, continental):
    """1-5 on the North American danger scale for each cell (0 = minimal snow), plus the reasons that fired."""
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
    pwl = continental & (hs >= 30) & (hs < 100) & (load >= 20)
    s = s + np.where(pwl, 0.5, 0)
    s = np.where(hs < 40, np.minimum(s, 2), np.where(hs < 70, np.minimum(s, 3), s))
    s = np.where(snow, np.clip(s, 1, 5), 0)
    flags = dict(load=load_eff >= 10, drift=drift, ros=ros, warm=warm, storm=fhn >= 10, pwl=pwl, shallow=snow & (hs < 70))
    return s, flags


# ---------------------------------------------------------------- build

def build(out_dir, workers=16, use_snotel=True):
    t0 = time.time()
    st = dict(np.load(STATIC))
    st["dem"] = st["dem"].astype(np.float32)
    C, w = hrrr_fields(st, workers)
    log.info("HRRR %s: %d mountain cells (%.0fs)", C, len(st["rid"]), time.time() - t0)

    stations = []
    if use_snotel:
        try:
            stations = snotel(dt.datetime.utcnow().date())
            hits = apply_snotel(st, w, stations)
            log.info("SNOTEL: %d stations, %d in ranges", len(stations), sum(len(v) for v in hits.values()))
        except Exception as e:  # noqa
            log.warning("SNOTEL failed: %s", e)
            stations = []

    off = official()
    zones = off["zones"]
    zmap = match_zones(st, zones) if zones else {}
    log.info("official: %d zones, %d rated, %d ranges matched", len(zones), sum(z["level"] >= 1 for z in zones), len(zmap))

    rid, band = st["rid"].astype(int), st["band"].astype(int)
    score, flags = cell_scores(w, band, st["lon"] > -119.0)
    ranges = []
    for k, (name, state, _line, zone_key) in enumerate(RANGES, 1):
        m = rid == k
        if not m.any():
            continue
        rec = {"id": k, "name": name, "state": state,
               "bbox": [round(float(st["lat"][m].min()), 3), round(float(st["lon"][m].min()), 3),
                        round(float(st["lat"][m].max()), 3), round(float(st["lon"][m].max()), 3)],
               "bands": [], "elev": [], "drivers": [], "reasons": [],
               "snotel": [s["id"] for s in stations if s.get("range") == k]}
        reasons = set()
        for b in (1, 2, 3):
            mb = m & (band == b)
            if mb.sum() < 3:
                rec["bands"].append(None); rec["elev"].append(None); rec["drivers"].append(None)
                continue
            sc = score[mb]
            lvl = 0 if (sc > 0).mean() < 0.3 else int(np.clip(np.round(np.percentile(sc[sc > 0], 75)), 1, 5))
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
        elif zone_key:  # no live zone list: still say which center covers it
            cid, zname = zone_key.split(":", 1)
            rec["center"] = {"center": CENTERS[cid][0], "zone": zname, "link": CENTERS[cid][1]}
        ranges.append(rec)

    meta = {"generated": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z", "cycle": C.isoformat() + "Z",
            "official_checked": off["checked"], "official_stale": bool(off.get("stale")), "snotel": len(stations),
            "grid": {"W": int(st["W"]), "H": int(st["H"]), "gx": float(st["gx"]), "gy": float(st["gy"]), "z": int(st["z"])}}
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
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    build(a.out, use_snotel=not a.no_snotel)


if __name__ == "__main__":
    main()
