"""Weak-layer ledger: buried persistent weak layers for every range, elevation band and aspect, all season.

Each day, for a shaded (north) and a sunny (south) aspect of every range and band, this watches for the weather that
grows persistent weak layers at the snow surface, records each one when snow buries it, and follows its depth, age
and loading until it heals, melts or gets too deep to matter:

  facets / depth hoar   bulk temperature gradient (0 degC ground minus HRRR skin temperature, over snow depth) above
                        10 degC/m; a cold, thin early snowpack grows a basal depth-hoar layer
  near-surface facets   clear nights with a large day-night swing in skin temperature during dry spells
  surface hoar          night samples with the snow surface below the dew point, light wind and clear sky
  crusts                rain, a thaw, or (south aspects) strong sun near freezing; facets on a crust are worse

A dry spell ends with a burial when 3 days bring at least 10 mm of water or 10 cm of snow. Thresholds come from the
literature (see the research note linked in README) and are first guesses to tune against the centers' problems,
which ingest.avy_history saves daily; verify() scores the agreement.

Inputs: history/avy.db (ingest.avy_history). Snow depth and new snow lean on SNOTEL wherever a range has stations.
"""
import datetime as dt
import math
import warnings

import numpy as np

from .avy_history import dayno, season_start, vhour

ASPECTS = ("N", "S")
KIND = {"dh": "Depth hoar", "fc": "Facets", "nsf": "Near-surface facets", "sh": "Surface hoar", "cf": "Facets on a crust"}


def attach(st, stations):
    """Attach stations to the nearest mountain cell within ~8 km: sets s["range"] and s["_cell"]."""
    from .avalanche import merc_px
    W, H, gx, gy, z = int(st["W"]), int(st["H"]), float(st["gx"]), float(st["gy"]), int(st["z"])
    cell = np.full((H, W), -1, np.int32)
    cell[st["iy"], st["ix"]] = np.arange(len(st["iy"]))
    hits = {}
    for s in stations:
        x, y = merc_px(s["lon"], s["lat"], z)
        x, y = int(x - gx), int(y - gy)
        best = None
        for r in range(0, 4):
            y0, y1, x0, x1 = max(y - r, 0), min(y + r + 1, H), max(x - r, 0), min(x + r + 1, W)
            win = cell[y0:y1, x0:x1]
            if (win >= 0).any():
                best = int(win[win >= 0][0]); break
        if best is None:
            continue
        s["range"] = int(st["rid"][best])
        s["_cell"] = best
        hits.setdefault(int(st["rid"][best]), []).append(s)
    return hits


def _sun_factor(lat, day, slope=38.0):
    """Noon direct-sun gain on a 38 deg south and north slope relative to flat ground."""
    decl = 23.44 * math.sin(math.radians(360 / 365 * (day.timetuple().tm_yday - 81)))
    h = max(90 - lat + decl, 5)
    s = math.sin(math.radians(h))
    return {"S": math.sin(math.radians(min(h + slope, 90))) / s, "N": max(math.sin(math.radians(h - slope)), 0) / s}


