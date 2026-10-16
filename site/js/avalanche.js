/* Avalanche view: official center ratings and estimated danger for ~150 ranges in ten western states. */
'use strict';
(function () {
  const { U, fmt, esc } = WX;
  const NAMES = ['Minimal snow', 'Low', 'Moderate', 'Considerable', 'High', 'Extreme'];
  const RGB = [[128, 150, 178], [80, 184, 72], [255, 242, 0], [247, 148, 30], [237, 28, 36], [35, 31, 32]];
  const HEX = RGB.map(c => '#' + c.map(v => v.toString(16).padStart(2, '0')).join(''));
  const BAND = ['Below treeline', 'Near treeline', 'Above treeline'];
  const REASONS = {
    load: 'Recent snowfall is still adding weight to the snowpack.',
    drift: "Strong ridgetop winds are moving last week's snow into drifts.",
    ros: 'Rain has fallen on the snowpack in the last three days.',
    warm: 'Warm temperatures are hitting fresh snow, so wet avalanches are possible.',
    storm: 'More snow is forecast in the next 24 hours.',
    pwl: 'A thin continental snowpack under new load often hides weak, sugary layers.',
    shallow: 'The snowpack is still shallow, which limits avalanche size but hides rocks.',
  };
  const STATES = ['All', 'WA', 'OR', 'CA', 'NV', 'ID', 'MT', 'WY', 'UT', 'CO', 'NM'];

  let D = null, byId = {}, map = null, base = null, raster = null, zoneLayer = null, snoLayer = null, canvas = null;
  let gridR = null, gridG = null, sel = null, stateF = 'All', q = '';
  const prefs = Object.assign({ mode: 'max', zones: true, snotel: true }, WX.prefs.avy || {});
  const save = () => WX.setPref('avy', prefs);
  const $ = id => document.getElementById(id);

  const zoneOf = r => r && r.zone !== undefined ? D.zones[r.zone] : null;
  const bandLevel = (r, b) => r.bands[b - 1] === null || r.bands[b - 1] === undefined ? null : r.isOfficial ? zoneOf(r).level : r.bands[b - 1];
  const level = r => prefs.mode === 'max' ? r.top : bandLevel(r, +prefs.mode);
  const pill = l => l === null || l === undefined ? '<span class="avy-pill none">No terrain</span>'
    : `<span class="avy-pill" style="background:${HEX[l]};color:${l >= 4 ? '#fff' : '#111'}">${l ? l + ' · ' : ''}${NAMES[l]}</span>`;
  // keep fitted areas clear of the side panel (right on desktop, bottom sheet on phones)
  const pad = () => { const el = $('avyPanel'), phone = innerWidth <= 760;
    return { paddingTopLeft: [20, phone ? 90 : 20], paddingBottomRight: phone ? [20, el.offsetHeight + 10] : [el.offsetWidth + 30, 20] }; };
  const when = s => s ? WX.time.hm(new Date(s)) + ' ' + WX.time.day(new Date(s)) : 'unknown';

  WX.on('showAvy', () => { init().then(() => setTimeout(() => map && map.invalidateSize(), 0)); });
  WX.on('base', () => { if (map) setBase(); });
  WX.on('units', () => { if (D) { detail(); list(); } });

  let loading = null;
  function init() {
    if (!loading) loading = load().catch(e => { console.error(e); $('avyBody').innerHTML = '<p class="muted">Avalanche data is not available yet. It is built with the hourly forecast (see README).</p>'; });
    return loading;
  }

  async function load() {
    const r = await fetch('data/avy/avy.json', { cache: 'no-cache' });
    if (!r.ok) throw new Error('avy.json ' + r.status);
    D = await r.json();
    D.ranges.forEach(x => {
      byId[x.id] = x;
      const z = zoneOf(x);
      x.isOfficial = !!(z && z.level >= 1);
      x.top = x.isOfficial ? z.level : Math.max(0, ...x.bands.filter(b => b !== null));
    });
    const g = D.meta.grid, n = 256 * 2 ** g.z;
    const lon = x => x / n * 360 - 180, lat = y => Math.atan(Math.sinh(Math.PI * (1 - 2 * y / n))) * 180 / Math.PI;
    const bounds = L.latLngBounds([lat(g.gy + g.H), lon(g.gx)], [lat(g.gy), lon(g.gx + g.W)]);

    map = L.map('avyMap', { zoomControl: false, minZoom: 4, maxZoom: 13, preferCanvas: true }).fitBounds(bounds, pad());
    L.control.zoom({ position: 'bottomright' }).addTo(map);
    setBase();
    raster = L.imageOverlay('data:image/gif;base64,R0lGODlhAQABAAAAACw=', bounds, { opacity: 0.72, interactive: false }).addTo(map);
    zoneLayer = L.layerGroup();
    D.zones.forEach((z, zi) => z.polys.forEach(p => {
      const rated = z.level >= 1;
      L.polygon(p.map(ring => ring.map(([x, y]) => [y, x])), {
        color: rated ? '#222' : '#555', weight: rated ? 1.2 : 1, dashArray: rated ? null : '4 4',
        fillColor: rated ? HEX[z.level] : '#000', fillOpacity: rated ? 0.45 : 0, interactive: true, bubblingMouseEvents: true,
      }).bindTooltip(`${esc(z.center)} · ${esc(z.name)}<br>${rated ? NAMES[z.level] : 'No rating'}`, { sticky: true }).addTo(zoneLayer);
    }));
    snoLayer = L.layerGroup();
    const sc = WX.scales.rampScale('blue', 0, 250);
    D.stations.forEach(s => {
      L.circleMarker([s.lat, s.lon], { radius: 4.5, weight: 1, color: '#123', fillColor: WX.rgb(sc.color(s.hs)), fillOpacity: 0.95 })
        .bindPopup(() => stationHtml(s)).addTo(snoLayer);
    });
    if (prefs.zones) zoneLayer.addTo(map);
    if (prefs.snotel) snoLayer.addTo(map);

    canvas = document.createElement('canvas'); canvas.width = g.W; canvas.height = g.H;
    const img = new Image();
    img.onload = () => {
      const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
      const cx = c.getContext('2d'); cx.drawImage(img, 0, 0);
      const px = cx.getImageData(0, 0, c.width, c.height).data;
      gridR = new Uint8Array(c.width * c.height); gridG = new Uint8Array(c.width * c.height);
      for (let i = 0; i < gridR.length; i++) { gridR[i] = px[i * 4]; gridG[i] = px[i * 4 + 1]; }
      render();
    };
    img.src = 'img/avy-grid.png?v=1';

    map.on('mousemove', e => {
      const h = lookup(e.latlng), el = $('avyHover');
      if (!h) { el.classList.add('hidden'); return; }
      const l = prefs.mode === 'max' ? h.r.top : bandLevel(h.r, h.b);
      el.innerHTML = `<b>${esc(h.r.name)}</b> · ${BAND[h.b - 1]} · ${NAMES[l ?? 0]} <span class="muted">${h.r.isOfficial ? 'official' : 'estimated'}</span>`;
      el.classList.remove('hidden');
    });
    map.on('mouseout', () => $('avyHover').classList.add('hidden'));
    map.on('click', e => { const h = lookup(e.latlng); select(h ? h.r : null, false); });

    // controls
    const seg = $('avyMode');
    seg.querySelectorAll('button').forEach(b => {
      b.classList.toggle('active', b.dataset.m === prefs.mode);
      b.onclick = () => { prefs.mode = b.dataset.m; save(); seg.querySelectorAll('button').forEach(x => x.classList.toggle('active', x === b)); render(); list(); };
    });
    $('avyZones').checked = prefs.zones; $('avySno').checked = prefs.snotel;
    $('avyZones').onchange = e => { prefs.zones = e.target.checked; save(); e.target.checked ? zoneLayer.addTo(map) : map.removeLayer(zoneLayer); };
    $('avySno').onchange = e => { prefs.snotel = e.target.checked; save(); e.target.checked ? snoLayer.addTo(map) : map.removeLayer(snoLayer); };
    $('avyGrip').onclick = () => $('avyPanel').classList.toggle('full');
    legend();
    select(null);
  }

  function setBase() {
    if (base) map.removeLayer(base);
    base = WX.BASES[WX.prefs.base]().addTo(map);
    base.bringToBack();
  }

  function render() {
    if (!gridR) return;
    const W = canvas.width, H = canvas.height, N = W * H, ctx = canvas.getContext('2d'), img = ctx.createImageData(W, H), px = img.data;
    const mode = prefs.mode;
    for (let i = 0; i < N; i++) {
      const r = gridR[i]; if (!r) continue;
      const rec = byId[r]; if (!rec) continue;
      const g = gridG[i];
      if (mode !== 'max' && g !== +mode) continue;
      const l = mode === 'max' ? rec.top : bandLevel(rec, g);
      if (l === null) continue;
      const edge = (i % W < W - 1 && gridR[i + 1] !== r) || (i + W < N && gridR[i + W] !== r) || (i % W > 0 && gridR[i - 1] !== r) || (i >= W && gridR[i - W] !== r);
      const o = i * 4;
      if (edge && sel && r === sel.id) { px[o] = 20; px[o + 1] = 110; px[o + 2] = 255; px[o + 3] = 255; continue; }
      const c = RGB[l], k = edge ? 0.7 : 1;
      px[o] = c[0] * k; px[o + 1] = c[1] * k; px[o + 2] = c[2] * k; px[o + 3] = l === 0 ? (edge ? 200 : 150) : 235;
    }
    ctx.putImageData(img, 0, 0);
    raster.setUrl(canvas.toDataURL());
  }

  function lookup(latlng) {
    if (!gridR) return null;
    const g = D.meta.grid, p = map.project(latlng, g.z), x = Math.floor(p.x - g.gx), y = Math.floor(p.y - g.gy);
    if (x < 0 || y < 0 || x >= g.W || y >= g.H) return null;
    const i = y * g.W + x;
    return gridR[i] && byId[gridR[i]] ? { r: byId[gridR[i]], b: gridG[i] } : null;
  }

  function legend() {
    $('avyLegend').innerHTML = `<div class="lg-title">Avalanche danger</div>` +
      [5, 4, 3, 2, 1, 0].map(l => `<div class="avy-lg"><i style="background:${HEX[l]}"></i>${l ? l + ' · ' : ''}${NAMES[l]}</div>`).join('') +
      `<div class="avy-lg"><i class="dash"></i>Center zone, no rating</div><div class="avy-lg"><i class="dot"></i>SNOTEL station</div>`;
  }

  function stationHtml(s) {
    const r = s.range ? byId[s.range] : null;
    const ch = v => v === null || v === undefined ? '–' : (v > 0 ? '+' : '') + fmt(U.snow(v), 0);
    return `<b>${esc(s.name)}</b> SNOTEL<br>${fmt(U.len((s.elev_ft || 0) * 0.3048), 0)} ${U.lenU()} · ${esc(s.date)}<br>
      Snow depth <b>${fmt(U.snow(s.hs), 0)} ${U.snowU()}</b>${s.swe !== null && s.swe !== undefined ? ` · SWE ${fmt(U.snow(s.swe), 1)} ${U.snowU()}` : ''}<br>
      Change 24 h ${ch(s.d24)} · 72 h ${ch(s.d72)} ${U.snowU()}${r ? `<br>Used for ${esc(r.name)}` : ''}`;
  }

  function overview() {
    const counts = [0, 0, 0, 0, 0, 0]; D.ranges.forEach(r => counts[r.top]++);
    const rated = D.zones.filter(z => z.level >= 1).length, nZone = D.ranges.filter(r => r.zone !== undefined || r.center).length;
    const worst = D.ranges.reduce((a, r) => Math.max(a, r.top), 0);
    return `<div class="pt-title">${worst >= 1 ? 'Highest danger: ' + NAMES[worst] : 'Mostly minimal snow so far'}</div>
      <div class="pt-sub">${D.ranges.length} ranges in WA, OR, CA, NV, ID, MT, WY, UT, CO and NM</div>
      <div class="cards" style="margin-top:10px">${[5, 4, 3, 2, 1, 0].map(l => `<div class="card" style="border-left:5px solid ${HEX[l]}"><div class="k">${NAMES[l]}</div><div class="v">${counts[l]}</div></div>`).join('')}</div>
      <div class="box small">${rated ? `<b>${rated} avalanche center ${rated === 1 ? 'zone is' : 'zones are'} rated today.</b> Ranges inside them show the center's rating.` : '<b>No avalanche center in these states is issuing ratings right now.</b>'}
      ${nZone} ranges sit inside a center's forecast zone. Everywhere else the rating is an estimate from HRRR snow, rain, wind and temperature, corrected with ${D.meta.snotel ? D.meta.snotel + ' SNOTEL stations' : 'SNOTEL stations when available'}. It is not a forecast.</div>
      <details class="afd"><summary>How the estimate works</summary><ul class="small">
        <li>Each ~2 km mountain cell is scored from HRRR for the past 7 days and next 24 hours. Each range and elevation band takes the 75th-percentile score.</li>
        <li>New snow (weighted toward the last 24 hours and boosted by wind near and above treeline), drifting, rain on snow, warming and forecast snow raise the score.</li>
        <li>SNOTEL snow depth and its 24 h and 72 h change pull the model toward measurements in ranges that have stations.</li>
        <li>Thin continental snowpacks under new load get a bump for weak layers. Under 6 in of snow counts as minimal snow; under 16 in caps the rating at Moderate, under 28 in at Considerable.</li>
        <li>Treeline is approximate. Observations and snowpits are not used, which is why official forecasts are always better.</li></ul></details>`;
  }

  function detail() {
    const body = $('avyBody');
    if (!sel) { body.innerHTML = overview() + listHtml(); bindList(); return; }
    const r = sel, d = r.drivers, z = zoneOf(r), cols = [3, 2, 1].filter(b => d[b - 1]);
    const row = (label, f) => `<tr><td>${label}</td>${cols.map(b => `<td>${f(d[b - 1])}</td>`).join('')}</tr>`;
    const sn = v => fmt(U.snow(v), 0) + ' ' + U.snowU();
    const why = r.reasons.filter(k => REASONS[k]).map(k => `<li>${REASONS[k]}</li>`).join('');
    const sts = r.snotel.map(id => D.stations.find(s => s.id === id)).filter(Boolean);
    let src;
    if (z) src = `<div class="box small"><b>${esc(z.center)} · ${esc(z.name)}</b><br>${z.level >= 1 ? `Rated <b>${NAMES[z.level]}</b> overall. ${esc(z.travel_advice || '')}` : z.off_season ? 'Off season. No danger rating yet, so the estimate is shown.' : 'No danger rating issued right now, so the estimate is shown.'}<br><a href="${esc(z.link)}" target="_blank" rel="noopener">Open the forecast</a></div>`;
    else if (r.center) src = `<div class="box small"><b>${esc(r.center.center)} · ${esc(r.center.zone)}</b><br>Center ratings could not be checked this hour. <a href="${esc(r.center.link)}" target="_blank" rel="noopener">Open the forecast</a></div>`;
    else src = '<div class="box small">No avalanche center forecasts this range. Treat the estimate as a rough guide and watch for recent avalanches, cracking and collapsing.</div>';
    body.innerHTML = `<button class="icon-btn avy-back" id="avyBack">← All ranges</button>
      <div class="pt-title" style="margin-top:8px">${esc(r.name)}</div>
      <div class="pt-sub">${esc(r.state)} · ${r.isOfficial ? 'official rating' : 'estimated'}</div>
      <div style="margin:8px 0">${pill(r.top)}</div>${src}
      <h4>By elevation</h4><div class="avy-bands">${[3, 2, 1].map(b => `<div class="avy-band"><div>${BAND[b - 1]}${r.elev[b - 1] ? `<span class="muted small"> ${fmt(U.len(r.elev[b - 1][0]), 0)}–${fmt(U.len(r.elev[b - 1][1]), 0)} ${U.lenU()}</span>` : ''}</div>${pill(bandLevel(r, b))}</div>`).join('')}</div>
      ${why ? `<h4>Why</h4><ul class="small avy-why">${why}</ul>` : ''}
      <h4>Model weather${sts.length ? ' (SNOTEL-corrected)' : ''}</h4><div class="tbl-wrap"><table class="data"><thead><tr><th></th>${cols.map(b => `<th>${['Below', 'Near', 'Above'][b - 1]}</th>`).join('')}</tr></thead><tbody>
      ${row('Snow depth', x => sn(x.hs))}${row('New snow 24 h', x => sn(x.hn24))}${row('New snow 72 h', x => sn(x.hn72))}${row('New snow 7 days', x => sn(x.hn7d))}
      ${row('Rain 72 h', x => fmt(U.precip(x.rain72), U.imperial() ? 2 : 0) + ' ' + U.precipU())}
      ${row('Wind avg / max', x => fmt(U.wind(x.wmean72), 0) + ' / ' + fmt(U.wind(x.wmax72), 0) + ' ' + U.windU())}
      ${row('High temp 24 h', x => fmt(U.temp(x.tmax24), 0) + U.tempU())}${row('Snow next 24 h', x => sn(x.fhn24))}${row('High next 24 h', x => fmt(U.temp(x.ftmax), 0) + U.tempU())}
      </tbody></table></div>
      ${sts.length ? `<h4>SNOTEL</h4><div class="tbl-wrap"><table class="data"><thead><tr><th>Station</th><th>Elev</th><th>Depth</th><th>24 h</th><th>72 h</th></tr></thead><tbody>${sts.map(s => `<tr><td>${esc(s.name)}</td><td>${fmt(U.len((s.elev_ft || 0) * 0.3048), 0)}</td><td>${sn(s.hs)}</td><td>${s.d24 === null ? '–' : fmt(U.snow(s.d24), 0)}</td><td>${s.d72 === null ? '–' : fmt(U.snow(s.d72), 0)}</td></tr>`).join('')}</tbody></table></div>` : ''}
      <p class="muted small">HRRR ${when(D.meta.cycle)} · center ratings checked ${when(D.meta.official_checked)}${D.meta.official_stale ? ' (last good copy)' : ''}</p>`;
    $('avyBack').onclick = () => select(null);
  }

  function listHtml() {
    return `<h4>Ranges</h4><div class="avy-chips">${STATES.map(s => `<button data-s="${s}" class="${s === stateF ? 'active' : ''}">${s}</button>`).join('')}</div>
      <input id="avyQ" type="search" placeholder="Find a range" value="${esc(q)}" aria-label="Find a range"><div id="avyList" class="avy-list"></div>`;
  }
  function bindList() {
    document.querySelectorAll('.avy-chips button').forEach(b => (b.onclick = () => { stateF = b.dataset.s; document.querySelectorAll('.avy-chips button').forEach(x => x.classList.toggle('active', x === b)); list(); }));
    $('avyQ').oninput = e => { q = e.target.value; list(); };
    $('avyList').onclick = e => { const b = e.target.closest('[data-id]'); if (b) select(byId[+b.dataset.id], true); };
    list();
  }
  function list() {
    const el = $('avyList'); if (!el) return;
    const ql = q.trim().toLowerCase();
    const rows = D.ranges.filter(r => (stateF === 'All' || r.state.split('/').includes(stateF)) && (!ql || r.name.toLowerCase().includes(ql)))
      .sort((a, b) => ((level(b) ?? -1) - (level(a) ?? -1)) || a.name.localeCompare(b.name));
    el.innerHTML = rows.map(r => { const l = level(r);
      return `<button data-id="${r.id}"><i style="background:${l === null ? 'transparent' : HEX[l]}"></i><span class="nm">${esc(r.name)} <span class="muted small">${esc(r.state)}</span></span><span class="small muted">${r.isOfficial ? '<b class="avy-off">Official</b> ' : ''}${l === null ? '–' : NAMES[l]}</span></button>`; }).join('') || '<p class="muted">No ranges match.</p>';
  }

  function select(r, zoom) {
    sel = r;
    detail();
    if (r && zoom) map.fitBounds([[r.bbox[0], r.bbox[1]], [r.bbox[2], r.bbox[3]]], Object.assign({ maxZoom: 9 }, pad()));
    $('avyBody').scrollTop = 0;
    render();
  }
})();
