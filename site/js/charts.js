/* Small chart kit: time-series panels (SVG), time-height section (canvas), vertical profile (SVG). */
'use strict';
(function () {
  const { fmt } = WX;
  const NS = 'http://www.w3.org/2000/svg';
  const el = (tag, attrs = {}, parent) => {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  };
  const css = WX.cssVar;

  function niceTicks(lo, hi, n = 4) {
    const span = hi - lo || 1, raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(s => s * mag).find(s => s >= raw) || raw;
    const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
    return { ticks: out, step };
  }

  /* Shared hover across panels */
  let hoverT = null;
  const hoverSubs = new Set();
  function setHover(t) { hoverT = t; hoverSubs.forEach(f => f(t)); }

  /* ------------------------------------------------------------ time-series panel
     opts: {title, height, series:[{name, color, values, type:'line'|'bar'|'step', width, dash, fmt}],
            unit, yMin, yMax, zeroBase, arrows:{u:[], v:[]}, dec, tipExtra(t)} */
  function panel(container, opts) {
    const T = WX.times.length;
    const wrap = document.createElement('div'); wrap.className = 'chart';
    if (opts.title) {
      const h = document.createElement('div'); h.className = 'chart-title';
      h.innerHTML = opts.title; container.appendChild(h);
    }
    // legend for >= 2 series
    if (opts.series.length >= 2) {
      const lg = document.createElement('div'); lg.className = 'legend-row';
      lg.innerHTML = opts.series.map(s => `<span><span class="sw ${s.type === 'bar' ? 'box' : ''}" style="background:${s.color}"></span>${WX.esc(s.name)}</span>`).join('');
      container.appendChild(lg);
    }
    container.appendChild(wrap);
    const W = Math.max(300, container.clientWidth || 600), H = opts.height || 130;
    const m = { l: 44, r: 10, t: 8, b: opts.noX ? 6 : 20 };
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, height: H, role: 'img', 'aria-label': opts.title || '' }, wrap);
    const x = t => m.l + (W - m.l - m.r) * (T === 1 ? 0.5 : t / (T - 1));
    let lo = Infinity, hi = -Infinity;
    for (const s of opts.series) for (const v of s.values) if (isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    if (opts.zeroBase) lo = Math.min(0, lo);
    if (opts.yMin !== undefined) lo = Math.min(lo, opts.yMin);
    if (opts.yMax !== undefined) hi = Math.max(hi, opts.yMax);
    if (opts.fixed) { lo = opts.yMin; hi = opts.yMax; }
    if (hi - lo < (opts.minSpan || 1e-6)) { const c = (hi + lo) / 2; lo = c - (opts.minSpan || 1) / 2; hi = c + (opts.minSpan || 1) / 2; if (opts.zeroBase) { lo = Math.max(0, lo); } }
    const pad = (hi - lo) * 0.08; if (!opts.fixed) { hi += pad; if (!opts.zeroBase) lo -= pad; }
    const y = v => m.t + (H - m.t - m.b) * (1 - (v - lo) / (hi - lo));
    // grid
    const g = el('g', {}, svg);
    for (const v of niceTicks(lo, hi, opts.ticks || 3).ticks) {
      el('line', { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: css('--grid'), 'stroke-width': 1 }, g);
      const tx = el('text', { x: m.l - 6, y: y(v) + 4, 'text-anchor': 'end', 'font-size': 10, fill: css('--text-muted') }, g);
      tx.textContent = fmt(v, opts.tickDec ?? (Math.abs(hi - lo) < 3 ? 1 : 0));
    }
    // days; hour labels only when there is room for them
    const pxPerHour = (W - m.l - m.r) / Math.max(1, T - 1);
    const labelEvery = pxPerHour * 6 >= 34 ? 6 : pxPerHour * 12 >= 34 ? 12 : 24;
    WX.times.forEach((d, t) => {
      const h = WX.time.localHour(d);
      if (h === 0) el('line', { x1: x(t), x2: x(t), y1: m.t, y2: H - m.b, stroke: css('--border'), 'stroke-width': 1 }, g);
      const lbl = opts.xLabel ? opts.xLabel(d, t) : (h % labelEvery === 0 ? (h === 0 ? WX.time.day(d) : WX.time.hour(d)) : null);
      if (!opts.noX && lbl) {
        const tx = el('text', { x: x(t), y: H - 6, 'text-anchor': 'middle', 'font-size': 10, fill: css('--text-muted') }, g);
        tx.textContent = lbl;
      }
      // night shading
      if (opts.night !== false && (h >= 20 || h < 7)) {
        el('rect', { x: x(t) - (W - m.l - m.r) / (T - 1) / 2, width: (W - m.l - m.r) / (T - 1), y: m.t, height: H - m.t - m.b, fill: css('--text-primary'), opacity: 0.035 }, g);
      }
    });
    if (opts.unit) {
      const tx = el('text', { x: 4, y: m.t + 9, 'font-size': 10, fill: css('--text-muted') }, g); tx.textContent = opts.unit;
    }
    // series
    const bw = Math.max(2, (W - m.l - m.r) / T - 2);
    for (const s of opts.series) {
      if (s.type === 'bar') {
        s.values.forEach((v, t) => {
          if (!(v > 0)) return;
          const top = y(v), base = y(Math.max(lo, 0)), hgt = Math.max(1, base - top);
          const r = Math.min(4, bw / 2, hgt);
          // rounded data-end at the top, anchored to the baseline
          el('path', { d: `M${x(t) - bw / 2},${base} V${top + r} Q${x(t) - bw / 2},${top} ${x(t) - bw / 2 + r},${top} H${x(t) + bw / 2 - r} Q${x(t) + bw / 2},${top} ${x(t) + bw / 2},${top + r} V${base} Z`, fill: s.color }, svg);
        });
      } else if (s.type === 'band') {
        let d = '', d2 = '';
        s.values.forEach((v, t) => { if (isFinite(v) && isFinite(s.lower[t])) { d += (d ? 'L' : 'M') + x(t) + ',' + y(v); } });
        for (let t = T - 1; t >= 0; t--) if (isFinite(s.values[t]) && isFinite(s.lower[t])) d2 += 'L' + x(t) + ',' + y(s.lower[t]);
        if (d) el('path', { d: d + d2 + 'Z', fill: s.color, opacity: 0.18 }, svg);
      } else {
        let d = '', pen = false;
        s.values.forEach((v, t) => {
          if (!isFinite(v)) { pen = false; return; }
          d += (pen ? 'L' : 'M') + x(t).toFixed(1) + ',' + y(v).toFixed(1); pen = true;
        });
        el('path', { d, fill: 'none', stroke: s.color, 'stroke-width': s.width || 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round', 'stroke-dasharray': s.dash || '' }, svg);
      }
    }
    // wind direction arrows along the top
    if (opts.arrows) {
      const stride = Math.max(1, Math.ceil(18 / ((W - m.l - m.r) / T)));
      for (let t = 0; t < T; t += stride) {
        const u = opts.arrows.u[t], v = opts.arrows.v[t]; if (!isFinite(u)) continue;
        const s = Math.hypot(u, v); if (s < 0.3) continue;
        const dx = u / s, dy = -v / s, cx = x(t), cy = m.t + 7, l = 6;
        el('path', { d: `M${cx - dx * l},${cy - dy * l} L${cx + dx * l},${cy + dy * l} M${cx + dx * l},${cy + dy * l} l${-dx * 4 - dy * 3},${-dy * 4 + dx * 3} M${cx + dx * l},${cy + dy * l} l${-dx * 4 + dy * 3},${-dy * 4 - dx * 3}`, stroke: css('--text-secondary'), 'stroke-width': 1.4, fill: 'none', 'stroke-linecap': 'round' }, svg);
      }
    }
    // selected time + hover crosshair
    const sel = el('line', { y1: m.t, y2: H - m.b, stroke: css('--accent'), 'stroke-width': 1.5 }, svg);
    const cross = el('line', { y1: m.t, y2: H - m.b, stroke: css('--text-secondary'), 'stroke-width': 1, 'stroke-dasharray': '3 3', visibility: 'hidden' }, svg);
    const tip = document.createElement('div'); tip.className = 'tooltip hidden'; wrap.appendChild(tip);
    const setSel = t => {
      sel.setAttribute('visibility', t >= 0 && t < T ? 'visible' : 'hidden');
      sel.setAttribute('x1', x(t)); sel.setAttribute('x2', x(t));
    };
    setSel(WX.t);
    const onHover = t => {
      if (t === null) { cross.setAttribute('visibility', 'hidden'); tip.classList.add('hidden'); return; }
      cross.setAttribute('x1', x(t)); cross.setAttribute('x2', x(t)); cross.setAttribute('visibility', 'visible');
    };
    hoverSubs.add(onHover);
    const tToX = (ev) => {
      const r = svg.getBoundingClientRect();
      const px = (ev.clientX - r.left) * W / r.width;
      return Math.max(0, Math.min(T - 1, Math.round((px - m.l) / (W - m.l - m.r) * (T - 1))));
    };
    const showTip = (ev) => {
      const t = tToX(ev); setHover(t);
      const rows = opts.series.filter(s => s.type !== 'band').map(s => `<div><span class="sw ${s.type === 'bar' ? 'box' : ''}" style="display:inline-block;width:10px;height:${s.type === 'bar' ? 10 : 3}px;background:${s.color};margin-right:5px;vertical-align:middle"></span>${WX.esc(s.name)}: <b>${s.fmt ? s.fmt(s.values[t], t) : fmt(s.values[t], opts.dec ?? 0)}</b> ${opts.unit || ''}</div>`).join('');
      tip.innerHTML = `<div class="t">${(opts.tipTime || WX.time.full)(WX.times[t])}</div>${rows}${opts.tipExtra ? opts.tipExtra(t) : ''}`;
      tip.classList.remove('hidden');
      const r = wrap.getBoundingClientRect(), px = ev.clientX - r.left;
      tip.style.left = Math.min(Math.max(0, px + 12), r.width - tip.offsetWidth - 4) + 'px';
      tip.style.top = (px > r.width - tip.offsetWidth - 20 ? 4 : 4) + 'px';
      if (px + 12 + tip.offsetWidth > r.width) tip.style.left = Math.max(0, px - tip.offsetWidth - 12) + 'px';
    };
    svg.addEventListener('pointermove', showTip);
    svg.addEventListener('pointerdown', showTip);
    svg.addEventListener('pointerleave', () => setHover(null));
    if (!opts.noClick) svg.addEventListener('click', ev => WX.setTime(tToX(ev)));
    return { setSel, destroy: () => hoverSubs.delete(onHover) };
  }

  /* ------------------------------------------------------------ time-height section
     colorBy: 'wind' | 'temp' | 'rh' | 'w' */
  function timeHeight(container, pt, cell, opts) {
    const T = WX.times.length, levels = WX.meta.levels;  // high pressure (low alt) first
    const wrap = document.createElement('div'); wrap.className = 'chart'; container.appendChild(wrap);
    const W = Math.max(320, container.clientWidth || 600), H = opts.height || 380;
    const m = { l: 50, r: 10, t: 10, b: 22 };
    const cv = document.createElement('canvas'); const dpr = window.devicePixelRatio || 1;
    cv.width = W * dpr; cv.height = H * dpr; cv.style.height = H + 'px'; wrap.appendChild(cv);
    const ctx = cv.getContext('2d'); ctx.scale(dpr, dpr);
    const tip = document.createElement('div'); tip.className = 'tooltip hidden'; wrap.appendChild(tip);

    const L = levels.length;
    const get = (v, l) => pt.get(`${v}:${levels[l]}`);
    const Z = levels.map((_, l) => get('HGT', l)), TT = levels.map((_, l) => get('TMP', l)), RH = levels.map((_, l) => get('RH', l));
    const UU = levels.map((_, l) => get('UGRD', l)), VV = levels.map((_, l) => get('VGRD', l)), OM = levels.map((_, l) => get('VVEL', l));
    const DP = levels.map((_, l) => get('DPT', l));
    const terr = cell.elev, pbl = pt.get('HPBL');
    const topM = opts.topFt / 3.28084, botM = Math.max(0, terr - 250);
    const x = t => m.l + (W - m.l - m.r) * (t + 0.5) / T, cw = (W - m.l - m.r) / T;
    const y = z => m.t + (H - m.t - m.b) * (1 - (z - botM) / (topM - botM));
    const S = WX.scales;
    const sc = { wind: S.rampScale('blue', 0, 30), temp: S.divScale('blue', 'red', -30, 0, 20), rh: S.rampScale('aqua', 0, 100), w: S.divScale('blue', 'red', -1.5, 0, 1.5) }[opts.colorBy];
    const val = (l, t) => {
      if (opts.colorBy === 'wind') return Math.hypot(UU[l][t], VV[l][t]);
      if (opts.colorBy === 'temp') return TT[l][t];
      if (opts.colorBy === 'rh') return RH[l][t];
      return WX.omegaToW(OM[l][t], TT[l][t], levels[l]);
    };
    ctx.fillStyle = css('--surface-1'); ctx.fillRect(0, 0, W, H);
    ctx.save(); ctx.beginPath(); ctx.rect(m.l, m.t, W - m.l - m.r, H - m.t - m.b); ctx.clip();
    for (let t = 0; t < T; t++) {
      for (let l = 0; l < L; l++) {
        const z = Z[l][t]; if (!isFinite(z)) continue;
        const zb = l > 0 && isFinite(Z[l - 1][t]) ? (z + Z[l - 1][t]) / 2 : z - 120;
        const zt = l < L - 1 && isFinite(Z[l + 1][t]) ? (z + Z[l + 1][t]) / 2 : z + 150;
        if (zt < terr || zb > topM) continue;
        const col = sc.color(val(l, t)); if (!col) continue;
        ctx.fillStyle = WX.rgb(col);
        ctx.fillRect(x(t) - cw / 2 - 0.3, y(zt), cw + 0.6, y(Math.max(zb, terr)) - y(zt) + 0.5);
      }
    }
    // cloud hatch: RH >= 90 %
    if (opts.clouds) {
      ctx.strokeStyle = 'rgba(255,255,255,.75)'; ctx.lineWidth = 1;
      for (let t = 0; t < T; t++) for (let l = 0; l < L; l++) {
        if (!(RH[l][t] >= 90)) continue;
        const z = Z[l][t];
        const zb = l > 0 ? (z + Z[l - 1][t]) / 2 : z - 120, zt = l < L - 1 ? (z + Z[l + 1][t]) / 2 : z + 150;
        if (zt < terr) continue;
        const x0 = x(t) - cw / 2, y0 = y(zt), y1 = y(Math.max(zb, terr));
        ctx.save(); ctx.beginPath(); ctx.rect(x0, y0, cw, y1 - y0); ctx.clip();
        ctx.beginPath();
        for (let k = -40; k < cw + 40; k += 5) { ctx.moveTo(x0 + k, y1); ctx.lineTo(x0 + k + (y1 - y0), y0); }
        ctx.stroke(); ctx.restore();
      }
    }
    // 0 °C line
    const frz = [];
    for (let t = 0; t < T; t++) {
      let zf = NaN;
      for (let l = L - 2; l >= 0; l--) {  // from top down: first crossing to above freezing
        const a = TT[l][t], b = TT[l + 1][t];
        if (a >= 0 && b < 0) { zf = Z[l][t] + (Z[l + 1][t] - Z[l][t]) * (a / (a - b)); break; }
      }
      frz.push(zf);
    }
    const line = (arr, color, dash, width) => {
      ctx.strokeStyle = color; ctx.setLineDash(dash); ctx.lineWidth = width; ctx.beginPath();
      let pen = false; arr.forEach((z, t) => { if (!isFinite(z)) { pen = false; return; } pen ? ctx.lineTo(x(t), y(z)) : ctx.moveTo(x(t), y(z)); pen = true; });
      ctx.stroke(); ctx.setLineDash([]);
    };
    line(frz, '#ffffff', [], 4); line(frz, '#111111', [6, 4], 2);
    // boundary layer top
    const pblTop = Array.from(pbl, v => v + terr);
    line(pblTop, '#ffffff', [], 4); line(pblTop, css('--s2'), [2, 3], 2);
    // wind arrows
    const strideT = Math.max(1, Math.ceil(22 / cw));
    for (let t = 0; t < T; t += strideT) {
      let lastY = 1e9;
      for (let l = 0; l < L; l++) {
        const z = Z[l][t]; if (!(z > terr + 50) || z > topM) continue;
        const py = y(z); if (lastY - py < 18) continue; lastY = py;
        const u = UU[l][t], v = VV[l][t], s = Math.hypot(u, v); if (!(s > 0.3)) continue;
        const dx = u / s, dy = -v / s, len = 5 + Math.min(1, s / 25) * 6, cx = x(t);
        for (const [w, col] of [[3, 'rgba(255,255,255,.85)'], [1.3, '#111']]) {
          ctx.strokeStyle = col; ctx.lineWidth = w; ctx.lineCap = 'round'; ctx.beginPath();
          ctx.moveTo(cx - dx * len, py - dy * len); ctx.lineTo(cx + dx * len, py + dy * len);
          ctx.moveTo(cx + dx * len, py + dy * len); ctx.lineTo(cx + dx * len - dx * 4 - dy * 3, py + dy * len - dy * 4 + dx * 3);
          ctx.moveTo(cx + dx * len, py + dy * len); ctx.lineTo(cx + dx * len - dx * 4 + dy * 3, py + dy * len - dy * 4 - dx * 3);
          ctx.stroke();
        }
        if (opts.numbers && cw * strideT >= 24) {
          ctx.font = '600 9px -apple-system, system-ui, sans-serif'; ctx.textAlign = 'center';
          const txt = fmt(WX.U.wind(s), 0);
          ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(255,255,255,.85)'; ctx.strokeText(txt, cx, py + 13); ctx.fillStyle = '#111'; ctx.fillText(txt, cx, py + 13);
        }
      }
    }
    // terrain
    ctx.fillStyle = css('--terrain'); ctx.fillRect(m.l, y(terr), W - m.l - m.r, H - m.b - y(terr));
    ctx.restore();
    // axes
    ctx.fillStyle = css('--text-muted'); ctx.font = '10px -apple-system, system-ui, sans-serif'; ctx.textAlign = 'right';
    const imp = WX.U.imperial(), stepU = imp ? (opts.topFt > 26000 ? 5000 : 2000) : (opts.topFt > 26000 ? 1500 : 500);
    for (let v = Math.ceil(WX.U.len(botM) / stepU) * stepU; v <= WX.U.len(topM); v += stepU) {
      const zm = imp ? v / 3.28084 : v; const py = y(zm);
      ctx.fillText(fmt(v), m.l - 5, py + 3);
      ctx.strokeStyle = 'rgba(0,0,0,.08)'; ctx.beginPath(); ctx.moveTo(m.l, py); ctx.lineTo(W - m.r, py); ctx.stroke();
    }
    ctx.save(); ctx.translate(11, m.t + (H - m.t - m.b) / 2); ctx.rotate(-Math.PI / 2); ctx.textAlign = 'center';
    ctx.fillText(`Altitude MSL (${WX.U.lenU()})`, 0, 0); ctx.restore();
    ctx.textAlign = 'center';
    WX.times.forEach((d, t) => {
      const h = WX.time.localHour(d);
      if (h % 6 === 0) ctx.fillText(h === 0 ? WX.time.day(d) : WX.time.hour(d), x(t), H - 7);
      if (h === 0) { ctx.strokeStyle = css('--text-primary'); ctx.globalAlpha = 0.35; ctx.beginPath(); ctx.moveTo(x(t) - cw / 2, m.t); ctx.lineTo(x(t) - cw / 2, H - m.b); ctx.stroke(); ctx.globalAlpha = 1; }
    });
    // selected time
    const base = ctx.getImageData(0, 0, cv.width, cv.height);
    const drawSel = (t, ht) => {
      ctx.putImageData(base, 0, 0);
      ctx.strokeStyle = css('--accent'); ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(x(t), m.t); ctx.lineTo(x(t), H - m.b); ctx.stroke();
      if (ht !== null && ht !== undefined) { ctx.strokeStyle = css('--text-secondary'); ctx.setLineDash([3, 3]); ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x(ht), m.t); ctx.lineTo(x(ht), H - m.b); ctx.stroke(); ctx.setLineDash([]); }
    };
    drawSel(WX.t);
    const pos = ev => {
      const r = cv.getBoundingClientRect(), px = (ev.clientX - r.left) * W / r.width, py = (ev.clientY - r.top) * H / r.height;
      const t = Math.max(0, Math.min(T - 1, Math.floor((px - m.l) / cw)));
      const z = botM + (1 - (py - m.t) / (H - m.t - m.b)) * (topM - botM);
      return { t, z, px, py };
    };
    cv.addEventListener('pointermove', ev => {
      const { t, z, px } = pos(ev);
      let best = 0; for (let l = 0; l < L; l++) if (Math.abs(Z[l][t] - z) < Math.abs(Z[best][t] - z)) best = l;
      const l = best, u = UU[l][t], v = VV[l][t];
      const below = Z[l][t] < terr;
      tip.innerHTML = `<div class="t">${WX.time.full(WX.times[t])} · ${levels[l]} mb</div>` + (below ? '<div>Below model terrain</div>' :
        `<div>Height <b>${fmt(WX.U.len(Z[l][t]))} ${WX.U.lenU()}</b></div>
         <div>Wind <b>${fmt(WX.U.wind(Math.hypot(u, v)))} ${WX.U.windU()}</b> from ${WX.dirName(WX.wdir(u, v))} (${fmt(WX.wdir(u, v))}°)</div>
         <div>Temp <b>${fmt(WX.U.temp(TT[l][t]), 1)}${WX.U.tempU()}</b> · Dew pt ${fmt(WX.U.temp(DP[l][t]), 1)}${WX.U.tempU()} · RH ${fmt(RH[l][t])}%</div>
         <div>Vertical ${fmt(WX.U.imperial() ? WX.omegaToW(OM[l][t], TT[l][t], levels[l]) * 196.85 : WX.omegaToW(OM[l][t], TT[l][t], levels[l]), WX.U.imperial() ? 0 : 2)} ${WX.U.imperial() ? 'ft/min' : 'm/s'}</div>`);
      tip.classList.remove('hidden');
      const r = wrap.getBoundingClientRect(), cx = ev.clientX - r.left;
      tip.style.left = (cx + 14 + tip.offsetWidth > r.width ? cx - tip.offsetWidth - 14 : cx + 14) + 'px';
      tip.style.top = Math.max(0, ev.clientY - r.top - 30) + 'px';
      drawSel(WX.t, t);
    });
    cv.addEventListener('pointerleave', () => { tip.classList.add('hidden'); drawSel(WX.t); });
    cv.addEventListener('click', ev => WX.setTime(pos(ev).t));
    return { setSel: t => drawSel(t), scale: sc };
  }

  /* ------------------------------------------------------------ vertical profile at one time (SVG) */
  function profile(container, pt, cell, t, opts) {
    const levels = WX.meta.levels, L = levels.length;
    const get = (v, l) => pt.get(`${v}:${levels[l]}`)[t];
    const rows = levels.map((p, l) => ({ p, z: get('HGT', l), T: get('TMP', l), D: get('DPT', l), u: get('UGRD', l), v: get('VGRD', l) }))
      .filter(r => isFinite(r.z) && r.z >= cell.elev - 1 && r.z * 3.28084 <= opts.topFt + 500);
    const W = Math.max(300, container.clientWidth || 500), H = opts.height || 360;
    const m = { l: 50, r: 46, t: 10, b: 26 };
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, height: H, role: 'img', 'aria-label': 'Temperature and dew point profile' });
    const wrap = document.createElement('div'); wrap.className = 'chart'; wrap.appendChild(svg); container.appendChild(wrap);
    if (!rows.length) return;
    const topM = opts.topFt / 3.28084, botM = Math.max(0, cell.elev - 200);
    const temps = rows.flatMap(r => [WX.U.temp(r.T), WX.U.temp(r.D)]).filter(isFinite);
    let lo = Math.min(...temps), hi = Math.max(...temps); const pad = (hi - lo) * 0.08 + 2; lo -= pad; hi += pad;
    const x = v => m.l + (W - m.l - m.r) * (v - lo) / (hi - lo), y = z => m.t + (H - m.t - m.b) * (1 - (z - botM) / (topM - botM));
    for (const v of niceTicks(lo, hi, 5).ticks) {
      el('line', { x1: x(v), x2: x(v), y1: m.t, y2: H - m.b, stroke: css('--grid') }, svg);
      const tx = el('text', { x: x(v), y: H - 10, 'text-anchor': 'middle', 'font-size': 10, fill: css('--text-muted') }, svg); tx.textContent = fmt(v);
    }
    const fz = WX.U.temp(0);
    el('line', { x1: x(fz), x2: x(fz), y1: m.t, y2: H - m.b, stroke: css('--s1'), 'stroke-dasharray': '4 3', 'stroke-width': 1.2 }, svg);
    const imp = WX.U.imperial(), stepU = imp ? 2000 : 500;
    for (let v = Math.ceil(WX.U.len(botM) / stepU) * stepU; v <= WX.U.len(topM); v += stepU) {
      const py = y(imp ? v / 3.28084 : v);
      el('line', { x1: m.l, x2: W - m.r, y1: py, y2: py, stroke: css('--grid') }, svg);
      const tx = el('text', { x: m.l - 5, y: py + 3, 'text-anchor': 'end', 'font-size': 10, fill: css('--text-muted') }, svg); tx.textContent = fmt(v);
    }
    const path = (key, color) => el('path', { d: rows.map((r, i) => (i ? 'L' : 'M') + x(WX.U.temp(r[key])) + ',' + y(r.z)).join(''), fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-linejoin': 'round' }, svg);
    path('D', css('--s3')); path('T', css('--s8'));
    for (const r of rows) {
      for (const [key, color] of [['D', css('--s3')], ['T', css('--s8')]]) el('circle', { cx: x(WX.U.temp(r[key])), cy: y(r.z), r: 2.5, fill: color }, svg);
      const s = Math.hypot(r.u, r.v); if (!(s > 0.3)) continue;
      const dx = r.u / s, dy = -r.v / s, cx = W - m.r + 22, cy = y(r.z), len = 5 + Math.min(1, s / 25) * 6;
      el('path', { d: `M${cx - dx * len},${cy - dy * len}L${cx + dx * len},${cy + dy * len}M${cx + dx * len},${cy + dy * len}l${-dx * 4 - dy * 3},${-dy * 4 + dx * 3}M${cx + dx * len},${cy + dy * len}l${-dx * 4 + dy * 3},${-dy * 4 - dx * 3}`, stroke: css('--text-primary'), 'stroke-width': 1.4, fill: 'none', 'stroke-linecap': 'round' }, svg);
    }
    el('rect', { x: m.l, y: y(cell.elev), width: W - m.l - m.r, height: Math.max(0, H - m.b - y(cell.elev)), fill: css('--terrain') }, svg);
    const ttl = el('text', { x: m.l + 4, y: m.t + 10, 'font-size': 10, fill: css('--text-muted') }, svg); ttl.textContent = `${WX.U.tempU()}  ·  altitude ${WX.U.lenU()} MSL`;
  }

  WX.charts = { panel, timeHeight, profile, setHover };
})();
