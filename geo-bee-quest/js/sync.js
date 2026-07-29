/* GeoBee Quest — persistence & sync (multi-profile).
 * localStorage is the source of truth (offline-first). If Supabase is
 * configured and a parent account is signed in, each profile's state is
 * merged with its cloud row on load and pushed (debounced) after changes.
 * Minimal REST client — no SDK, no external scripts.
 */
window.Sync = (function () {
  const LS_META = "gbq.meta.v1";       // { profiles: [{id,name,avatar}], active }
  const LS_STATE = "gbq.state.v1";     // legacy/default profile; others: gbq.state.v1.<id>
  const LS_SESSION = "gbq.auth.v1";
  const cfg = window.GBQ_CONFIG || {};
  let pushTimer = null;
  let status = { configured: !!(cfg.supabaseUrl && cfg.supabaseAnonKey), signedIn: false, lastSync: null, error: null };
  let listeners = [];

  function onChange(fn) { listeners.push(fn); }
  function emit() { listeners.forEach((fn) => fn(getStatus())); }
  function getStatus() { return { ...status, email: (auth() || {}).email || null }; }

  // --- profiles meta -------------------------------------------------------
  function meta() {
    try {
      const m = JSON.parse(localStorage.getItem(LS_META) || "null");
      if (m && m.profiles && m.profiles.length) return m;
    } catch (e) {}
    // migrate a legacy single-profile install
    const legacy = readState("default");
    if (legacy) {
      const m = { profiles: [{ id: "default", name: legacy.name || "Explorer", avatar: legacy.avatar || "🌍" }], active: "default" };
      saveMeta(m);
      return m;
    }
    return { profiles: [], active: null };
  }
  function saveMeta(m) {
    try { localStorage.setItem(LS_META, JSON.stringify(m)); } catch (e) {}
  }
  function stateKey(profileId) {
    return profileId === "default" ? LS_STATE : LS_STATE + "." + profileId;
  }

  // --- local ---------------------------------------------------------------
  function readState(profileId) {
    try {
      const raw = localStorage.getItem(stateKey(profileId));
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }
  function writeState(state, profileId) {
    try { localStorage.setItem(stateKey(profileId), JSON.stringify(state)); } catch (e) {}
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
      if (/refresh/i.test(e.message) || /invalid/i.test(e.message)) setAuth(null);
      throw e;
    }
  }

  // --- cloud ---------------------------------------------------------------
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
  async function fetchCloud(profileId) {
    const token = await freshToken();
    if (!token) return null;
    const rows = await rest("GET", "?profile=eq." + encodeURIComponent(profileId) + "&select=state", token);
    return rows && rows[0] ? rows[0].state : null;
  }
  async function pushCloud(state, profileId) {
    const token = await freshToken();
    if (!token) return;
    const a = auth();
    await rest("POST", "", token, [{
      user_id: a.uid, profile: profileId, state, updated_at: new Date().toISOString(),
    }]);
    status.lastSync = Date.now();
    status.error = null;
    emit();
  }
  // All cloud profiles for this account (used to discover profiles on a new device).
  async function fetchCloudProfiles() {
    const token = await freshToken();
    if (!token) return [];
    const rows = await rest("GET", "?select=profile,state", token);
    return (rows || []).map((r) => ({
      id: r.profile,
      name: (r.state && r.state.name) || r.profile,
      avatar: (r.state && r.state.avatar) || "🌍",
    }));
  }

  // --- public API ----------------------------------------------------------
  async function load(profileId) {
    let state = readState(profileId);
    if (status.configured && auth()) {
      status.signedIn = true;
      try {
        const cloud = await fetchCloud(profileId);
        if (cloud) state = window.Engine.mergeState(state, cloud);
        if (state) { writeState(state, profileId); pushCloud(state, profileId).catch(() => {}); }
        status.error = null;
      } catch (e) {
        status.error = "offline";
      }
    }
    emit();
    return state;
  }
  function save(state, profileId) {
    writeState(state, profileId);
    if (status.configured && auth()) {
      clearTimeout(pushTimer);
      pushTimer = setTimeout(() => {
        pushCloud(state, profileId).catch(() => { status.error = "offline"; emit(); });
      }, 2500);
    }
  }
  function flush(state, profileId) {
    writeState(state, profileId);
    if (status.configured && auth()) pushCloud(state, profileId).catch(() => {});
  }

  // Manual backup codes (works with no account at all)
  function exportCode(state) {
    return btoa(unescape(encodeURIComponent(JSON.stringify(state))));
  }
  function importCode(code) {
    return JSON.parse(decodeURIComponent(escape(atob(code.trim()))));
  }

  window.addEventListener("online", () => {
    const m = meta();
    if (m.active && status.configured && auth()) {
      const s = readState(m.active);
      if (s) pushCloud(s, m.active).catch(() => {});
    }
  });

  return {
    meta, saveMeta, load, save, flush, readState,
    signUp, signIn, signOut, getStatus, onChange,
    fetchCloudProfiles, exportCode, importCode,
  };
})();
