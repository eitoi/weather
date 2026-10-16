"""HRRR (3 km, hourly) from the NOAA Open Data bucket on AWS, using .idx byte ranges.

Pulls every 25 mb pressure level (wrfprs) plus surface/single-level fields (wrfsfc),
decodes with eccodes and crops to the Wasatch domain at native grid resolution.
"""
import datetime as dt
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from .config import HRRR_BUCKET, LEVELS, UPPER_VARS, SFC_FIELDS, CMP_FIELDS
from . import gribio as G

log = logging.getLogger("hrrr")


def url(run, fh, kind):
    return f"{HRRR_BUCKET}/hrrr.{run:%Y%m%d}/conus/hrrr.t{run:%H}z.wrf{kind}f{fh:02d}.grib2"


def max_fh(run):
    return 48 if run.hour % 6 == 0 else 18


def run_complete(run, kinds=("sfc", "prs")):
    fh = max_fh(run)
    return all(G.exists(url(run, fh, k) + ".idx") for k in kinds)


def latest_runs(now=None, look_back=10, kinds=("sfc", "prs")):
    """Return (newest complete run, newest complete 48-h run)."""
    now = now or dt.datetime.utcnow()
    base = now.replace(minute=0, second=0, microsecond=0)
    newest = synoptic = None
    for k in range(look_back + 1):
        r = base - dt.timedelta(hours=k)
        if newest is None and run_complete(r, kinds):
            newest = r
        if newest is not None and r.hour % 6 == 0 and run_complete(r, kinds):
            synoptic = r
            break
    if newest is None:
        raise RuntimeError("no complete HRRR run found")
    return newest, synoptic


# ---------------------------------------------------------------- field specs

def full_specs(fh):
    """(key, file kind, var, level, desc predicate, conversion) for the full product."""
    specs = []
    for lev in LEVELS:
        for v in UPPER_VARS:
            specs.append((f"{v}:{lev}", "prs", v, f"{lev} mb", None, "K2C" if v in ("TMP", "DPT") else None))
    for key, (var, level, _u, _s, conv) in SFC_FIELDS.items():
        pred = None
        if key == "APCP1":
            if fh == 0:
                continue
            pred = (lambda d, fh=fh: f"{fh - 1}-{fh} hour acc" in d)
        elif key == "APCPT":
            if fh == 0:
                continue
            pred = (lambda d, fh=fh: f"0-{fh} hour acc" in d)
        elif key == "ASNOW" and fh == 0:
            continue
        specs.append((key, "sfc", var, level, pred, conv))
    return specs


def cmp_specs(fh):
    """Light field set used for time-lagged comparison (all in wrfsfc)."""
    specs = []
    for key, (var, level, conv) in CMP_FIELDS.items():
        pred = None
        if key == "APCP":
            if fh == 0:
                continue
            pred = (lambda d, fh=fh: f"{fh - 1}-{fh} hour acc" in d)
            key = "APCP1"
        specs.append((key, "sfc", var, level, pred, conv))
    return specs


# ---------------------------------------------------------------- extraction

class Crop:
    def __init__(self):
        self.win = None
        self.lats = self.lons = None

    def apply(self, msg):
        if self.win is None:
            vals, _i, la, lo = G.decode(msg, with_latlon=True)
            self.win = G.crop_window(la, lo)
            j0, j1, i0, i1 = self.win
            self.lats, self.lons = la[j0:j1, i0:i1].copy(), lo[j0:j1, i0:i1].copy()
        else:
            vals, _i, _la, _lo = G.decode(msg)
        j0, j1, i0, i1 = self.win
        return vals[j0:j1, i0:i1].copy()


def extract(jobs, crop, workers=24, progress=None):
    """jobs: list of (tag, run, fh, specs). Returns {tag: {key: 2-D array}}.

    Downloads only the needed GRIB messages via byte ranges, in parallel.
    """
    # 1) inventories
    inv = {}

    def get_idx(run, fh, kind):
        u = url(run, fh, kind)
        return (u, G.parse_idx(G.http_get(u + ".idx").decode()))

    needed = {(r, fh, s[1]) for _t, r, fh, specs in jobs for s in specs}
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(get_idx, *n): n for n in needed}
        for f in as_completed(futs):
            inv[futs[f]] = f.result()

    # 2) work units: one byte-range group per request
    units = []
    for tag, run, fh, specs in jobs:
        by_kind = {}
        for key, kind, var, level, pred, conv in specs:
            u, entries = inv[(run, fh, kind)]
            e = G.find(entries, var, level, pred)
            if e is None:
                log.debug("missing %s %s %s f%02d", var, level, run, fh)
                continue
            by_kind.setdefault(kind, {}).setdefault(e, []).append((key, conv))
        for kind, emap in by_kind.items():
            u = inv[(run, fh, kind)][0]
            for g in G.merge_ranges(list(emap.keys())):
                units.append((tag, u, g, {e: emap[e] for e in g[2]}))

    # Make sure the crop window is initialised once before going parallel.
    out = {t: {} for t, *_ in jobs}
    if units and crop.win is None:
        tag, u, g, emap = units[0]
        e0 = g[2][0]
        msg = G.fetch_ranges(u, [(e0.start, e0.end, [e0])])[e0]
        crop.apply(msg)

    def work(unit):
        tag, u, g, emap = unit
        msgs = G.fetch_ranges(u, [g])
        res = []
        for e, m in msgs.items():
            arr = crop.apply(m)
            for key, conv in emap[e]:
                res.append((tag, key, G.convert(arr, conv)))
        return res

    done = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(work, u) for u in units]
        for f in as_completed(futs):
            for tag, key, arr in f.result():
                out[tag][key] = arr
            done += 1
            if progress and done % 200 == 0:
                progress(done, len(units))
    return out
