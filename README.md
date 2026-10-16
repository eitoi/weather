# Wasatch Wx

A personal mountain-weather app for the Wasatch, from Mount Wire south to Mount Nebo, between the Salt Lake
and Utah valleys and Park City. It works in a desktop browser, and you can install it on an iPhone home screen
(it runs full-screen like an app and keeps the last forecast for offline use).

## What it shows

**Map** (native HRRR 3 km grid cells, no smoothing or interpolation):
wind (10 m, 80 m, gusts, hourly max, and every pressure level from 900 to 200 mb), temperature, dew point, RH,
vertical motion, precipitation (hourly by type, accumulated, rate), snowfall, cloud layers, cloud base, ceiling,
freezing level, mixed-layer top, CAPE, lifted index, lightning, simulated radar, visibility, HRRR confidence,
and model terrain. Turn on **Values** to print the exact HRRR number in each cell when zoomed in. Use the
slider or the ← → keys to scrub time.

**Point forecast** (click or tap anywhere, or a named place):
- *Now*: key numbers plus a winds-aloft table for the selected hour
- *Meteogram*: temperature, wind and gusts with direction arrows, 700 mb wind, precip by type, snowfall,
  clouds, freezing level, mixed-layer top, confidence
- *Upper air*: time-height section using every HRRR level at that cell, colored by wind, temperature, humidity
  or vertical motion. It also draws wind arrows, the 0 °C line, the mixed-layer top and cloud hatching.
- *Sounding*: temperature/dew-point profile and a table of all 29 levels (height, AGL, T, Td, RH, wind, w, ω)
- *Hourly table*: every surface field, hour by hour
- *Confidence*: HRRR against the NAM 3 km nest, RAP 13 km and the two previous HRRR runs, with the score
  broken down by category

**15-min (dawn & dusk)**: switch the time bar to **15-min**, or open a point's **15-min** tab. You get HRRR's
15-minute surface output for the daylight 3 hours after sunrise and before sunset, within the newest run's first
18 hours:
- wind at 10 m and 80 m, gusts, and the 5-minute average
- temperature and dew point
- sunshine (solar radiation), precipitation and type
- cloud base, visibility, radar

HRRR doesn't produce 15-minute upper-air data or anything beyond 18 h.
The window length is `WINDOW_H` in `ingest/subhourly.py`.

**Launches**: your known launches (triangles point the way the launch faces) and the predicted launch spots
from `~/Documents/paraglider-launch`. Turn on **Wind fit** to colour the known launches by how the forecast
10 m wind lines up with each launch: on the slope, crossing, over the back, or too strong. Rebuild the file
with `.venv/bin/python tools/import_launches.py`.

**Outlook** (after the HRRR window ends, out to about 10 days): a written summary of wind, precipitation and
fronts. It is built from the ECMWF, GFS, ICON, GEM and UKMO deterministic runs, the GEFS and ECMWF
ensembles, cold-front detection (700 mb cooling plus a wind veer or pressure trough), and the latest NWS
Salt Lake City Area Forecast Discussion. It also shows per-model charts and tables for six zones.

## Data sources

| Source | Use | How |
|---|---|---|
| HRRR CONUS (NOAA, 3 km, hourly runs; 48 h at 00/06/12/18Z, 18 h otherwise) | Everything on the map and in point views | AWS Open Data bucket, byte-range downloads of only the needed GRIB2 messages, cropped to the domain |
| NAM CONUS Nest (3 km) | Confidence score | NOMADS grib filter, cropped server-side |
| RAP (13 km) | Confidence score | NOMADS grib filter |
| Previous HRRR runs | Confidence score (time-lagged ensemble) | AWS |
| ECMWF / GFS / ICON / GEM / UKMO, GEFS and ECMWF ensembles | Outlook | Open-Meteo API |
| NWS SLC Area Forecast Discussion | Outlook | api.weather.gov |

