"""Comparison models and the HRRR confidence score.

Comparators, all regridded onto the HRRR 3 km points:
  NAM  - NAM CONUS Nest (3 km, 00/06/12/18Z, to 60 h)           via NOMADS grib filter (server-side crop)
  RAP  - Rapid Refresh (13 km, hourly, to 21 h / 51 h)          via NOMADS grib filter
  LAG1 - previous HRRR run (time-lagged ensemble member)        via AWS
  LAG2 - HRRR run two hours earlier
"""
import datetime as dt
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .config import NOMADS, BBOX_PAD
from . import gribio as G

log = logging.getLogger("compare")

NOMADS_VARS = ["TMP", "UGRD", "VGRD", "GUST", "APCP"]
NOMADS_LEVS = ["2_m_above_ground", "10_m_above_ground", "surface", "700_mb"]

MODELS = {
    "NAM": dict(filter="filter_nam_conusnest.pl", dir="/nam.{d:%Y%m%d}",
                file="nam.t{d:%H}z.conusnest.hiresf{fh:02d}.tm00.grib2",
                cycles=(0, 6, 12, 18), maxfh=lambda c: 60, latency=2, k=1, label="NAM 3 km nest"),
    "RAP": dict(filter="filter_rap.pl", dir="/rap.{d:%Y%m%d}",
                file="rap.t{d:%H}z.awp130pgrbf{fh:02d}.grib2",
                cycles=tuple(range(24)), maxfh=lambda c: 51 if c % 6 == 3 else 21, latency=1, k=4,
                label="RAP 13 km"),
}

_last_call = [0.0]


def nomads_get(model, cycle, fh):
    m = MODELS[model]
    params = {
        "dir": m["dir"].format(d=cycle),
        "file": m["file"].format(d=cycle, fh=fh),
        "subregion": "",
        "toplat": BBOX_PAD["north"], "bottomlat": BBOX_PAD["south"],
        "leftlon": BBOX_PAD["west"], "rightlon": BBOX_PAD["east"],
    }
    for v in NOMADS_VARS:
        params[f"var_{v}"] = "on"
    for lv in NOMADS_LEVS:
        params[f"lev_{lv}"] = "on"
    q = "&".join(f"{k}={v}" if v != "" else k + "=" for k, v in params.items())
    # NOMADS asks for gentle request rates.
    wait = 0.25 - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()
    data = G.http_get(f"{NOMADS}/{m['filter']}?{q}", tries=3)
    return data if data[:4] == b"GRIB" else None


def latest_cycle(model, now=None):
    m = MODELS[model]
    now = now or dt.datetime.utcnow()
    base = now.replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=m["latency"])
    for k in range(0, 13):
        c = base - dt.timedelta(hours=k)
        if c.hour not in m["cycles"]:
            continue
        try:
            if nomads_get(model, c, m["maxfh"](c.hour)):
                return c
        except Exception as e:  # noqa
            log.warning("%s probe %s failed: %s", model, c, e)
    return None


def classify(info):
    sn, tl, lv = info["shortName"], info["typeOfLevel"], info["level"]
    if sn == "2t" or (sn == "t" and tl == "heightAboveGround" and lv == 2):
        return "T2"
    if sn == "10u" or (sn == "u" and tl == "heightAboveGround" and lv == 10):
        return "U10"
    if sn == "10v" or (sn == "v" and tl == "heightAboveGround" and lv == 10):
        return "V10"
    if sn in ("gust", "i10fg"):
        return "GUST"
    if sn == "tp":
        return "APCP"
    if tl == "isobaricInhPa" and lv == 700:
        return {"t": "T700", "u": "U700", "v": "V700"}.get(sn)
    return None


