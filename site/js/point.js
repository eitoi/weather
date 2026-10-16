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
    if (tab === 'now' || tab === 'sounding' || tab === 'table' || tab === 'models') render(true);
    else charts.forEach(c => c.setSel && c.setSel(WX.t));
  });
  WX.on('units', () => pt && render());
  let rz; window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => pt && !panelEl.classList.contains('hidden') && render(true), 200); });

  function render(keepScroll) {
    document.querySelectorAll('#ptTabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    const sc = body.scrollTop;
    charts.forEach(c => c.destroy && c.destroy()); charts = [];
    body.innerHTML = '';
    ({ now, meteogram, upper, sounding, table, models })[tab]();
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

  /* ------------------------------------------------------------ Now */
  function now() {
    const t = t0(), src = WX.meta.sources[t];
    const u = g('U10')[t], v = g('V10')[t], s = Math.hypot(u, v), d = WX.wdir(u, v);
    const ap = g('APCP1'), snow = snowHourly();
    const next24p = sumRange(ap, t + 1, t + 24), next24s = sumRange(snow, t + 1, t + 24);
    const frz = g('CPOFP')[t], cb = g('CBASE')[t], ce = g('CEIL')[t];
    const conf = g('CONF_ALL')[t];
    let h = `<div class="controls-row"><b>${WX.time.full(WX.times[t])}</b><span class="muted small">HRRR ${new Date(src.run).getUTCHours()}Z run, forecast hour ${src.fh}</span></div><div class="cards">`;
    h += card('Temperature', `${fmt(U.temp(g('T2')[t]))}${U.tempU()}`, `Dew pt ${fmt(U.temp(g('D2')[t]))}${U.tempU()} · RH ${fmt(g('RH2')[t])}%`);
    h += card('Wind 10 m', `${fmt(U.wind(s))} <small>${U.windU()}</small>`, `from ${WX.dirName(d)} ${fmt(d)}° · gust ${fmt(U.wind(g('GUST')[t]))}`);
    h += card('Precip this hour', `${fmt(U.precip(ap[t] || 0), U.imperial() ? 2 : 1)} <small>${U.precipU()}</small>`, isFinite(frz) && ap[t] > 0 ? `${fmt(frz)}% frozen` : (ap[t] > 0 ? '' : 'none'));
    h += card('Next 24 h', `${fmt(U.precip(next24p), U.imperial() ? 2 : 1)} <small>${U.precipU()}</small>`, `snow ${fmt(U.snow(next24s), 1)} ${U.snowU()}`);
    h += card('Cloud cover', `${fmt(g('TCDC')[t])}%`, `L ${fmt(g('LCDC')[t])} · M ${fmt(g('MCDC')[t])} · H ${fmt(g('HCDC')[t])}`);
    h += card('Cloud base', ceilOk(cb) ? `${fmt(U.len(cb))} <small>${U.lenU()}</small>` : '–', ceilOk(ce) ? `ceiling ${fmt(U.len(ce))} ${U.lenU()}` : 'no ceiling');
    h += card('Freezing level', `${fmt(U.len(g('FRZLVL')[t]))} <small>${U.lenU()}</small>`, 'MSL');
    h += card('Mixed layer top', `${fmt(U.len(g('HPBL')[t] + cell.elev))} <small>${U.lenU()}</small>`, `${fmt(U.len(g('HPBL')[t]))} ${U.lenU()} above model ground`);
    h += card('CAPE / LI', `${fmt(g('CAPE')[t])}`, `J/kg · lifted index ${fmt(g('LFTX')[t], 1)}`);
    h += card('Visibility', `${fmt(U.vis(g('VIS')[t]), 1)} <small>${U.visU()}</small>`, `radar ${fmt(g('REFC')[t])} dBZ`);
    h += card('Pressure', `${fmt(g('MSLP')[t], 1)}`, `hPa MSL · sfc ${fmt(g('PSFC')[t])}`);
    h += card('HRRR confidence', confPill(conf), `${fmt(g('CONF_N')[t])} comparison models`);
    h += '</div>';
    // winds aloft table
    const lv = WX.meta.levels;
    let rows = '';
    for (let l = lv.length - 1; l >= 0; l--) {
      const p = lv[l], z = g(`HGT:${p}`)[t]; if (!(z >= cell.elev) || z * 3.28084 > WX.prefs.top + 500) continue;
      const uu = g(`UGRD:${p}`)[t], vv = g(`VGRD:${p}`)[t], tc = g(`TMP:${p}`)[t];
      rows += `<tr><td>${fmt(U.len(z))} ${U.lenU()}</td><td>${p}</td><td>${WX.dirName(WX.wdir(uu, vv))} ${fmt(WX.wdir(uu, vv))}°</td><td><b>${fmt(U.wind(Math.hypot(uu, vv)))}</b></td><td>${fmt(U.temp(tc))}</td><td>${fmt(g(`RH:${p}`)[t])}</td></tr>`;
    }
    h += `<h4>Winds aloft (every 25 mb)</h4><div class="tbl-wrap"><table class="data"><thead><tr><th>Altitude MSL</th><th>mb</th><th>Direction</th><th>Speed ${U.windU()}</th><th>Temp ${U.tempU()}</th><th>RH %</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    body.innerHTML = h;
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
