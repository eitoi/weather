/* Login: when the build encrypted the data (auth.json exists), ask for the username and password, derive the AES key
   and decrypt every data/ response in place, so the rest of the app fetches as usual. Loaded before the other scripts. */
'use strict';

(function () {
  const STORE = 'wx-key', realFetch = window.fetch.bind(window);
  const b64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
  const toB64 = buf => btoa(String.fromCharCode(...new Uint8Array(buf)));
  const isData = input => {
    const u = new URL(input instanceof Request ? input.url : input, location.href);
    return u.origin === location.origin && u.pathname.includes('/data/');
  };
  const decrypt = (key, buf) => crypto.subtle.decrypt({ name: 'AES-GCM', iv: buf.slice(0, 12) }, key, buf.slice(12));

  let auth = null, unlock;
  const ready = new Promise(res => { unlock = res; });   // resolves with the key, or null when the site is open

  async function tryKey(key) {
    try { return new TextDecoder().decode(await decrypt(key, b64(auth.check).buffer)) === 'wasatch-wx'; } catch (e) { return false; }
  }
  async function derive(user, pw) {
    const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(user + '\n' + pw), 'PBKDF2', false, ['deriveKey']);
    return crypto.subtle.deriveKey({ name: 'PBKDF2', hash: 'SHA-256', salt: b64(auth.salt), iterations: auth.iterations },
      base, { name: 'AES-GCM', length: 256 }, true, ['decrypt']);
  }
  const importRaw = raw => crypto.subtle.importKey('raw', b64(raw), 'AES-GCM', true, ['decrypt']);

  window.fetch = async function (input, init) {
    if (!isData(input)) return realFetch(input, init);
    const key = await ready;
    const r = await realFetch(input, init);
    if (!key || !r.ok) return r;
    const plain = await decrypt(key, await r.arrayBuffer());
    return new Response(plain, { status: r.status, statusText: r.statusText, headers: r.headers });
  };

  window.WXAuth = {
    enabled: () => !!auth,
    signOut() { try { localStorage.removeItem(STORE); } catch (e) { /* private mode */ } location.reload(); },
  };

  function showLogin() {
    const box = document.createElement('div');
    box.id = 'login';
    box.innerHTML = `<form autocomplete="on">
      <div class="brand">Wasatch&nbsp;Wx</div>
      <label>Username <input name="username" autocomplete="username" autocapitalize="off" spellcheck="false" required></label>
      <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
      <p class="login-err" role="alert"></p>
      <button type="submit">Sign in</button></form>`;
    document.body.appendChild(box);
    const form = box.querySelector('form'), err = box.querySelector('.login-err'), btn = form.querySelector('button');
    form.username.focus();
    form.addEventListener('submit', async e => {
      e.preventDefault();
      btn.disabled = true; err.textContent = '';
      const key = await derive(form.username.value, form.password.value);
      if (await tryKey(key)) {
        try { localStorage.setItem(STORE, toB64(await crypto.subtle.exportKey('raw', key))); } catch (e2) { /* private mode */ }
        box.remove();
        unlock(key);
      } else {
        err.textContent = 'Wrong username or password.';
        btn.disabled = false; form.password.select();
      }
    });
  }

  (async () => {
    let offline = false, saved = null;
    try {
      const r = await realFetch('auth.json?t=' + Date.now(), { cache: 'no-store' });
      auth = r.ok ? await r.json() : null;
    } catch (e) { offline = true; }            // offline: trust the saved key for the cached data
    try { saved = localStorage.getItem(STORE); } catch (e) { /* private mode */ }
    if (!auth && (!saved || !offline)) return unlock(null);
    if (saved) {
      const key = await importRaw(saved).catch(() => null);
      if (key && (!auth || await tryKey(key))) return unlock(key);
      try { localStorage.removeItem(STORE); } catch (e) { /* private mode */ }
      if (!auth) return unlock(null);
    }
    if (document.body) showLogin(); else document.addEventListener('DOMContentLoaded', showLogin);
  })();
})();
