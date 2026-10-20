#!/usr/bin/env python3
"""One-time prep for the avalanche view. Rerun only when ingest/avy_ranges.py changes.

    pip install pillow scipy pyshp     # dev-only, not needed by the hourly build
    python tools/make_avy_static.py

Builds a Web Mercator (zoom 6, ~2 km) grid over the ten western states, finds rugged high terrain,
assigns each mountain cell to the nearest named range and an elevation band (below / near / above an
approximate treeline), and links each cell to its HRRR grid point. Writes:
  ingest/avy_static.npz   per-cell arrays used by ingest/avalanche.py
  site/img/avy-grid.png   R = range id, G = elevation band, for the browser

Elevation: AWS Terrain Tiles (terrarium PNGs, zoom 7). States: Natural Earth 1:10m admin-1.
"""
import io, json, math, os, sys, zipfile, urllib.request, concurrent.futures as cf
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
from scipy.spatial import cKDTree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ingest.avy_ranges import RANGES, MAX_KM

CACHE = os.path.join(ROOT, "cache", "avy-static")
LON0, LON1, LAT0, LAT1 = -125.0, -102.0, 31.2, 49.1
Z = 7
STATES = ["Washington", "Oregon", "California", "Nevada", "Idaho", "Montana", "Wyoming", "Utah", "Colorado", "New Mexico"]
ABBR = ["WA", "OR", "CA", "NV", "ID", "MT", "WY", "UT", "CO", "NM"]


def merc_px(lon, lat, z):
    n = 256 * 2 ** z
    x = (np.asarray(lon) + 180) / 360 * n
    lr = np.radians(np.asarray(lat))
    y = (1 - np.log(np.tan(lr) + 1 / np.cos(lr)) / math.pi) / 2 * n
    return x, y


def px_lonlat(x, y, z):
    n = 256 * 2 ** z
    lon = np.asarray(x) / n * 360 - 180
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * np.asarray(y) / n))))
    return lon, lat


def get(url):
    for i in range(5):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except Exception as e:
            err = e
    raise err


