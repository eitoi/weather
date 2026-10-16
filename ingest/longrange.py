"""Beyond the HRRR window: multi-model consensus (Open-Meteo), ensemble probabilities,
NWS Salt Lake City Area Forecast Discussion, front detection, and a written summary."""
import datetime as dt
import json
import logging
import math
import os
import re
from collections import defaultdict

import warnings

import numpy as np

from .config import PLACES, TIMEZONE, NWS_OFFICE, USER_AGENT
from . import gribio as G

log = logging.getLogger("longrange")

DET_MODELS = {
    "ecmwf_ifs025": "ECMWF IFS",
    "gfs_seamless": "GFS",
    "icon_seamless": "ICON (DWD)",
    "gem_seamless": "GEM (Canada)",
    "ukmo_seamless": "UKMO",
}
ENS_MODELS = {"gfs025": "GEFS", "ecmwf_ifs025": "ECMWF ENS"}
HOURLY = ["temperature_2m", "precipitation", "snowfall", "wind_speed_10m", "wind_direction_10m",
          "wind_gusts_10m", "pressure_msl", "cloud_cover", "freezing_level_height",
          "temperature_700hPa", "wind_speed_700hPa", "wind_direction_700hPa", "geopotential_height_500hPa"]


def zones():
    return [p for p in PLACES if p.get("zone")]


