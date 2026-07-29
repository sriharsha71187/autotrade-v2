/* GeoBee Quest — UI layer. */
(function () {
  const $ = (sel) => document.querySelector(sel);
  const E = window.Engine, Q = window.QBank;
  let S = null;          // learner state
  let act = null;        // current activity {kind, sess|plan, q, answered}

  // ---------- helpers ----------
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function show(id) {
    // Clear inactive screens so ids never collide across screens.
    document.querySelectorAll(".screen").forEach((el) => {
      el.classList.remove("active");
      if ("#" + el.id !== id) el.innerHTML = "";
    });
    $(id).classList.add("active");
    window.scrollTo(0, 0);
  }
  function save() { window.Sync.save(S); }

  function topicMeta(id) {
    return window.GEO_DATA.TOPICS.find((t) => t.id === id) || { name: id, emoji: "🌍" };
  }
  function tierStars(t) { return "★".repeat(t) + "☆".repeat(5 - t); }

  // ---------- audio ----------
  let actx = null;
  function beep(seq) {
    if (!S || !S.settings.sound) return;
    try {
      actx = actx || new (window.AudioContext || window.webkitAudioContext)();
      let t = actx.currentTime;
      for (const [freq, dur] of seq) {
        const o = actx.createOscillator(), g = actx.createGain();
        o.type = "sine"; o.frequency.value = freq;
        g.gain.setValueAtTime(0.12, t);
        g.gain.exponentialRampToValueAtTime(0.001, t + dur);
        o.connect(g); g.connect(actx.destination);
        o.start(t); o.stop(t + dur);
        t += dur * 0.85;
      }
    } catch (e) {}
  }
  const sndGood = () => beep([[523, 0.12], [659, 0.12], [784, 0.2]]);
  const sndBad = () => beep([[220, 0.2], [185, 0.3]]);
  const sndLevel = () => beep([[523, 0.1], [659, 0.1], [784, 0.1], [1047, 0.3]]);

  function speak(text) {
    if (!S || !S.settings.speech || !window.speechSynthesis) return;
    try {
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.rate = 0.95;
      speechSynthesis.speak(u);
    } catch (e) {}
  }

  function confetti(n) {
    const emo = ["🎉", "⭐", "🌟", "🎈", "✨", "🏆"];
    for (let i = 0; i < (n || 20); i++) {
      const el = document.createElement("div");
      el.className = "confetti";
      el.textContent = emo[Math.floor(Math.random() * emo.length)];
      el.style.left = Math.random() * 100 + "vw";
      el.style.animationDuration = 1.6 + Math.random() * 1.6 + "s";
      el.style.animationDelay = Math.random() * 0.4 + "s";
      document.body.appendChild(el);
      setTimeout(() => el.remove(), 3800);
    }
  }

  function mediaHTML(m, big) {
    if (!m) return "";
    const size = big ? "w320" : "w160";
    if (m.type === "flag" && m.code) {
      return `<img class="flag" src="https://flagcdn.com/${size}/${m.code}.png" alt="${esc(m.alt || "flag")}" data-emoji="${esc(m.emoji)}">`;
    }
    return `<div class="flag-emoji">${esc(m.emoji)}</div>`;
  }
  // Offline fallback: swap broken flag images for their emoji.
  document.addEventListener("error", (e) => {
    const t = e.target;
    if (t && t.tagName === "IMG" && t.dataset && t.dataset.emoji) {
      const span = document.createElement("div");
      span.className = "flag-emoji";
      span.textContent = t.dataset.emoji;
      t.replaceWith(span);
    }
  }, true);

  // ---------- welcome ----------
  function renderWelcome() {
    show("#screen-welcome");
    $("#screen-welcome").innerHTML = `
      <div class="card hero">
        <div class="mascot">🌍</div>
        <h1>GeoBee Quest</h1>
        <p class="muted">Your geography adventure — get ready for the bee!</p>
        <div class="field" style="text-align:left;margin-top:16px">
          <label for="name-input">What's your explorer name?</label>
          <input id="name-input" type="text" maxlength="20" placeholder="Type your name" autocomplete="off" />
        </div>
        <button class="big green" id="btn-welcome-go">Let's go! 🚀</button>
      </div>`;
    $("#btn-welcome-go").onclick = () => {
      const name = $("#name-input").value.trim();
      if (!name) { $("#name-input").focus(); return; }
      S.name = name; S.metaUpdated = Date.now(); save();
      renderPlacementIntro();
    };
  }

  function renderPlacementIntro() {
    show("#screen-welcome");
    $("#screen-welcome").innerHTML = `
      <div class="card hero">
        <div class="mascot">🧭</div>
        <h1>Hi, ${esc(S.name)}!</h1>
        <p style="margin:10px 0">First, a quick <b>treasure hunt of 12 questions</b> so I can find the perfect starting level for you. Some will be easy, some tricky — just do your best!</p>
        <button class="big green" id="btn-place-go">Start the hunt 🗺️</button>
        <button class="big ghost" id="btn-place-skip">Skip — start easy</button>
      </div>`;
    $("#btn-place-go").onclick = startPlacement;
    $("#btn-place-skip").onclick = () => { S.placementDone = true; save(); renderHome(); };
  }

  // ---------- home ----------
  function renderHome() {
    const r = E.rank(S);
    const nextXp = r.next ? r.next[0] : null;
    const pct = nextXp ? Math.min(100, Math.round(((S.xp - r.at) / (nextXp - r.at)) * 100)) : 100;
    const due = E.dueCount(S);
    show("#screen-home");
    $("#screen-home").innerHTML = `
      <div class="card hero">
        <div class="mascot">🌍</div>
        <h1>Hi, ${esc(S.name)}!</h1>
        <div class="rank-chip">${r.emoji} ${r.name}</div>
        <div class="xp-bar"><div style="width:${pct}%"></div></div>
        <div class="muted">${S.xp} XP${nextXp ? ` — ${nextXp - S.xp} to ${r.next[2]} ${r.next[1]}` : " — top rank!"}</div>
        ${due > 0 ? `<div style="margin-top:8px"><span class="due-chip">🔔 ${due} review${due > 1 ? "s" : ""} ready</span></div>` : ""}
      </div>
      <button class="big green" id="btn-play">▶️ &nbsp;Play Adventure</button>
      <div class="row">
        <button class="big violet" id="btn-bee-written">📝 Written Bee</button>
        <button class="big amber" id="btn-bee-oral">🎤 Oral Bee</button>
      </div>
      <div class="row">
        <button class="big ghost" id="btn-progress">📊 My Progress</button>
        <button class="big ghost" id="btn-settings">⚙️ Settings</button>
      </div>`;
    $("#btn-play").onclick = startPractice;
    $("#btn-bee-written").onclick = () => startBee("written");
    $("#btn-bee-oral").onclick = () => startBee("oral");
    $("#btn-progress").onclick = renderProgress;
    $("#btn-settings").onclick = renderSettings;
  }

  // ---------- activities ----------
  function startPractice() {
    act = { kind: "practice", sess: E.newSession("practice") };
    nextStep();
  }
  function startPlacement() {
    act = { kind: "placement", plan: E.placementPlan(S) };
    nextStep();
  }
  function startBee(kind) {
    act = { kind, plan: E.beePlan(kind, S) };
    nextStep();
  }

  function nextStep() {
    if (act.kind === "practice") {
      if (act.sess.i >= E.ROUND_LEN) return renderSummary();
      const item = E.nextQuestion(S, act.sess);
      if (!item) return renderSummary();
      if (item.teach) return renderTeach(item.fact);
      return renderQuestion(item);
    }
    if (act.kind === "placement") {
      const q = E.placementNext(S, act.plan);
      if (!q) return finishPlacement();
      return renderQuestion(q);
    }
    // bees
    const q = E.beeNext(S, act.plan);
    if (!q) return renderSummary();
    return renderQuestion(q);
  }

  function finishPlacement() {
    const start = E.placementFinish(S, act.plan);
    save();
    confetti(24); sndLevel();
    show("#screen-summary");
    $("#screen-summary").innerHTML = `
      <div class="card hero">
        <div class="mascot">🎯</div>
        <h1>Level found!</h1>
        <p class="summary-line">You're starting at Tier ${start} ${tierStars(start).slice(0, 5)}</p>
        <p class="muted">The game will get harder as you get stronger — and it always remembers what you know.</p>
        <button class="big green" id="btn-sum-play">Start playing! ▶️</button>
      </div>`;
    $("#btn-sum-play").onclick = startPractice;
  }

  // ---------- teach card ----------
  function renderTeach(fact) {
    const m = topicMeta(fact.topic);
    let media = "";
    if (fact.topic === "flags") {
      media = `<div class="qmedia">${mediaHTML({ type: "flag", code: Q.flagCode(fact.src.f), emoji: fact.src.f }, true)}</div>`;
    }
    show("#screen-quiz");
    $("#screen-quiz").innerHTML = `
      ${quizTopBar()}
      <div class="card teach">
        <span class="new-tag">🆕 New fact!</span>
        <span class="topic-chip">${m.emoji} ${esc(m.name)}</span>
        ${media}
        ${fact.teachQ
          ? `<div class="fact" style="font-size:1.05rem;margin-bottom:6px">${esc(fact.teachQ)}</div><div class="teach-main">${esc(fact.teachA)}</div>`
          : `<div class="teach-main">${esc(fact.teachText)}</div>`}
        ${fact.fact ? `<div class="fact">💡 ${esc(fact.fact)}</div>` : ""}
        <button class="big green" id="btn-gotit">Got it! Quiz me 💪</button>
      </div>`;
    $("#btn-gotit").onclick = nextStep;
    $("#btn-quit").onclick = quitActivity;
    speak(fact.teachText);
  }

  function quizTopBar() {
    let progress = "", extra = "";
    if (act.kind === "practice") {
      progress = `${Math.min(act.sess.i + 1, E.ROUND_LEN)}/${E.ROUND_LEN}`;
      extra = `<span class="stat">🔥 ${act.sess.streak}</span><span class="stat">⭐ ${act.sess.xp}</span>`;
    } else if (act.kind === "placement") {
      progress = `${act.plan.i + 1}/${act.plan.n}`;
    } else {
      progress = `${act.plan.i + 1}/${act.plan.tiers.length}`;
      extra = act.kind === "written"
        ? `<span class="stat">🏅 ${act.plan.score}</span>`
        : `<span class="stat">💚 ${"❤️".repeat(Math.max(0, 3 - act.plan.strikes))}</span>`;
    }
    return `<div class="quiz-top">
      <button class="icon-btn" id="btn-quit" aria-label="quit">✖</button>
      <span class="stat">${progress}</span>${extra}
    </div>`;
  }

  // ---------- question ----------
  function renderQuestion(q) {
    act.q = q; act.answered = false;
    const m = topicMeta(q.topic);
    let modeChip = "";
    if (q.flags) {
      if (q.flags.reask) modeChip = `<span class="mode-chip mode-reask">🔁 Try again!</span>`;
      else if (q.flags.review) modeChip = `<span class="mode-chip mode-review">🔔 Review</span>`;
      else if (q.flags.challenge) modeChip = `<span class="mode-chip mode-challenge">⚡ Challenge — 2× XP</span>`;
    }
    const media = q.media ? `<div class="qmedia">${mediaHTML(q.media, true)}</div>` : "";
    let body;
    if (q.kind === "typed") {
      body = `
        <div class="typed-row">
          <input id="typed-input" type="text" autocomplete="off" autocapitalize="off" placeholder="Type your answer" />
          <button class="green" id="btn-typed-go">Go</button>
        </div>
        <button class="ghost small" id="btn-dontknow" style="margin-top:10px">I don't know 🤷</button>`;
    } else {
      const flagOpts = q.options.some((o) => o.media);
      body = `<div class="options ${flagOpts ? "flags" : ""}">
        ${q.options.map((o, i) => `
          <button class="opt" data-i="${i}" aria-label="${esc(o.media ? o.media.alt : o.label)}">
            ${o.media ? mediaHTML(o.media, false) : esc(o.label)}
          </button>`).join("")}
      </div>
      ${act.kind === "written" ? `<button class="ghost small" id="btn-skip" style="margin-top:10px">Skip (0 points) ⏭️</button>` : ""}`;
    }
    show("#screen-quiz");
    $("#screen-quiz").innerHTML = `
      ${quizTopBar()}
      <div class="card">
        <span class="topic-chip">${m.emoji} ${esc(m.name)}</span>${modeChip}
        <div class="prompt" id="prompt">${esc(q.prompt)}
          <button class="icon-btn small" id="btn-say" aria-label="read aloud" style="box-shadow:none">🔊</button>
        </div>
        ${media}
        ${body}
        <div id="feedback"></div>
      </div>`;
    $("#btn-quit").onclick = quitActivity;
    $("#btn-say").onclick = () => { const keep = S.settings.speech; S.settings.speech = true; speak(q.speak); S.settings.speech = keep; };
    if (q.kind === "typed") {
      const input = $("#typed-input");
      input.focus();
      $("#btn-typed-go").onclick = () => submitTyped(input.value);
      input.onkeydown = (e) => { if (e.key === "Enter") submitTyped(input.value); };
      $("#btn-dontknow").onclick = () => submitTyped(null);
    } else {
      document.querySelectorAll(".opt").forEach((b) => (b.onclick = () => submitMcq(+b.dataset.i)));
      const skip = $("#btn-skip");
      if (skip) skip.onclick = () => resolveAnswer("skip");
    }
    speak(q.prompt);
  }

  function submitMcq(i) {
    if (act.answered) return;
    const q = act.q;
    const correct = i === q.answerIdx;
    document.querySelectorAll(".opt").forEach((b, bi) => {
      b.disabled = true;
      if (bi === q.answerIdx) b.classList.add(correct ? "correct" : "reveal");
      if (bi === i && !correct) b.classList.add("wrong");
    });
    resolveAnswer(correct ? "correct" : "wrong");
  }
  function submitTyped(value) {
    if (act.answered) return;
    const q = act.q;
    let correct = false;
    if (value != null) {
      const norm = Q.normalize(value);
      correct = norm.length > 0 && q.accept.some((a) => a === norm);
    }
    const input = $("#typed-input");
    if (input) { input.disabled = true; input.style.borderColor = correct ? "#66bb6a" : "#ef5350"; }
    resolveAnswer(correct ? "correct" : "wrong");
  }

  function resolveAnswer(result) {
    act.answered = true;
    const q = act.q;
    let events = [], xpGain = 0;
    if (act.kind === "practice") {
      const before = act.sess.xp;
      events = E.record(S, act.sess, q, result === "correct");
      xpGain = act.sess.xp - before;
    } else if (act.kind === "placement") {
      E.placementRecord(S, act.plan, q, result === "correct");
    } else {
      E.beeRecord(S, act.plan, q, result);
    }
    save();
    if (result === "correct") sndGood(); else if (result === "wrong") sndBad();
    if (events.some((e) => e.type === "levelup" || e.type === "badge")) { confetti(26); sndLevel(); }

    const good = result === "correct";
    const headline = good
      ? ["Awesome! ✅", "You got it! 🎉", "Brilliant! 🌟", "Nailed it! 💪"][Math.floor(Math.random() * 4)]
      : result === "skip" ? "Skipped ⏭️" : "Not quite ❌";
    const answerLine = good ? "" : `<div style="margin-top:4px">The answer is <b>${esc(q.answerText)}</b>.</div>`;
    const factLine = q.fact && act.kind !== "placement" ? `<div class="fact">💡 ${esc(q.fact)}</div>` : "";
    const xpLine = xpGain ? `<span class="xp-gain">+${xpGain} XP</span>` : "";
    const eventChips = events.map((e) =>
      e.type === "levelup" ? `<span class="event-chip">🎉 Level up! ${esc(topicMeta(e.topic).name)} → Tier ${e.tier}</span>`
      : e.type === "badge" ? `<span class="event-chip">${e.badge.emoji} Badge: ${esc(e.badge.name)}</span>` : ""
    ).join("");
    $("#feedback").innerHTML = `
      <div class="feedback ${good ? "good" : "bad"}">
        <div class="headline">${headline} ${xpLine}</div>
        ${answerLine}${factLine}
        <div>${eventChips}</div>
        <button class="big ${good ? "green" : "ghost"}" id="btn-next" style="margin-top:10px">Next ➜</button>
      </div>`;
    const btn = $("#btn-next");
    btn.focus();
    btn.onclick = nextStep;
    if (!good && q.answerText) speak("The answer is " + q.answerText);
  }

  function quitActivity() {
    if (act && act.kind === "practice" && act.sess.i > 0) return renderSummary();
    renderHome();
  }

  // ---------- summary ----------
  function renderSummary() {
    let html = "";
    if (act.kind === "practice") {
      const s = act.sess;
      const acc = s.i ? s.correct / s.i : 0;
      const stars = acc >= 0.9 ? 3 : acc >= 0.7 ? 2 : 1;
      if (stars === 3) { confetti(30); sndLevel(); }
      html = `
        <div class="mascot">${stars === 3 ? "🏆" : stars === 2 ? "🌟" : "🌱"}</div>
        <h1>Round complete!</h1>
        <div class="stars">${"⭐".repeat(stars)}${"▫️".repeat(3 - stars)}</div>
        <p class="summary-line">${s.correct} / ${s.i} correct &nbsp;·&nbsp; +${s.xp} XP</p>`;
    } else if (act.kind === "written") {
      const p = act.plan;
      const bonus = Math.max(0, p.score);
      S.xp += bonus;
      const events = E.beeFinish(S, p); save();
      if (p.score >= 40) { confetti(30); sndLevel(); }
      html = `
        <div class="mascot">📝</div>
        <h1>Written Bee done!</h1>
        <p class="summary-line">Score: ${p.score} / 50</p>
        <p class="muted">Bee scoring: +2 correct, −1 wrong, 0 skipped — just like the real qualifying exam.</p>
        <p class="summary-line">🏅 Best: ${S.best.written} &nbsp;·&nbsp; +${bonus} XP</p>
        ${events.some((e) => e.type === "badge") ? `<span class="event-chip">🎖️ New badge earned!</span>` : ""}`;
    } else if (act.kind === "oral") {
      const p = act.plan;
      const bonus = p.correct * 8;
      S.xp += bonus;
      const events = E.beeFinish(S, p); save();
      if (p.correct >= 12) { confetti(30); sndLevel(); }
      html = `
        <div class="mascot">🎤</div>
        <h1>${p.strikes >= 3 ? "Three strikes — good try!" : "Oral Bee done!"}</h1>
        <p class="summary-line">${p.correct} correct answers</p>
        <p class="muted">Oral rounds are spoken in a real bee — typing the answer is great practice for recall.</p>
        <p class="summary-line">🏅 Best: ${S.best.oral} &nbsp;·&nbsp; +${bonus} XP</p>
        ${events.some((e) => e.type === "badge") ? `<span class="event-chip">🎖️ New badge earned!</span>` : ""}`;
    }
    show("#screen-summary");
    $("#screen-summary").innerHTML = `
      <div class="card hero">
        ${html}
        <button class="big green" id="btn-again">Play again ▶️</button>
        <button class="big ghost" id="btn-home">Home 🏠</button>
      </div>`;
    const kind = act.kind;
    $("#btn-again").onclick = () => (kind === "practice" ? startPractice() : startBee(kind));
    $("#btn-home").onclick = renderHome;
    act = null;
  }

  // ---------- progress (parent view) ----------
  function renderProgress() {
    const topics = E.topicSummary(S);
    const r = E.rank(S);
    const acc = S.totals.answered ? Math.round((S.totals.correct / S.totals.answered) * 100) : 0;
    const st = window.Sync.getStatus();
    const syncLine = !st.configured
      ? `<span class="muted">Device-only (set up Supabase in js/config.js to sync)</span>`
      : st.signedIn
        ? `<span class="sync-ok">✔ Syncing as ${esc(st.email)}${st.error ? " — offline, will retry" : ""}</span>`
        : `<span class="sync-warn">⚠ Not signed in — progress is only on this device</span>`;
    const recent = S.log.slice(-7).map((e) =>
      `<div class="counts">${e.d}: ${e.c}/${e.a} correct</div>`).join("") || `<div class="counts">No sessions yet.</div>`;
    show("#screen-progress");
    $("#screen-progress").innerHTML = `
      <div class="quiz-top">
        <button class="icon-btn" id="btn-back">← Back</button>
        <span class="stat">📊 Parent view</span>
      </div>
      <div class="card">
        <h2>${esc(S.name || "Explorer")} — ${r.emoji} ${r.name}</h2>
        <div class="counts">${S.xp} XP · ${S.totals.answered} questions answered · ${acc}% correct overall · best streak ${S.totals.bestStreak}</div>
        <div class="counts">Bee bests: written ${S.best.written == null ? "—" : S.best.written + "/50"} · oral ${S.best.oral == null ? "—" : S.best.oral}</div>
        <div style="margin-top:6px">${syncLine}</div>
      </div>
      <div class="card">
        <h2>Topic coverage</h2>
        ${topics.map((t) => `
          <div class="topic-row">
            <div class="topic-head">
              <span>${t.emoji} ${esc(t.name)}</span>
              <span class="tier-stars" title="current difficulty tier">${tierStars(t.tier)}</span>
            </div>
            <div class="bar">
              <div class="seg-mastered" style="width:${(t.mastered / t.total) * 100}%"></div>
              <div class="seg-known" style="width:${(t.known / t.total) * 100}%"></div>
              <div class="seg-learning" style="width:${(t.learning / t.total) * 100}%"></div>
            </div>
            <div class="counts">${t.pct}% known · ${t.mastered} mastered · ${t.known} solid · ${t.learning} learning · ${t.unseen} not yet covered</div>
            ${t.weakest.length ? `<ul class="weak-list">${t.weakest.map((w) =>
              `<li>${esc(w.label)} <span style="opacity:.7">(${w.c}✓ ${w.w}✗)</span></li>`).join("")}</ul>` : ""}
          </div>`).join("")}
        <p class="note">Green = mastered (long-term memory) · light green = solid · yellow = still learning. "Weak spots" list what the app is currently re-teaching.</p>
      </div>
      <div class="card">
        <h2>Badges</h2>
        <div class="badge-grid">
          ${E.BADGES.map((b) => `
            <div class="badge-tile ${S.badges.includes(b.id) ? "" : "locked"}" title="${esc(b.desc)}">
              <div class="b-emoji">${b.emoji}</div><div class="b-name">${esc(b.name)}</div>
            </div>`).join("")}
        </div>
      </div>
      <div class="card"><h2>Last 7 active days</h2>${recent}</div>`;
    $("#btn-back").onclick = renderHome;
  }

  // ---------- settings ----------
  function renderSettings() {
    const st = window.Sync.getStatus();
    const topics = window.GEO_DATA.TOPICS;
    let syncBody;
    if (!st.configured) {
      syncBody = `<p class="note">To sync between devices: create a free Supabase project, run <b>supabase/schema.sql</b>, and paste your project URL + anon key into <b>js/config.js</b>. Until then, progress stays on this device (use Backup below to move it by hand).</p>`;
    } else if (st.signedIn) {
      syncBody = `
        <p class="sync-ok">✔ Signed in as ${esc(st.email)}</p>
        <p class="note">${st.lastSync ? "Last sync: " + new Date(st.lastSync).toLocaleTimeString() : "Will sync after the next answer."}</p>
        <button class="berry" id="btn-signout">Sign out</button>`;
    } else {
      syncBody = `
        <div class="field"><label>Parent email</label><input id="sync-email" type="email" autocomplete="username" /></div>
        <div class="field"><label>Password</label><input id="sync-pass" type="password" autocomplete="current-password" /></div>
        <div class="row">
          <button class="green" id="btn-signin">Sign in</button>
          <button class="ghost" id="btn-signup">Create account</button>
        </div>
        <p class="note" id="sync-msg">One account for the family — sign in once on each device and progress merges automatically.</p>`;
    }
    show("#screen-settings");
    $("#screen-settings").innerHTML = `
      <div class="quiz-top">
        <button class="icon-btn" id="btn-back">← Back</button>
        <span class="stat">⚙️ Settings</span>
      </div>
      <div class="card">
        <div class="field"><label>Explorer name</label><input id="set-name" type="text" maxlength="20" value="${esc(S.name)}" /></div>
        <div class="toggle-row"><span class="t-label">🔊 Sounds</span><label class="switch"><input type="checkbox" id="tg-sound" ${S.settings.sound ? "checked" : ""}/><span></span></label></div>
        <div class="toggle-row"><span class="t-label">🗣️ Read questions aloud</span><label class="switch"><input type="checkbox" id="tg-speech" ${S.settings.speech ? "checked" : ""}/><span></span></label></div>
        <div class="toggle-row"><span class="t-label">⌨️ Spelling mode (type answers you know well)</span><label class="switch"><input type="checkbox" id="tg-typed" ${S.settings.typed ? "checked" : ""}/><span></span></label></div>
      </div>
      <div class="card">
        <h2>Topics</h2>
        ${topics.map((t) => `
          <div class="toggle-row"><span class="t-label">${t.emoji} ${esc(t.name)}</span>
          <label class="switch"><input type="checkbox" class="tg-topic" data-id="${t.id}" ${S.settings.topics[t.id] !== false ? "checked" : ""}/><span></span></label></div>`).join("")}
      </div>
      <div class="card"><h2>Family Sync ☁️</h2>${syncBody}</div>
      <div class="card">
        <h2>Backup</h2>
        <div class="row">
          <button class="ghost" id="btn-export">📤 Copy backup code</button>
          <button class="ghost" id="btn-import">📥 Paste backup code</button>
        </div>
        <textarea id="backup-box" rows="3" style="width:100%;margin-top:8px" placeholder="Backup code appears here / paste one here"></textarea>
      </div>
      <div class="card">
        <h2>Danger zone</h2>
        <button class="berry" id="btn-reset">Reset ALL progress</button>
      </div>`;
    $("#btn-back").onclick = () => { applySettings(); renderHome(); };
    $("#tg-sound").onchange = (e) => { S.settings.sound = e.target.checked; save(); };
    $("#tg-speech").onchange = (e) => { S.settings.speech = e.target.checked; save(); };
    $("#tg-typed").onchange = (e) => { S.settings.typed = e.target.checked; save(); };
    document.querySelectorAll(".tg-topic").forEach((el) => {
      el.onchange = () => {
        const on = document.querySelectorAll(".tg-topic:checked").length;
        if (!on) { el.checked = true; return; }
        S.settings.topics[el.dataset.id] = el.checked; save();
      };
    });
    const so = $("#btn-signout");
    if (so) so.onclick = () => { window.Sync.signOut(); renderSettings(); };
    const si = $("#btn-signin"), su = $("#btn-signup");
    if (si) {
      const msg = (t, warn) => { $("#sync-msg").innerHTML = `<span class="${warn ? "sync-warn" : "sync-ok"}">${esc(t)}</span>`; };
      si.onclick = async () => {
        try {
          msg("Signing in…");
          await window.Sync.signIn($("#sync-email").value.trim(), $("#sync-pass").value);
          const cloudMerged = await window.Sync.load();
          if (cloudMerged) S = cloudMerged;
          window.Sync.flush(S);
          renderSettings();
        } catch (e) { msg(e.message, true); }
      };
      su.onclick = async () => {
        try {
          msg("Creating account…");
          const d = await window.Sync.signUp($("#sync-email").value.trim(), $("#sync-pass").value);
          if (d.access_token) { window.Sync.flush(S); renderSettings(); }
          else msg("Check your email to confirm the account, then sign in here.", false);
        } catch (e) { msg(e.message, true); }
      };
    }
    $("#btn-export").onclick = () => {
      const code = window.Sync.exportCode(S);
      $("#backup-box").value = code;
      $("#backup-box").select();
      try { document.execCommand("copy"); } catch (e) {}
      if (navigator.clipboard) navigator.clipboard.writeText(code).catch(() => {});
    };
    $("#btn-import").onclick = () => {
      try {
        const incoming = window.Sync.importCode($("#backup-box").value);
        if (!incoming || incoming.v !== 1) throw new Error("bad");
        S = E.mergeState(S, incoming);
        window.Sync.flush(S);
        alert("Backup restored and merged! ✅");
        renderHome();
      } catch (e) { alert("That code doesn't look right — paste the whole backup code."); }
    };
    $("#btn-reset").onclick = () => {
      if (!confirm("Really erase ALL progress? This cannot be undone.")) return;
      if (!confirm("Last chance — erase everything?")) return;
      S = E.defaultState();
      window.Sync.flush(S);
      renderWelcome();
    };
  }
  function applySettings() {
    const name = $("#set-name");
    if (name && name.value.trim()) S.name = name.value.trim();
    S.metaUpdated = Date.now();
    save();
  }

  // ---------- keyboard ----------
  document.addEventListener("keydown", (e) => {
    if (!$("#screen-quiz").classList.contains("active")) return;
    if (e.key >= "1" && e.key <= "4") {
      const opts = document.querySelectorAll(".opt");
      const b = opts[+e.key - 1];
      if (b && !b.disabled) b.click();
    }
  });

  // save on tab close
  window.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden" && S) window.Sync.flush(S);
  });

  // ---------- boot ----------
  (async function boot() {
    let loaded = null;
    try { loaded = await window.Sync.load(); } catch (e) { loaded = window.Sync.loadLocal(); }
    S = loaded || E.defaultState();
    if (!S.name) renderWelcome();
    else if (!S.placementDone && S.totals.answered < 5) renderPlacementIntro();
    else renderHome();
  })();
})();