def fetch_dem():
    x0, y0 = merc_px(LON0, LAT1, Z)
    x1, y1 = merc_px(LON1, LAT0, Z)
    tx0, ty0, tx1, ty1 = int(x0 // 256), int(y0 // 256), int(x1 // 256), int(y1 // 256)
    W, H = (tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256
    dem = np.zeros((H, W), np.float32)

    def one(t):
        tx, ty = t
        png = get(f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{Z}/{tx}/{ty}.png")
        a = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(np.float32)
        return tx, ty, a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768

    tiles = [(tx, ty) for tx in range(tx0, tx1 + 1) for ty in range(ty0, ty1 + 1)]
    with cf.ThreadPoolExecutor(16) as ex:
        for tx, ty, e in ex.map(one, tiles):
            dem[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = e
    # crop to exact bounds (even sizes so the zoom-6 grid is exactly half)
    cx0, cy0 = int(x0) - tx0 * 256, int(y0) - ty0 * 256
    cx0 -= cx0 % 2; cy0 -= cy0 % 2
    w = int(x1 - x0); h = int(y1 - y0); w -= w % 2; h -= h % 2
    return dem[cy0:cy0 + h, cx0:cx0 + w], tx0 * 256 + cx0, ty0 * 256 + cy0


def load_states():
    zpath = os.path.join(CACHE, "ne_states.zip")
    if not os.path.exists(zpath):
        open(zpath, "wb").write(get("https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_1_states_provinces_lakes.zip"))
    import shapefile  # pyshp
    z = zipfile.ZipFile(zpath)
    r = shapefile.Reader(shp=io.BytesIO(z.read("ne_10m_admin_1_states_provinces_lakes.shp")),
                         dbf=io.BytesIO(z.read("ne_10m_admin_1_states_provinces_lakes.dbf")))
    fields = [f[0] for f in r.fields[1:]]
    out = {}
    for sr in r.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        if rec.get("admin") != "United States of America":
            continue
        name = rec.get("name")
        pts, parts = sr.shape.points, list(sr.shape.parts) + [len(sr.shape.points)]
        rings = [pts[parts[i]:parts[i + 1]] for i in range(len(parts) - 1)]
        out[name] = rings
    return out


def simplify(ring, tol):
    """Douglas-Peucker on (lon, lat) tuples."""
    if len(ring) < 4:
        return ring
    a = np.asarray(ring)
    keep = np.zeros(len(a), bool); keep[0] = keep[-1] = True
    stack = [(0, len(a) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        p, q = a[i], a[j]
        d = q - p
        seg = a[i + 1:j] - p
        L = np.hypot(*d)
        dist = np.hypot(seg[:, 0], seg[:, 1]) if L < 1e-9 else np.abs(seg[:, 0] * d[1] - seg[:, 1] * d[0]) / L
        k = int(np.argmax(dist))
        if dist[k] > tol:
            keep[i + 1 + k] = True
            stack += [(i, i + 1 + k), (i + 1 + k, j)]
    return [tuple(map(float, x)) for x in a[keep]]


def treeline(lat, lon):
    interior = 3550 - 105 * (lat - 37)
    pacific = np.interp(lat, [36, 37, 39, 40.5, 42, 45, 49], [3550, 3450, 3050, 2700, 2400, 2050, 1800])
    w = np.clip((-117.5 - lon) / 2.0, 0, 1)
    tl = w * pacific + (1 - w) * interior
    # northern Idaho / NW Montana run lower than the interior formula
    nw = np.clip((lat - 46) / 2.5, 0, 1) * np.clip((-112.5 - lon) / 2.5, 0, 1)
    return tl - 250 * nw


def main():
    os.makedirs(CACHE, exist_ok=True)
    print("fetching DEM tiles...")
    dem7, gx7, gy7 = fetch_dem()
    dem7 = np.where(dem7 < -100, 0, dem7)
    H7, W7 = dem7.shape
    print("dem7", dem7.shape)

    # analysis grid: zoom 6 = 2x2 mean of zoom 7
    dem = dem7.reshape(H7 // 2, 2, W7 // 2, 2).mean(axis=(1, 3))
    H, W = dem.shape
    gx6, gy6 = gx7 / 2, gy7 / 2
    lon_c, _ = px_lonlat(gx6 + np.arange(W) + 0.5, 0, Z - 1)
    _, lat_r = px_lonlat(0, gy6 + np.arange(H) + 0.5, Z - 1)
    LON, LAT = np.meshgrid(lon_c, lat_r)

    # states raster
    print("rasterizing states...")
    states = load_states()
    st_img = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(st_img)
    for i, name in enumerate(STATES):
        for ring in states[name]:
            lo = np.array([p[0] for p in ring]); la = np.array([p[1] for p in ring])
            x, y = merc_px(lo, la, Z - 1)
            dr.polygon(list(zip(x - gx6, y - gy6)), fill=i + 1)
    state = np.asarray(st_img)

    # mountain mask: rugged and high, inside the ten states
    relief = ndimage.maximum_filter(dem, 5) - ndimage.minimum_filter(dem, 5)
    tl0 = treeline(LAT, LON)
    thresh = np.where(LAT < 36.5, np.minimum(tl0 - 1000, 2300), tl0 - 1000)
    mount = (relief > 380) & (dem > thresh) & (state > 0)
    mount = ndimage.binary_opening(mount, iterations=1)

    # assign to nearest crest line within MAX_KM
    print("assigning ranges...")
    iy, ix = np.nonzero(mount)
    plat, plon = LAT[iy, ix], LON[iy, ix]
    best = np.full(len(iy), 1e9); rid = np.zeros(len(iy), np.int16)
    for k, (name, st, line, zone) in enumerate(RANGES):
        pts = line if len(line) > 1 else line * 2
        for (a_lat, a_lon), (b_lat, b_lon) in zip(pts[:-1], pts[1:]):
            c = math.cos(math.radians((a_lat + b_lat) / 2))
            ax, ay, bx, by = a_lon * c * 111.2, a_lat * 111.2, b_lon * c * 111.2, b_lat * 111.2
            px, py = plon * c * 111.2, plat * 111.2
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy or 1e-9
            t = np.clip(((px - ax) * dx + (py - ay) * dy) / L2, 0, 1)
            d = np.hypot(px - (ax + t * dx), py - (ay + t * dy))
            upd = d < best
            best[upd] = d[upd]; rid[upd] = k + 1
    rid[best > MAX_KM] = 0
    range_id = np.zeros((H, W), np.uint8)
    range_id[iy, ix] = rid

    tl = treeline(LAT, LON)
    band = np.where(dem >= tl + 150, 3, np.where(dem >= tl - 300, 2, 1)).astype(np.uint8)
    band[range_id == 0] = 0

    # link every mountain cell to its HRRR grid point (nearest neighbour on the full CONUS grid)
    from ingest import gribio as G, hrrr as HR
    run = HR.latest_runs(kinds=("sfc",))[0]
    u = HR.url(run, 0, "sfc")
    e = G.find(G.parse_idx(G.http_get(u + ".idx").decode()), "HGT", "surface")
    hgt, _info, hlat, hlon = G.decode(G.fetch_ranges(u, [(e.start, e.end, [e])])[e], with_latlon=True)
    def xyz(la, lo):
        la, lo = np.radians(la), np.radians(lo)
        return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], -1)
    iy, ix = np.nonzero(range_id)
    _, hidx = cKDTree(xyz(hlat.ravel(), hlon.ravel())).query(xyz(LAT[iy, ix], LON[iy, ix]))
    np.savez_compressed(os.path.join(ROOT, "ingest", "avy_static.npz"),
                        W=W, H=H, gx=gx6, gy=gy6, z=Z - 1, hrrr_shape=np.array(hgt.shape),
                        iy=iy.astype(np.int16), ix=ix.astype(np.int16), dem=np.round(dem[iy, ix]).astype(np.int16),
                        band=band[iy, ix], rid=range_id[iy, ix], hidx=hidx.astype(np.int32),
                        hhgt=np.round(hgt.ravel()[hidx]).astype(np.int16),
                        lat=LAT[iy, ix].astype(np.float32), lon=LON[iy, ix].astype(np.float32))
    rgb = np.zeros((H, W, 3), np.uint8)
    rgb[..., 0] = range_id; rgb[..., 1] = band
    os.makedirs(os.path.join(ROOT, "site", "img"), exist_ok=True)
    Image.fromarray(rgb, "RGB").save(os.path.join(ROOT, "site", "img", "avy-grid.png"), optimize=True)
    counts = np.bincount(range_id.ravel(), minlength=len(RANGES) + 1)
    empty = [RANGES[i][0] for i in range(len(RANGES)) if counts[i + 1] == 0]
    print("grid", W, H, "mountain cells", int((range_id > 0).sum()), "empty ranges:", empty)


if __name__ == "__main__":
    main()
