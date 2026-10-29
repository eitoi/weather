"""Build the static forecast dataset for the web app.

    python -m ingest.run [--out site/data] [--hours N] [--no-hrrr] [--no-compare] [--no-longrange] [--no-avalanche]

The Weather tab's HRRR data (everything but longrange.json and avy/) is only downloaded when asked for. With
--no-hrrr the last HRRR build, saved in cache/weather/, is reused and only the outlook and avalanche data are
rebuilt; the first build with no saved copy fetches HRRR anyway. The avalanche data (avy/) works the same way:
with --no-avalanche the last avalanche build, saved in cache/avy-saved/, is reused.

Output (all static files, served as-is by any web server / GitHub Pages):
  meta.json                 run info, time axis, field catalog, places
  grid.bin                  float32 lat[ny*nx], lon[ny*nx], HRRR model terrain (m)[ny*nx]
  maps/<field>.bin          int16 [time][ny][nx]   (value = int * scale, -32768 = missing)
  pts/<j>_<i>.bin           int16 [field][time] for one grid point (all fields, for point views)
  sub/<field>.bin           int16 [step][ny][nx]   15-minute surface data around sunrise/sunset (meta.sub)
  subpts/<j>_<i>.bin        int16 [field][step] for one grid point
  longrange.json            multi-model extended outlook, NWS AFD, summary
  avy/avy.json              western avalanche danger by range (see ingest/avalanche.py)
"""
import argparse
import datetime as dt
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from .config import LEVELS, UPPER_VARS, SCALE, UNITS, SFC_FIELDS, PLACES, BBOX
from . import hrrr as H
from . import compare as C
from . import subhourly as SUB

log = logging.getLogger("run")
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
SAVED = CACHE / "weather"   # the last HRRR build (Weather tab), reused by --no-hrrr builds
SAVED_AVY = CACHE / "avy-saved"   # the last avalanche build, reused by --no-avalanche builds
MISSING = -32768

CMP_SCALE = {"T2": 0.01, "WS10": 0.01, "WD10": 0.1, "GUST": 0.01, "APCP1": 0.01, "WS700": 0.01,
             "WD700": 0.1, "T700": 0.01}
CMP_UNITS = {"T2": "C", "WS10": "m/s", "WD10": "deg", "GUST": "m/s", "APCP1": "mm", "WS700": "m/s",
             "WD700": "deg", "T700": "C"}
CONF_KEYS = ["ALL", "T2", "WIND", "GUST", "PRECIP", "UPPER", "N"]


def load_env():
    """Read KEY=VALUE lines from .env (for ANTHROPIC_API_KEY) without overriding the real environment."""
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ---------------------------------------------------------------- cache helpers

def cache_path(kind, run, fh):
    return CACHE / kind / f"{run:%Y%m%d%H}_f{fh:02d}.npz"


def cache_load(kind, run, fh):
    p = cache_path(kind, run, fh)
    if p.exists():
        try:
            with np.load(p) as z:
                return {k: z[k] for k in z.files}
        except Exception:  # noqa
            p.unlink(missing_ok=True)
    return None


def cache_save(kind, run, fh, fields):
    p = cache_path(kind, run, fh)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, **fields)
    tmp.replace(p)


def cache_prune(keep_hours=30):
    cutoff = dt.datetime.utcnow() - dt.timedelta(hours=keep_hours)
    for p in CACHE.glob("*/*.npz"):
        try:
            if dt.datetime.strptime(p.name[:10], "%Y%m%d%H") < cutoff:
                p.unlink()
        except ValueError:
            pass


