/* Outlook: beyond the HRRR window — written summary, fronts, daily multi-model table, ensembles, NWS AFD. */
'use strict';
(function () {
  const { U, fmt, esc } = WX;
  const root = document.getElementById('outlookBody');
  let zone = 'alta', rendered = false;
  const css = WX.cssVar;

  WX.on('longrange', () => { if (WX.view === 'outlook') render(); else rendered = false; });
  WX.on('showOutlook', () => { if (!rendered) render(); });
  WX.on('units', () => { if (WX.view === 'outlook') render(); else rendered = false; });
  let rz, lastW = 0;
  window.addEventListener('resize', () => {
    clearTimeout(rz);
    rz = setTimeout(() => {
      if (Math.abs(window.innerWidth - lastW) < 40) return;
      lastW = window.innerWidth;
      if (WX.view === 'outlook' && WX.lr) zoneDetail(); else rendered = false;
    }, 250);
  });

  const pill = c => `<span class="conf-pill conf-${c}">${c[0].toUpperCase() + c.slice(1)}</span>`;
  const dayLabel = iso => new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });

  function render() {
    rendered = true;
    const lr = WX.lr;
    if (!lr) { root.innerHTML = '<p class="muted">Long-range outlook not available yet.</p>'; return; }
    const s = lr.summary;
    const start = new Date(lr.period_start_local);
    let h = `<p class="muted small">Starts after the HRRR window ends: ${start.toLocaleString('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric' })} MT · summary by ${esc(s.source)}</p>
      <h2>${esc(s.headline)}</h2><p class="lede">${esc(s.overview)}</p>`;
    if (s.fronts && s.fronts.length) {
      h += '<h4>Fronts & systems</h4>' + s.fronts.map(f => `<div class="front"><b>${esc(f.timing)}</b> ${pill(f.confidence)}<br>${esc(f.description)}</div>`).join('');
    }
    h += '<h4>Day by day</h4><div class="day-grid">' + (s.days || []).map(d => `<div class="day-card">
        <div class="d">${esc(d.label)} ${pill(d.confidence)}</div>
        <p><span class="lbl">Wind</span>${esc(d.wind)}</p><p><span class="lbl">Precipitation</span>${esc(d.precip)}</p>
        ${d.systems && d.systems !== '—' ? `<p><span class="lbl">Systems</span>${esc(d.systems)}</p>` : ''}</div>`).join('') + '</div>';
    if (s.nws_vs_models) h += `<div class="box"><b>NWS vs. models.</b> ${esc(s.nws_vs_models)}</div>`;

    // zone selector + charts
    h += `<h4>Model detail</h4><div class="controls-row"><label>Zone <select id="lrZone">${lr.zones.map(z => `<option value="${z.id}" ${z.id === zone ? 'selected' : ''}>${esc(z.name)}</option>`).join('')}</select></label>
      <span class="muted small">${Object.values(lr.model_names).join(', ')} (deterministic) · ${lr.ensemble_names.join(', ')} (ensembles)</span></div>
      <div id="lrCharts"></div><div id="lrTable"></div>`;

    // fronts detected by algorithm
    if (lr.fronts && lr.fronts.length) {
      h += `<h4>Cold-front candidates (700 mb cooling + wind shift, Alta)</h4><div class="tbl-wrap"><table class="data"><thead><tr><th>Time (MT)</th><th>Models</th><th>Timing spread</th><th>700 mb cooling</th><th>Wind shift</th><th>Precip ${U.precipU()}</th></tr></thead><tbody>` +
        lr.fronts.map(f => `<tr><td>${new Date(f.time).toLocaleString('en-US', { weekday: 'short', hour: 'numeric' })}</td><td>${esc(f.agreement)} <span class="muted">${esc(f.models.join(', '))}</span></td><td>${fmt(f.timing_spread_h)} h</td><td>${fmt(U.dtemp(f.cooling_700mb_c), 1)} ${U.tempU()}</td><td>${esc(f.wind_shift)}</td><td>${fmt(U.precip(f.precip_mm), U.imperial() ? 2 : 1)}</td></tr>`).join('') + '</tbody></table></div>';
    }
    // AFD
    if (lr.afd && lr.afd.sections) {
      h += `<h4>NWS Salt Lake City Area Forecast Discussion</h4><p class="muted small">Issued ${new Date(lr.afd.issued).toLocaleString('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</p>`;
      const order = Object.keys(lr.afd.sections);
      h += order.map((k, i) => `<details class="afd" ${i < 2 ? 'open' : ''}><summary>${esc(k)}</summary><pre>${esc(lr.afd.sections[k])}</pre></details>`).join('');
    }
    root.innerHTML = h;
    root.querySelector('#lrZone').onchange = e => { zone = e.target.value; zoneDetail(); };
    zoneDetail();
  }

  function zoneDetail() {
    const lr = WX.lr, ser = lr.series[zone], days = lr.daily[zone] || [];
    const box = root.querySelector('#lrCharts'); box.innerHTML = '';
    const tbl = root.querySelector('#lrTable');
    if (!ser) return;
    const models = Object.keys(ser.models), colors = ['--s1', '--s2', '--s3', '--s4', '--s5', '--s6'].map(css);
    // Daily bars would be cleaner but hourly lines show timing; use a simple per-day table + 3 small charts drawn with the shared panel API
    // (the panel expects WX.times; build a temporary axis)
    const saveTimes = WX.times, saveT = WX.t;
    // ser.time is local wall-clock (America/Denver)
    WX.times = ser.time.map(s => denverToDate(s));
    WX.t = -1;
    try {
      const lines = (key, conv) => models.map((m, i) => ({ name: lr.model_names[m] || m, color: colors[i], width: 1.6, values: (ser.models[m][key] || []).map(v => v === null ? NaN : conv(v)) }));
      const cum = arr => { let s = 0; return arr.map(v => (s += (v || 0))); };
      WX.charts.panel(box, { title: 'Accumulated precipitation by model', unit: U.precipU(), zeroBase: true, dec: U.imperial() ? 2 : 1, tickDec: U.imperial() ? 2 : 1, minSpan: U.imperial() ? 0.1 : 2, night: false, noClick: true,
        series: models.map((m, i) => ({ name: lr.model_names[m] || m, color: colors[i], width: 1.8, values: cum(ser.models[m].precipitation || []).map(U.precip) })), height: 150 });
      WX.charts.panel(box, { title: 'Ridge-level wind (700 mb)', unit: U.windU(), zeroBase: true, night: false, noClick: true, noClick: true, series: lines('wind_speed_700hPa', U.wind), height: 150 });
      WX.charts.panel(box, { title: 'Temperature (2 m at the zone point)', unit: U.tempU(), night: false, noClick: true, noClick: true, series: lines('temperature_2m', U.temp), height: 150 });
    } finally { WX.times = saveTimes; WX.t = saveT; }

    // daily table: consensus + per-model precip
    const dec = U.imperial() ? 2 : 1;
    let h = `<h4>Daily consensus</h4><div class="tbl-wrap"><table class="data"><thead><tr><th>Day</th><th>High/Low ${U.tempU()}</th><th>Precip ${U.precipU()} mean (range)</th><th>Models wet</th><th>Snow ${U.snowU()}</th><th>700 mb ${U.windU()}</th><th>Gust ${U.windU()}</th><th>Ensemble P(≥0.1")</th><th>Ens. P(snow ≥2")</th></tr></thead><tbody>`;
    for (const d of days) {
      const c = d.consensus, e = d.ensemble || {};
      const ens = Object.entries(e).map(([k, v]) => `${k.split(' ')[0]} ${fmt(v.p_precip_2mm)}%`).join(' · ');
      const ensS = Object.entries(e).filter(([, v]) => v.p_snow_5cm !== undefined).map(([k, v]) => `${k.split(' ')[0]} ${fmt(v.p_snow_5cm)}%`).join(' · ');
      const rng = c.precip ? `${fmt(U.precip(c.precip.mean), dec)} <span class="muted">(${fmt(U.precip(c.precip.min), dec)}–${fmt(U.precip(c.precip.max), dec)})</span>` : '–';
      h += `<tr><td>${dayLabel(d.date)}</td><td>${c.tmax ? fmt(U.temp(c.tmax.mean)) : '–'} / ${c.tmin ? fmt(U.temp(c.tmin.mean)) : '–'}</td><td>${rng}</td><td>${c.models_wet}/${c.models_total}</td><td>${c.snow ? fmt(U.snow(c.snow.mean), 1) : '–'}</td><td>${c.w700_dir} ${c.w700_max ? fmt(U.wind(c.w700_max.mean)) : '–'} <span class="muted">${c.w700_max ? `(${fmt(U.wind(c.w700_max.min))}–${fmt(U.wind(c.w700_max.max))})` : ''}</span></td><td>${c.gust_max ? fmt(U.wind(c.gust_max.mean)) : '–'}</td><td>${ens || '–'}</td><td>${ensS || '–'}</td></tr>`;
    }
    h += '</tbody></table></div>';
    // per-model precip matrix
    const mk = Object.keys(lr.model_names);
    h += `<h4>Precipitation by model (${U.precipU()})</h4><div class="tbl-wrap"><table class="data"><thead><tr><th>Day</th>${mk.map(m => `<th>${esc(lr.model_names[m])}</th>`).join('')}</tr></thead><tbody>` +
      days.map(d => `<tr><td>${dayLabel(d.date)}</td>${mk.map(m => { const v = d.models[m]; return `<td>${v && v.precip !== null ? fmt(U.precip(v.precip), dec) : '–'}</td>`; }).join('')}</tr>`).join('') + '</tbody></table></div>';
    tbl.innerHTML = h;
  }

  function denverToDate(local) {
    // local "YYYY-MM-DDTHH:MM" in America/Denver -> Date
    const guess = new Date(local + ':00Z');
    const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Denver', hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).formatToParts(guess);
    const p = Object.fromEntries(parts.map(x => [x.type, x.value]));
    const asLocal = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour % 24, +p.minute);
    return new Date(guess.getTime() - (asLocal - guess.getTime()));
  }
})();