def inputs(db, st, today):
    """Daily per-track arrays for the season. Day D runs 12Z D to 12Z D+1 (about 5 am to 5 am mountain time)."""
    d0 = season_start(today)
    days = [d0 + dt.timedelta(days=i) for i in range((today - d0).days + 1)]
    ND = len(days)
    rows = db.execute("SELECT valid, range_id, band, asnow, apcp, t2, td2, tsk, w10, w80, tcdc, dswrf, snod FROM hrrr_track "
                      "WHERE valid >= ?", (vhour(dt.datetime.combine(d0, dt.time(18))),)).fetchall()
    keys = sorted({(r[1], r[2]) for r in rows})
    ti = {k: i for i, k in enumerate(keys)}
    A = np.full((len(keys), ND, 4, 10), np.nan, np.float32)  # [track, day, sample 18Z/00Z/06Z/12Z, field]
    base = vhour(dt.datetime.combine(d0, dt.time(12)))
    for v, r, b, *f in rows:
        k = (v - base) // 6 - 1
        d, s = k // 4, k % 4
        if 0 <= d < ND:
            A[ti[(r, b)], d, s] = [np.nan if x is None else x / 10 for x in f]
    asnow, apcp, t2, td2, tsk, w10, w80, tcdc, dswrf, snod = (A[..., j] for j in range(10))
    with np.errstate(all="ignore"):
        rain = np.where(t2 > 1, np.maximum(apcp - asnow, 0), 0)
        swe = np.where(t2 <= 1, apcp, np.minimum(apcp, asnow))
        out = dict(days=days, keys=keys,
                   hs=np.nanmax(snod, -1), hn=np.nansum(asnow, -1), swe=np.nansum(swe, -1), rain=np.nansum(rain, -1),
                   t2mean=np.nanmean(t2, -1), t2max=np.nanmax(t2[:, :, :2], -1), tskmean=np.nanmean(tsk, -1),
                   tskmax=np.nanmax(tsk[:, :, :2], -1), tsknight=tsk[:, :, 2:], tdnight=td2[:, :, 2:],
                   wnight=w10[:, :, 2:], cnight=tcdc[:, :, 2:], w10mean=np.nanmean(w10, -1), w80mean=np.nanmean(w80, -1),
                   sw=dswrf[:, :, 0], have=np.isfinite(t2).any(-1))
    # SNOTEL: pull snow depth, new snow and new water toward what nearby stations measured
    stn = db.execute("SELECT sid, lat, lon, elev_ft FROM snotel_station WHERE active = 1").fetchall()
    sts = [{"sid": s, "lat": la, "lon": lo, "elev_ft": e} for s, la, lo, e in stn]
    hits = attach(st, sts)
    obs = {}
    for r in db.execute("SELECT sid, day, snwd, wteq FROM snotel_daily WHERE day >= ?", (dayno(d0) - 1,)):
        obs[(r[0], r[1])] = r[2:]
    elev = {}
    rid, band, dem = st["rid"].astype(int), st["band"].astype(int), st["dem"]
    for k in keys:
        m = (rid == k[0]) & (band == k[1])
        elev[k] = float(np.median(dem[m])) if m.any() else 0.0
    for r, ss in hits.items():
        st_elev = float(np.median([(s["elev_ft"] or 0) * 0.3048 for s in ss]))
        for d in range(ND):
            dn = dayno(days[d]) + 1  # SNOTEL's midnight reading closest to the end of day D
            hs = [obs[(s["sid"], dn)][0] * 2.54 for s in ss if obs.get((s["sid"], dn), (None,))[0] is not None]
            dh = [(obs[(s["sid"], dn)][0] - obs[(s["sid"], dn - 1)][0]) * 2.54 for s in ss
                  if obs.get((s["sid"], dn), (None,))[0] is not None and obs.get((s["sid"], dn - 1), (None,))[0] is not None]
            dw = [(obs[(s["sid"], dn)][1] - obs[(s["sid"], dn - 1)][1]) * 0.254 * 10 for s in ss
                  if obs.get((s["sid"], dn), (None, None))[1] is not None and obs.get((s["sid"], dn - 1), (None, None))[1] is not None]
            for b in (1, 2, 3):
                i = ti.get((r, b))
                if i is None:
                    continue
                f = float(np.clip(1 + (elev[(r, b)] - st_elev) / 1500, 0, 2))
                if hs:
                    h, o = out["hs"][i, d], float(np.median(hs)) * f
                    out["hs"][i, d] = 0.3 * h + 0.7 * o if np.isfinite(h) else o
                if dh:
                    out["hn"][i, d] = 0.4 * out["hn"][i, d] + 0.6 * max(float(np.median(dh)), 0.0) * f
                if dw:
                    out["swe"][i, d] = 0.4 * out["swe"][i, d] + 0.6 * max(float(np.median(dw)), 0.0) * f
    lat = {k: float(np.median(st["lat"][(rid == k[0]) & (band == k[1])])) for k in keys}
    out["lat"] = lat
    return out


def _concern(L, depth):
    f = 0.0 if depth < 15 else 0.6 if depth < 25 else 1.0 if depth <= 120 else 0.6 if depth <= 180 else 0.35
    return L["strength"] * f


