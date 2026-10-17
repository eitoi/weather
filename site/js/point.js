/* Point forecast panel: now, meteogram, upper air, sounding, hourly table, confidence. */
'use strict';
(function () {
  const { U, fmt, esc } = WX;
  const panelEl = document.getElementById('pointPanel'), body = document.getElementById('ptBody');
  let tab = 'now', pt = null, cell = null, charts = [], upperOpts = { colorBy: 'wind', clouds: true, numbers: false };
  const css = WX.cssVar;

  document.querySelectorAll('#ptTabs button').forEach(b => (b.onclick = () => { tab = b.dataset.tab; render(); }));
  document.getElementById('ptClose').onclick = () => WX.clearPoint();
  document.getElementById('sheetGrip').onclick = () => panelEl.classList.toggle('full');

  WX.on('point', async p => {
    if (!p) { panelEl.classList.add('hidden'); return; }
    cell = p;
    panelEl.classList.remove('hidden');
    const near = WX.meta.places.find(q => p.name && q.name === p.name);
    document.getElementById('ptTitle').textContent = p.name || `${p.clickLat.toFixed(4)}, ${p.clickLon.toFixed(4)}`;
    document.getElementById('ptSub').innerHTML =
      `HRRR cell ${p.j},${p.i} · center ${p.lat.toFixed(3)}, ${p.lon.toFixed(3)} (${fmt(p.dist_km, 1)} km away) · model terrain ${fmt(U.len(p.elev))} ${U.lenU()}`;
    body.innerHTML = '<p class="muted">Loading…</p>';
    try { pt = await WX.getPoint(p.j, p.i); } catch (e) { body.innerHTML = '<p>Could not load point data.</p>'; return; }
    if (WX.point !== p) return;
    render();
  });
  WX.on('time', () => {
    if (!pt) return;
    if (tab === 'sub') return;
    if (tab === 'now') {
      body.querySelectorAll('tr[data-h]').forEach(r => r.classList.toggle('sel', r.dataset.h !== '' && +r.dataset.h === WX.t));
      charts.forEach(c => c.setSel && c.setSel(WX.t));
      const pill = body.querySelector('.box .conf-pill'); if (pill) pill.outerHTML = confPill(g('CONF_ALL')[WX.t]);
      return;
    }
    if (tab === 'sounding' || tab === 'table' || tab === 'models') render(true);
    else charts.forEach(c => c.setSel && c.setSel(WX.t));
  });
  WX.on('units', () => pt && render());
  WX.on('subtime', ts => { if (pt && tab === 'sub') charts.forEach(c => c.setSel && c.setSel(ts)); });
  let rz; window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => pt && !panelEl.classList.contains('hidden') && render(true), 200); });

  function render(keepScroll) {
    document.querySelectorAll('#ptTabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    const sc = body.scrollTop;
    charts.forEach(c => c.destroy && c.destroy()); charts = [];
    body.innerHTML = '';
    document.getElementById('ptSubTab').classList.toggle('hidden', !(WX.meta.sub && WX.subTimes.length));
    if (tab === 'sub' && !(WX.meta.sub && WX.subTimes.length)) tab = 'now';
    ({ now, meteogram, upper, sounding, table, models, sub })[tab]();
    if (keepScroll) body.scrollTop = sc;
  }

  const g = k => pt.get(k);
  const T = () => WX.times.length;
  const t0 = () => WX.t;
  function sumRange(arr, a, b) { let s = 0; for (let t = a; t <= b && t < arr.length; t++) if (isFinite(arr[t])) s += arr[t]; return s; }
  function snowHourly() {
    const as = g('ASNOW'), ap = g('APCP1'), fz = g('CPOFP'), src = WX.meta.sources, out = new Float32Array(T());
    for (let t = 1; t < T(); t++) {
      let inc;
      if (src[t].run === src[t - 1].run) inc = as[t] - as[t - 1];
      else if (src[t].fh === 1) inc = as[t];
      else inc = (ap[t] || 0) * (fz[t] || 0) / 100;
      out[t] = isFinite(inc) ? Math.max(0, inc) : 0;
    }
    return out;
  }
  const confClass = v => v >= 75 ? 'high' : v >= 50 ? 'moderate' : 'low';
  const confPill = v => isFinite(v) ? `<span class="conf-pill conf-${confClass(v)}">${confClass(v) === 'moderate' ? 'Moderate' : confClass(v) === 'high' ? 'High' : 'Low'} ${fmt(v)}%</span>` : '<span class="muted">n/a</span>';
  const card = (k, v, s = '') => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;
  const ceilOk = v => isFinite(v) && v < 15000;

  /* ------------------------------------------------------------ Summary: winds, confidence, precip */
  const FT10K = 3048;   // 10,000 ft in metres
  /* Wind and temperature at 10,000 ft MSL, interpolated (linear in height, u/v as vectors) between the two
     HRRR pressure levels that bracket it at this grid cell, every hour. NaN where 10,000 ft is below model terrain. */
  function wind10k() {
    const lv = WX.meta.levels, n = T(), u = new Float32Array(n).fill(NaN), v = new Float32Array(n).fill(NaN), tc = new Float32Array(n).fill(NaN);
    const Z = lv.map(p => g(`HGT:${p}`)), UU = lv.map(p => g(`UGRD:${p}`)), VV = lv.map(p => g(`VGRD:${p}`)), TT = lv.map(p => g(`TMP:${p}`));
    for (let t = 0; t < n; t++) {
      if (cell.elev > FT10K) continue;
      for (let l = 0; l < lv.length - 1; l++) {
        const z0 = Z[l][t], z1 = Z[l + 1][t];
        if (z0 <= FT10K && FT10K <= z1) {
          const f = (FT10K - z0) / (z1 - z0);
          u[t] = UU[l][t] + (UU[l + 1][t] - UU[l][t]) * f;
          v[t] = VV[l][t] + (VV[l + 1][t] - VV[l][t]) * f;
          tc[t] = TT[l][t] + (TT[l + 1][t] - TT[l][t]) * f;
          break;
        }
      }
    }
    return { u, v, tc };
  }

  function precipSummary() {
    const ap = g('APCP1'), fz = g('CPOFP'), sn = snowHourly(), n = T(), wetMin = 0.254;   // 0.01"
    const end = WX.times[n - 1];
    let tot = 0, frozen = 0, snow = 0, first = -1, last = -1, peak = -1;
    for (let t = 0; t < n; t++) {
      const p = ap[t] || 0;
      tot += p; snow += sn[t] || 0; frozen += p * (isFinite(fz[t]) ? fz[t] : 0) / 100;
      if (p >= wetMin) { if (first < 0) first = t; last = t; if (peak < 0 || p > ap[peak]) peak = t; }
    }
    const dec = U.imperial() ? 2 : 1, tm = d => WX.time.full(d);
    if (first < 0) return tot >= 0.05 ? `Only a trace (${fmt(U.precip(tot), dec)} ${U.precipU()}) through ${tm(end)}.` : `Dry through ${tm(end)}.`;
    const fr = frozen / tot, kind = fr >= 0.7 ? 'Snow' : fr > 0.2 ? 'Rain/snow mix' : 'Rain';
    const snowTxt = snow >= 0.25 ? ` (~${fmt(U.snow(snow), 1)} ${U.snowU()} of snow)` : '';
    const span = first === last ? `at ${tm(WX.times[first])}` : `${tm(WX.times[first])} to ${tm(WX.times[last])}`;
    return `${kind}, ${fmt(U.precip(tot), dec)} ${U.precipU()} water${snowTxt}, ${span}; heaviest ${tm(WX.times[peak])} (${fmt(U.precip(ap[peak]), dec)} ${U.precipU()}/h).`;
  }

  async function now() {
    const p = WX.point;
    let sp = null;
    if (WX.meta.sub && WX.subTimes.length) { try { sp = await WX.getSubPoint(p.j, p.i); } catch (e) { sp = null; } }
    if (WX.point !== p || tab !== 'now') return;
    body.innerHTML = '';
    const t = t0(), conf = g('CONF_ALL'), up = wind10k();
    const head = document.createElement('div');
    head.innerHTML = `<div class="box"><div class="controls-row" style="margin:0"><b>HRRR confidence now:</b> ${confPill(conf[t])}
        <span class="muted small">${WX.time.full(WX.times[t])} · agreement with NAM, RAP and earlier HRRR runs</span></div>
      <div style="margin-top:6px"><b>Precipitation:</b> ${esc(precipSummary())}</div></div>`;
    body.appendChild(head);

    // Surface wind on a real time axis: hourly, every 15 minutes inside the dawn/dusk windows
    const u10 = g('U10'), v10 = g('V10'), gust = g('GUST');
    const pts = [];
    const inWin = q => sp && WX.meta.sub.spans.some(s => q >= new Date(s.start) && q <= new Date(s.end));
    for (let h = 0; h < T(); h++) if (!inWin(WX.times[h])) pts.push({ q: WX.times[h], u: u10[h], v: v10[h], gu: gust[h], h });
    if (sp) WX.subTimes.forEach((q, i) => pts.push({ q, u: sp.get('U10')[i], v: sp.get('V10')[i], gu: sp.get('GUST')[i], q15: true,
      h: WX.times.findIndex(d => +d === +q) }));
    pts.sort((a, b) => a.q - b.q);
    const hourly = WX.times.slice();
    const hourToPt = h => { let b = -1; pts.forEach((p, i) => { if (b < 0 || Math.abs(p.q - hourly[h]) < Math.abs(pts[b].q - hourly[h])) b = i; }); return b; };
    {
      const saveTimes = WX.times, saveT = WX.t;
      WX.times = pts.map(p => p.q); WX.t = hourToPt(t);
      const pu = pts.map(p => p.u), pv = pts.map(p => p.v);
      try {
        const c = WX.charts.panel(body, { title: `Surface wind (10 m)${sp ? ' · every 15 min around sunrise/sunset, hourly otherwise' : ''}`, unit: U.windU(), zeroBase: true, height: 150,
          timeAxis: true, tipTime: d => (pts.find(p => +p.q === +d) || {}).q15 ? WX.time.fullMin(d) : WX.time.full(d),
          onPick: i => { const hh = pts[i].h; if (hh >= 0) WX.setTime(hh); },
          series: [{ name: 'Sustained', color: css('--s1'), values: pts.map(p => U.wind(Math.hypot(p.u, p.v))) },
            { name: 'Gust', color: css('--s2'), values: pts.map(p => U.wind(p.gu)), width: 1.5, dash: '4 3' }],
          arrows: { u: pu, v: pv },
          tipExtra: i => `<div>From ${WX.dirName(WX.wdir(pu[i], pv[i]))} ${fmt(WX.wdir(pu[i], pv[i]))}°${pts[i].q15 ? ' · 15-min' : ''}</div>` });
        charts.push({ setSel: hh => c.setSel(hourToPt(hh)), destroy: c.destroy });
      } finally { WX.times = saveTimes; WX.t = saveT; }
    }
    const below = cell.elev > FT10K;
    charts.push(WX.charts.panel(body, { title: `10,000 ft wind${U.imperial() ? '' : ' (3,048 m)'}${below ? ' (below model terrain here)' : ''}`, unit: U.windU(), zeroBase: true, height: 130,
      series: [{ name: '10,000 ft', color: css('--s7'), values: Array.from(up.u, (u, i) => U.wind(Math.hypot(u, up.v[i]))) }],
      arrows: { u: up.u, v: up.v },
      tipExtra: i => `<div>From ${WX.dirName(WX.wdir(up.u[i], up.v[i]))} ${fmt(WX.wdir(up.u[i], up.v[i]))}° · ${fmt(U.temp(up.tc[i]))}${U.tempU()}</div>` }));
    charts.push(WX.charts.panel(body, { title: 'HRRR confidence', unit: '%', fixed: true, yMin: 0, yMax: 100, height: 90,
      series: [{ name: 'Overall', color: css('--s3'), values: conf }] }));

    // merged timeline: hourly rows, replaced by 15-minute rows inside the dawn/dusk windows
    const rows = [];
    const subIn = [];
    if (sp) WX.subTimes.forEach((q, i) => subIn.push({ q, i }));
    const covered = q => subIn.length && WX.meta.sub.spans.some(s => q >= new Date(s.start) && q <= new Date(s.end));
    for (let h = 0; h < T(); h++) if (!covered(WX.times[h])) rows.push({ q: WX.times[h], h });
    for (const { q, i } of subIn) {
      const h = WX.times.findIndex(d => +d === +q);
      rows.push({ q, s: i, h: h >= 0 ? h : null });
    }
    rows.sort((a, b) => a.q - b.q);
    const dir = (u, v) => isFinite(u) ? `${WX.dirName(WX.wdir(u, v))} ${fmt(WX.wdir(u, v))}°` : '–';
    let html = '', lastDay = '';
    for (const r of rows) {
      let su, sv, gu;
      if (r.s !== undefined) { su = sp.get('U10')[r.s]; sv = sp.get('V10')[r.s]; gu = sp.get('GUST')[r.s]; }
      else { su = u10[r.h]; sv = v10[r.h]; gu = g('GUST')[r.h]; }
      const hasH = r.h !== null && r.h !== undefined;
      const uu = hasH ? up.u[r.h] : NaN, vv = hasH ? up.v[r.h] : NaN;
      const day = WX.time.dateKey(r.q), newDay = lastDay && day !== lastDay; lastDay = day;
      const label = r.s !== undefined ? WX.time.hmShort(r.q) : WX.time.hour(r.q);
      html += `<tr data-h="${hasH ? r.h : ''}" class="${hasH && r.h === t ? 'sel' : ''} ${newDay ? 'day' : ''} ${r.s !== undefined ? 'q15' : ''}">
        <td>${WX.time.day(r.q)} ${label}</td><td>${dir(su, sv)}</td><td><b>${fmt(U.wind(Math.hypot(su, sv)))}</b></td><td>${fmt(U.wind(gu))}</td>
        <td>${hasH ? dir(uu, vv) : ''}</td><td>${hasH ? `<b>${fmt(U.wind(Math.hypot(uu, vv)))}</b>` : ''}</td>
        <td>${hasH && isFinite(conf[r.h]) ? fmt(conf[r.h]) : ''}</td></tr>`;
    }
    const tb = document.createElement('div');
    tb.innerHTML = `<h4>Winds by time</h4>
      <div class="tbl-wrap tall"><table class="data"><thead><tr><th>Time</th><th>Surface dir</th><th>Speed ${U.windU()}</th><th>Gust</th><th>10,000 ft dir</th><th>Speed</th><th>Conf %</th></tr></thead><tbody>${html}</tbody></table></div>
      <p class="muted small">Hourly HRRR, with 15-minute HRRR rows (shaded) for the ${WX.meta.sub ? WX.meta.sub.window_h : 3} daylight hours after sunrise and before sunset. HRRR has no 15-minute data aloft, so 10,000 ft winds and confidence are hourly${below ? '; 10,000 ft is below the 3 km model terrain at this cell' : ''}. Tap a row to show that hour on the map.</p>`;
    body.appendChild(tb);
    tb.querySelectorAll('tr[data-h]').forEach(r => { if (r.dataset.h !== '') r.onclick = () => WX.setTime(+r.dataset.h); });
  }

  /* ------------------------------------------------------------ 15-minute dawn/dusk: wind & gust only */
  async function sub() {
    const meta = WX.meta.sub, p = WX.point;
    body.innerHTML = '<p class="muted">Loading 15-minute data…</p>';
    let sp;
    try { sp = await WX.getSubPoint(p.j, p.i); } catch (e) { body.innerHTML = '<p>15-minute data not available.</p>'; return; }
    if (WX.point !== p || tab !== 'sub') return;
    body.innerHTML = '';
    const all = WX.subTimes, u = sp.get('U10'), v = sp.get('V10');
    const spanStart = new Set(meta.spans.map(s => +new Date(s.start)));
    const saveTimes = WX.times, saveT = WX.t;
    WX.times = all.slice(); WX.t = WX.mode === 'sub' ? WX.ts : -1;
    try {
      charts.push(WX.charts.panel(body, {
        title: `Surface wind every 15 min · ${meta.spans.map(s => `${WX.time.day(new Date(s.start))} ${s.label === 'morning' ? 'after sunrise' : 'before sunset'}`).join(', ')}`,
        unit: U.windU(), zeroBase: true, height: Math.max(220, Math.min(360, window.innerHeight * 0.4)), night: false, noClick: true,
        tipTime: WX.time.fullMin,
        xLabel: (d, t) => spanStart.has(+d) ? `${WX.time.day(d)} ${WX.time.hmShort(d)}` : (d.getUTCMinutes() === 0 ? WX.time.hmShort(d) : null),
        series: [
          { name: 'Wind (10 m)', color: css('--s1'), values: Array.from(u, (x, i) => U.wind(Math.hypot(x, v[i]))) },
          { name: 'Gust', color: css('--s2'), values: Array.from(sp.get('GUST'), U.wind), dash: '4 3' }],
        arrows: { u, v },
        tipExtra: i => `<div>From ${WX.dirName(WX.wdir(u[i], v[i]))} ${fmt(WX.wdir(u[i], v[i]))}°</div>`,
      }));
    } finally { WX.times = saveTimes; WX.t = saveT; }
  }

  /* ------------------------------------------------------------ Meteogram */
  function meteogram() {
    const tt = Array.from(g('T2'), U.temp), dd = Array.from(g('D2'), U.temp);
    charts.push(WX.charts.panel(body, { title: 'Temperature & dew point (2 m)', unit: U.tempU(), series: [
      { name: 'Temperature', color: css('--s8'), values: tt }, { name: 'Dew point', color: css('--s3'), values: dd }], height: 140 }));
    const ws = Array.from(g('U10'), (u, t) => U.wind(Math.hypot(u, g('V10')[t])));
    charts.push(WX.charts.panel(body, { title: 'Wind 10 m (arrows show direction it blows toward)', unit: U.windU(), zeroBase: true, series: [
      { name: 'Sustained', color: css('--s1'), values: ws }, { name: 'Gust', color: css('--s2'), values: Array.from(g('GUST'), U.wind), width: 1.5, dash: '4 3' }],
      arrows: { u: g('U10'), v: g('V10') }, height: 150,
      tipExtra: t => `<div>Direction: ${WX.dirName(WX.wdir(g('U10')[t], g('V10')[t]))} ${fmt(WX.wdir(g('U10')[t], g('V10')[t]))}°</div>` }));
    const lv = 700, u7 = g(`UGRD:${lv}`), v7 = g(`VGRD:${lv}`);
    charts.push(WX.charts.panel(body, { title: `Ridge-top wind (700 mb, ~${fmt(U.len(3050) / (U.imperial() ? 1000 : 1000), 0)}k ${U.lenU()})`, unit: U.windU(), zeroBase: true, series: [
      { name: '700 mb', color: css('--s7'), values: Array.from(u7, (u, t) => U.wind(Math.hypot(u, v7[t]))) }], arrows: { u: u7, v: v7 }, height: 120,
      tipExtra: t => `<div>From ${WX.dirName(WX.wdir(u7[t], v7[t]))} ${fmt(WX.wdir(u7[t], v7[t]))}° · ${fmt(U.len(g('HGT:700')[t]))} ${U.lenU()}</div>` }));
    const ap = g('APCP1'), fz = g('CPOFP');
    const rain = Array.from(ap, (p, t) => U.precip((p || 0) * (1 - (isFinite(fz[t]) ? fz[t] : 0) / 100)));
    const snowW = Array.from(ap, (p, t) => U.precip((p || 0) * (isFinite(fz[t]) ? fz[t] : 0) / 100));
    const dec = U.imperial() ? 2 : 1;
    charts.push(WX.charts.panel(body, { title: 'Hourly precipitation (water equivalent)', unit: U.precipU(), zeroBase: true, minSpan: U.imperial() ? 0.04 : 1, dec, tickDec: dec, series: [
      { name: 'Rain', color: css('--s3'), values: Array.from(rain, (r, t) => r + snowW[t]), type: 'bar', fmt: (v, t) => fmt(rain[t], dec) },
      { name: 'Frozen', color: css('--s7'), values: snowW, type: 'bar', fmt: v => fmt(v, dec) }], height: 120 }));
    const sh = snowHourly(); let acc = 0, accP = 0;
    const snowAcc = Array.from(sh, v => U.snow(acc += v)), pAcc = Array.from(ap, v => U.precip(accP += (v || 0)));
    charts.push(WX.charts.panel(body, { title: 'Accumulated snowfall (HRRR snow-to-liquid)', unit: U.snowU(), zeroBase: true, minSpan: 1, dec: 1, series: [
      { name: 'Snowfall', color: css('--s7'), values: snowAcc }], height: 100 }));
    charts.push(WX.charts.panel(body, { title: 'Accumulated precipitation (water)', unit: U.precipU(), zeroBase: true, minSpan: U.imperial() ? 0.1 : 2, dec, tickDec: dec, series: [
      { name: 'Precip', color: css('--s1'), values: pAcc }], height: 100 }));
    charts.push(WX.charts.panel(body, { title: 'Cloud cover by layer', unit: '%', fixed: true, yMin: 0, yMax: 100, series: [
      { name: 'Low', color: css('--s1'), values: g('LCDC') }, { name: 'Middle', color: css('--s2'), values: g('MCDC') },
      { name: 'High', color: css('--s3'), values: g('HCDC') }], height: 110 }));
    charts.push(WX.charts.panel(body, { title: 'Freezing level & mixed-layer top (MSL)', unit: U.lenU(), series: [
      { name: 'Freezing level', color: css('--s1'), values: Array.from(g('FRZLVL'), U.len) },
      { name: 'Mixed layer top', color: css('--s2'), values: Array.from(g('HPBL'), v => U.len(v + cell.elev)) }], height: 120 }));
    charts.push(WX.charts.panel(body, { title: 'HRRR confidence (agreement with NAM, RAP, earlier HRRR runs)', unit: '%', fixed: true, yMin: 0, yMax: 100, series: [
      { name: 'Overall', color: css('--s1'), values: g('CONF_ALL') }], height: 100 }));
  }

  /* ------------------------------------------------------------ Upper air (time-height) */
  function upper() {
    const ctr = document.createElement('div'); ctr.className = 'controls-row';
    ctr.innerHTML = `<span>Color:</span><span class="seg">${[['wind', 'Wind speed'], ['temp', 'Temperature'], ['rh', 'Humidity'], ['w', 'Vertical motion']]
      .map(([k, l]) => `<button data-k="${k}" class="${upperOpts.colorBy === k ? 'active' : ''}">${l}</button>`).join('')}</span>
      <label><input type="checkbox" id="upClouds" ${upperOpts.clouds ? 'checked' : ''}> Cloud (RH ≥ 90%)</label>
      <label><input type="checkbox" id="upNums" ${upperOpts.numbers ? 'checked' : ''}> Wind numbers</label>`;
    body.appendChild(ctr);
    ctr.querySelectorAll('.seg button').forEach(b => (b.onclick = () => { upperOpts.colorBy = b.dataset.k; render(true); }));
    ctr.querySelector('#upClouds').onchange = e => { upperOpts.clouds = e.target.checked; render(true); };
    ctr.querySelector('#upNums').onchange = e => { upperOpts.numbers = e.target.checked; render(true); };
    const c = WX.charts.timeHeight(body, pt, cell, { colorBy: upperOpts.colorBy, topFt: WX.prefs.top, clouds: upperOpts.clouds, numbers: upperOpts.numbers, height: Math.max(320, Math.min(520, window.innerHeight * 0.5)) });
    charts.push(c);
    // legend
    const sc = c.scale, lg = document.createElement('div'); lg.className = 'legend-row';
    const conv = { wind: v => U.wind(v), temp: v => U.temp(v), rh: v => v, w: v => U.imperial() ? v * 196.85 : v }[upperOpts.colorBy];
    const unit = { wind: U.windU(), temp: U.tempU(), rh: '%', w: U.imperial() ? 'ft/min' : 'm/s' }[upperOpts.colorBy];
    const stops = Array.from({ length: 5 }, (_, q) => sc.lo + (sc.hi - sc.lo) * q / 4);
    lg.innerHTML = stops.map(v => `<span><span class="sw box" style="background:${WX.rgb(sc.color(v))}"></span>${fmt(conv(v))}</span>`).join('') + `<span class="muted">${unit}</span>`;
    body.appendChild(lg);
    const k = document.createElement('div'); k.className = 'legend-row';
    k.innerHTML = `<span><span class="sw" style="background:#111;height:2px"></span>0 °C (dashed)</span><span><span class="sw" style="background:${css('--s2')}"></span>Mixed-layer top (dotted)</span><span>Arrows: wind blowing toward</span><span><span class="sw box" style="background:${css('--terrain')}"></span>Model terrain</span>`;
    body.appendChild(k);
    const p = document.createElement('p'); p.className = 'muted small';
    p.textContent = 'Every HRRR pressure level (25 mb ≈ 750–900 ft apart) at this grid cell, plotted at its forecast height. Hover or tap for exact values; click to set the map time.';
    body.appendChild(p);
  }

  /* ------------------------------------------------------------ Sounding */
  function sounding() {
    const t = t0();
    const nav = document.createElement('div'); nav.className = 'controls-row';
    nav.innerHTML = `<button class="icon-btn" id="sPrev">‹</button><b>${WX.time.full(WX.times[t])}</b><button class="icon-btn" id="sNext">›</button>
      <span class="legend-row" style="margin:0"><span><span class="sw" style="background:${css('--s8')}"></span>Temperature</span><span><span class="sw" style="background:${css('--s3')}"></span>Dew point</span><span><span class="sw" style="background:${css('--s1')}"></span>0 °C</span></span>`;
    body.appendChild(nav);
    nav.querySelector('#sPrev').onclick = () => WX.setTime(t - 1);
    nav.querySelector('#sNext').onclick = () => WX.setTime(t + 1);
    WX.charts.profile(body, pt, cell, t, { topFt: WX.prefs.top, height: 340 });
    const lv = WX.meta.levels;
    let rows = '';
    for (let l = lv.length - 1; l >= 0; l--) {
      const p = lv[l], z = g(`HGT:${p}`)[t];
      const under = z < cell.elev;
      const uu = g(`UGRD:${p}`)[t], vv = g(`VGRD:${p}`)[t], tc = g(`TMP:${p}`)[t];
      const w = WX.omegaToW(g(`VVEL:${p}`)[t], tc, p);
      rows += `<tr class="${under ? 'muted' : ''}"><td>${p}</td><td>${fmt(U.len(z))}</td><td>${under ? '<i>below</i>' : fmt(U.len(z - cell.elev))}</td><td>${fmt(U.temp(tc), 1)}</td><td>${fmt(U.temp(g(`DPT:${p}`)[t]), 1)}</td><td>${fmt(g(`RH:${p}`)[t])}</td><td>${fmt(WX.wdir(uu, vv))}° ${WX.dirName(WX.wdir(uu, vv))}</td><td><b>${fmt(U.wind(Math.hypot(uu, vv)))}</b></td><td>${fmt(U.imperial() ? w * 196.85 : w, U.imperial() ? 0 : 2)}</td><td>${fmt(g(`VVEL:${p}`)[t], 3)}</td></tr>`;
    }
    const div = document.createElement('div');
    div.innerHTML = `<h4>All HRRR levels at this cell</h4><div class="tbl-wrap tall"><table class="data"><thead><tr><th>mb</th><th>Height ${U.lenU()} MSL</th><th>AGL</th><th>T ${U.tempU()}</th><th>Td ${U.tempU()}</th><th>RH %</th><th>Dir</th><th>Speed ${U.windU()}</th><th>w ${U.imperial() ? 'ft/min' : 'm/s'}</th><th>ω Pa/s</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="muted small">AGL is relative to the 3 km model terrain at this cell (${fmt(U.len(cell.elev))} ${U.lenU()}). ω is the raw HRRR vertical velocity; w is converted with the hydrostatic approximation (positive = rising).</p>`;
    body.appendChild(div);
  }

  /* ------------------------------------------------------------ Hourly table */
  function table() {
    const ap = g('APCP1'), sn = snowHourly(), dec = U.imperial() ? 2 : 1;
    const cols = [
      ['Temp', U.tempU(), t => fmt(U.temp(g('T2')[t]))], ['Dew', U.tempU(), t => fmt(U.temp(g('D2')[t]))], ['RH', '%', t => fmt(g('RH2')[t])],
      ['Dir', '', t => WX.dirName(WX.wdir(g('U10')[t], g('V10')[t]))], ['Wind', U.windU(), t => fmt(U.wind(Math.hypot(g('U10')[t], g('V10')[t])))],
      ['Gust', U.windU(), t => fmt(U.wind(g('GUST')[t]))], ['700mb', U.windU(), t => `${WX.dirName(WX.wdir(g('UGRD:700')[t], g('VGRD:700')[t]))} ${fmt(U.wind(Math.hypot(g('UGRD:700')[t], g('VGRD:700')[t])))}`],
      ['Precip', U.precipU(), t => ap[t] > 0 ? fmt(U.precip(ap[t]), dec) : '–'], ['Frozen', '%', t => ap[t] > 0 ? fmt(g('CPOFP')[t]) : '–'],
      ['Snow', U.snowU(), t => sn[t] > 0.04 ? fmt(U.snow(sn[t]), 1) : '–'],
      ['Cloud', '%', t => fmt(g('TCDC')[t])], ['Low', '%', t => fmt(g('LCDC')[t])], ['Base', U.lenU(), t => ceilOk(g('CBASE')[t]) ? fmt(U.len(g('CBASE')[t])) : '–'],
      ['Vis', U.visU(), t => fmt(U.vis(g('VIS')[t]), 1)], ['Frz lvl', U.lenU(), t => fmt(U.len(g('FRZLVL')[t]))],
      ['Mix top', U.lenU(), t => fmt(U.len(g('HPBL')[t] + cell.elev))], ['CAPE', '', t => fmt(g('CAPE')[t])],
      ['Conf', '%', t => fmt(g('CONF_ALL')[t])],
    ];
    let h = `<div class="tbl-wrap tall"><table class="data"><thead><tr><th>Time</th>${cols.map(c => `<th>${c[0]}${c[1] ? `<br><span class="muted">${c[1]}</span>` : ''}</th>`).join('')}</tr></thead><tbody>`;
    for (let t = 0; t < T(); t++) {
      const newDay = t > 0 && WX.time.localHour(WX.times[t]) === 0;
      h += `<tr data-t="${t}" class="${t === WX.t ? 'sel' : ''} ${newDay ? 'day' : ''}"><td>${WX.time.day(WX.times[t])} ${WX.time.hour(WX.times[t])}</td>${cols.map(c => `<td>${c[2](t)}</td>`).join('')}</tr>`;
    }
    h += '</tbody></table></div><p class="muted small">Exact HRRR values at this 3 km grid cell (no interpolation). Tap a row to move the map to that hour.</p>';
    body.innerHTML = h;
    body.querySelectorAll('tr[data-t]').forEach(r => (r.onclick = () => WX.setTime(+r.dataset.t)));
    const sel = body.querySelector('tr.sel'); if (sel && !body.dataset.scrolled) sel.scrollIntoView({ block: 'center' });
  }

  /* ------------------------------------------------------------ Confidence / model comparison */
  function models() {
    const t = t0(), srcs = [['HRRR', css('--s1')], ['NAM', css('--s2')], ['RAP', css('--s3')], ['LAG1', css('--s4')], ['LAG2', css('--s5')]];
    const names = { HRRR: 'HRRR (this run)', NAM: 'NAM 3 km nest', RAP: 'RAP 13 km', LAG1: 'HRRR previous run', LAG2: 'HRRR 2 runs back' };
    const comp = WX.meta.comparators || {};
    let intro = `<div class="box"><b>How the score works.</b> Each hour, HRRR is checked against the NAM 3 km nest${comp.NAM ? ` (${new Date(comp.NAM.cycles[0]).getUTCHours()}Z)` : ''}, RAP${comp.RAP ? ` (${comp.RAP.cycles.map(c => new Date(c).getUTCHours() + 'Z').join(', ')})` : ''} and the two previous HRRR runs that reach that hour. Close agreement in temperature, wind speed/direction, gusts, precipitation (3-hour window) and 700 mb wind/temperature gives a high score. Precipitation only weighs heavily where some model has precip.
      <div style="margin-top:8px">Now: ${confPill(g('CONF_ALL')[t])} &nbsp; Temp ${fmt(g('CONF_T2')[t])} · Wind ${fmt(g('CONF_WIND')[t])} · Gust ${fmt(g('CONF_GUST')[t])} · Precip ${fmt(g('CONF_PRECIP')[t])} · 700 mb ${fmt(g('CONF_UPPER')[t])}</div></div>`;
    body.innerHTML = intro;
    const series = (key, conv, hrrrVals) => srcs.map(([s, color]) => ({
      name: names[s], color, width: s === 'HRRR' ? 2.5 : 1.5, dash: s.startsWith('LAG') ? '4 3' : '',
      values: s === 'HRRR' ? Array.from(hrrrVals, conv) : Array.from(g(`${s}_${key}`), conv),
    }));
    const hws = Array.from(g('U10'), (u, i) => Math.hypot(u, g('V10')[i]));
    const h7 = Array.from(g('UGRD:700'), (u, i) => Math.hypot(u, g('VGRD:700')[i]));
    charts.push(WX.charts.panel(body, { title: 'Temperature 2 m', unit: U.tempU(), series: series('T2', U.temp, g('T2')), height: 140 }));
    charts.push(WX.charts.panel(body, { title: 'Wind 10 m', unit: U.windU(), zeroBase: true, series: series('WS10', U.wind, hws), height: 140 }));
    charts.push(WX.charts.panel(body, { title: 'Gusts', unit: U.windU(), zeroBase: true, series: series('GUST', U.wind, g('GUST')), height: 130 }));
    const cum = a => { let s = 0; return Array.from(a, v => (s += isFinite(v) ? v : 0, isFinite(v) ? s : NaN)); };
    const dec = U.imperial() ? 2 : 1;
    charts.push(WX.charts.panel(body, { title: 'Accumulated precipitation', unit: U.precipU(), zeroBase: true, dec, tickDec: dec, minSpan: U.imperial() ? 0.05 : 1, series: srcs.map(([s, color]) => ({
      name: names[s], color, width: s === 'HRRR' ? 2.5 : 1.5, dash: s.startsWith('LAG') ? '4 3' : '',
      values: Array.from(cum(s === 'HRRR' ? g('APCP1') : g(`${s}_APCP1`)), U.precip) })), height: 130 }));
    charts.push(WX.charts.panel(body, { title: '700 mb wind (ridge level)', unit: U.windU(), zeroBase: true, series: series('WS700', U.wind, h7), height: 130 }));
    charts.push(WX.charts.panel(body, { title: 'Confidence by category', unit: '%', fixed: true, yMin: 0, yMax: 100, series: [
      { name: 'Overall', color: css('--s1'), values: g('CONF_ALL'), width: 2.5 }, { name: 'Temp', color: css('--s2'), values: g('CONF_T2') },
      { name: 'Wind', color: css('--s3'), values: g('CONF_WIND') }, { name: 'Precip', color: css('--s4'), values: g('CONF_PRECIP') },
      { name: '700 mb', color: css('--s5'), values: g('CONF_UPPER') }], height: 140 }));
    // table for selected hour
    const row = (s, label) => {
      const ws = s === 'HRRR' ? hws[t] : g(`${s}_WS10`)[t], wd = s === 'HRRR' ? WX.wdir(g('U10')[t], g('V10')[t]) : g(`${s}_WD10`)[t];
      const w7 = s === 'HRRR' ? h7[t] : g(`${s}_WS700`)[t];
      const val = k => s === 'HRRR' ? { T2: g('T2')[t], GUST: g('GUST')[t], APCP1: g('APCP1')[t], T700: g('TMP:700')[t] }[k] : g(`${s}_${k}`)[t];
      return `<tr><td>${label}</td><td>${fmt(U.temp(val('T2')), 1)}</td><td>${WX.dirName(wd)} ${fmt(U.wind(ws))}</td><td>${fmt(U.wind(val('GUST')))}</td><td>${fmt(U.precip(val('APCP1')), dec)}</td><td>${fmt(U.wind(w7))}</td><td>${fmt(U.temp(val('T700')), 1)}</td></tr>`;
    };
    const div = document.createElement('div');
    div.innerHTML = `<h4>${WX.time.full(WX.times[t])}</h4><div class="tbl-wrap"><table class="data"><thead><tr><th>Model</th><th>T ${U.tempU()}</th><th>Wind ${U.windU()}</th><th>Gust</th><th>Precip ${U.precipU()}</th><th>700mb ${U.windU()}</th><th>T700 ${U.tempU()}</th></tr></thead><tbody>${srcs.map(([s]) => row(s, names[s])).join('')}</tbody></table></div>
      <p class="muted small">Blank values mean that model doesn't cover this hour (RAP reaches 21 h, or 51 h from its 03/09/15/21Z runs; NAM nest reaches 60 h).</p>`;
    body.appendChild(div);
  }
})();
