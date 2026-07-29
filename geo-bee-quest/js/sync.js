/* GeoBee Quest — persistence & sync.
 * localStorage is the source of truth (offline-first). If Supabase is
 * configured and a parent account is signed in, state is merged with the
 * cloud copy on load and pushed (debounced) after changes, so every device
 * shares one memory of what's been learned. Minimal REST client — no SDK,
 * no external scripts.
 */
window.Sync = (function () {
  const LS_STATE = "gbq.state.v1";
  const LS_SESSION = "gbq.auth.v1";
  const cfg = window.GBQ_CONFIG || {};
  let pushTimer = null;
  let status = { configured: !!(cfg.supabaseUrl && cfg.supabaseAnonKey), signedIn: false, lastSync: null, error: null };
  let listeners = [];

  function onChange(fn) { listeners.push(fn); }
  function emit() { listeners.forEach((fn) => fn(getStatus())); }
  function getStatus() {
    return { ...status, email: (auth() || {}).email || null };
  }

  // --- local ---------------------------------------------------------------
  function loadLocal() {
    try {
      const raw = localStorage.getItem(LS_STATE);
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }
  function saveLocal(state) {
    try { localStorage.setItem(LS_STATE, JSON.stringify(state)); } catch (e) {}
  }

  // --- auth ----------------------------------------------------------------
  function auth() {
    try { return JSON.parse(localStorage.getItem(LS_SESSION) || "null"); } catch (e) { return null; }
  }
  function setAuth(a) {
    if (a) localStorage.setItem(LS_SESSION, JSON.stringify(a));
    else localStorage.removeItem(LS_SESSION);
    status.signedIn = !!a;
    emit();
  }
  function jwtSub(token) {
    try { return JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).sub; }
    catch (e) { return null; }
  }
  async function authFetch(path, body) {
    const res = await fetch(cfg.supabaseUrl + "/auth/v1/" + path, {
      method: "POST",
      headers: { "Content-Type": "application/json", apikey: cfg.supabaseAnonKey },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.msg || data.error_description || data.message || ("Sign-in failed (" + res.status + ")"));
    return data;
  }
  function storeSession(data, email) {
    setAuth({
      email: email || (data.user && data.user.email),
      access: data.access_token, refresh: data.refresh_token,
      exp: Date.now() + ((data.expires_in || 3600) - 120) * 1000,
      uid: jwtSub(data.access_token),
    });
  }
  async function signUp(email, password) {
    const data = await authFetch("signup", { email, password });
    if (data.access_token) storeSession(data, email);
    return data;
  }
  async function signIn(email, password) {
    const data = await authFetch("token?grant_type=password", { email, password });
    storeSession(data, email);
    return data;
  }
  function signOut() { setAuth(null); }
  async function freshToken() {
    let a = auth();
    if (!a) return null;
    if (Date.now() < a.exp) return a.access;
    try {
      const data = await authFetch("token?grant_type=refresh_token", { refresh_token: a.refresh });
      storeSession(data, a.email);
      return data.access_token;
    } catch (e) {
      // refresh token rejected → force re-login
      if (/refresh/i.test(e.message) || /invalid/i.test(e.message)) setAuth(null);
      throw e;
    }
  }

  // --- cloud state ---------------------------------------------------------
  async function rest(method, query, token, body) {
    const res = await fetch(cfg.supabaseUrl + "/rest/v1/progress" + query, {
      method,
      headers: {
        apikey: cfg.supabaseAnonKey,
        Authorization: "Bearer " + token,
        "Content-Type": "application/json",
        Prefer: method === "POST" ? "resolution=merge-duplicates,return=minimal" : "return=representation",
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw new Error("Sync error " + res.status);
    if (method === "GET") return res.json();
    return null;
  }
  async function fetchCloud() {
    const token = await freshToken();
    if (!token) return null;
    const rows = await rest("GET", "?profile=eq.default&select=state,updated_at", token);
    return rows && rows[0] ? rows[0].state : null;
  }
  async function pushCloud(state) {
    const token = await freshToken();
    if (!token) return;
    const a = auth();
    await rest("POST", "", token, [{
      user_id: a.uid, profile: "default", state, updated_at: new Date().toISOString(),
    }]);
    status.lastSync = Date.now();
    status.error = null;
    emit();
  }

  // --- public API ----------------------------------------------------------
  // Load: local state merged with cloud (if signed in). Returns state.
  async function load() {
    let state = loadLocal();
    if (status.configured && auth()) {
      status.signedIn = true;
      try {
        const cloud = await fetchCloud();
        if (cloud) state = window.Engine.mergeState(state, cloud);
        if (state) { saveLocal(state); pushCloud(state).catch(() => {}); }
        status.error = null;
      } catch (e) {
        status.error = "offline";
      }
    }
    emit();
    return state;
  }

  // Save locally now; push to cloud debounced.
  function save(state) {
    saveLocal(state);
    if (status.configured && auth()) {
      clearTimeout(pushTimer);
      pushTimer = setTimeout(() => {
        pushCloud(state).catch(() => { status.error = "offline"; emit(); });
      }, 2500);
    }
  }
  function flush(state) {
    saveLocal(state);
    if (status.configured && auth()) pushCloud(state).catch(() => {});
  }

  // Manual backup codes (works with no account at all)
  function exportCode(state) {
    return btoa(unescape(encodeURIComponent(JSON.stringify(state))));
  }
  function importCode(code) {
    return JSON.parse(decodeURIComponent(escape(atob(code.trim()))));
  }

  window.addEventListener("online", () => {
    const s = loadLocal();
    if (s && status.configured && auth()) pushCloud(s).catch(() => {});
  });

  return { load, save, flush, signUp, signIn, signOut, getStatus, onChange, exportCode, importCode, loadLocal };
})();
