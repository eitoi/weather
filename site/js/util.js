/* Shared state, data access, units and color scales. */
'use strict';

const WX = window.WX = {
  meta: null, grid: null, lr: null,
  t: 0,                    // selected time index
  layer: 'wind', level: 'sfc',
  point: null,             // {j, i, lat, lon, name}
  prefs: loadPrefs(),
  maps: new Map(), pts: new Map(),
  listeners: {},
};

function loadPrefs() {
  const d = { units: 'imperial', wind: 'mph', top: 18000, base: 'topo', numbers: true, arrows: true };
  try { return Object.assign(d, JSON.parse(localStorage.getItem('wx-prefs') || '{}')); } catch (e) { return d; }
}
function savePrefs() { try { localStorage.setItem('wx-prefs', JSON.stringify(WX.prefs)); } catch (e) { /* private mode */ } }

WX.on = (ev, fn) => (WX.listeners[ev] = WX.listeners[ev] || []).push(fn);
WX.emit = (ev, ...a) => (WX.listeners[ev] || []).forEach(fn => fn(...a));

/* ---------------------------------------------------------------- data */

const DATA = 'data/';

async function fetchBin(path) {
  const r = await fetch(DATA + path, { cache: 'no-cache' });
  if (!r.ok) throw new Error(path + ' ' + r.status);
  return r.arrayBuffer();
}

WX.load = async function () {
  const r = await fetch(DATA + 'meta.json', { cache: 'no-cache' });
  if (!r.ok) throw new Error('meta.json ' + r.status);
  const meta = await r.json();
  const buf = await fetchBin('grid.bin?v=' + encodeURIComponent(meta.generated));
  const f = new Float32Array(buf), n = meta.grid.ny * meta.grid.nx;
  WX.meta = meta;
  WX.times = meta.times.map(s => new Date(s));
  WX.grid = { ny: meta.grid.ny, nx: meta.grid.nx, lat: f.subarray(0, n), lon: f.subarray(n, 2 * n), terrain: f.subarray(2 * n, 3 * n) };
  WX.fidx = Object.fromEntries(meta.point_fields.map((k, i) => [k, i]));
  WX.maps.clear(); WX.pts.clear();
  if (meta.longrange) {
    fetch(DATA + meta.longrange + '?v=' + encodeURIComponent(meta.generated)).then(r => r.ok ? r.json() : null)
      .then(j => { WX.lr = j; WX.emit('longrange'); }).catch(() => {});
  }
  return meta;
};

/* Map field: Float32Array [T][ny][nx] with NaN for missing. */
WX.getMap = function (key) {
  if (WX.maps.has(key)) return WX.maps.get(key);
  const p = fetchBin('maps/' + key.replace(':', '_') + '.bin?v=' + encodeURIComponent(WX.meta.generated)).then(buf => {
    const raw = new Int16Array(buf), s = WX.meta.fields[key].scale, out = new Float32Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw[i] === -32768 ? NaN : raw[i] * s;
    return out;
  });
  WX.maps.set(key, p);
  p.catch(() => WX.maps.delete(key));
  return p;
};

/* Point column: {get(key) -> Float32Array[T]} for grid cell (j, i). */
WX.getPoint = function (j, i) {
  const id = j + '_' + i;
  if (WX.pts.has(id)) return WX.pts.get(id);
  const p = fetchBin('pts/' + id + '.bin?v=' + encodeURIComponent(WX.meta.generated)).then(buf => {
    const raw = new Int16Array(buf), T = WX.times.length, fields = WX.meta.point_fields, cache = {};
    return {
      get(key) {
        if (cache[key]) return cache[key];
        const f = WX.fidx[key];
        const out = new Float32Array(T);
        if (f === undefined) { out.fill(NaN); return out; }
        const s = WX.meta.fields[key].scale;
        for (let t = 0; t < T; t++) { const v = raw[f * T + t]; out[t] = v === -32768 ? NaN : v * s; }
        return (cache[key] = out);
      }, fields,
    };
  });
  WX.pts.set(id, p);
  p.catch(() => WX.pts.delete(id));
  if (WX.pts.size > 40) WX.pts.delete(WX.pts.keys().next().value);
  return p;
};

/* Nearest HRRR grid cell to a lat/lon (brute force over ~1500 points). */
WX.nearest = function (lat, lon) {
  const g = WX.grid, c = Math.cos(lat * Math.PI / 180);
  let best = 1e9, bi = 0;
  for (let k = 0; k < g.lat.length; k++) {
    const d = (g.lat[k] - lat) ** 2 + ((g.lon[k] - lon) * c) ** 2;
    if (d < best) { best = d; bi = k; }
  }
  const j = Math.floor(bi / g.nx), i = bi % g.nx;
  return { j, i, k: bi, dist_km: Math.sqrt(best) * 111.2, lat: g.lat[bi], lon: g.lon[bi], elev: g.terrain[bi] };
};

/* ---------------------------------------------------------------- units */