The time axis starts with the newest complete HRRR run. When that run only reaches 18 h, it continues with
the newest 48-hour run, so you always get about 48 hours. The label under the slider shows which run and
forecast hour each step comes from.

### Confidence score
Each hour, at every grid cell, HRRR is compared with each comparison model:
- 2 m temperature (tolerance 2 °C)
- 10 m wind speed and direction (direction counts only when it's windy)
- gusts
- precipitation summed over 3 hours
- 700 mb wind and temperature

Each difference becomes a 0–1 agreement value (Gaussian with the tolerance as its width). The values are
weighted by source (NAM 1.0, previous HRRR 1.0, RAP 0.8, HRRR two runs back 0.7) and then by category.
Precipitation only weighs heavily where some model actually has precipitation.
Scores of 75 and above are labelled *High*, 50–75 *Moderate*, and below 50 *Low*.

## Running it on your Mac

```bash
cd ~/Documents/wasatch-wx
./update.sh            # build the forecast data (~15 min the first time, ~5–8 min after)
./serve.sh             # open http://localhost:8080
```

`./update.sh --loop` rebuilds every hour at :55.

**iPhone on the same Wi-Fi:** `serve.sh` prints an address like `http://192.168.1.20:8080`. Open it in Safari.
Offline caching only works over HTTPS, so for a home-screen app that works anywhere, use GitHub Pages
(below).

**Written outlook:** without an API key the outlook uses a rule-based summary. For the Claude-written summary
(which also compares the NWS discussion with the models), create a `.env` file containing
`ANTHROPIC_API_KEY=sk-ant-...`.

## Hosting for iPhone (free, works anywhere)

1. Create a **public** GitHub repository and push this folder (`git init && git add . && git commit -m init`, then
   `git remote add origin …` and `git push -u origin main`). GitHub Pages is only free for public repositories.
   Public repositories also get unlimited Actions minutes; the hourly job uses about 5–10 min.
2. In the repository, go to **Settings → Pages → Build and deployment** and set Source to **GitHub Actions**.
3. Optional: **Settings → Secrets and variables → Actions → New repository secret** named `ANTHROPIC_API_KEY`.
4. Under **Actions → Update forecast → Run workflow**, start the first build. After that it runs every hour.
5. On the iPhone, open `https://<you>.github.io/<repo>/` in Safari, tap **Share → Add to Home Screen**.

## Keeping it up to date

You don't have to run anything. GitHub rebuilds the forecast every hour at :55, and scheduled runs can start
5–20 minutes late when GitHub is busy. An open copy of the app checks for new data every 5 minutes, and again
whenever you switch back to it. When a new run is available, it loads it in place and keeps your point, layer
and time.

The **↻** button checks for new data right away. To make it *start* a build, add a token in **Settings**:
1. Create a fine-grained token with *Only select repositories → weather* and *Actions: Read and write* (nothing
   else).
2. Paste it into Settings on each device where you want the button.

The token is stored only in that browser. The page is public, so never put a token in the repository.

## Layout

```
ingest/      Python data pipeline (python -m ingest.run)
  config.py    domain, levels, fields, places
  gribio.py    .idx parsing, byte-range download, eccodes decode, crop, regrid
  hrrr.py      HRRR run discovery and extraction
  compare.py   NAM/RAP download and the confidence score
  longrange.py Open-Meteo multi-model + ensembles, front detection, NWS AFD, summary
  subhourly.py 15-minute HRRR surface data for the sunrise/sunset windows
  run.py       orchestration and static-file output
site/        static web app (no build step)
  data/        generated: meta.json, grid.bin, maps/*.bin, pts/*.bin, longrange.json
cache/       downloaded HRRR fields (reused between runs; pruned after 30 h)
```

Data format: `maps/<field>.bin` is int16 `[time][y][x]`. `pts/<y>_<x>.bin` is int16 `[field][time]`, where
value = int × scale (scales are in `meta.json`) and −32768 means missing. Grid rows run south to north on
the native HRRR Lambert grid. `grid.bin` holds float32 latitudes, longitudes and model terrain.
