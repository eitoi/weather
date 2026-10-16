/* Paraglider launches: known launches (CalTopo) + predicted launch spots (launch-potential model),
   with a forecast wind-fit readout at the selected hour. */
'use strict';
(function () {
  const { U, fmt, esc } = WX;
  let data = null, knownLayer = null, spotLayer = null, renderer = null;
  let wind = null;   // {u, v, g} Float32Arrays [T*n] for 10 m wind + gust

  const angDiff = (a, b) => Math.abs(((a - b) % 360 + 540) % 360 - 180);

  /* Wind fit for a launch facing `facing` (deg, the direction you run / the slope faces).
     Ideal wind blows FROM the facing direction, i.e. straight up the slope into your face. */
  function fit(facing, k, t) {
    if (!wind || facing === null || facing === undefined) return null;
    const n = WX.grid.ny * WX.grid.nx, u = wind.u[t * n + k], v = wind.v[t * n + k], g = wind.g[t * n + k];
    if (!isFinite(u)) return null;
    const spd = Math.hypot(u, v), dir = WX.wdir(u, v), off = angDiff(dir, facing);
    let cls, label;
    if (spd > 8 || g > 11) { cls = 'off'; label = 'Too strong'; }
    else if (spd < 1.5) { cls = 'cross'; label = 'Light & variable'; }
    else if (off <= 35) { cls = 'on'; label = 'On the slope'; }
    else if (off <= 70) { cls = 'cross'; label = 'Crossing'; }
    else if (off <= 110) { cls = 'off'; label = 'Cross'; }
    else { cls = 'off'; label = 'Over the back'; }
    return { spd, dir, off, g, cls, label };
  }
  const FIT_COLOR = { on: '#0ca30c', cross: '#fab219', off: '#d03b3b' };

  function fitHtml(facing, lat, lon) {
    const c = WX.nearest(lat, lon), t = WX.t, f = fit(facing, c.k, t);
    if (!f) return '';
    return `<div style="margin-top:4px"><b class="fit-${f.cls}">${f.label}</b> · ${WX.time.full(WX.times[t])}<br>
      HRRR 10 m wind from ${WX.dirName(f.dir)} (${fmt(f.dir)}°) ${fmt(U.wind(f.spd))} ${U.windU()}, gust ${fmt(U.wind(f.g))} — ${fmt(f.off)}° off the launch<br>
      <span class="muted">From the 3 km grid cell (model elev ${fmt(U.len(c.elev))} ${U.lenU()}); local slope winds can differ.</span>
      <br><a href="#" data-open="${lat},${lon}">Open point forecast</a></div>`;
  }

  function triangle(facing, color, size) {
    const rot = facing === null ? 0 : facing;
    const shape = facing === null
      ? `<circle cx="10" cy="10" r="5.5" fill="${color}" stroke="#fff" stroke-width="2"/>`
      : `<path d="M10 1 L17 17 L10 13 L3 17 Z" fill="${color}" stroke="#fff" stroke-width="1.8" stroke-linejoin="round"/>`;
    return L.divIcon({ className: '', iconSize: [size, size], iconAnchor: [size / 2, size / 2],
      html: `<svg width="${size}" height="${size}" viewBox="0 0 20 20" style="transform:rotate(${rot}deg);filter:drop-shadow(0 0 1px #0008)">${shape}</svg>` });
  }

  function buildKnown() {
    if (knownLayer) knownLayer.remove();
    knownLayer = L.layerGroup();
    for (const k of data.known) {
      let color = k.status === 'established' ? '#e34948' : '#eda100';
      if (WX.prefs.fit) {
        const f = fit(k.facing, WX.nearest(k.lat, k.lon).k, WX.t);
        color = f ? FIT_COLOR[f.cls] : '#8e8e8a';
      }
      const m = L.marker([k.lat, k.lon], { icon: triangle(k.facing, color, 20), zIndexOffset: 500, keyboard: false });
      m.bindTooltip(esc(k.name), { direction: 'top', offset: [0, -8], className: 'launch-label', permanent: WX.map.getZoom() >= 12 });
      m.bindPopup(() => `<div class="launch-tip"><b>${esc(k.name)}</b><br>${k.status === 'established' ? 'Established launch' : 'Less certain launch'}${k.facing !== null ? ` · faces ${WX.dirName(k.facing)} (${k.facing}°)${k.facing_src === 'model' ? ' <span class="muted">(direction from launch model)</span>' : ''}` : ' · direction unknown'}${fitHtml(k.facing, k.lat, k.lon)}</div>`);
      m.on('click', ev => L.DomEvent.stopPropagation(ev));
      knownLayer.addLayer(m);
    }
    if (WX.prefs.launches) knownLayer.addTo(WX.map);
  }

  function buildSpots() {
    if (spotLayer) spotLayer.remove();
    const min = +WX.prefs.spots;
    if (!min) return;
    renderer = renderer || L.canvas({ padding: 0.3 });
    spotLayer = L.layerGroup();
    const cls = s => data.classes.find(c => s.score >= c[0]) || data.classes[data.classes.length - 1];
    for (const s of data.spots) {
      if (s.score < min) continue;
      const m = L.circleMarker([s.lat, s.lon], { renderer, radius: s.score >= 75 ? 4 : 3, weight: 1, color: '#fff',
        fillColor: cls(s)[2], fillOpacity: 0.95 });
      m.bindPopup(() => `<div class="launch-tip"><b>Predicted launch · ${s.score}%</b> (${cls(s)[1]})<br>
        Run toward ${WX.dirName(s.heading)} (${s.heading}°), wants wind from ${WX.dirName(s.heading)}. Slope ${s.slope}°, elev ${fmt(U.len(s.elev_ft / 3.28084))} ${U.lenU()}.<br>
        <span class="muted">Patch scoring 60%+ around it: ~${fmt(s.area_m2)} m²</span>${fitHtml(s.heading, s.lat, s.lon)}</div>`);
      m.on('click', ev => L.DomEvent.stopPropagation(ev));
      spotLayer.addLayer(m);
    }
    spotLayer.addTo(WX.map);
  }

  async function loadWind() {
    const [u, v, g] = await Promise.all([WX.getMap('U10'), WX.getMap('V10'), WX.getMap('GUST')]);
    wind = { u, v, g };
  }

  WX.on('ready', async () => {
    try {
      const r = await fetch('launches.json');
      if (!r.ok) return;
      data = await r.json();
    } catch (e) { return; }
    const optL = document.getElementById('optLaunches'), optS = document.getElementById('optSpots'), optF = document.getElementById('optFit');
    optL.checked = WX.prefs.launches; optS.value = WX.prefs.spots; optF.checked = WX.prefs.fit;
    optL.onchange = () => { WX.setPref('launches', optL.checked); optL.checked ? knownLayer.addTo(WX.map) : knownLayer.remove(); };
    optS.onchange = () => { WX.setPref('spots', optS.value); buildSpots(); };
    optF.onchange = () => { WX.setPref('fit', optF.checked); if (optF.checked && !optL.checked) { optL.checked = true; WX.setPref('launches', true); } buildKnown(); };
    await loadWind().catch(() => {});
    buildSpots(); buildKnown();
    // labels appear when zoomed in
    let wasNear = WX.map.getZoom() >= 12;
    WX.map.on('zoomend', () => { const near = WX.map.getZoom() >= 12; if (near !== wasNear) { wasNear = near; buildKnown(); } });
    WX.map.on('popupopen', e => {
      const a = e.popup.getElement().querySelector('[data-open]');
      if (a) a.onclick = ev => { ev.preventDefault(); const [la, lo] = a.dataset.open.split(',').map(Number); WX.map.closePopup(); WX.selectPoint(la, lo); };
    });
  });
  WX.on('time', () => { if (data && WX.prefs.fit) buildKnown(); });
  WX.on('units', () => { if (data) { buildKnown(); } });
})();