def decode_any(msg):
    import eccodes
    h = eccodes.codes_new_from_message(msg)
    try:
        info = {k: eccodes.codes_get(h, k) for k in
                ("shortName", "typeOfLevel", "level", "startStep", "endStep", "Nx", "Ny", "jScansPositively")}
        vals = eccodes.codes_get_values(h).reshape(info["Ny"], info["Nx"]).astype(np.float32)
        la = eccodes.codes_get_array(h, "latitudes").reshape(info["Ny"], info["Nx"])
        lo = eccodes.codes_get_array(h, "longitudes").reshape(info["Ny"], info["Nx"])
        lo = np.where(lo > 180, lo - 360, lo)
        return info, vals, la, lo
    finally:
        eccodes.codes_release(h)


def hourly_precip(acc_by_fh):
    """acc_by_fh: {fh: [(start, end, arr)]} -> {fh: 1-hour precip}. Handles any bucket scheme."""
    total = {0: None}
    out = {}
    for fh in sorted(acc_by_fh):
        cands = sorted(acc_by_fh[fh], key=lambda x: x[0])
        for s, e, a in cands:
            if e == fh and s in total:
                base = total[s]
                total[fh] = a if base is None else base + a
                break
        if fh in total and (fh - 1) in total:
            prev = total[fh - 1]
            out[fh] = np.maximum(total[fh] - (0 if prev is None else prev), 0)
    return out