def ledger(X, i, aspect, band):
    """Run one track and aspect through the season. Returns (daily danger floor, layers alive at the end)."""
    days, ND = X["days"], len(X["days"])
    hs, hn, swe, rain = X["hs"][i], X["hn"][i], X["swe"][i], X["rain"][i]
    floor = np.zeros(ND, np.int8)
    layers, basal = [], None
    spell = None
    hs_dry = 0.0
    asp_f = 1.0 if aspect == "N" else 0.5   # sun warms south slopes, slowing gradient growth and frost
    for d in range(ND):
        if not X["have"][i, d] or not np.isfinite(hs[d]):
            floor[d] = floor[d - 1] if d else 0
            continue
        H = float(hs[d])
        if H < 5:  # no snowpack: nothing can be buried in it
            layers, basal, spell = [], None, None
            continue
        if basal is None and H >= 10:
            basal = {"kind": "dh", "buried": d, "h": 0.0, "strength": 0.0, "last_load": -99, "basal": True}
            layers.append(basal)
        if spell is None:
            spell = {"fc": 0.0, "nsf": 0.0, "sh": 0.0, "crust": False, "days": 0}
        tg = max(0.0, -float(X["tskmean"][i, d])) / max(H / 100, 0.1)          # degC per m
        fdi = float(np.clip((tg - 10) / 15, 0, 1.5))
        dry = swe[d] < 2 and hn[d] < 2
        if dry:
            hs_dry = H
            spell["days"] += 1
            spell["fc"] += fdi * asp_f
            cn = np.nanmean(X["cnight"][i, d])
            swing = float(X["tskmax"][i, d] - np.nanmin(X["tsknight"][i, d]))
            if cn < 30 and np.isfinite(swing):
                spell["nsf"] += float(np.clip((swing - 6) / 10, 0, 1))
            dep = np.clip(X["tdnight"][i, d] - X["tsknight"][i, d], 0, 4) / 4
            ok = (X["wnight"][i, d] >= 0.3) & (X["wnight"][i, d] <= 5) & (X["cnight"][i, d] < 40)
            spell["sh"] += float(np.nansum(np.where(ok, dep, 0))) * asp_f
        sun = _sun_factor(X["lat"][X["keys"][i]], days[d])[aspect]
        if rain[d] >= 2 or X["t2max"][i, d] > 3 or (dry and X["t2max"][i, d] > -1 and X["sw"][i, d] * sun > 600):
            spell["crust"] = True
        a = max(0, d - 2)
        load3, hn3, rain3 = float(swe[a:d + 1].sum()), float(hn[a:d + 1].sum()), float(rain[a:d + 1].sum())
        if not dry:
            if (load3 >= 10 or hn3 >= 10) and spell["days"] >= 2:
                sh, fc = spell["sh"] / 3, spell["fc"] / 6 + spell["nsf"] / 5
                s, kind = (sh, "sh") if sh >= fc else (fc + (0.3 if spell["crust"] else 0), "cf" if spell["crust"] else "nsf" if spell["nsf"] / 5 > spell["fc"] / 6 else "fc")
                if kind == "sh" and X["w10mean"][i, d] > 7:
                    s *= 0.5   # wind during the storm tends to destroy surface hoar before it is buried
                if s >= 0.35:
                    layers.append({"kind": kind, "buried": d, "h": hs_dry, "strength": min(s, 1.5), "last_load": d})
                spell = None
            elif hn[d] < 5:
                for k in ("fc", "nsf", "sh"):
                    spell[k] *= 0.8
            else:
                for k in ("fc", "nsf", "sh"):
                    spell[k] *= 0.5
        loaded = load3 >= 10 or rain3 >= 5 or (band >= 2 and X["w80mean"][i, d] > 12 and hn3 >= 5)
        t = float(X["t2mean"][i, d])
        f = 0
        for L in layers:
            if L.get("retired"):
                continue
            if not L.get("basal"):
                L["h"] *= 0.997          # the snow under the layer settles too
            depth = H - L["h"]
            if depth < -2:               # melted down to it
                L["retired"] = True; continue
            r = 0.03 if t > -3 else 0.006 if t < -10 else 0.015
            if tg > 10 and depth < 60:
                r = 0.0                  # a strong gradient keeps facets weak
            if rain3 >= 10 and depth < 60:
                r = 0.1                  # rain soaking into the layer
            r *= {"sh": 0.7, "dh": 0.5}.get(L["kind"], 1.0)
            L["strength"] *= 1 - r
            if L.get("basal"):
                if H < 120:
                    L["strength"] = min(1.5, L["strength"] + fdi * asp_f / 8)
            elif depth < 40 and tg > 10:
                L["strength"] = min(1.5, L["strength"] + fdi / 30)
            if loaded and d >= L["buried"]:
                L["last_load"] = d
            if L["strength"] < 0.25 and not L.get("basal"):
                L["retired"] = True; continue
            c = _concern(L, depth)
            L["depth"], L["concern"] = depth, c
            since = d - L["last_load"]
            L["state"] = "deep" if depth > 150 else "active" if since <= 10 else "dormant"
            if L["state"] == "active":
                if c >= 0.35:
                    f = max(f, 2)
                if since <= 3 and c >= 0.5:
                    f = max(f, 3)
                if load3 >= 35 and since <= 1 and c >= 0.9:
                    f = max(f, 4)
            elif c >= 0.5:
                f = max(f, 2)
        floor[d] = min(f, 2 if H < 40 else 3 if H < 70 else 5)
    alive = [L for L in layers if not L.get("retired") and L.get("concern", 0) >= 0.2]
    return floor, alive