def _om(url, params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return json.loads(G.http_get(f"{url}?{q}"))


def fetch_deterministic(days=10):
    zs = zones()
    params = {
        "latitude": ",".join(str(z["lat"]) for z in zs),
        "longitude": ",".join(str(z["lon"]) for z in zs),
        "hourly": ",".join(HOURLY), "models": ",".join(DET_MODELS),
        "forecast_days": days, "timezone": TIMEZONE, "wind_speed_unit": "ms",
    }
    res = _om("https://api.open-meteo.com/v1/forecast", params)
    if isinstance(res, dict):
        res = [res]
    out = {}
    for z, r in zip(zs, res):
        h = r["hourly"]
        times = h["time"]
        per = {}
        for m in DET_MODELS:
            d = {}
            for v in HOURLY:
                arr = h.get(f"{v}_{m}")
                if arr is not None:
                    d[v] = np.array([np.nan if x is None else x for x in arr], dtype=float)
            if d and np.isfinite(d.get("temperature_2m", np.array([np.nan]))).any():
                per[m] = d
        out[z["id"]] = {"time": times, "models": per, "elevation": r.get("elevation")}
    return out


def fetch_ensemble(days=10):
    zs = zones()
    params = {
        "latitude": ",".join(str(z["lat"]) for z in zs),
        "longitude": ",".join(str(z["lon"]) for z in zs),
        "hourly": "precipitation,snowfall,wind_speed_700hPa,temperature_2m",
        "models": ",".join(ENS_MODELS), "forecast_days": days, "timezone": TIMEZONE, "wind_speed_unit": "ms",
    }
    try:
        res = _om("https://ensemble-api.open-meteo.com/v1/ensemble", params)
    except Exception as e:  # noqa
        log.warning("ensemble fetch failed: %s", e)
        return {}
    if isinstance(res, dict):
        res = [res]
    out = {}
    for z, r in zip(zs, res):
        h = r["hourly"]
        members = defaultdict(list)
        for k, arr in h.items():
            m = re.match(r"(precipitation|snowfall|wind_speed_700hPa|temperature_2m)(?:_member\d+)?_(\w+)$", k)
            if not m or arr is None:
                continue
            members[(m.group(1), m.group(2))].append(np.array([np.nan if x is None else x for x in arr], float))
        out[z["id"]] = {"time": h["time"], "members": members}
    return out


# ---------------------------------------------------------------- daily digest

def _vec_dir(speed, direction):
    u = -np.nansum(speed * np.sin(np.radians(direction)))
    v = -np.nansum(speed * np.cos(np.radians(direction)))
    return (math.degrees(math.atan2(-u, -v)) + 360) % 360 if (u or v) else float("nan")


def compass(deg):
    if deg is None or not np.isfinite(deg):
        return "—"
    return ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"][
        int((deg + 11.25) % 360 // 22.5)]


def _r(x, n=1):
    return None if x is None or not np.isfinite(x) else round(float(x), n)


def daily(det, ens, start_local):
    """Per zone, per local day: per-model values + consensus stats."""
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    digest = {}
    for zid, z in det.items():
        times = [dt.datetime.fromisoformat(t) for t in z["time"]]
        days = sorted({t.date() for t in times if t >= start_local.replace(minute=0)})
        zd = []
        for day in days:
            idx = np.array([i for i, t in enumerate(times) if t.date() == day and t >= start_local])
            if len(idx) < 3:
                continue
            row = {"date": day.isoformat(), "models": {}}
            for m, d in z["models"].items():
                if "temperature_2m" not in d or not np.isfinite(d["temperature_2m"][idx]).any():
                    continue
                g = lambda v: d[v][idx] if v in d else np.full(len(idx), np.nan)  # noqa
                ws7, wd7 = g("wind_speed_700hPa"), g("wind_direction_700hPa")
                row["models"][m] = {
                    "tmax": _r(np.nanmax(g("temperature_2m"))), "tmin": _r(np.nanmin(g("temperature_2m"))),
                    "precip": _r(np.nansum(g("precipitation")), 1), "snow": _r(np.nansum(g("snowfall")), 1),
                    "wind_max": _r(np.nanmax(g("wind_speed_10m"))), "gust_max": _r(np.nanmax(g("wind_gusts_10m"))),
                    "wind_dir": _r(_vec_dir(g("wind_speed_10m"), g("wind_direction_10m")), 0),
                    "w700_max": _r(np.nanmax(ws7)), "w700_dir": _r(_vec_dir(ws7, wd7), 0),
                    "t700_min": _r(np.nanmin(g("temperature_700hPa"))),
                    "z500_min": _r(np.nanmin(g("geopotential_height_500hPa")), 0),
                    "frz_min": _r(np.nanmin(g("freezing_level_height")), 0),
                    "cloud": _r(np.nanmean(g("cloud_cover")), 0),
                }
            ms = row["models"].values()

            def stat(k):
                v = np.array([x[k] for x in ms if x.get(k) is not None], float)
                if not len(v):
                    return None
                return {"mean": _r(v.mean()), "min": _r(v.min()), "max": _r(v.max()), "n": int(len(v))}

            row["consensus"] = {k: stat(k) for k in
                                ("tmax", "tmin", "precip", "snow", "wind_max", "gust_max", "w700_max", "t700_min",
                                 "z500_min", "frz_min", "cloud")}
            dirs = [x["w700_dir"] for x in ms if x.get("w700_dir") is not None]
            row["consensus"]["w700_dir"] = compass(_vec_dir(np.ones(len(dirs)), np.array(dirs))) if dirs else "—"
            dirs = [x["wind_dir"] for x in ms if x.get("wind_dir") is not None]
            row["consensus"]["wind_dir"] = compass(_vec_dir(np.ones(len(dirs)), np.array(dirs))) if dirs else "—"
            wet = [x["precip"] for x in ms if x.get("precip") is not None]
            row["consensus"]["models_wet"] = int(sum(1 for p in wet if p >= 1.0))
            row["consensus"]["models_total"] = len(wet)

            # Ensemble probabilities
            e = ens.get(zid)
            if e:
                et = [dt.datetime.fromisoformat(t) for t in e["time"]]
                eidx = np.array([i for i, t in enumerate(et) if t.date() == day and t >= start_local])
                probs = {}
                for em, label in ENS_MODELS.items():
                    pm = e["members"].get(("precipitation", em))
                    sm = e["members"].get(("snowfall", em))
                    wm = e["members"].get(("wind_speed_700hPa", em))
                    if pm and len(eidx):
                        tot = np.array([np.nansum(a[eidx]) for a in pm])
                        p = {"members": len(tot), "p_precip_2mm": _r(100 * np.mean(tot >= 2.0), 0),
                             "p_precip_10mm": _r(100 * np.mean(tot >= 10.0), 0),
                             "precip_p10": _r(np.percentile(tot, 10)), "precip_p50": _r(np.percentile(tot, 50)),
                             "precip_p90": _r(np.percentile(tot, 90))}
                        if sm:
                            st = np.array([np.nansum(a[eidx]) for a in sm])
                            p.update({"p_snow_5cm": _r(100 * np.mean(st >= 5), 0),
                                      "p_snow_15cm": _r(100 * np.mean(st >= 15), 0),
                                      "snow_p50": _r(np.percentile(st, 50)), "snow_p90": _r(np.percentile(st, 90))})
                        if wm:
                            mx = np.array([np.nanmax(a[eidx]) for a in wm])
                            p.update({"p_w700_15ms": _r(100 * np.mean(mx >= 15), 0),
                                      "w700_p50": _r(np.percentile(mx, 50))})
                        probs[label] = p
                row["ensemble"] = probs
            zd.append(row)
        digest[zid] = zd
    return digest


# ---------------------------------------------------------------- fronts & troughs

def _unit_mean_dir(wd):
    wd = wd[np.isfinite(wd)]
    if not len(wd):
        return float("nan")
    return (math.degrees(math.atan2(np.sin(np.radians(wd)).mean(), np.cos(np.radians(wd)).mean())) + 360) % 360


def detect_fronts(det, start_local, zone="alta"):
    """Cold-front candidates per model: 700 mb cooling over 12 h with a wind veer to W/NW or a
    surface-pressure trough (or very strong cooling alone). Clustered across models within 18 h."""
    z = det.get(zone) or next(iter(det.values()))
    times = [dt.datetime.fromisoformat(t) for t in z["time"]]
    cands = []
    for m, d in z["models"].items():
        t7 = d.get("temperature_700hPa")
        if t7 is None or np.isfinite(t7).sum() < 48:
            continue
        n = len(t7)
        wd = d.get("wind_direction_700hPa", np.full(n, np.nan))
        p = d.get("pressure_msl", np.full(n, np.nan))
        pr = d.get("precipitation", np.zeros(n))
        ts = np.convolve(np.nan_to_num(t7, nan=np.nanmean(t7)), np.ones(3) / 3, mode="same")
        d12 = np.full(n, np.nan)
        d12[6:n - 6] = ts[12:] - ts[:-12]
        i = 9
        while i < n - 9:
            if not (np.isfinite(d12[i]) and d12[i] <= -3.0 and times[i] >= start_local
                    and np.isfinite(t7[i - 6:i + 7]).all()):
                i += 1
                continue
            win = range(i, min(i + 9, n - 9))
            j = min(win, key=lambda q: d12[q] if np.isfinite(d12[q]) else 0)
            drop = float(-d12[j])
            before, after = _unit_mean_dir(wd[j - 9:j - 2]), _unit_mean_dir(wd[j + 3:j + 10])
            shift = float(np.abs((after - before + 180) % 360 - 180)) if np.isfinite(before + after) else 0
            veer = np.isfinite(after) and (after >= 240 or after <= 20) and shift >= 30
            pw = p[max(j - 9, 0):j + 10]
            ptrough = np.isfinite(pw).sum() > 10 and 2 <= int(np.nanargmin(pw)) <= len(pw) - 3
            if (veer or ptrough) or drop >= 5:
                cands.append({"model": m, "time": times[j], "drop": round(drop, 1), "veer": bool(veer),
                              "trough": bool(ptrough), "precip": float(round(np.nansum(pr[max(j - 6, 0):j + 12]), 1)),
                              "wind_before": compass(before), "wind_after": compass(after)})
                i = j + 24
            else:
                i = j + 1
    # cluster
    cands.sort(key=lambda c: c["time"])
    clusters = []
    for c in cands:
        if clusters and (c["time"] - clusters[-1][-1]["time"]).total_seconds() <= 18 * 3600 and \
                c["model"] not in {x["model"] for x in clusters[-1]}:
            clusters[-1].append(c)
        else:
            clusters.append([c])
    n_models = len(z["models"])
    fronts = []
    for cl in clusters:
        ts = [c["time"].timestamp() for c in cl]
        mid = dt.datetime.fromtimestamp(sum(ts) / len(ts))
        spread_h = (max(ts) - min(ts)) / 3600
        fronts.append({
            "time": mid.isoformat(timespec="minutes"),
            "earliest": min(c["time"] for c in cl).isoformat(timespec="minutes"),
            "latest": max(c["time"] for c in cl).isoformat(timespec="minutes"),
            "timing_spread_h": round(spread_h, 1),
            "models": [DET_MODELS.get(c["model"], c["model"]) for c in cl],
            "agreement": f"{len(cl)}/{n_models}",
            "cooling_700mb_c": round(float(np.mean([c["drop"] for c in cl])), 1),
            "precip_mm": round(float(np.mean([c["precip"] for c in cl])), 1),
            "wind_shift": f'{cl[0]["wind_before"]} → {cl[0]["wind_after"]}',
            "strength": "strong" if np.mean([c["drop"] for c in cl]) >= 6 else
                        "moderate" if np.mean([c["drop"] for c in cl]) >= 4 else "weak",
        })
    return fronts


# ---------------------------------------------------------------- NWS AFD

def fetch_afd():
    hdr = {"User-Agent": USER_AGENT, "Accept": "application/ld+json"}
    try:
        lst = json.loads(G.http_get(f"https://api.weather.gov/products/types/AFD/locations/{NWS_OFFICE}", headers=hdr))
        pid = lst["@graph"][0]["id"]
        prod = json.loads(G.http_get(f"https://api.weather.gov/products/{pid}", headers=hdr))
    except Exception as e:  # noqa
        log.warning("AFD fetch failed: %s", e)
        return None
    text = prod.get("productText", "")
    sections, issued = {}, {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"^\.([A-Z][A-Z /]+?)(?:\s*\(([^)]*)\))?(?:,\s*(Issued [^.]*?))?\.\.\.(.*)$", line)
        if m:
            cur = m.group(1).strip().title() + (f" ({m.group(2)})" if m.group(2) else "")
            if m.group(3):
                issued[cur] = m.group(3)
            sections[cur] = (m.group(4).strip() + "\n") if m.group(4).strip() else ""
            continue
        if line.strip() == "&&":
            cur = None
            continue
        if cur:
            sections[cur] += line + "\n"
    sections = {k: re.sub(r"\n{3,}", "\n\n", v).strip() for k, v in sections.items() if v.strip()}
    return {"issued": prod.get("issuanceTime"), "office": NWS_OFFICE, "sections": sections,
            "section_issued": issued, "text": text}


# ---------------------------------------------------------------- summary

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "overview": {"type": "string"},
        "fronts": {"type": "array", "items": {
            "type": "object",
            "properties": {"timing": {"type": "string"}, "description": {"type": "string"},
                           "confidence": {"type": "string", "enum": ["high", "moderate", "low"]}},
            "required": ["timing", "description", "confidence"], "additionalProperties": False}},
        "days": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "date": {"type": "string"}, "label": {"type": "string"},
                "wind": {"type": "string"}, "precip": {"type": "string"},
                "systems": {"type": "string"},
                "confidence": {"type": "string", "enum": ["high", "moderate", "low"]}},
            "required": ["date", "label", "wind", "precip", "systems", "confidence"], "additionalProperties": False}},
        "nws_vs_models": {"type": "string"},
    },
    "required": ["headline", "overview", "fronts", "days", "nws_vs_models"],
    "additionalProperties": False,
}