def fetch_model(model, cycle, valid_times, dst_lat, dst_lon, workers=3):
    """Returns {valid_time: {key: array on HRRR grid}} for the comparison fields."""
    m = MODELS[model]
    maxfh = m["maxfh"](cycle.hour)
    # Need fh-1 too, to difference accumulations.
    fhs = sorted({int((t - cycle).total_seconds() // 3600) for t in valid_times})
    fhs = [f for f in fhs if 0 <= f <= maxfh]
    if fhs:
        fhs = sorted(set(fhs) | {max(f - 1, 0) for f in fhs} | set(range(0, min(fhs[0], 7))))
    regrid = [None]
    raw = {}

    def one(fh):
        try:
            return fh, nomads_get(model, cycle, fh)
        except Exception as e:  # noqa
            log.warning("%s f%02d failed: %s", model, fh, e)
            return fh, None

    with ThreadPoolExecutor(workers) as ex:
        for fh, data in ex.map(one, fhs):
            if data:
                raw[fh] = data

    fields, acc = {}, {}
    for fh, data in sorted(raw.items()):
        for msg in G.split_messages(data):
            info, vals, la, lo = decode_any(msg)
            key = classify(info)
            if key is None:
                continue
            if regrid[0] is None:
                regrid[0] = G.Regridder(la, lo, dst_lat, dst_lon, k=m["k"])
            arr = regrid[0](vals)
            if key == "APCP":
                acc.setdefault(fh, []).append((info["startStep"], info["endStep"], arr))
            else:
                fields.setdefault(fh, {})[key] = G.convert(arr, "K2C") if key in ("T2", "T700") else arr
    for fh, a in hourly_precip(acc).items():
        fields.setdefault(fh, {})["APCP1"] = a
    out = {}
    for t in valid_times:
        fh = int((t - cycle).total_seconds() // 3600)
        if fh in fields:
            out[t] = fields[fh]
    return out


# ---------------------------------------------------------------- confidence score

WEIGHTS_SRC = {"NAM": 1.0, "RAP": 0.8, "LAG1": 1.0, "LAG2": 0.7}


def _spd(u, v):
    return np.hypot(u, v)


def _dir(u, v):
    return (np.degrees(np.arctan2(-u, -v)) + 360) % 360


def _angdiff(a, b):
    d = np.abs(a - b) % 360
    return np.minimum(d, 360 - d)


def _score(d, tol):
    return np.exp(-0.5 * (d / tol) ** 2)


def derived(f):
    """Comparison quantities from a field dict (HRRR or comparator)."""
    out = {}
    if "T2" in f:
        out["T2"] = f["T2"]
    if "U10" in f and "V10" in f:
        out["WS10"], out["WD10"] = _spd(f["U10"], f["V10"]), _dir(f["U10"], f["V10"])
    if "GUST" in f:
        out["GUST"] = f["GUST"]
    if "APCP1" in f:
        out["APCP1"] = f["APCP1"]
    if "U700" in f and "V700" in f:
        out["WS700"], out["WD700"] = _spd(f["U700"], f["V700"]), _dir(f["U700"], f["V700"])
    if "T700" in f:
        out["T700"] = f["T700"]
    return out


def confidence(hrrr_d, comps, precip_win):
    """hrrr_d: derived HRRR dict for one valid time; comps: {src: derived dict};
    precip_win: {src or 'HRRR': 3-h precip window sum}. Returns dict of 0-100 score maps."""
    shape = next(iter(hrrr_d.values())).shape
    acc = {k: [np.zeros(shape), 0.0] for k in ("T2", "WIND", "GUST", "PRECIP", "UPPER")}

    def add(cat, s, w):
        acc[cat][0] += np.nan_to_num(s, nan=0.5) * w
        acc[cat][1] += w

    for src, c in comps.items():
        w = WEIGHTS_SRC[src]
        if "T2" in c and "T2" in hrrr_d:
            add("T2", _score(np.abs(hrrr_d["T2"] - c["T2"]), 2.0), w)
        if "WS10" in c and "WS10" in hrrr_d:
            hs, cs = hrrr_d["WS10"], c["WS10"]
            s = _score(np.abs(hs - cs), 2.5 + 0.25 * np.maximum(hs, cs))
            dirw = np.clip((np.minimum(hs, cs) - 2.0) / 3.0, 0, 1)  # direction only matters when it's windy
            sd = _score(_angdiff(hrrr_d["WD10"], c["WD10"]), 40.0)
            add("WIND", s * (1 - 0.5 * dirw) + sd * 0.5 * dirw, w)
        if "GUST" in c and "GUST" in hrrr_d:
            add("GUST", _score(np.abs(hrrr_d["GUST"] - c["GUST"]), 4.0 + 0.25 * hrrr_d["GUST"]), w)
        if src in precip_win and "HRRR" in precip_win:
            h, p = precip_win["HRRR"], precip_win[src]
            s = _score(np.abs(h - p), 0.75 + 0.5 * np.maximum(h, p))
            add("PRECIP", s, w)
        if "WS700" in c and "WS700" in hrrr_d:
            hs, cs = hrrr_d["WS700"], c["WS700"]
            s = _score(np.abs(hs - cs), 3.0 + 0.15 * np.maximum(hs, cs))
            dirw = np.clip((np.minimum(hs, cs) - 4.0) / 4.0, 0, 1)
            sd = _score(_angdiff(hrrr_d["WD700"], c["WD700"]), 30.0)
            st = _score(np.abs(hrrr_d["T700"] - c["T700"]), 1.5) if "T700" in c else s
            add("UPPER", (s * (1 - 0.5 * dirw) + sd * 0.5 * dirw) * 0.7 + st * 0.3, w)

    out = {}
    for k, (s, w) in acc.items():
        out[k] = (s / w * 100) if w > 0 else np.full(shape, np.nan)

    # Precip only weighs in where some model actually has precipitation.
    wet = np.zeros(shape, bool)
    for v in precip_win.values():
        wet |= v >= 0.25
    wp = np.where(wet, 1.5, 0.25)
    cats = [("T2", 1.0), ("WIND", 1.5), ("GUST", 0.5), ("UPPER", 1.0)]
    num = sum(np.nan_to_num(out[k]) * w * np.isfinite(out[k]) for k, w in cats)
    den = sum(w * np.isfinite(out[k]) for k, w in cats)
    num = num + np.nan_to_num(out["PRECIP"]) * wp * np.isfinite(out["PRECIP"])
    den = den + wp * np.isfinite(out["PRECIP"])
    with np.errstate(invalid="ignore", divide="ignore"):
        out["ALL"] = np.where(den > 0, num / den, np.nan)
    out["N"] = np.full(shape, float(len(comps)))
    return out