def fetch_hrrr(kind, pairs, crop, specs_fn, workers):
    """pairs: list of (run, fh). Returns {(run, fh): fields}. Uses cache."""
    out, todo = {}, []
    for run, fh in pairs:
        f = cache_load(kind, run, fh)
        if f is not None:
            out[(run, fh)] = f
        else:
            todo.append((run, fh))
    if not todo:
        return out
    log.info("HRRR %s: downloading %d run-hours (%d cached)", kind, len(todo), len(out))
    # Batches keep memory bounded and let progress persist in the cache if interrupted.
    batch = 6
    for b in range(0, len(todo), batch):
        chunk = todo[b:b + batch]
        t0 = time.time()
        res = H.extract([(f"{r:%Y%m%d%H}_{fh}", r, fh, specs_fn(fh)) for r, fh in chunk], crop, workers=workers)
        for r, fh in chunk:
            fields = res[f"{r:%Y%m%d%H}_{fh}"]
            if fields:
                cache_save(kind, r, fh, fields)
                out[(r, fh)] = fields
        log.info("  %s %d/%d  (%.0fs)", kind, min(b + batch, len(todo)), len(todo), time.time() - t0)
    return out


def ensure_crop(crop, run):
    p = CACHE / "grid.npz"
    if p.exists():
        with np.load(p) as z:
            if tuple(z["bbox"]) == tuple(BBOX.values()):
                crop.win = tuple(int(x) for x in z["win"])
                crop.lats, crop.lons = z["lats"], z["lons"]
                return
    H.extract([("g", run, 0, [("T2", "sfc", "TMP", "2 m above ground", None, None)])], crop)
    p.parent.mkdir(parents=True, exist_ok=True)
    np.savez(p, win=np.array(crop.win), lats=crop.lats, lons=crop.lons, bbox=np.array(list(BBOX.values())))


# ---------------------------------------------------------------- main build

