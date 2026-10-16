"""Import paraglider launches into site/launches.json.

    .venv/bin/python tools/import_launches.py [known.json] [top_spots.json]

known.json     CalTopo export of known launches (red = established, orange = less certain; photos are skipped)
top_spots.json best spot per patch from ~/Documents/paraglider-launch (score, run heading, slope, elevation)

Only names, positions, status and directions are kept (no CalTopo account ids or timestamps).
"""
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KNOWN = Path(sys.argv[1] if len(sys.argv) > 1 else "~/Downloads/Flying.json").expanduser()
SPOTS = Path(sys.argv[2] if len(sys.argv) > 2 else "~/Documents/paraglider-launch/output/top_spots.json").expanduser()
COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
# Same bounds the app displays, padded a little
BOX = dict(south=39.6, north=41.0, west=-112.2, east=-111.2)


def facing_from_name(name):
    """'Hounds Tooth SW' -> 225; 'Aire E/NE' -> 22.5+...; None if the name has no direction."""
    toks = [t for t in re.split(r"[\s/,()-]+", name.upper()) if t in COMPASS]
    if not toks:
        return None
    x = sum(math.sin(math.radians(COMPASS.index(t) * 22.5)) for t in toks)
    y = sum(math.cos(math.radians(COMPASS.index(t) * 22.5)) for t in toks)
    return round((math.degrees(math.atan2(x, y)) + 360) % 360)


def inside(lat, lon):
    return BOX["south"] <= lat <= BOX["north"] and BOX["west"] <= lon <= BOX["east"]


known = []
for f in json.loads(KNOWN.read_text())["features"]:
    p, g = f["properties"], f["geometry"]
    if g["type"] != "Point" or p.get("class") == "MapMediaObject":
        continue
    color = p.get("marker-color", "").lstrip("#").upper()
    status = {"FF0000": "established", "FFAA00": "uncertain"}.get(color)
    if not status:
        continue
    lon, lat = g["coordinates"][:2]
    known.append({"name": p.get("title", "").strip(), "status": status, "lat": round(lat, 5), "lon": round(lon, 5),
                  "facing": facing_from_name(p.get("title", ""))})

spots = []
for s in json.loads(SPOTS.read_text()):
    if not inside(s["lat"], s["lon"]):
        continue
    spots.append({"lat": round(s["lat"], 5), "lon": round(s["lon"], 5), "score": int(s["score"]),
                  "heading": int(s["heading"]), "slope": round(s["slope"]), "elev_ft": round(s["elev_ft"]),
                  "area_m2": int(s["area_m2"])})

# Sanity check: named launch direction vs. the nearest predicted spot's run heading
agree = []
for k in known:
    if k["facing"] is None:
        continue
    near = min(spots, key=lambda s: (s["lat"] - k["lat"]) ** 2 + ((s["lon"] - k["lon"]) * 0.76) ** 2)
    d_km = math.hypot(near["lat"] - k["lat"], (near["lon"] - k["lon"]) * 0.76) * 111
    if d_km < 0.3:
        agree.append(abs((near["heading"] - k["facing"] + 180) % 360 - 180))
if agree:
    print(f"name direction vs nearest spot heading (within 300 m, n={len(agree)}): "
          f"median diff {sorted(agree)[len(agree) // 2]} deg, "
          f"{sum(a <= 45 for a in agree)} within 45 deg, {sum(a >= 135 for a in agree)} opposite")

# Launches whose names carry no direction: borrow the run heading of a predicted spot within 300 m
borrowed = 0
for k in known:
    k["facing_src"] = "name" if k["facing"] is not None else None
    if k["facing"] is None:
        near = min(spots, key=lambda s: (s["lat"] - k["lat"]) ** 2 + ((s["lon"] - k["lon"]) * 0.76) ** 2)
        if math.hypot(near["lat"] - k["lat"], (near["lon"] - k["lon"]) * 0.76) * 111 < 0.3:
            k["facing"], k["facing_src"] = near["heading"], "model"
            borrowed += 1
print(f"{borrowed} launches got their direction from the nearest predicted spot; "
      f"{sum(k['facing'] is None for k in known)} still have none")

out = ROOT / "site" / "launches.json"
out.write_text(json.dumps({"known": known, "spots": spots,
                           "classes": [[75, "Very likely", "#1a9641"], [55, "Likely", "#a6d96a"],
                                       [35, "Possible", "#fdae61"], [20, "Marginal", "#d7191c"]]},
                          separators=(",", ":")))
print(f"{len(known)} known launches, {len(spots)} predicted spots -> {out} ({out.stat().st_size // 1024} KB)")
