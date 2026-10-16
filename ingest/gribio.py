"""GRIB2 helpers: .idx inventories, HTTP byte-range downloads, eccodes decoding, cropping, regridding."""
import re
import time
import threading
import numpy as np
import httpx
import eccodes

from .config import USER_AGENT, BBOX

_local = threading.local()


def client():
    c = getattr(_local, "client", None)
    if c is None:
        c = httpx.Client(timeout=httpx.Timeout(60, connect=20), headers={"User-Agent": USER_AGENT},
                         follow_redirects=True)
        _local.client = c
    return c


def http_get(url, headers=None, tries=4, ok404=False):
    for k in range(tries):
        try:
            r = client().get(url, headers=headers or {})
            if r.status_code == 404 and ok404:
                return None
            if r.status_code in (200, 206):
                return r.content
            if r.status_code == 404:
                raise FileNotFoundError(url)
            raise RuntimeError(f"HTTP {r.status_code} for {url}")
        except FileNotFoundError:
            raise
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1.5 * (k + 1))


def exists(url):
    try:
        r = client().head(url)
        return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------- idx inventories

class IdxEntry:
    __slots__ = ("n", "start", "end", "var", "level", "desc")

    def __init__(self, n, start, var, level, desc):
        self.n, self.start, self.end, self.var, self.level, self.desc = n, start, None, var, level, desc


def parse_idx(text):
    """Parse a wgrib2-style .idx. Returns list of IdxEntry with byte ranges (end inclusive or None=EOF)."""
    out = []
    for line in text.strip().splitlines():
        p = line.split(":")
        if len(p) < 6:
            continue
        out.append(IdxEntry(int(p[0]), int(p[1]), p[3], p[4], p[5]))
    for a, b in zip(out, out[1:]):
        a.end = b.start - 1
    return out


def find(entries, var, level, desc_pred=None):
    for e in entries:
        if e.var == var and e.level == level and (desc_pred is None or desc_pred(e.desc)):
            return e
    return None


def merge_ranges(entries):
    """Group entries that are adjacent in the file into single byte ranges. Returns [(start, end, [entries])]."""
    es = sorted(entries, key=lambda e: e.start)
    groups = []
    for e in es:
        if groups and groups[-1][1] is not None and groups[-1][1] + 1 == e.start:
            g = groups[-1]
            groups[-1] = (g[0], e.end, g[2] + [e])
        else:
            groups.append((e.start, e.end, [e]))
    return groups


def fetch_ranges(url, groups):
    """Download each byte-range group; returns {entry: bytes} split into individual GRIB messages."""
    out = {}
    for start, end, es in groups:
        rng = f"bytes={start}-" + ("" if end is None else str(end))
        data = http_get(url, headers={"Range": rng})
        msgs = split_messages(data)
        if len(msgs) != len(es):
            raise RuntimeError(f"expected {len(es)} messages got {len(msgs)} from {url} {rng}")
        for e, m in zip(es, msgs):
            out[e] = m
    return out


def split_messages(data):
    msgs, off = [], 0
    while True:
        i = data.find(b"GRIB", off)
        if i < 0 or i + 16 > len(data):
            break
        ln = int.from_bytes(data[i + 8:i + 16], "big")
        msgs.append(data[i:i + ln])
        off = i + ln
    return msgs


# ---------------------------------------------------------------- decoding

def decode(msg, with_latlon=False):
    """Decode one GRIB2 message -> (2-D array [ny, nx] south-to-north, info dict, lats, lons)."""
    h = eccodes.codes_new_from_message(msg)
    try:
        nx, ny = eccodes.codes_get(h, "Nx"), eccodes.codes_get(h, "Ny")
        vals = eccodes.codes_get_values(h).reshape(ny, nx)
        miss = eccodes.codes_get(h, "missingValue")
        info = {
            "shortName": eccodes.codes_get(h, "shortName"),
            "stepRange": eccodes.codes_get(h, "stepRange"),
            "startStep": eccodes.codes_get(h, "startStep"),
            "endStep": eccodes.codes_get(h, "endStep"),
            "jpos": eccodes.codes_get(h, "jScansPositively"),
        }
        vals = np.where(vals == miss, np.nan, vals) if eccodes.codes_get(h, "bitmapPresent") else vals
        lats = lons = None
        if with_latlon:
            lats = eccodes.codes_get_array(h, "latitudes").reshape(ny, nx)
            lons = eccodes.codes_get_array(h, "longitudes").reshape(ny, nx)
            lons = np.where(lons > 180, lons - 360, lons)
        if not info["jpos"]:
            vals = vals[::-1]
            if with_latlon:
                lats, lons = lats[::-1], lons[::-1]
        return vals.astype(np.float32), info, lats, lons
    finally:
        eccodes.codes_release(h)


def crop_window(lats, lons, bbox=BBOX):
    m = (lats >= bbox["south"]) & (lats <= bbox["north"]) & (lons >= bbox["west"]) & (lons <= bbox["east"])
    jj, ii = np.where(m)
    return int(jj.min()), int(jj.max()) + 1, int(ii.min()), int(ii.max()) + 1


def convert(vals, how):
    if how == "K2C":
        return vals - 273.15
    if how == "m2cm":
        return vals * 100.0
    if how == "Pa2hPa":
        return vals / 100.0
    if how == "m2km":
        return vals / 1000.0
    if how == "s2h":  # kg m-2 s-1 -> mm/h
        return vals * 3600.0
    return vals


# ---------------------------------------------------------------- regridding

class Regridder:
    """Inverse-distance regrid from a source lat/lon grid onto target points (k nearest)."""

    def __init__(self, src_lat, src_lon, dst_lat, dst_lon, k=4):
        sl, so = src_lat.ravel(), src_lon.ravel()
        cos = np.cos(np.radians(np.nanmean(dst_lat)))
        dl, do = dst_lat.ravel(), dst_lon.ravel()
        d2 = (dl[:, None] - sl[None, :]) ** 2 + ((do[:, None] - so[None, :]) * cos) ** 2
        idx = np.argpartition(d2, kth=min(k, d2.shape[1] - 1), axis=1)[:, :k]
        dd = np.take_along_axis(d2, idx, axis=1)
        w = 1.0 / np.maximum(np.sqrt(dd), 1e-6)
        if k == 1:
            w = np.ones_like(w)
        self.idx, self.w, self.shape = idx, w / w.sum(axis=1, keepdims=True), dst_lat.shape

    def __call__(self, src):
        v = src.ravel()[self.idx]
        return (v * self.w).sum(axis=1).reshape(self.shape).astype(np.float32)


HOUR_ACC = re.compile(r"(\d+)-(\d+) hour acc")
