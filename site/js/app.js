/* Map view: layers, native-grid overlay, time slider, point selection. */
'use strict';
(function () {
  const { U, fmt } = WX;
  const S = WX.scales;
  const stdAltM = p => 44330.8 * (1 - Math.pow(p / 1013.25, 0.190263));

  /* ---------------------------------------------------------------- layer catalog
     Each layer: levels() -> [{id, label, keys}], value(get, k, lv) -> SI value, show(v) -> display value */
  const upperLevels = (vars, extra = []) => () => extra.concat(WX.meta.levels.map(p => ({
    id: String(p), label: `${p} mb · ~${fmt(U.len(stdAltM(p)) / (U.imperial() ? 1000 : 1000), 1)}k ${U.lenU()}`,
    keys: vars.map(v => `${v}:${p}`), p,
  })));

  const LAYERS = [
    {
      id: 'wind', label: 'Wind', arrows: true,
      levels: upperLevels(['UGRD', 'VGRD'], [
        { id: 'sfc', label: 'Surface 10 m', keys: ['U10', 'V10'] },
        { id: 'gust', label: 'Gusts (surface)', keys: ['GUST', 'U10', 'V10'], scalar: true },
        { id: 'max10', label: 'Max 10 m wind in hour', keys: ['WMAX10', 'U10', 'V10'], scalar: true },
        { id: '80m', label: '80 m above ground', keys: ['U80', 'V80'] }]),
      subLevels: () => [
        { id: 'sfc', label: 'Surface 10 m', keys: ['U10', 'V10'] },
        { id: 'gust', label: 'Gusts (surface)', keys: ['GUST', 'U10', 'V10'], scalar: true },
        { id: 'avg5', label: '10 m, 5-minute average', keys: ['WAVG10', 'U10', 'V10'], scalar: true },
        { id: '80m', label: '80 m above ground', keys: ['U80', 'V80'] }],
      value: (g, k, lv) => lv.scalar ? g(lv.keys[0], k) : WX.wspd(g(lv.keys[0], k), g(lv.keys[1], k)),
      uv: (g, k, lv) => lv.scalar ? [g('U10', k), g('V10', k)] : [g(lv.keys[0], k), g(lv.keys[1], k)],
      scale: () => S.rampScale('blue', 0, 30), show: v => U.wind(v), unit: () => U.windU(), dec: 0,
    },
    {
      id: 'temp', label: 'Temperature',
      levels: upperLevels(['TMP'], [{ id: 'sfc', label: '2 m above ground', keys: ['T2'] }]),
      value: (g, k, lv) => g(lv.keys[0], k),
      scale: lv => lv.id === 'sfc' || lv.p >= 600 ? S.divScale('blue', 'red', -25, 0, 30) : S.divScale('blue', 'red', -55, 0, 10),
      show: v => U.temp(v), unit: () => U.tempU(), dec: 0,
    },
    {
      id: 'dewpt', label: 'Dew point',
      levels: upperLevels(['DPT'], [{ id: 'sfc', label: '2 m above ground', keys: ['D2'] }]),
      value: (g, k, lv) => g(lv.keys[0], k),
      scale: () => S.divScale('blue', 'red', -35, 0, 20), show: v => U.temp(v), unit: () => U.tempU(), dec: 0,
    },
    {
      id: 'rh', label: 'Relative humidity',
      levels: upperLevels(['RH'], [{ id: 'sfc', label: '2 m above ground', keys: ['RH2'] }]),
      value: (g, k, lv) => g(lv.keys[0], k),
      scale: () => S.rampScale('aqua', 0, 100), show: v => v, unit: () => '%', dec: 0,
    },
    {
      id: 'vvel', label: 'Vertical motion', arrows: false,
      levels: upperLevels(['VVEL', 'TMP']),
      value: (g, k, lv) => WX.omegaToW(g(lv.keys[0], k), g(lv.keys[1], k), lv.p),
      scale: () => S.divScale('blue', 'red', -2, 0, 2),
      show: v => U.imperial() ? v * 196.85 : v, unit: () => U.imperial() ? 'ft/min' : 'm/s', dec: () => U.imperial() ? 0 : 1,
      note: 'Red = rising air, blue = sinking (from HRRR omega)',
    },
    {
      id: 'precip', label: 'Precipitation',
      levels: () => [
        { id: '1h', label: 'Hourly, by type', keys: ['APCP1', 'CPOFP'] },
        { id: 'acc', label: 'Accumulated from first hour', keys: ['APCP1'], acc: true },
        { id: 'rate', label: 'Rate (instantaneous)', keys: ['PRATE'] }],
      subLevels: () => [
        { id: '15m', label: '15-minute, by type', keys: ['APCP15', 'CPOFP'] },
        { id: 'rate', label: 'Rate (instantaneous)', keys: ['PRATE'] }],
      value: (g, k, lv) => g(lv.keys[0], k),
      scale: lv => S.rampScale('aqua', 0, lv.id === 'acc' ? 40 : lv.id === '15m' ? 3 : 8, { clearBelow: 0.01 }),
      colorFn: (lv, g, k, v, sc) => {
        if ((lv.id !== '1h' && lv.id !== '15m') || !(v >= 0.01)) return sc.color(v);
        const fz = g('CPOFP', k);
        const name = fz >= 70 ? 'violet' : fz > 20 ? 'orange' : 'aqua';
        return S.rampScale(name, 0, lv.id === '15m' ? 3 : 8).color(v);
      },
      show: v => U.precip(v), unit: () => U.precipU(), dec: () => U.imperial() ? 2 : 1,
      note: 'Hourly: aqua = rain, orange = mixed, violet = snow (HRRR % frozen)',
    },
    {
      id: 'snow', label: 'Snowfall',
      levels: () => [{ id: 'acc', label: 'Accumulated from first hour', keys: ['ASNOW', 'APCP1', 'CPOFP'], snowAcc: true },
        { id: 'depth', label: 'Snow depth (model)', keys: ['SNOD'] }],
      value: (g, k, lv) => g(lv.keys[0], k),
      scale: lv => S.rampScale('violet', 0, lv.id === 'depth' ? 150 : 50, { clearBelow: 0.05 }),
      show: v => U.snow(v), unit: () => U.snowU(), dec: 1,
    },
    {
      id: 'cloud', label: 'Clouds',
      levels: () => [{ id: 'tcdc', label: 'Total cover', keys: ['TCDC'] }, { id: 'lcdc', label: 'Low', keys: ['LCDC'] },
        { id: 'mcdc', label: 'Middle', keys: ['MCDC'] }, { id: 'hcdc', label: 'High', keys: ['HCDC'] },
        { id: 'cbase', label: 'Cloud base (MSL)', keys: ['CBASE'] }, { id: 'ceil', label: 'Ceiling (MSL)', keys: ['CEIL'] },
        { id: 'ctop', label: 'Cloud top (MSL)', keys: ['CTOP'] }],
      value: (g, k, lv) => { const v = g(lv.keys[0], k); return (lv.id === 'cbase' || lv.id === 'ceil' || lv.id === 'ctop') && v > 15000 ? NaN : v; },
      scale: lv => ['cbase', 'ceil', 'ctop'].includes(lv.id) ? S.rampScale('blue', 1000, 8000) : S.rampScale('gray', 0, 100, { clearBelow: 5 }),
      show: (v, lv) => ['cbase', 'ceil', 'ctop'].includes(lv.id) ? U.len(v) : v,
      unit: lv => ['cbase', 'ceil', 'ctop'].includes(lv.id) ? U.lenU() : '%', dec: 0,
    },
    {
      id: 'frz', label: 'Freezing level',
      levels: () => [{ id: 'sfc', label: '0 °C height (MSL)', keys: ['FRZLVL'] }],
      value: (g, k, lv) => g('FRZLVL', k), scale: () => S.rampScale('orange', 1000, 4500),
      show: v => U.len(v), unit: () => U.lenU(), dec: 0,
    },
    {
      id: 'pbl', label: 'Boundary layer',
      levels: () => [{ id: 'msl', label: 'Top of mixed layer (MSL)', keys: ['HPBL'], msl: true },
        { id: 'agl', label: 'Depth above ground', keys: ['HPBL'] }],
      value: (g, k, lv) => g('HPBL', k) + (lv.msl ? WX.grid.terrain[k] : 0),
      scale: lv => lv.msl ? S.rampScale('orange', 1500, 5000) : S.rampScale('orange', 0, 3000),
      show: v => U.len(v), unit: () => U.lenU(), dec: 0,
      note: 'HRRR planetary boundary layer height; roughly the top of daytime thermals',
    },
    {
      id: 'cape', label: 'Instability',
      levels: () => [{ id: 'cape', label: 'Surface CAPE', keys: ['CAPE'] }, { id: 'lftx', label: 'Lifted index', keys: ['LFTX'] },
        { id: 'ltng', label: 'Lightning threat', keys: ['LTNG'] }],
      value: (g, k, lv) => g(lv.keys[0], k),
      scale: lv => lv.id === 'lftx' ? S.divScale('red', 'blue', -6, 0, 8) : lv.id === 'ltng' ? S.rampScale('orange', 0, 2, { clearBelow: 0.01 }) : S.rampScale('orange', 0, 1500, { clearBelow: 10 }),
      show: v => v, unit: lv => lv.id === 'cape' ? 'J/kg' : lv.id === 'lftx' ? '°C' : 'fl/km²/5min', dec: lv => lv.id === 'ltng' ? 2 : 0,
    },
    {
      id: 'radar', label: 'Radar (simulated)',
      levels: () => [{ id: 'refc', label: 'Composite reflectivity', keys: ['REFC'] }],
      value: (g, k) => g('REFC', k), scale: () => S.rampScale('aqua', 5, 60, { clearBelow: 5 }),
      show: v => v, unit: () => 'dBZ', dec: 0,
    },
    {
      id: 'vis', label: 'Visibility',
      levels: () => [{ id: 'vis', label: 'Surface visibility', keys: ['VIS'] }],
      value: (g, k) => g('VIS', k), scale: () => S.rampScale('gray', 30, 0),
      show: v => U.vis(v), unit: () => U.visU(), dec: 1,
    },
    {
      id: 'conf', label: 'HRRR confidence',
      levels: () => [{ id: 'ALL', label: 'Overall', keys: ['CONF_ALL'] }, { id: 'T2', label: 'Temperature', keys: ['CONF_T2'] },
        { id: 'WIND', label: 'Surface wind', keys: ['CONF_WIND'] }, { id: 'GUST', label: 'Gusts', keys: ['CONF_GUST'] },
        { id: 'PRECIP', label: 'Precipitation', keys: ['CONF_PRECIP'] }, { id: 'UPPER', label: '700 mb wind/temp', keys: ['CONF_UPPER'] }],
      value: (g, k, lv) => g(lv.keys[0], k), scale: () => S.divScale('red', 'blue', 20, 60, 100),
      show: v => v, unit: () => '%', dec: 0,
      note: 'Agreement of HRRR with NAM 3 km, RAP and the two previous HRRR runs',
    },
    {
      id: 'solar', label: 'Sunshine',
      levels: () => [{ id: 'dswrf', label: 'Solar radiation at the ground', keys: ['DSWRF'] }],
      value: (g, k) => g('DSWRF', k), scale: () => S.rampScale('orange', 0, 900),
      show: v => v, unit: () => 'W/m²', dec: 0,
      note: 'Sunlight reaching the ground after clouds; drives thermals',
    },
    {
      id: 'terrain', label: 'Model terrain', static: true,
      levels: () => [{ id: 'hgt', label: 'HRRR 3 km terrain', keys: [] }],
      value: (g, k) => WX.grid.terrain[k], scale: () => S.rampScale('gray', 1200, 3500),
      show: v => U.len(v), unit: () => U.lenU(), dec: 0,
      note: 'Smoothed 3 km model terrain: ridges are lower and valleys higher than reality',
    },
  ];
  WX.LAYERS = LAYERS;

  /* ---------------------------------------------------------------- map */
  const map = L.map('map', { zoomControl: false, attributionControl: true, minZoom: 8, maxZoom: 15 })
    .fitBounds([[39.72, -112.05], [40.9, -111.35]]);
  L.control.zoom({ position: 'bottomright' }).addTo(map);
  WX.map = map;
  const BASES = {
    topo: () => L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}', { maxZoom: 15, attribution: 'Esri' }),
    relief: () => L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Shaded_Relief/MapServer/tile/{z}/{y}/{x}', { maxNativeZoom: 13, maxZoom: 15, attribution: 'Esri' }),
    sat: () => L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 15, attribution: 'Esri' }),
    otm: () => L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', { maxZoom: 15, maxNativeZoom: 15, attribution: '© OpenTopoMap (CC-BY-SA), © OpenStreetMap' }),
  };
  let base = null;
  function setBase() { if (base) map.removeLayer(base); base = BASES[WX.prefs.base]().addTo(map); }
  setBase();

  /* ---------------------------------------------------------------- grid overlay (native 3 km cells) */
  let corners = null;   // Float64Array [(ny+1)*(nx+1)*2]
  function buildCorners() {
    const { ny, nx, lat, lon } = WX.grid;
    const P = (j, i) => {   // padded center with linear extrapolation
      const jj = Math.max(0, Math.min(ny - 1, j)), ii = Math.max(0, Math.min(nx - 1, i));
      const k = jj * nx + ii;
      let la = lat[k], lo = lon[k];
      if (j !== jj) { const k2 = (jj + (j < 0 ? 1 : -1)) * nx + ii; la += (la - lat[k2]) * Math.abs(j - jj); lo += (lo - lon[k2]) * Math.abs(j - jj); }
      if (i !== ii) { const k2 = jj * nx + ii + (i < 0 ? 1 : -1); la += (lat[k] - lat[k2]) * Math.abs(i - ii); lo += (lon[k] - lon[k2]) * Math.abs(i - ii); }
      return [la, lo];
    };
    corners = new Float64Array((ny + 1) * (nx + 1) * 2);
    for (let j = 0; j <= ny; j++) for (let i = 0; i <= nx; i++) {
      const a = P(j - 1, i - 1), b = P(j - 1, i), c = P(j, i - 1), d = P(j, i);
      const o = (j * (nx + 1) + i) * 2;
      corners[o] = (a[0] + b[0] + c[0] + d[0]) / 4; corners[o + 1] = (a[1] + b[1] + c[1] + d[1]) / 4;
    }
  }

  const Overlay = L.Layer.extend({
    onAdd(m) {
      this._c = L.DomUtil.create('canvas', 'wx-overlay');
      this._c.style.pointerEvents = 'none';
      m.getPanes().overlayPane.appendChild(this._c);
      m.on('moveend zoomend resize viewreset', this.redraw, this);
      m.on('zoomanim', this._anim, this);
      this.redraw();
    },
    onRemove(m) { this._c.remove(); m.off('moveend zoomend resize viewreset', this.redraw, this); m.off('zoomanim', this._anim, this); },
    _anim(e) {
      const s = this._map.getZoomScale(e.zoom), off = this._map._latLngBoundsToNewLayerBounds(this._map.getBounds(), e.zoom, e.center).min;
      L.DomUtil.setTransform(this._c, off, s);
    },
    redraw() { if (this._map) draw(this._c, this._map); },
  });
  const overlay = new Overlay();

  let current = { vals: null, layer: null, lv: null, getters: null };

  function draw(canvas, m) {
    const size = m.getSize(), dpr = window.devicePixelRatio || 1;
    const tl = m.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(canvas, tl);
    canvas.width = size.x * dpr; canvas.height = size.y * dpr;
    canvas.style.width = size.x + 'px'; canvas.style.height = size.y + 'px';
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.x, size.y);
    if (!current.getters || !corners) return;
    const { ny, nx } = WX.grid;
    const { layer, lv, getters: g } = current;
    const sc = layer.scale(lv);
    // project corners
    const pc = new Float32Array((ny + 1) * (nx + 1) * 2);
    for (let q = 0; q < pc.length / 2; q++) {
      const p = m.latLngToContainerPoint([corners[q * 2], corners[q * 2 + 1]]);
      pc[q * 2] = p.x; pc[q * 2 + 1] = p.y;
    }
    const cellPx = Math.abs(pc[2] - pc[0]);
    ctx.globalAlpha = layerOpacity(layer.id);
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      const k = j * nx + i, v = layer.value(g, k, lv);
      const col = layer.colorFn ? layer.colorFn(lv, g, k, v, sc) : sc.color(v);
      if (!col) continue;
      const a = (j * (nx + 1) + i) * 2, b = a + 2, c = ((j + 1) * (nx + 1) + i + 1) * 2, d = ((j + 1) * (nx + 1) + i) * 2;
      ctx.fillStyle = ctx.strokeStyle = WX.rgb(col);
      ctx.beginPath(); ctx.moveTo(pc[a], pc[a + 1]); ctx.lineTo(pc[b], pc[b + 1]); ctx.lineTo(pc[c], pc[c + 1]); ctx.lineTo(pc[d], pc[d + 1]);
      ctx.closePath(); ctx.fill(); ctx.lineWidth = 0.6; ctx.stroke();
    }
    ctx.globalAlpha = 1;
    // wind arrows & numbers at cell centers
    const showArrows = layer.uv && WX.prefs.arrows, showNums = WX.prefs.numbers && cellPx >= 24;
    const step = showArrows ? Math.max(1, Math.ceil(16 / cellPx)) : 1;
    const dec = typeof layer.dec === 'function' ? layer.dec(lv) : layer.dec;
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    const fs = Math.max(9, Math.min(13, cellPx / 3.2));
    ctx.font = `600 ${fs}px -apple-system, system-ui, sans-serif`;
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      const k = j * nx + i;
      const a = (j * (nx + 1) + i) * 2, c = ((j + 1) * (nx + 1) + i + 1) * 2;
      const x = (pc[a] + pc[c]) / 2, y = (pc[a + 1] + pc[c + 1]) / 2;
      if (x < -20 || y < -20 || x > size.x + 20 || y > size.y + 20) continue;
      if (showArrows && j % step === 0 && i % step === 0) {
        const [u, vv] = layer.uv(g, k, lv);
        if (isFinite(u)) drawArrow(ctx, x, showNums ? y - fs * 0.55 : y, u, vv, Math.min(cellPx * step * 0.42, 16));
      }
      if (showNums) {
        const v = layer.value(g, k, lv);
        if (!isFinite(v)) continue;
        const txt = fmt(layer.show(v, lv), dec);
        const ty = showArrows ? y + fs * 0.65 : y;
        ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(255,255,255,.85)'; ctx.strokeText(txt, x, ty);
        ctx.fillStyle = '#111'; ctx.fillText(txt, x, ty);
      }
    }
  }
  function drawArrow(ctx, x, y, u, v, len) {
    const s = Math.hypot(u, v); if (s < 0.3) return;
    const dx = u / s, dy = -v / s;   // screen y down; arrow points where wind blows TO
    const l = len * (0.55 + 0.45 * Math.min(1, s / 15));
    const x0 = x - dx * l / 2, y0 = y - dy * l / 2, x1 = x + dx * l / 2, y1 = y + dy * l / 2;
    ctx.lineCap = 'round';
    for (const [w, col] of [[3.2, 'rgba(255,255,255,.9)'], [1.4, '#111']]) {
      ctx.strokeStyle = col; ctx.lineWidth = w;
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
      const hx = -dx * 4.5, hy = -dy * 4.5;
      ctx.moveTo(x1, y1); ctx.lineTo(x1 + hx - dy * 3, y1 + hy + dx * 3);
      ctx.moveTo(x1, y1); ctx.lineTo(x1 + hx + dy * 3, y1 + hy - dx * 3);
      ctx.stroke();
    }
  }

  /* ---------------------------------------------------------------- layer data plumbing */
  WX.mode = 'hourly';   // or 'sub' (15-minute dawn/dusk data)
  WX.ts = 0;            // selected 15-minute step
  function levelsFor(layer) {
    if (WX.mode !== 'sub') return layer.levels();
    if (layer.static) return [];
    const f = WX.meta.sub.fields;
    return (layer.subLevels ? layer.subLevels() : layer.levels())
      .filter(l => l.keys.length && !l.p && !l.acc && !l.snowAcc && l.keys.every(k => f[k]));
  }
  const availLayers = () => LAYERS.filter(l => levelsFor(l).length);
  function layerObj() { const av = availLayers(); return av.find(l => l.id === WX.layer) || av[0]; }
  function levelObj(layer) { const lvls = levelsFor(layer); return lvls.find(l => l.id === WX.level) || lvls[0]; }

  async function accumulate(key, lv) {
    // cumulative sum along the time axis (precip), or snowfall built from per-run ASNOW totals
    const T = WX.times.length, n = WX.grid.ny * WX.grid.nx;
    if (lv.snowAcc) {
      const [as, ap, fz] = await Promise.all([WX.getMap('ASNOW'), WX.getMap('APCP1'), WX.getMap('CPOFP')]);
      const out = new Float32Array(T * n), src = WX.meta.sources;
      for (let t = 1; t < T; t++) for (let k = 0; k < n; k++) {
        let inc;
        if (src[t].run === src[t - 1].run) inc = as[t * n + k] - as[(t - 1) * n + k];
        else if (src[t].fh === 1) inc = as[t * n + k];
        else inc = (ap[t * n + k] || 0) * (fz[t * n + k] || 0) / 100; // run switch: ~10:1 from frozen precip
        out[t * n + k] = out[(t - 1) * n + k] + (isFinite(inc) ? Math.max(inc, 0) : 0);
      }
      return out;
    }
    const a = await WX.getMap(key), out = new Float32Array(T * n);
    for (let t = 1; t < T; t++) for (let k = 0; k < n; k++) {
      const v = a[t * n + k]; out[t * n + k] = out[(t - 1) * n + k] + (isFinite(v) ? v : 0);
    }
    return out;
  }
  const accCache = new Map();
  WX.accumulated = (key, lv) => {
    const id = key + (lv.snowAcc ? ':snow' : '');
    if (!accCache.has(id)) accCache.set(id, accumulate(key, lv));
    return accCache.get(id);
  };

  let reqId = 0;
  async function refresh() {
    const my = ++reqId;
    const layer = layerObj(), lv = levelObj(layer);
    const n = WX.grid.ny * WX.grid.nx;
    let arrays = {};
    if (lv.acc || lv.snowAcc) {
      arrays[lv.keys[0]] = await WX.accumulated(lv.keys[0], lv);
      for (const k of lv.keys.slice(1)) arrays[k] = await WX.getMap(k);
    } else {
      const getM = WX.mode === 'sub' ? WX.getSubMap : WX.getMap;
      const got = await Promise.all(lv.keys.map(k => getM(k)));
      lv.keys.forEach((k, i) => (arrays[k] = got[i]));
    }
    if (lv.p) arrays['HGT'] = await WX.getMap('HGT:' + lv.p).catch(() => null);
    if (my !== reqId) return;
    const t = WX.mode === 'sub' ? WX.ts : WX.t;
    const g = (key, k) => { const a = arrays[key]; return a ? a[t * n + k] : NaN; };
    current = { layer, lv, getters: g, arrays };
    overlay.redraw();
    drawLegend(layer, lv);
    updateReadout();
    // prefetch next hour's data implicitly cached by getMap (already whole time series)
  }
  WX.refreshMap = refresh;

  /* ---------------------------------------------------------------- legend */
  function drawLegend(layer, lv) {
    const el = document.getElementById('legend');
    const sc = layer.scale(lv), unit = layer.unit(lv);
    const dec = typeof layer.dec === 'function' ? layer.dec(lv) : layer.dec;
    el.innerHTML = `<div class="lg-title">${WX.esc(layer.label)} · ${WX.esc(lv.label)} <span class="muted">(${unit})</span></div>
      <canvas width="240" height="10"></canvas><div class="lg-ticks"></div>${layer.note ? `<div class="muted" style="margin-top:3px">${WX.esc(layer.note)}</div>` : ''}`;
    const cv = el.querySelector('canvas'), ctx = cv.getContext('2d');
    for (let x = 0; x < 240; x++) {
      const v = sc.lo + (sc.hi - sc.lo) * x / 239;
      ctx.fillStyle = WX.rgb(sc.color(v) || [255, 255, 255]); ctx.fillRect(x, 0, 1, 10);
    }
    const ticks = el.querySelector('.lg-ticks');
    for (let q = 0; q <= 4; q++) {
      const v = sc.lo + (sc.hi - sc.lo) * q / 4;
      const s = document.createElement('span');
      s.style.left = (q * 25) + '%';
      if (q === 0) s.style.transform = 'none'; if (q === 4) s.style.transform = 'translateX(-100%)';
      s.textContent = fmt(layer.show(v, lv), Math.max(0, dec - (Math.abs(layer.show(sc.hi, lv)) > 20 ? 1 : 0)));
      ticks.appendChild(s);
    }
  }

  /* ---------------------------------------------------------------- hover readout */
  let hoverLL = null;
  function updateReadout() {
    const el = document.getElementById('hoverReadout');
    if (!hoverLL || !current.getters) { el.classList.add('hidden'); return; }
    const c = WX.nearest(hoverLL.lat, hoverLL.lng);
    if (c.dist_km > 3) { el.classList.add('hidden'); return; }
    const { layer, lv, getters: g } = current;
    const v = layer.value(g, c.k, lv);
    const dec = typeof layer.dec === 'function' ? layer.dec(lv) : layer.dec;
    let s = `<b>${fmt(layer.show(v, lv), dec)} ${layer.unit(lv)}</b>`;
    if (layer.uv) { const [u, vv] = layer.uv(g, c.k, lv); s += ` from ${WX.dirName(WX.wdir(u, vv))} (${fmt(WX.wdir(u, vv))}°)`; }
    if (lv.p && current.arrays.HGT) { s += ` · ${lv.p} mb at ${fmt(U.len(g('HGT', c.k)))} ${U.lenU()}`; }
    s += `<br><span class="muted">Cell ${c.j},${c.i} · ${c.lat.toFixed(3)}, ${c.lon.toFixed(3)} · model elev ${fmt(U.len(c.elev))} ${U.lenU()}</span>`;
    el.innerHTML = s; el.classList.remove('hidden');
  }
  map.on('mousemove', e => { hoverLL = e.latlng; updateReadout(); });
  map.on('mouseout', () => { hoverLL = null; updateReadout(); });

  /* ---------------------------------------------------------------- controls */
  const layerSel = document.getElementById('layerSel'), levelSel = document.getElementById('levelSel');
  function fillLayers() {
    const av = availLayers();
    layerSel.innerHTML = av.map(l => `<option value="${l.id}">${WX.esc(l.label)}</option>`).join('');
    if (!av.find(l => l.id === WX.layer)) WX.layer = av[0].id;
    layerSel.value = WX.layer;
    fillLevels();
  }
  function fillLevels() {
    const layer = layerObj(), lvls = levelsFor(layer);
    levelSel.innerHTML = lvls.map(l => `<option value="${l.id}">${WX.esc(l.label)}</option>`).join('');
    if (!lvls.find(l => l.id === WX.level)) WX.level = lvls[0].id;
    levelSel.value = WX.level;
    levelSel.disabled = lvls.length < 2;
  }
  layerSel.onchange = () => { WX.layer = layerSel.value; fillLevels(); syncOpacity(); saveHash(); refresh(); };
  levelSel.onchange = () => { WX.level = levelSel.value; saveHash(); refresh(); };
  const optN = document.getElementById('optNumbers'), optA = document.getElementById('optArrows');
  optN.checked = WX.prefs.numbers; optA.checked = WX.prefs.arrows;
  optN.onchange = () => { WX.setPref('numbers', optN.checked); overlay.redraw(); };
  optA.onchange = () => { WX.setPref('arrows', optA.checked); overlay.redraw(); };
  // per-layer overlay opacity (remembered for each layer)
  const optO = document.getElementById('optOpacity');
  function layerOpacity(id) { const v = (WX.prefs.opacity || {})[id]; return (v !== undefined ? v : id === 'terrain' ? 55 : 62) / 100; }
  WX.layerOpacity = layerOpacity;
  function syncOpacity() { optO.value = Math.round(layerOpacity(WX.layer) * 100); }
  optO.oninput = () => {
    WX.prefs.opacity = Object.assign({}, WX.prefs.opacity, { [WX.layer]: +optO.value });
    WX.setPref('opacity', WX.prefs.opacity); overlay.redraw();
  };

  /* time slider */
  const slider = document.getElementById('timeSlider'), tlabel = document.getElementById('timeLabel');
  function setTime(t, from) {
    WX.t = Math.max(0, Math.min(WX.times.length - 1, t));
    if (WX.mode === 'hourly') {
      slider.value = WX.t;
      const src = WX.meta.sources[WX.t], run = new Date(src.run);
      tlabel.innerHTML = `${WX.time.full(WX.times[WX.t])}<span class="src">HRRR ${String(run.getUTCHours()).padStart(2, '0')}Z +${src.fh}h</span>`;
      refresh();
    }
    if (from !== 'point') WX.emit('time', WX.t);
  }
  WX.setTime = setTime;
  function spanOf(ts) {
    const q = WX.subTimes[ts];
    return WX.meta.sub.spans.find(sp => q >= new Date(sp.start) && q <= new Date(sp.end));
  }
  function setSubTime(ts) {
    WX.ts = Math.max(0, Math.min(WX.subTimes.length - 1, ts));
    if (WX.mode === 'sub') {
      slider.value = WX.ts;
      const sp = spanOf(WX.ts), q = WX.subTimes[WX.ts];
      const sun = sp ? `${sp.label === 'morning' ? 'sunrise' : 'sunset'} ${WX.time.hm(new Date(sp.sun))}` : '';
      tlabel.innerHTML = `${WX.time.fullMin(q)}<span class="src">15-min · ${sun} · HRRR ${new Date(WX.meta.sub.run).getUTCHours()}Z</span>`;
      refresh();
    }
    WX.emit('subtime', WX.ts);
  }
  WX.setSubTime = setSubTime;
  const step = d => WX.mode === 'sub' ? setSubTime(WX.ts + d) : setTime(WX.t + d);
  slider.oninput = () => WX.mode === 'sub' ? setSubTime(+slider.value) : setTime(+slider.value);

  /* hourly <-> 15-minute mode */
  const modeSeg = document.getElementById('modeSeg');
  function setMode(m) {
    if (m === 'sub' && !(WX.meta.sub && WX.subTimes.length)) m = 'hourly';
    const was = WX.mode; WX.mode = m;
    modeSeg.querySelectorAll('button').forEach(b => b.classList.toggle('active', b.dataset.m === m));
    document.body.classList.toggle('sub-mode', m === 'sub');
    fillLayers(); syncOpacity();
    if (m === 'sub') {
      slider.max = WX.subTimes.length - 1;
      if (was !== 'sub') {   // jump to the 15-minute step nearest the hour being viewed (or now)
        const ref = WX.times[WX.t] < Date.now() - 3600e3 ? Date.now() : +WX.times[WX.t];
        let b = 0; WX.subTimes.forEach((q, i) => { if (Math.abs(q - ref) < Math.abs(WX.subTimes[b] - ref)) b = i; });
        WX.ts = b;
      }
      dayTicks(); setSubTime(WX.ts);
    } else {
      slider.max = WX.times.length - 1;
      if (was === 'sub') { const ref = WX.subTimes[WX.ts]; let b = 0; WX.times.forEach((d, i) => { if (Math.abs(d - ref) < Math.abs(WX.times[b] - ref)) b = i; }); WX.t = b; }
      dayTicks(); setTime(WX.t);
    }
    saveHash();
  }
  WX.setMode = setMode;
  modeSeg.querySelectorAll('button').forEach(b => (b.onclick = () => setMode(b.dataset.m)));
  function dayTicks() {
    const el = document.getElementById('dayTicks'); el.innerHTML = '';
    if (WX.mode === 'sub') {   // one tick per dawn/dusk window
      const S = WX.subTimes.length;
      WX.meta.sub.spans.forEach(sp => {
        const i = WX.subTimes.findIndex(q => +q === +new Date(sp.start)); if (i < 0) return;
        const s = document.createElement('span');
        s.style.left = (S > 1 ? i / (S - 1) * 100 : 0) + '%';
        s.textContent = `${WX.time.day(new Date(sp.start))} ${sp.label === 'morning' ? 'AM' : 'PM'}`;
        el.appendChild(s);
      });
      return;
    }
    const T = WX.times.length;
    const firstMidnight = WX.times.findIndex(d => WX.time.localHour(d) === 0);
    WX.times.forEach((d, t) => {
      // skip the start label when the first midnight is too close to it to fit both
      if (t === 0 && firstMidnight > 0 && firstMidnight / (T - 1) < 0.12) return;
      if (t === 0 || WX.time.localHour(d) === 0) {
        const s = document.createElement('span');
        s.style.left = (t / (T - 1) * 100) + '%'; s.textContent = WX.time.day(d);
        if (t / (T - 1) > 0.9) s.style.display = 'none';
        el.appendChild(s);
      }
    });
  }
  let playing = null;
  document.getElementById('playBtn').onclick = e => {
    if (playing) { clearInterval(playing); playing = null; e.currentTarget.textContent = '▶'; return; }
    e.currentTarget.textContent = '❚❚';
    playing = setInterval(() => WX.mode === 'sub'
      ? setSubTime(WX.ts + 1 >= WX.subTimes.length ? 0 : WX.ts + 1)
      : setTime(WX.t + 1 >= WX.times.length ? 0 : WX.t + 1), 650);
  };
  document.addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
    if (e.key === 'ArrowRight') step(1); if (e.key === 'ArrowLeft') step(-1);
  });

  /* ---------------------------------------------------------------- point selection */
  let marker = null;
  WX.selectPoint = function (lat, lon, name) {
    const c = WX.nearest(lat, lon);
    if (c.dist_km > 4) return;
    WX.point = Object.assign(c, { name: name || null, clickLat: lat, clickLon: lon });
    if (marker) marker.remove();
    marker = L.marker([c.lat, c.lon], { icon: L.divIcon({ className: '', html: '<div class="pt-marker"></div>', iconSize: [14, 14] }) }).addTo(map);
    document.body.classList.add('has-point');
    // keep the point visible: above the bottom sheet on phones, left of the drawer on desktop
    const sz = map.getSize(), pp = map.latLngToContainerPoint([c.lat, c.lon]);
    if (window.innerWidth <= 760) {
      const target = L.point(sz.x / 2, sz.y * 0.14);
      if (pp.y > sz.y * 0.25 || pp.y < 60) map.panBy(pp.subtract(target), { animate: true });
    } else {
      const drawer = Math.min(760, window.innerWidth * 0.58) + 20;
      if (pp.x > sz.x - drawer) map.panBy([pp.x - (sz.x - drawer) / 2, 0], { animate: true });
    }
    saveHash();
    WX.emit('point', WX.point);
  };
  WX.clearPoint = function () {
    WX.point = null; if (marker) marker.remove(); marker = null;
    document.body.classList.remove('has-point'); saveHash(); WX.emit('point', null);
  };
  map.on('click', e => WX.selectPoint(e.latlng.lat, e.latlng.lng));

  function addPlaces() {
    for (const p of WX.meta.places) {
      const m = L.circleMarker([p.lat, p.lon], { radius: 3.5, color: '#111', weight: 1, fillColor: '#fff', fillOpacity: 1 }).addTo(map);
      m.bindTooltip(p.name, { permanent: map.getZoom() >= 11, direction: 'right', className: 'place-label', offset: [4, 0] });
      m.on('click', ev => { L.DomEvent.stop(ev); WX.selectPoint(p.lat, p.lon, p.name); });
    }
    const b = WX.meta.grid.bbox;
    L.rectangle([[b.south, b.west], [b.north, b.east]], { color: '#111', weight: 1, dashArray: '4 4', fill: false, interactive: false }).addTo(map);
  }

  /* ---------------------------------------------------------------- hash state */
  function saveHash() {
    const p = new URLSearchParams();
    p.set('l', WX.layer); p.set('lv', WX.level);
    if (WX.point) p.set('p', `${WX.point.clickLat.toFixed(4)},${WX.point.clickLon.toFixed(4)}${WX.point.name ? ',' + WX.point.name : ''}`);
    if (WX.view && WX.view !== 'map') p.set('v', WX.view);
    if (WX.mode === 'sub') p.set('m', 'sub');
    history.replaceState(null, '', '#' + p.toString());
  }
  WX.saveHash = saveHash;
  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    if (p.get('l')) WX.layer = p.get('l'); if (p.get('lv')) WX.level = p.get('lv');
    return p;
  }

  /* ---------------------------------------------------------------- views & settings */
  function setView(v) {
    WX.view = v;
    document.querySelectorAll('#mainTabs button').forEach(b => b.classList.toggle('active', b.dataset.view === v));
    document.getElementById('mapView').classList.toggle('active', v === 'map');
    document.getElementById('outlookView').classList.toggle('active', v === 'outlook');
    if (v === 'map') setTimeout(() => map.invalidateSize(), 0);
    if (v === 'outlook') WX.emit('showOutlook');
    saveHash();
  }
  document.querySelectorAll('#mainTabs button').forEach(b => (b.onclick = () => setView(b.dataset.view)));

  const dlg = document.getElementById('settings');
  document.getElementById('settingsBtn').onclick = () => {
    document.getElementById('setUnits').value = WX.prefs.units;
    document.getElementById('setWind').value = WX.prefs.wind;
    document.getElementById('setTop').value = WX.prefs.top;
    document.getElementById('setBase').value = WX.prefs.base;
    document.getElementById('setToken').value = WX.prefs.ghToken || '';
    document.getElementById('setRepo').value = WX.prefs.ghRepo || repoGuess();
    dlg.showModal();
  };
  dlg.addEventListener('close', () => {
    const u = document.getElementById('setUnits').value, w = document.getElementById('setWind').value;
    const top = +document.getElementById('setTop').value, b = document.getElementById('setBase').value;
    WX.prefs.ghToken = document.getElementById('setToken').value.trim();
    WX.prefs.ghRepo = document.getElementById('setRepo').value.trim();
    const changed = u !== WX.prefs.units || w !== WX.prefs.wind || top !== WX.prefs.top;
    WX.prefs.units = u; WX.prefs.wind = w; WX.prefs.top = top;
    if (b !== WX.prefs.base) { WX.prefs.base = b; setBase(); }
    WX.setPref('units', u);
    if (changed) { fillLevels(); refresh(); WX.emit('units'); }
  });

  function runInfo() {
    const m = WX.meta, run = new Date(m.run);
    const age = Math.round((Date.now() - new Date(m.generated)) / 60000);
    const ageS = age < 90 ? `${age} min ago` : `${Math.round(age / 60)} h ago`;
    document.getElementById('runInfo').innerHTML =
      `HRRR ${String(run.getUTCHours()).padStart(2, '0')}Z ${WX.time.day(run)} · updated ${ageS}`;
  }

  /* ---------------------------------------------------------------- new data while the app is open */
  function toast(msg, ms = 5000) {
    let el = document.getElementById('toast');
    if (!el) { el = document.createElement('div'); el.id = 'toast'; document.body.appendChild(el); }
    el.innerHTML = msg; el.classList.add('show');
    clearTimeout(toast._t); toast._t = setTimeout(() => el.classList.remove('show'), ms);
  }
  WX.toast = toast;
  let checking = false;
  async function checkForUpdate(manual) {
    if (checking || !WX.meta) return false;
    checking = true;
    try {
      const r = await fetch('data/meta.json?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return false;
      const m = await r.json();
      if (m.generated === WX.meta.generated) { if (manual) toast('Already the newest forecast (HRRR ' + new Date(m.run).getUTCHours() + 'Z).'); return false; }
      // keep what you're looking at: same valid time, point and view
      const keepT = +WX.times[WX.t], keepS = WX.subTimes[WX.ts] ? +WX.subTimes[WX.ts] : null, pt = WX.point, mode = WX.mode;
      await WX.load();
      buildCorners();
      let b = 0; WX.times.forEach((d, i) => { if (Math.abs(d - keepT) < Math.abs(WX.times[b] - keepT)) b = i; }); WX.t = b;
      if (keepS !== null && WX.subTimes.length) { let c = 0; WX.subTimes.forEach((q, i) => { if (Math.abs(q - keepS) < Math.abs(WX.subTimes[c] - keepS)) c = i; }); WX.ts = c; }
      modeSeg.classList.toggle('hidden', !(WX.meta.sub && WX.subTimes.length));
      accCache.clear();
      setMode(mode);
      runInfo();
      if (pt) WX.selectPoint(pt.clickLat, pt.clickLon, pt.name);
      WX.emit('reloaded');
      toast(`Updated to the HRRR ${new Date(WX.meta.run).getUTCHours()}Z run.`);
      return true;
    } catch (e) { console.warn(e); return false; } finally { checking = false; }
  }
  WX.checkForUpdate = checkForUpdate;

  /* "Update now": start the GitHub Actions build (needs a token saved in Settings on this device) */
  function repoGuess() {
    if (WX.prefs.ghRepo) return WX.prefs.ghRepo;
    if (location.hostname.endsWith('.github.io')) return location.hostname.split('.')[0] + '/' + location.pathname.split('/')[1];
    return '';
  }
  document.getElementById('updateBtn').onclick = async () => {
    if (await checkForUpdate(false)) return;
    const repo = repoGuess();
    if (!WX.prefs.ghToken || !repo) {
      toast('The forecast rebuilds automatically every hour (~:55). To start a build from here, add a GitHub token in Settings.', 8000);
      return;
    }
    try {
      const r = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/update.yml/dispatches`, {
        method: 'POST', headers: { Authorization: 'Bearer ' + WX.prefs.ghToken, Accept: 'application/vnd.github+json' },
        body: JSON.stringify({ ref: 'main' }),
      });
      if (r.status === 204) {
        toast('Update started. New data usually appears in 5–10 minutes; the app will load it automatically.', 8000);
        let n = 0; const iv = setInterval(async () => { n++; if (await checkForUpdate() || n > 20) clearInterval(iv); }, 60e3);
      } else if (r.status === 401 || r.status === 403 || r.status === 404) {
        toast(`GitHub refused the request (${r.status}). Check the token has Actions: Read and write on ${WX.esc(repo)}.`, 9000);
      } else toast(`Couldn't start the update (GitHub ${r.status}).`);
    } catch (e) { toast('Couldn\'t reach GitHub.'); }
  };

  /* ---------------------------------------------------------------- boot */
  async function boot() {
    try {
      await WX.load();
    } catch (e) {
      document.getElementById('runInfo').textContent = 'No forecast data yet: run the ingest (see README)';
      console.error(e);
      return;
    }
    buildCorners();
    overlay.addTo(map);
    addPlaces();
    const p = readHash();
    fillLayers(); syncOpacity();
    slider.max = WX.times.length - 1;
    // start at the hour closest to now
    const now = Date.now();
    let t0 = 0; WX.times.forEach((d, t) => { if (Math.abs(d - now) < Math.abs(WX.times[t0] - now)) t0 = t; });
    modeSeg.classList.toggle('hidden', !(WX.meta.sub && WX.subTimes.length));
    dayTicks(); runInfo(); setInterval(runInfo, 60000);
    setTime(t0);
    if (p.get('m') === 'sub') setMode('sub');
    setInterval(checkForUpdate, 5 * 60e3);
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') checkForUpdate(); });
    if (p.get('p')) { const [la, lo, ...nm] = p.get('p').split(','); WX.selectPoint(+la, +lo, nm.join(',') || null); }
    if (p.get('v') === 'outlook') setView('outlook');
    WX.emit('ready');
  }
  boot();

  if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('sw.js').catch(() => {});
})();