def build(out_dir, max_hours=48, do_compare=True, do_longrange=True, workers=24, do_sub=True, do_avalanche=True,
          do_hrrr=True):
    t_start = time.time()
    out_dir = Path(out_dir)
    tmp = out_dir.parent / (out_dir.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)

    if not do_hrrr and not (SAVED / "meta.json").exists():
        log.info("no saved HRRR data yet; fetching HRRR this once")
        do_hrrr = True
    if do_hrrr:
        meta = build_hrrr(tmp, max_hours, do_compare, workers, do_sub)
        save_copy(tmp, SAVED)
    else:
        shutil.copytree(SAVED, tmp)
        meta = json.loads((tmp / "meta.json").read_text())
        log.info("reusing the HRRR %s run built %s (HRRR is fetched only on request)", meta["run"],
                 meta.get("hrrr_generated", meta["generated"]))

    meta["generated"] = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    meta["longrange"] = "longrange.json" if do_longrange else None
    (tmp / "meta.json").write_text(json.dumps(meta, separators=(",", ":")))
    lr_end = dt.datetime.fromisoformat(meta["times"][-1].rstrip("Z"))

    if do_longrange:
        from . import longrange
        try:
            lr = longrange.build(lr_end)
            (tmp / "longrange.json").write_text(json.dumps(lr, separators=(",", ":"), default=str))
        except Exception as e:  # noqa
            log.exception("long-range build failed: %s", e)
            old = out_dir / "longrange.json"
            if old.exists():
                shutil.copy(old, tmp / "longrange.json")

    if not do_avalanche and not (SAVED_AVY / "avy.json").exists():
        log.info("no saved avalanche data yet; building it this once")
        do_avalanche = True
    if do_avalanche:
        from . import avalanche
        try:
            avalanche.build(tmp / "avy", workers=min(workers, 16))
            save_copy(tmp / "avy", SAVED_AVY)
        except Exception as e:  # noqa
            log.exception("avalanche build failed: %s", e)
            do_avalanche = False
    if not do_avalanche and (SAVED_AVY / "avy.json").exists():
        shutil.rmtree(tmp / "avy", ignore_errors=True)
        shutil.copytree(SAVED_AVY, tmp / "avy")
        log.info("reusing the avalanche data built %s (fetched only on request)",
                 json.loads((SAVED_AVY / "avy.json").read_text())["meta"]["generated"])
    elif not do_avalanche and (out_dir / "avy").exists():
        shutil.rmtree(tmp / "avy", ignore_errors=True)
        shutil.copytree(out_dir / "avy", tmp / "avy")

    old = out_dir.parent / (out_dir.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if out_dir.exists():
        out_dir.rename(old)
    tmp.rename(out_dir)
    shutil.rmtree(old, ignore_errors=True)
    cache_prune()
    log.info("done in %.0fs: %d times, %d map fields, grid %dx%d -> %s", time.time() - t_start, len(meta["times"]),
             len(meta["map_fields"]), meta["grid"]["ny"], meta["grid"]["nx"], out_dir)


def save_copy(src, dst):
    """Replace dst with a copy of src, without leaving a half-written dst behind."""
    part = dst.with_name(dst.name + ".tmp")
    shutil.rmtree(part, ignore_errors=True)
    shutil.copytree(src, part)
    shutil.rmtree(dst, ignore_errors=True)
    part.rename(dst)


def build_hrrr(tmp, max_hours, do_compare, workers, do_sub):
    """Download HRRR (and the comparison models) and write the Weather tab's files into tmp. Returns meta."""
    R, S = H.latest_runs()
    log.info("newest HRRR run %s, newest 48-h run %s", R, S)
    crop = H.Crop()
    ensure_crop(crop, R)
    ny, nx = crop.lats.shape

    # Time axis: newest run first, extended with the latest 48-h run beyond its horizon.
    axis = [(R + dt.timedelta(hours=fh), R, fh) for fh in range(0, H.max_fh(R) + 1)]
    if S is not None and S != R:
        last = axis[-1][0]
        axis += [(S + dt.timedelta(hours=fh), S, fh) for fh in range(0, H.max_fh(S) + 1)
                 if S + dt.timedelta(hours=fh) > last]
    axis = [a for a in axis if (a[0] - R).total_seconds() / 3600 <= max_hours]
    times = [a[0] for a in axis]
    T = len(times)

    full = fetch_hrrr("full", [(r, fh) for _t, r, fh in axis], crop, H.full_specs, workers)

    # ---- comparison models
    comps = {t: {} for t in times}
    cmp_info = {}
    if do_compare:
        # time-lagged HRRR: the two most recent earlier runs that reach each valid time
        lag_pairs = {}
        for t, src, _fh in axis:
            found = 0
            for k in range(1, 13):
                r = src - dt.timedelta(hours=k)
                fh = int((t - r).total_seconds() // 3600)
                if fh <= H.max_fh(r):
                    found += 1
                    lag_pairs[(t, f"LAG{found}")] = (r, fh)
                    if found == 2:
                        break
        lag = fetch_hrrr("cmp", sorted(set(lag_pairs.values())), crop, H.cmp_specs, workers)
        for (t, name), key in lag_pairs.items():
            if key in lag:
                comps[t][name] = lag[key]
        cmp_info["LAG"] = "Previous two HRRR runs reaching each hour (time-lagged ensemble)"

        for model in ("NAM", "RAP"):
            try:
                cyc = C.latest_cycle(model)
                if cyc is None:
                    log.warning("%s: no cycle available", model)
                    continue
                got = C.fetch_model(model, cyc, times, crop.lats, crop.lons)
                used = [cyc]
                missing = [t for t in times if t not in got]
                if model == "RAP" and missing:  # extended RAP cycles (03/09/15/21Z) reach 51 h
                    ext = cyc
                    while ext.hour % 6 != 3:
                        ext -= dt.timedelta(hours=1)
                    if ext != cyc:
                        got.update(C.fetch_model(model, ext, missing, crop.lats, crop.lons))
                        used.append(ext)
                for t, f in got.items():
                    comps[t][model] = f
                cmp_info[model] = {"label": C.MODELS[model]["label"],
                                   "cycles": [u.isoformat() + "Z" for u in used], "hours": len(got)}
                log.info("%s: %d hours from %s", model, len(got), [str(u) for u in used])
            except Exception as e:  # noqa
                log.warning("%s failed: %s", model, e)

    # ---- assemble arrays
    hrrr_keys = [f"{v}:{lev}" for lev in LEVELS for v in UPPER_VARS] + list(SFC_FIELDS)
    fields = {}
    for k in hrrr_keys:
        a = np.full((T, ny, nx), np.nan, np.float32)
        for ti, (_t, r, fh) in enumerate(axis):
            v = full.get((r, fh), {}).get(k)
            if v is not None:
                a[ti] = v
        fields[k] = a
    # Model terrain doesn't change; fill any gaps from the first hour.
    terrain = np.nanmax(fields["TERRAIN"], axis=0)

    # derived + confidence
    hd = []
    for ti in range(T):
        f = {k: fields[k][ti] for k in ("T2", "U10", "V10", "GUST", "APCP1")}
        f.update({"T700": fields["TMP:700"][ti], "U700": fields["UGRD:700"][ti], "V700": fields["VGRD:700"][ti]})
        hd.append(C.derived(f))
    srcs = ["NAM", "RAP", "LAG1", "LAG2"]
    cmp_fields = {f"{s}_{k}": np.full((T, ny, nx), np.nan, np.float32) for s in srcs for k in CMP_SCALE}
    conf = {f"CONF_{k}": np.full((T, ny, nx), np.nan, np.float32) for k in CONF_KEYS}
    cd = [{s: C.derived(c) for s, c in comps[t].items()} for t in times]

    def pwin(series, ti):
        vals = [series[j] for j in (ti - 1, ti, ti + 1) if 0 <= j < T and series[j] is not None]
        return np.nansum(vals, axis=0) if vals else None

    hp = [h.get("APCP1") if np.isfinite(h.get("APCP1", np.array([np.nan]))).any() else None for h in hd]
    sp = {s: [cd[ti].get(s, {}).get("APCP1") for ti in range(T)] for s in srcs}
    for ti in range(T):
        for s, d in cd[ti].items():
            for k in CMP_SCALE:
                if k in d:
                    cmp_fields[f"{s}_{k}"][ti] = d[k]
        if not cd[ti]:
            continue
        win = {}
        if pwin(hp, ti) is not None:
            win["HRRR"] = pwin(hp, ti)
            for s in cd[ti]:
                w = pwin(sp[s], ti)
                if w is not None:
                    win[s] = w
        c = C.confidence(hd[ti], cd[ti], win)
        for k in CONF_KEYS:
            conf[f"CONF_{k}"][ti] = c[k]

    # ---- catalog
    catalog = {}
    for k in hrrr_keys:
        if ":" in k:
            v, lev = k.split(":")
            catalog[k] = {"scale": SCALE[v], "units": UNITS[v], "src": "HRRR", "level": int(lev), "var": v}
        else:
            _var, level, units, scale, _c = SFC_FIELDS[k]
            catalog[k] = {"scale": scale, "units": units, "src": "HRRR", "level": level}
    for k in conf:
        catalog[k] = {"scale": 0.1, "units": "%" if k != "CONF_N" else "", "src": "derived"}
    for k in cmp_fields:
        s, b = k.split("_", 1)
        catalog[k] = {"scale": CMP_SCALE[b], "units": CMP_UNITS[b], "src": s}
    map_keys = hrrr_keys + list(conf)
    point_keys = map_keys + list(cmp_fields)
    allf = {**fields, **conf, **cmp_fields}

    # ---- 15-minute surface data around sunrise / sunset (newest run, first 18 h)
    sub_meta, sub_arrays = None, {}
    if do_sub:
        try:
            sub_times, spans = SUB.windows(R, H.max_fh(R) if H.max_fh(R) <= 18 else 18)
            by_fh = SUB.specs_for(R, sub_times)
            got = fetch_hrrr("sub", [(R, fh) for fh in sorted(by_fh)], crop, lambda fh: by_fh[fh], workers)
            S_n = len(sub_times)
            for key in SUB.SUB_FIELDS:
                a = np.full((S_n, ny, nx), np.nan, np.float32)
                for si, q in enumerate(sub_times):
                    m = int((q - R).total_seconds() // 60)
                    v = got.get((R, -(-m // 60)), {}).get(f"{key}@{m}")
                    if v is not None:
                        a[si] = v
                sub_arrays[key] = a
            sub_meta = {
                "times": [q.isoformat() + "Z" for q in sub_times],
                "spans": [{"label": sp["label"], "start": sp["start"].isoformat() + "Z", "end": sp["end"].isoformat() + "Z",
                           "sun": sp["sun"].isoformat(timespec="minutes") + "Z"} for sp in spans],
                "window_h": SUB.WINDOW_H, "run": R.isoformat() + "Z",
                "fields": {k: {"scale": v[3], "units": v[2], "src": "HRRR subhourly"} for k, v in SUB.SUB_FIELDS.items()},
                "point_fields": list(SUB.SUB_FIELDS),
            }
            log.info("15-min: %d steps in %d windows", S_n, len(spans))
        except Exception as e:  # noqa
            log.warning("15-minute data failed: %s", e)
            sub_meta, sub_arrays = None, {}

    # ---- write (into a temp dir, then swap)
    (tmp / "maps").mkdir(parents=True)
    (tmp / "pts").mkdir()

    def q(k, a):
        s = catalog[k]["scale"]
        v = np.round(a / s)
        v = np.clip(v, -32767, 32767)
        return np.where(np.isfinite(v), v, MISSING).astype("<i2")

    qa = {k: q(k, allf[k]) for k in point_keys}
    for k in map_keys:
        qa[k].tofile(tmp / "maps" / f"{k.replace(':', '_')}.bin")
    stack = np.stack([qa[k] for k in point_keys])  # [F, T, ny, nx]
    for j in range(ny):
        for i in range(nx):
            np.ascontiguousarray(stack[:, :, j, i]).tofile(tmp / "pts" / f"{j}_{i}.bin")
    np.concatenate([crop.lats.ravel(), crop.lons.ravel(), terrain.ravel()]).astype("<f4").tofile(tmp / "grid.bin")
    if sub_meta and sub_meta["times"]:
        (tmp / "sub").mkdir(); (tmp / "subpts").mkdir()
        sq = {}
        for k, a in sub_arrays.items():
            v = np.clip(np.round(a / SUB.SUB_FIELDS[k][3]), -32767, 32767)
            sq[k] = np.where(np.isfinite(v), v, MISSING).astype("<i2")
            sq[k].tofile(tmp / "sub" / f"{k}.bin")
        sst = np.stack([sq[k] for k in sub_meta["point_fields"]])
        for j in range(ny):
            for i in range(nx):
                np.ascontiguousarray(sst[:, :, j, i]).tofile(tmp / "subpts" / f"{j}_{i}.bin")

    now = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    meta = {
        "generated": now, "hrrr_generated": now,
        "run": R.isoformat() + "Z", "ext_run": (S.isoformat() + "Z") if S else None,
        "times": [t.isoformat() + "Z" for t in times],
        "sources": [{"run": r.isoformat() + "Z", "fh": fh} for _t, r, fh in axis],
        "grid": {"ny": ny, "nx": nx, "dx_km": 3.0, "projection": "HRRR Lambert conformal (native)",
                 "window": list(crop.win), "bbox": BBOX},
        "levels": LEVELS, "upper_vars": UPPER_VARS,
        "fields": catalog, "map_fields": map_keys, "point_fields": point_keys,
        "places": PLACES, "comparators": cmp_info,
        "longrange": None,
        "sub": sub_meta if sub_meta and sub_meta["times"] else None,
    }
    (tmp / "meta.json").write_text(json.dumps(meta, separators=(",", ":")))
    log.info("HRRR %s: %d times, %d map fields, grid %dx%d", R, T, len(map_keys), ny, nx)
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "site" / "data"))
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--no-hrrr", action="store_true",
                    help="reuse the last HRRR build and only refresh the outlook and avalanche data")
    ap.add_argument("--no-compare", action="store_true")
    ap.add_argument("--no-longrange", action="store_true")
    ap.add_argument("--no-sub", action="store_true", help="skip the 15-minute dawn/dusk data")
    ap.add_argument("--no-avalanche", action="store_true",
                    help="reuse the last avalanche build instead of fetching HRRR history, ratings and SNOTEL")
    ap.add_argument("--workers", type=int, default=24)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_env()
    build(a.out, a.hours, not a.no_compare, not a.no_longrange, a.workers, not a.no_sub, not a.no_avalanche,
          not a.no_hrrr)


if __name__ == "__main__":
    sys.exit(main())