def run(db, st, today):
    """-> {(range, band): {"floor": today's floor, "series": [...], "layers": [...]}}, plus the season's days."""
    warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN days (missing HRRR windows) are expected
    X = inputs(db, st, today)
    out = {}
    for i, (r, b) in enumerate(X["keys"]):
        if b < 1:
            continue
        fl, lay = {}, []
        for a in ASPECTS:
            fl[a], alive = ledger(X, i, a, b)
            for L in alive:
                lay.append({"kind": L["kind"], "aspect": a, "buried": X["days"][L["buried"]].isoformat(),
                            "depth": round(float(L["depth"])), "strength": round(float(L["strength"]), 2),
                            "concern": round(float(L["concern"]), 2), "state": L["state"],
                            "last_load": X["days"][L["last_load"]].isoformat() if L["last_load"] >= 0 else None})
        series = np.maximum(fl["N"], fl["S"])
        # one entry per layer: the same burial on both aspects is shown once
        merged = {}
        for L in sorted(lay, key=lambda x: -x["concern"]):
            k = (L["kind"] if L["kind"] != "cf" else "fc", L["buried"])
            if k in merged:
                merged[k]["aspect"] = "all"
            else:
                merged[k] = dict(L)
        out[(r, b)] = {"floor": int(series[-1]), "series": series.tolist(),
                       "layers": sorted(merged.values(), key=lambda x: -x["concern"])[:4]}
    return out, X["days"]


def verify(db, led, days, zones, zmap):
    """Compare the model's persistent-slab call with the centers' problem lists, day by day, in matched zones."""
    tally = {"both": 0, "center_only": 0, "model_only": 0, "neither": 0}
    per = {}
    idx = {d.isoformat(): i for i, d in enumerate(days)}
    for r, zi in zmap.items():
        z = zones[zi]
        rows = db.execute("""SELECT f.day, max(p.name LIKE '%Persistent%') FROM center_forecast f
                             LEFT JOIN center_problem p ON p.forecast_id = f.id
                             WHERE f.center = ? AND f.zone = ? AND f.day >= ? GROUP BY f.day""",
                          (z["center_id"], z["name"], days[0].isoformat())).fetchall()
        t = {"both": 0, "center_only": 0, "model_only": 0, "neither": 0}
        for day, ps in rows:
            i = idx.get(day)
            if i is None:
                continue
            m = max((led[(r, b)]["series"][i] for b in (1, 2, 3) if (r, b) in led), default=0) >= 2
            k = "both" if ps and m else "center_only" if ps else "model_only" if m else "neither"
            t[k] += 1
            tally[k] += 1
        if sum(t.values()):
            per[r] = t
    return tally, per