SYSTEM = """You write the extended-range mountain weather summary for a personal forecast app covering the Wasatch \
Range between the Salt Lake Valley and Park City, from Mount Wire south to Mount Nebo. The reader is an experienced \
mountain user (backcountry skiing, hiking, paragliding) who wants wind, precipitation and frontal timing in plain, \
specific language. You get (1) a daily multi-model digest for several zones, already restricted to the period after \
the HRRR forecast ends, with per-model values, consensus mean/range and ensemble probabilities, (2) algorithmically \
detected cold-front candidates, and (3) the latest NWS Salt Lake City Area Forecast Discussion.

Units in the digest: temperatures °C, precip and snow-water mm (snowfall cm), wind m/s, heights m. Write for a US \
reader: convert to °F, inches, mph and feet. Say when models disagree and how much; name the outlier model when one \
drives the spread. Prefer the NWS discussion's reasoning about pattern and timing when it is more specific than the \
models, and say when the NWS and the models differ. Keep each field short: one or two sentences. Use local day names \
("Sat", "Sun night"). Don't invent data that is not in the inputs."""


def llm_summary(payload):
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        log.info("no ANTHROPIC_API_KEY; using rule-based summary")
        return None
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic()
        resp = client.beta.messages.create(
            model="claude-opus-5-5",
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium",
                           "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}},
            system=SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
        )
        if resp.stop_reason == "refusal":
            log.warning("summary refused")
            return None
        text = next(b.text for b in resp.content if b.type == "text")
        out = json.loads(text)
        out["source"] = f"Claude ({resp.model})"
        return out
    except anthropic.AuthenticationError:
        log.info("no Anthropic credentials; using rule-based summary")
    except anthropic.APIConnectionError as e:
        log.warning("Anthropic connection error: %s", e)
    except anthropic.APIStatusError as e:
        log.warning("Anthropic API error %s: %s", e.status_code, e.message)
    except Exception as e:  # noqa
        log.warning("summary failed: %s", e)
    return None


