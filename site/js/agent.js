/* Agent view: chat with the WeatherAgent running on your Mac (its web.py, default http://localhost:8787). */
'use strict';
(function () {
  const { esc } = WX;
  const $ = id => document.getElementById(id);
  const log = $('agentLog'), form = $('agentForm'), box = $('agentQ'), send = $('agentSend'), status = $('agentStatus');
  const url = () => WX.prefs.agentUrl || 'http://localhost:8787';
  const newId = () => Math.random().toString(36).slice(2, 10);
  let conv = sessionStorage.getItem('agentConv') || newId(), online = null, busy = false, started = false;
  sessionStorage.setItem('agentConv', conv);

  // Small Markdown subset: headings, bold, italics, inline code, bullet and numbered lists, paragraphs.
  function md(text) {
    const inline = s => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<i>$2</i>');
    let html = '', list = null;
    const close = () => { if (list) { html += `</${list}>`; list = null; } };
    for (const block of text.split(/\n{2,}/)) {
      for (const line of block.split('\n')) {
        let m;
        if ((m = line.match(/^\s*[-*•]\s+(.*)/)) || (m = line.match(/^\s*\d+[.)]\s+(.*)/))) {
          const t = /^\s*\d/.test(line) ? 'ol' : 'ul';
          if (list !== t) { close(); html += `<${t}>`; list = t; }
          html += `<li>${inline(m[1])}</li>`;
        } else if ((m = line.match(/^#{1,4}\s+(.*)/))) { close(); html += `<h4>${inline(m[1])}</h4>`; }
        else if (line.trim()) { close(); html += `<p>${inline(line)}</p>`; }
      }
      close();
    }
    return html;
  }

  function add(cls, html) {
    const d = document.createElement('div');
    d.className = 'agent-msg ' + cls;
    d.innerHTML = html;
    log.appendChild(d);
    log.scrollTop = log.scrollHeight;
    return d;
  }

  function help() {
    return `<div class="agent-help"><p><b>The agent isn't reachable at ${esc(url())}.</b> It runs on your Mac, so start it there:</p>
      <p>Double-click <code>Agent Web.command</code> in your WeatherAgent folder, or run
      <code>.venv/bin/python web.py</code> there.</p>
      <p class="small">Then press <b>Check again</b>. To use it from another device, put the Mac behind an HTTPS tunnel
      (for example <code>tailscale serve 8787</code>) and paste that address under ⚙︎ Settings → Agent address.</p>
      <button class="agent-btn" type="button" id="agentRetry">Check again</button></div>`;
  }

  async function check() {
    status.textContent = 'checking…';
    try {
      const r = await fetch(url() + '/health', { cache: 'no-store' });
      const j = await r.json();
      online = !!j.ok;
      status.innerHTML = `<span class="dot-ok">●</span> on your Mac · ${esc(j.model)}`;
    } catch (e) {
      online = false;
      status.innerHTML = '<span class="dot-off">●</span> not running';
    }
    const h = log.querySelector('.agent-help');
    if (online && h) h.remove();
    if (!online && !h) { add('', help()).className = ''; }
    send.disabled = busy || !online;
    return online;
  }

  log.addEventListener('click', e => { if (e.target.id === 'agentRetry') check(); });

  async function ask(q) {
    busy = true; send.disabled = true;
    add('you', esc(q));
    const t0 = Date.now();
    const wait = add('bot', '<span class="muted">Pulling HRRR, observations and history… 0 s</span>');
    const tick = setInterval(() => { wait.querySelector('span').textContent = `Pulling HRRR, observations and history… ${Math.round((Date.now() - t0) / 1000)} s`; }, 1000);
    try {
      const r = await fetch(url() + '/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ conv, q }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
      const tools = j.tools.length ? `<details><summary>${j.tools.length} data pulls</summary>${j.tools.map(t => `<div>${esc(t)}</div>`).join('')}</details>` : '';
      wait.innerHTML = md(j.answer) + `<div class="agent-meta">${j.fid ? `Saved as forecast #${j.fid} · ` : ''}about $${j.cost.toFixed(2)} · ${Math.round((Date.now() - t0) / 1000)} s${tools}</div>`;
    } catch (e) {
      wait.className = 'agent-msg err';
      wait.textContent = e instanceof TypeError ? 'Lost the connection to the agent on your Mac.' : e.message;
      if (e instanceof TypeError) check();
    } finally {
      clearInterval(tick);
      busy = false; send.disabled = !online;
      log.scrollTop = log.scrollHeight;
    }
  }

  form.addEventListener('submit', e => {
    e.preventDefault();
    const q = box.value.trim();
    if (!q || busy || !online) return;
    box.value = '';
    ask(q);
  });
  box.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });

  $('agentNew').onclick = () => {
    if (busy) return;
    if (online) fetch(url() + '/new', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ conv }) }).catch(() => {});
    conv = newId(); sessionStorage.setItem('agentConv', conv);
    log.innerHTML = '';
    check();
  };

  WX.on('showAgent', () => { if (!started) { started = true; check(); } else if (!online) check(); setTimeout(() => box.focus(), 0); });
  WX.on('prefs', () => { if (started) check(); });
})();