const U = WX.U = {
  imperial: () => WX.prefs.units === 'imperial',
  temp(c) { return U.imperial() ? c * 9 / 5 + 32 : c; },
  tempU() { return U.imperial() ? '°F' : '°C'; },
  dtemp(c) { return U.imperial() ? c * 9 / 5 : c; },
  wind(ms) { return ms * { mph: 2.23694, kt: 1.94384, kmh: 3.6, ms: 1 }[WX.prefs.wind]; },
  windU() { return { mph: 'mph', kt: 'kt', kmh: 'km/h', ms: 'm/s' }[WX.prefs.wind]; },
  len(m) { return U.imperial() ? m * 3.28084 : m; },          // heights
  lenU() { return U.imperial() ? 'ft' : 'm'; },
  precip(mm) { return U.imperial() ? mm / 25.4 : mm; },
  precipU() { return U.imperial() ? 'in' : 'mm'; },
  snow(cm) { return U.imperial() ? cm / 2.54 : cm; },
  snowU() { return U.imperial() ? 'in' : 'cm'; },
  vis(km) { return U.imperial() ? km * 0.621371 : km; },
  visU() { return U.imperial() ? 'mi' : 'km'; },
  ftFromM: m => m * 3.28084,
};

WX.fmt = function (v, d = 0) {
  if (v === null || v === undefined || !isFinite(v)) return '–';
  return (Math.abs(v) < 1e-9 ? 0 : v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
};
WX.dirName = function (deg) {
  if (!isFinite(deg)) return '–';
  return ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'][Math.round(((deg % 360) + 360) % 360 / 22.5) % 16];
};
WX.wspd = (u, v) => Math.hypot(u, v);
WX.wdir = (u, v) => (Math.atan2(-u, -v) * 180 / Math.PI + 360) % 360;
/* omega (Pa/s) -> w (m/s) using T (C) and p (hPa). Positive = rising. */
WX.omegaToW = (omega, tc, p) => -omega * 287.05 * (tc + 273.15) / (p * 100 * 9.81);

const TZ = 'America/Denver';
const _fmts = {};
function tf(opts) { const k = JSON.stringify(opts); return _fmts[k] || (_fmts[k] = new Intl.DateTimeFormat('en-US', Object.assign({ timeZone: TZ }, opts))); }
WX.time = {
  full: d => tf({ weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric' }).format(d),
  hour: d => tf({ hour: 'numeric' }).format(d).replace(' ', '').toLowerCase(),
  day: d => tf({ weekday: 'short', day: 'numeric' }).format(d),
  dayLong: d => tf({ weekday: 'long', month: 'short', day: 'numeric' }).format(d),
  hm: d => tf({ hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }).format(d),
  localHour: d => +tf({ hour: 'numeric', hourCycle: 'h23' }).format(d),
  dateKey: d => tf({ year: 'numeric', month: '2-digit', day: '2-digit' }).format(d),
};

/* ---------------------------------------------------------------- color scales
   Sequential = one hue light->dark; diverging = two hues around a gray midpoint. */

function hex(h) { return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]; }
const RAMPS = {
  blue: ['#eef5fd', '#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#2a78d6', '#1c5cab', '#104281', '#0d366b'],
  red: ['#fbeceb', '#f6cfcd', '#efa6a4', '#e8807e', '#e34948', '#c73232', '#a32424', '#7d1a1a', '#5c1212'],
  aqua: ['#e6f6ef', '#bfe9d6', '#8fd9b8', '#55c695', '#1baf7a', '#14936a', '#0f7556', '#0b5a3d', '#08412c'],
  violet: ['#efedfb', '#d9d4f6', '#b9b0ef', '#978ae6', '#7a69de', '#6250d6', '#4d3db4', '#3a2d8c', '#2b2073'],
  orange: ['#fdf0ea', '#fad6c6', '#f6b597', '#f19267', '#eb6834', '#cf5422', '#a9431b', '#813314', '#5e250e'],
  gray: ['#ffffff', '#eeeeec', '#d9d9d6', '#c2c2be', '#a8a8a4', '#8e8e8a', '#757571', '#5c5c59', '#454542'],
};
const MID = '#f0efec';

function rampScale(name, lo, hi, opts = {}) {
  const stops = RAMPS[name].map(hex);
  return {
    lo, hi, kind: 'seq', ramp: name,
    color(v) {
      if (!isFinite(v)) return null;
      if (opts.clearBelow !== undefined && v < opts.clearBelow) return null;
      let x = (v - lo) / (hi - lo); x = Math.max(0, Math.min(1, x)) * (stops.length - 1);
      const k = Math.min(Math.floor(x), stops.length - 2), f = x - k, a = stops[k], b = stops[k + 1];
      return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
    },
  };
}
function divScale(negRamp, posRamp, lo, mid, hi) {
  const neg = [hex(MID), ...RAMPS[negRamp].slice(2).map(hex)], pos = [hex(MID), ...RAMPS[posRamp].slice(2).map(hex)];
  const interp = (stops, x) => {
    x = Math.max(0, Math.min(1, x)) * (stops.length - 1);
    const k = Math.min(Math.floor(x), stops.length - 2), f = x - k, a = stops[k], b = stops[k + 1];
    return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
  };
  return {
    lo, hi, mid, kind: 'div',
    color(v) { if (!isFinite(v)) return null; return v < mid ? interp(neg, (mid - v) / (mid - lo)) : interp(pos, (v - mid) / (hi - mid)); },
  };
}
WX.scales = { rampScale, divScale, RAMPS };
WX.rgb = c => c ? `rgb(${c[0] | 0},${c[1] | 0},${c[2] | 0})` : 'transparent';
WX.cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

WX.setPref = function (k, v) { WX.prefs[k] = v; savePrefs(); WX.emit('prefs', k); };

WX.html = (s) => { const t = document.createElement('template'); t.innerHTML = s.trim(); return t.content.firstChild; };
WX.esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