def _in(mm):
    return mm / 25.4


def _mph(ms):
    return ms * 2.23694


def _front_conf(f):
    n, tot = (int(x) for x in f["agreement"].split("/"))
    if n >= max(tot - 1, 3) and f["timing_spread_h"] <= 12:
        return "high"
    return "moderate" if n >= 3 else "low"


def _key_messages(afd):
    if not afd:
        return ""
    km = afd["sections"].get("Key Messages", "")
    items = [re.sub(r"\s+", " ", x).strip() for x in km.split("- ") if x.strip()]
    return " ".join(items)


def rule_summary(digest, fronts, afd=None, zone="alta"):
    rows = digest.get(zone) or next(iter(digest.values()), [])
    days = []
    for r in rows:
        c = r["consensus"]
        d = dt.date.fromisoformat(r["date"])
        wet, tot = c["models_wet"], c["models_total"]
        p = c["precip"] or {}
        s = c["snow"] or {}
        w = c["w700_max"] or {}
        g = c["gust_max"] or {}
        if p.get("mean", 0) < 0.5 and wet == 0:
            precip = "Dry in all models."
        else:
            precip = (f'{wet}/{tot} models wet; {(_in(p["mean"])):.2f}" avg '
                      f'(range {_in(p["min"]):.2f}–{_in(p["max"]):.2f}")')
            if s.get("mean", 0) >= 1:
                precip += f'; snow ~{s["mean"] / 2.54:.0f}" (up to {s["max"] / 2.54:.0f}")'
        wind = (f'Ridge (700 mb) {c["w700_dir"]} {_mph(w.get("mean", 0)):.0f} mph '
                f'(range {_mph(w.get("min", 0)):.0f}–{_mph(w.get("max", 0)):.0f}); '
                f'gusts {_mph(g.get("mean", 0)):.0f} mph') if w else "—"
        fr = [f for f in fronts if f["time"][:10] == r["date"] and int(f["agreement"].split("/")[0]) >= 2]
        systems = "; ".join(f'{f["strength"]} cold front ~{dt.datetime.fromisoformat(f["time"]):%a %-I %p} '
                            f'({f["agreement"]} models, ±{f["timing_spread_h"] / 2:.0f} h)' for f in fr) or "—"
        spread = (p.get("max", 0) - p.get("min", 0)) if p else 0
        conf = "high" if (wet in (0, tot) and spread < 5) else "moderate" if spread < 12 else "low"
        days.append({"date": r["date"], "label": f"{d:%a %b %-d}", "wind": wind, "precip": precip,
                     "systems": systems, "confidence": conf})
    wet_days = [d["label"] for d in days if not d["precip"].startswith("Dry")]
    headline = ("Unsettled: precipitation signals on " + ", ".join(wet_days[:4])) if wet_days else \
        "Mostly dry extended period"
    return {
        "headline": headline,
        "overview": (f"NWS key messages: {_key_messages(afd)} " if afd else "") +
                    f"Models show {sum(1 for f in fronts if int(f['agreement'].split('/')[0]) >= 2)} cold front(s) "
                    "that at least two models agree on in the extended period.",
        "fronts": [{"timing": f'{dt.datetime.fromisoformat(f["time"]):%a %b %-d %-I %p}',
                    "description": f'{f["strength"].title()} front, 700 mb cooling {f["cooling_700mb_c"]} °C, wind '
                                   f'{f["wind_shift"]}, ~{_in(f["precip_mm"]):.2f}" precip with it',
                    "confidence": _front_conf(f)} for f in fronts if int(f["agreement"].split("/")[0]) >= 2],
        "days": days,
        "nws_vs_models": "Automatic comparison needs the Claude summary (set ANTHROPIC_API_KEY). "
                         "Read the NWS discussion below next to the model table.",
        "source": "rule-based",
    }


def build(hrrr_end_utc, days=10):
    import zoneinfo
    tz = zoneinfo.ZoneInfo(TIMEZONE)
    start_local = hrrr_end_utc.replace(tzinfo=dt.timezone.utc).astimezone(tz).replace(tzinfo=None)
    det = fetch_deterministic(days)
    ens = fetch_ensemble(days)
    digest = daily(det, ens, start_local)
    fronts = detect_fronts(det, start_local)
    afd = fetch_afd()
    payload = {
        "period_start_local": start_local.isoformat(timespec="minutes"),
        "zones": {z["id"]: z["name"] for z in zones()},
        "model_names": DET_MODELS,
        "daily_digest": digest, "front_candidates": fronts,
        "nws_afd": afd["text"] if afd else None,
    }
    summary = llm_summary(payload) if os.environ.get("WX_NO_LLM") != "1" else None
    if summary is None:
        summary = rule_summary(digest, fronts, afd)
    # Hourly consensus series for charts (zone -> model -> few vars), trimmed to the period.
    series = {}
    for zid, z in det.items():
        times = z["time"]
        keep = [i for i, t in enumerate(times) if dt.datetime.fromisoformat(t) >= start_local - dt.timedelta(hours=12)]
        series[zid] = {"time": [times[i] for i in keep], "models": {
            m: {v: [None if not np.isfinite(d[v][i]) else round(float(d[v][i]), 2) for i in keep]
                for v in ("temperature_2m", "precipitation", "snowfall", "wind_speed_10m", "wind_gusts_10m",
                          "wind_speed_700hPa", "wind_direction_700hPa", "temperature_700hPa") if v in d}
            for m, d in z["models"].items()}}
    return {
        "generated": dt.datetime.utcnow().isoformat(timespec="minutes") + "Z",
        "period_start_local": start_local.isoformat(timespec="minutes"),
        "zones": [{"id": z["id"], "name": z["name"], "lat": z["lat"], "lon": z["lon"]} for z in zones()],
        "model_names": DET_MODELS, "ensemble_names": list(ENS_MODELS.values()),
        "summary": summary, "fronts": fronts, "daily": digest, "series": series,
        "afd": {k: v for k, v in (afd or {}).items() if k != "text"} if afd else None,
    }
