/* GeoBee Quest — UI layer. */
(function () {
  const $ = (sel) => document.querySelector(sel);
  const E = window.Engine, Q = window.QBank;
  const AVATARS = ["🦁", "🐯", "🐸", "🦊", "🐼", "🦄", "🐵", "🐧", "🦖", "🐙", "🚀", "🌍"];
  let S = null;          // learner state
  let PID = null;        // active profile id
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
  function save() { window.Sync.save(S, PID); }
  function flush() { window.Sync.flush(S, PID); }

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
    forceSpeak(text);
  }
  function forceSpeak(text) {
    if (!window.speechSynthesis) return;
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
  // Full-screen celebration (rank-ups)
  function celebrate(emoji, title, sub) {
    confetti(40); sndLevel();
    const el = document.createElement("div");
    el.className = "celebrate-overlay";
    el.innerHTML = `<div class="celebrate-box"><div class="c-emoji">${emoji}</div><h1>${esc(title)}</h1><p>${esc(sub)}</p><p class="muted">tap to continue</p></div>`;
    el.onclick = () => el.remove();
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  // ---------- media (flags + maps) ----------
  function mediaHTML(m, big) {
    if (!m) return "";
    if (m.type === "map") return mapHTML(m);
    const size = big ? "w320" : "w160";
    if (m.type === "flag" && m.code) {
      return `<img class="flag" src="https://flagcdn.com/${size}/${m.code}.png" alt="${esc(m.alt || "flag")}" data-emoji="${esc(m.emoji)}">`;
    }
    return `<div class="flag-emoji">${esc(m.emoji)}</div>`;
  }
  document.addEventListener("error", (e) => {
    const t = e.target;
    if (t && t.tagName === "IMG" && t.dataset && t.dataset.emoji) {
      const span = document.createElement("div");
      span.className = "flag-emoji";
      span.textContent = t.dataset.emoji;
      t.replaceWith(span);
    }
  }, true);

  function mapHTML(m) {
    const M = window.GEO_MAPS;
    if (!M) return "";
    const src = m.kind === "us" ? M.us.states : M.world.countries;
    const viewBox = m.kind === "us" ? M.us.viewBox : M.world.viewBox;
    const paths = Object.entries(src).map(([n, v]) =>
      `<path d="${v.d}" data-name="${esc(n)}" class="${m.highlight === n ? "hl" : ""}"></path>`).join("");
    return `<svg class="map-svg ${m.kind} ${m.clickable ? "clickable" : ""}" viewBox="${viewBox}" preserveAspectRatio="xMidYMid meet">${paths}</svg>`;
  }
  // Add a pulsing ring around tiny highlighted places so they're findable.
  function decorateMap(container) {
    const svg = container.querySelector(".map-svg");
    if (!svg) return;
    const hl = svg.querySelector("path.hl");
    if (!hl) return;
    try {
      const b = hl.getBBox();
      if (b.width * b.height < 900) {
        const ring = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        ring.setAttribute("cx", b.x + b.width / 2);
        ring.setAttribute("cy", b.y + b.height / 2);
        ring.setAttribute("r", 22);
        ring.setAttribute("class", "pulse-ring");
        svg.appendChild(ring);
      }
    } catch (e) {}
  }

  // ---------- profiles ----------
  function activeProfile() {
    const m = window.Sync.meta();
    return m.profiles.find((p) => p.id === m.active) || m.profiles[0] || null;
  }
  async function switchProfile(id) {
    if (S) flush();
    const m = window.Sync.meta();
    m.active = id;
    window.Sync.saveMeta(m);
    PID = id;
    S = E.migrate((await window.Sync.load(PID)) || E.defaultState());
    const p = m.profiles.find((x) => x.id === id);
    if (p) { S.name = S.name || p.name; S.avatar = S.avatar || p.avatar; }
    if (!S.placementDone && S.totals.answered < 5) renderPlacementIntro();
    else renderHome();
  }
  function profileOverlay() {
    const m = window.Sync.meta();
    const el = document.createElement("div");
    el.className = "celebrate-overlay";
    el.innerHTML = `<div class="celebrate-box">
      <h2>Who's playing? 👋</h2>
      <div class="profile-list">
        ${m.profiles.map((p) => `<button class="ghost profile-btn" data-id="${esc(p.id)}">${esc(p.avatar)} ${esc(p.name)}${p.id === m.active ? " ✓" : ""}</button>`).join("")}
        <button class="green profile-btn" data-id="__new">➕ New explorer</button>
      </div></div>`;
    el.onclick = (e) => {
      const b = e.target.closest(".profile-btn");
      if (!b) { el.remove(); return; }
      el.remove();
      if (b.dataset.id === "__new") renderWelcome(true);
      else switchProfile(b.dataset.id);
    };
    document.body.appendChild(el);
  }

  // ---------- welcome ----------
  function renderWelcome(addingProfile) {
    show("#screen-welcome");
    $("#screen-welcome").innerHTML = `
      <div class="card hero">
        <div class="mascot bounce">🌍</div>
        <h1 class="title-sheen">GeoBee Quest</h1>
        <p class="muted">Your geography adventure — get ready for the bee!</p>
        <div class="field" style="text-align:left;margin-top:16px">
          <label for="name-input">What's your explorer name?</label>
          <input id="name-input" type="text" maxlength="20" placeholder="Type your name" autocomplete="off" />
        </div>
        <div class="field" style="text-align:left">
          <label>Pick your explorer buddy!</label>
          <div class="avatar-grid" id="avatar-grid">
            ${AVATARS.map((a, i) => `<button class="avatar-pick ${i === 0 ? "sel" : ""}" data-a="${a}">${a}</button>`).join("")}
          </div>
        </div>
        <button class="big green" id="btn-welcome-go">Let's go! 🚀</button>
        ${addingProfile ? `<button class="big ghost" id="btn-welcome-cancel">Cancel</button>` : ""}
      </div>`;
    let avatar = AVATARS[0];
    $("#avatar-grid").onclick = (e) => {
      const b = e.target.closest(".avatar-pick");
      if (!b) return;
      avatar = b.dataset.a;
      document.querySelectorAll(".avatar-pick").forEach((x) => x.classList.remove("sel"));
      b.classList.add("sel");
    };
    $("#btn-welcome-go").onclick = async () => {
      const name = $("#name-input").value.trim();
      if (!name) { $("#name-input").focus(); return; }
      const m = window.Sync.meta();
      const id = m.profiles.length ? "p" + Date.now().toString(36) : "default";
      m.profiles.push({ id, name, avatar });
      m.active = id;
      window.Sync.saveMeta(m);
      PID = id;
      S = E.defaultState();
      S.name = name; S.avatar = avatar; S.metaUpdated = Date.now();
      save();
      renderPlacementIntro();
    };
    const cancel = $("#btn-welcome-cancel");
    if (cancel) cancel.onclick = renderHome;
  }

  function renderPlacementIntro() {
    show("#screen-welcome");
    $("#screen-welcome").innerHTML = `
      <div class="card hero">
        <div class="mascot bounce">🧭</div>
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
    const streak = E.dayStreak(S);
    const qp = E.questProgress(S);
    const c = E.coach(S);
    const coachLines = [];
    if (c.days != null) {
      coachLines.push(c.days === 0
        ? `🏆 <b>Bee day is TODAY — you've got this!</b>`
        : `🗓️ <b>${c.days} day${c.days > 1 ? "s" : ""}</b> until ${esc(S.settings.beeName || "the bee")}!`);
      if (c.days > 0) coachLines.push(`Today's plan: clear <b>${c.dueN}</b> reviews · learn ~<b>${c.perDay}</b> new facts`);
    } else {
      coachLines.push(`🔔 <b>${c.dueN}</b> reviews ready · <b>${c.unseen}</b> facts left to discover`);
      coachLines.push(`<span class="muted">Set your bee date in Settings to get a countdown plan!</span>`);
    }
    if (c.mockDue) coachLines.push(`🐝 <b>Mock bee day!</b> Try a Written or Oral Bee below.`);
    show("#screen-home");
    $("#screen-home").innerHTML = `
      <div class="card hero">
        <button class="avatar-chip" id="btn-profile" title="Switch explorer">${esc(S.avatar || "🌍")}</button>
        <div class="mascot bounce">${esc(S.avatar || "🌍")}</div>
        <h1>Hi, ${esc(S.name)}!</h1>
        <div class="rank-chip">${r.emoji} ${r.name}</div>
        ${streak > 1 ? `<div class="rank-chip" style="border-color:#ff7043;background:#fff3e0">🔥 ${streak}-day streak</div>` : ""}
        <div class="xp-bar"><div style="width:${pct}%"></div></div>
        <div class="muted">${S.xp} XP${nextXp ? ` — ${nextXp - S.xp} to ${r.next[2]} ${r.next[1]}` : " — top rank!"}</div>
      </div>
      <div class="card coach">
        ${coachLines.map((l) => `<div class="coach-line">${l}</div>`).join("")}
        <div class="quest-bar">
          <span>🎯 Daily quest: ${qp.done}/${qp.target}${qp.claimed ? " ✅" : ""}</span>
          <div class="bar" style="flex:1"><div class="seg-mastered" style="width:${(qp.done / qp.target) * 100}%"></div></div>
        </div>
      </div>
      <button class="big green" id="btn-play">▶️ &nbsp;Play Adventure</button>
      <button class="big ghost" id="btn-learn">📚 &nbsp;Learn — explore the world</button>
      <div class="row">
        <button class="big violet" id="btn-bee-written">📝 Written Bee</button>
        <button class="big amber" id="btn-bee-oral">🎤 Oral Bee</button>
      </div>
      <div class="row">
        <button class="big ghost" id="btn-stickers">🎒 Stickers (${(S.stickers || []).length})</button>
        <button class="big ghost" id="btn-progress">📊 Progress</button>
        <button class="big ghost" id="btn-settings">⚙️</button>
      </div>`;
    $("#btn-play").onclick = () => startPractice();
    $("#btn-learn").onclick = renderLearnTopics;
    $("#btn-bee-written").onclick = () => startBee("written");
    $("#btn-bee-oral").onclick = () => startBee("oral");
    $("#btn-progress").onclick = renderProgress;
    $("#btn-settings").onclick = renderSettings;
    $("#btn-profile").onclick = profileOverlay;
    $("#btn-stickers").onclick = stickersOverlay;
  }

  function stickersOverlay() {
    const owned = S.stickers || [];
    const total = E.STICKERS.length;
    const el = document.createElement("div");
    el.className = "celebrate-overlay";
    el.innerHTML = `<div class="celebrate-box">
      <h2>🎒 Sticker book</h2>
      <p class="muted">${owned.length} of ${total} collected — win rounds & daily quests to earn more!</p>
      <div class="sticker-grid">
        ${E.STICKERS.map((s) => `<span class="sticker ${owned.includes(s) ? "" : "locked"}">${owned.includes(s) ? s : "❔"}</span>`).join("")}
      </div>
      <p class="muted">tap to close</p></div>`;
    el.onclick = () => el.remove();
    document.body.appendChild(el);
  }

  // ---------- learn mode (curriculum browser) ----------
  function learnFacts(topicId) {
    return E.topicFacts(S, topicId).slice().sort((a, b) => a.tier - b.tier);
  }
  function renderLearnTopics() {
    const topics = E.enabledTopics(S);
    show("#screen-learn");
    $("#screen-learn").innerHTML = `
      <div class="quiz-top">
        <button class="icon-btn" id="btn-back">← Back</button>
        <span class="stat">📚 Learn</span>
      </div>
      <div class="card">
        <h2>Pick an adventure</h2>
        <p class="muted" style="margin-bottom:12px">Flip through fact cards at your own pace — no scores, no timers. Quiz yourself whenever you're ready!</p>
        <div class="learn-grid">
          ${topics.map((t) => {
            const total = E.topicFacts(S, t.id).length;
            const seen = Math.min(S.learnPos[t.id] || 0, total);
            return `<button class="learn-tile" data-id="${t.id}">
              <span class="lt-emoji">${t.emoji}</span>
              <span class="lt-name">${esc(t.name)}</span>
              <span class="lt-count">${seen}/${total} explored</span>
            </button>`;
          }).join("")}
        </div>
      </div>`;
    $("#btn-back").onclick = renderHome;
    document.querySelectorAll(".learn-tile").forEach((b) => {
      b.onclick = () => renderLearnDeck(b.dataset.id, S.learnPos[b.dataset.id] || 0);
    });
  }
  function renderLearnDeck(topicId, idx) {
    const facts = learnFacts(topicId);
    if (!facts.length) return renderLearnTopics();
    idx = Math.max(0, Math.min(idx, facts.length - 1));
    S.learnPos[topicId] = Math.max(S.learnPos[topicId] || 0, idx + 1);
    S.metaUpdated = Date.now();
    save();
    const f = facts[idx];
    const m = topicMeta(topicId);
    let media = "";
    if (topicId === "flags") {
      media = `<div class="qmedia">${mediaHTML({ type: "flag", code: Q.flagCode(f.src.f), emoji: f.src.f }, true)}</div>`;
    } else if (f.teachMap) {
      media = `<div class="qmedia">${mapHTML(f.teachMap)}</div>`;
    }
    show("#screen-learn");
    $("#screen-learn").innerHTML = `
      <div class="quiz-top">
        <button class="icon-btn" id="btn-back">← Topics</button>
        <span class="stat">${m.emoji} ${idx + 1} / ${facts.length}</span>
        <span class="stat">${tierStars(f.tier).slice(0, 5)}</span>
      </div>
      <div class="card teach">
        <span class="topic-chip">${m.emoji} ${esc(m.name)}</span>
        ${media}
        ${f.teachQ
          ? `<div class="fact" style="font-size:1.05rem;margin-bottom:6px">${esc(f.teachQ)}</div><div class="teach-main">${esc(f.teachA)}</div>`
          : `<div class="teach-main">${esc(f.teachText)}</div>`}
        ${f.fact ? `<div class="fact">💡 ${esc(f.fact)}</div>` : ""}
        <button class="icon-btn small" id="btn-say" aria-label="read aloud">🔊 Read to me</button>
      </div>
      <div class="row learn-nav">
        <button class="big ghost" id="btn-prev" ${idx === 0 ? "disabled" : ""}>◀ Back</button>
        <button class="big green" id="btn-next-card">${idx === facts.length - 1 ? "Done! 🎉" : "Next ▶"}</button>
      </div>
      <button class="big violet" id="btn-quiz-topic">🎯 Quiz me on ${esc(m.name)}!</button>`;
    decorateMap($("#screen-learn"));
    $("#btn-back").onclick = renderLearnTopics;
    $("#btn-say").onclick = () => forceSpeak(f.teachText + (f.fact ? ". " + f.fact : ""));
    $("#btn-prev").onclick = () => renderLearnDeck(topicId, idx - 1);
    $("#btn-next-card").onclick = () =>
      idx === facts.length - 1 ? renderLearnTopics() : renderLearnDeck(topicId, idx + 1);
    $("#btn-quiz-topic").onclick = () => startPractice(topicId);
    speak(f.teachText);
  }

  // ---------- activities ----------
  function startPractice(topicId) {
    act = { kind: "practice", sess: E.newSession("practice", topicId), topicFilter: topicId || null };
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
    } else if (fact.teachMap) {
      media = `<div class="qmedia">${mapHTML(fact.teachMap)}</div>`;
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
    decorateMap($("#screen-quiz"));
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
        : `<span class="stat">${"❤️".repeat(Math.max(0, 3 - act.plan.strikes)) || "💔"}</span>`;
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
    if (q.kind === "mapclick") {
      body = `<div class="qmedia">${mapHTML({ ...q.map, clickable: true })}</div>`;
    } else if (q.kind === "typed") {
      const hasMic = !!(window.SpeechRecognition || window.webkitSpeechRecognition);
      body = `
        <div class="typed-row">
          <input id="typed-input" type="text" autocomplete="off" autocapitalize="off" placeholder="${hasMic ? "Say or type your answer" : "Type your answer"}" />
          ${hasMic ? `<button class="amber" id="btn-mic" aria-label="answer by voice">🎙️</button>` : ""}
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
    decorateMap($("#screen-quiz"));
    $("#btn-quit").onclick = quitActivity;
    $("#btn-say").onclick = () => forceSpeak(q.speak);
    if (q.kind === "mapclick") {
      const svg = $(".map-svg");
      svg.addEventListener("click", (e) => {
        if (act.answered) return;
        const p = e.target.closest("path[data-name]");
        if (!p) return;
        const clicked = p.getAttribute("data-name");
        const correct = clicked === q.map.target;
        svg.classList.remove("clickable");
        svg.querySelectorAll("path").forEach((el) => {
          const n = el.getAttribute("data-name");
          if (n === q.map.target) el.classList.add("good");
          else if (n === clicked && !correct) el.classList.add("bad");
        });
        resolveAnswer(correct ? "correct" : "wrong");
      });
    } else if (q.kind === "typed") {
      const input = $("#typed-input");
      input.focus();
      $("#btn-typed-go").onclick = () => submitTyped(input.value);
      input.onkeydown = (e) => { if (e.key === "Enter") submitTyped(input.value); };
      $("#btn-dontknow").onclick = () => submitTyped(null);
      const mic = $("#btn-mic");
      if (mic) mic.onclick = () => listen(input, mic);
    } else {
      document.querySelectorAll(".opt").forEach((b) => (b.onclick = () => submitMcq(+b.dataset.i)));
      const skip = $("#btn-skip");
      if (skip) skip.onclick = () => resolveAnswer("skip");
    }
    // oral rounds are moderator-read in a real bee — always speak the question
    if (act.kind === "oral") forceSpeak(q.speak);
    else speak(q.prompt);
  }

  // Voice answers via the browser's built-in speech recognition (no AI service).
  function listen(input, mic) {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR || act.answered) return;
    try {
      const rec = new SR();
      rec.lang = "en-US";
      rec.interimResults = false;
      rec.maxAlternatives = 3;
      mic.classList.add("listening");
      mic.textContent = "👂";
      rec.onresult = (e) => {
        mic.classList.remove("listening"); mic.textContent = "🎙️";
        const alts = Array.from(e.results[0]).map((r) => r.transcript);
        // pick the alternative that matches, else show the first
        const hit = alts.find((t) => act.q.accept && act.q.accept.includes(Q.normalize(t)));
        input.value = hit || alts[0] || "";
        if (input.value) submitTyped(input.value);
      };
      rec.onerror = rec.onend = () => { mic.classList.remove("listening"); mic.textContent = "🎙️"; };
      rec.start();
    } catch (e) {
      mic.classList.remove("listening"); mic.textContent = "🎙️";
    }
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
    const rankBefore = E.rank(S).name;
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
    if (events.some((e) => e.type === "levelup" || e.type === "badge" || e.type === "quest")) { confetti(26); sndLevel(); }
    if (E.rank(S).name !== rankBefore) {
      const r = E.rank(S);
      celebrate(r.emoji, "RANK UP!", `${S.name} is now a ${r.name}!`);
    }

    const good = result === "correct";
    const headline = good
      ? ["Awesome! ✅", "You got it! 🎉", "Brilliant! 🌟", "Nailed it! 💪"][Math.floor(Math.random() * 4)]
      : result === "skip" ? "Skipped ⏭️" : "Not quite ❌";
    const answerLine = good ? "" : `<div style="margin-top:4px">The answer is <b>${esc(q.answerText)}</b>.</div>`;
    const factLine = q.fact && act.kind !== "placement" ? `<div class="fact">💡 ${esc(q.fact)}</div>` : "";
    const xpLine = xpGain ? `<span class="xp-gain">+${xpGain} XP</span>` : "";
    const eventChips = events.map((e) =>
      e.type === "levelup" ? `<span class="event-chip">🎉 Level up! ${esc(topicMeta(e.topic).name)} → Tier ${e.tier}</span>`
      : e.type === "badge" ? `<span class="event-chip">${e.badge.emoji} Badge: ${esc(e.badge.name)}</span>`
      : e.type === "quest" ? `<span class="event-chip">🎯 Daily quest done! +30 XP ${e.sticker ? "· new sticker " + e.sticker : ""}</span>` : ""
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
      let stickerLine = "";
      if (acc >= 0.7 && s.i >= E.ROUND_LEN) {
        const st = E.awardSticker(S);
        if (st) { save(); stickerLine = `<p class="summary-line">New sticker: <span style="font-size:2rem">${st}</span></p>`; }
      }
      if (stars === 3) { confetti(30); sndLevel(); }
      html = `
        <div class="mascot">${stars === 3 ? "🏆" : stars === 2 ? "🌟" : "🌱"}</div>
        <h1>Round complete!</h1>
        <div class="stars">${"⭐".repeat(stars)}${"▫️".repeat(3 - stars)}</div>
        <p class="summary-line">${s.correct} / ${s.i} correct &nbsp;·&nbsp; +${s.xp} XP</p>
        ${stickerLine}`;
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
        <p class="muted">Oral rounds are spoken in a real bee — say or type the answer, no choices to lean on.</p>
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
    const kind = act.kind, tf = act.topicFilter;
    $("#btn-again").onclick = () => (kind === "practice" ? startPractice(tf) : startBee(kind));
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
        <button class="icon-btn" id="btn-print">🖨️ Review sheet</button>
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
        <p class="note">Green = mastered (long-term memory) · light green = solid · yellow = still learning. "Weak spots" list what the app is currently re-teaching. Use 🖨️ Review sheet for a printable miss list.</p>
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
    $("#btn-print").onclick = printReviewSheet;
  }

  // Printable miss list — what a coach would drill next.
  function printReviewSheet() {
    const groups = [];
    for (const t of E.enabledTopics(S)) {
      const rows = [];
      for (const f of Q.byTopic[t.id] || []) {
        const r = S.facts[f.id];
        if (!r || r.b === 0) continue;
        if (r.w > 0 && r.b <= 3) {
          rows.push({ label: E.factLabel(f), stats: `${r.c}✓ ${r.w}✗` });
        }
      }
      if (rows.length) groups.push({ name: `${t.emoji} ${t.name}`, rows });
    }
    let sheet = $("#print-sheet");
    if (!sheet) {
      sheet = document.createElement("div");
      sheet.id = "print-sheet";
      document.body.appendChild(sheet);
    }
    sheet.innerHTML = `
      <h1>GeoBee Quest — Review Sheet</h1>
      <p>${esc(S.name)} · ${new Date().toLocaleDateString()} · facts to practice (missed at least once, not yet solid)</p>
      ${groups.length ? groups.map((g) => `
        <h2>${esc(g.name)}</h2>
        <ul>${g.rows.map((r) => `<li>${esc(r.label)} <em>(${r.stats})</em></li>`).join("")}</ul>`).join("")
      : "<p><b>Nothing to review — everything practiced is solid! 🎉</b></p>"}`;
    window.print();
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
        <p class="note" id="sync-msg">One account for the family — sign in once on each device and every explorer's progress merges automatically.</p>`;
    }
    show("#screen-settings");
    $("#screen-settings").innerHTML = `
      <div class="quiz-top">
        <button class="icon-btn" id="btn-back">← Back</button>
        <span class="stat">⚙️ Settings</span>
      </div>
      <div class="card">
        <div class="field"><label>Explorer name</label><input id="set-name" type="text" maxlength="20" value="${esc(S.name)}" /></div>
        <div class="field"><label>Explorer buddy</label>
          <div class="avatar-grid" id="set-avatar-grid">
            ${AVATARS.map((a) => `<button class="avatar-pick ${S.avatar === a ? "sel" : ""}" data-a="${a}">${a}</button>`).join("")}
          </div>
        </div>
        <div class="toggle-row"><span class="t-label">🔊 Sounds</span><label class="switch"><input type="checkbox" id="tg-sound" ${S.settings.sound ? "checked" : ""}/><span></span></label></div>
        <div class="toggle-row"><span class="t-label">🗣️ Read questions aloud</span><label class="switch"><input type="checkbox" id="tg-speech" ${S.settings.speech ? "checked" : ""}/><span></span></label></div>
        <div class="toggle-row"><span class="t-label">⌨️ Spelling mode (type answers you know well)</span><label class="switch"><input type="checkbox" id="tg-typed" ${S.settings.typed ? "checked" : ""}/><span></span></label></div>
        <div class="toggle-row"><span class="t-label">🚀 Advanced mode (senior-bee level)</span><label class="switch"><input type="checkbox" id="tg-advanced" ${S.settings.advanced ? "checked" : ""}/><span></span></label></div>
        <p class="note">Advanced mode unlocks the next competition levels: straits, ocean currents, peninsulas & capes, plus two whole topics — 🌾 Products & Trade and ⛵ Explorers & Journeys.</p>
      </div>
      <div class="card">
        <h2>My competition 🐝</h2>
        <div class="field"><label>Bee name (optional)</label><input id="set-beename" type="text" maxlength="40" placeholder="e.g. NSF Junior Geography Bee" value="${esc(S.settings.beeName || "")}" /></div>
        <div class="field"><label>Bee date</label><input id="set-beedate" type="date" value="${esc(S.settings.beeDate || "")}" /></div>
        <p class="note">Setting a date turns on the countdown coach: a daily plan that paces new material to finish ~10 days before the bee, leaving pure review time.</p>
      </div>
      <div class="card">
        <h2>Topics</h2>
        ${topics.filter((t) => !t.advOnly || S.settings.advanced).map((t) => `
          <div class="toggle-row"><span class="t-label">${t.emoji} ${esc(t.name)}${t.advOnly ? " 🚀" : ""}</span>
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
        <button class="berry" id="btn-reset">Reset this explorer's progress</button>
      </div>`;
    $("#btn-back").onclick = () => { applySettings(); renderHome(); };
    $("#set-avatar-grid").onclick = (e) => {
      const b = e.target.closest(".avatar-pick");
      if (!b) return;
      S.avatar = b.dataset.a;
      document.querySelectorAll("#set-avatar-grid .avatar-pick").forEach((x) => x.classList.remove("sel"));
      b.classList.add("sel");
      const m = window.Sync.meta();
      const p = m.profiles.find((x) => x.id === PID);
      if (p) { p.avatar = S.avatar; window.Sync.saveMeta(m); }
      save();
    };
    $("#tg-sound").onchange = (e) => { S.settings.sound = e.target.checked; save(); };
    $("#tg-speech").onchange = (e) => { S.settings.speech = e.target.checked; save(); };
    $("#tg-typed").onchange = (e) => { S.settings.typed = e.target.checked; save(); };
    $("#tg-advanced").onchange = (e) => {
      S.settings.advanced = e.target.checked;
      S.metaUpdated = Date.now();
      save();
      renderSettings(); // reveal/hide the advanced-only topic rows
    };
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
          // pull any profiles that exist in the cloud but not on this device
          const cloudProfiles = await window.Sync.fetchCloudProfiles().catch(() => []);
          const m = window.Sync.meta();
          for (const cp of cloudProfiles) {
            if (!m.profiles.some((p) => p.id === cp.id)) m.profiles.push(cp);
          }
          window.Sync.saveMeta(m);
          const merged = await window.Sync.load(PID);
          if (merged) S = E.migrate(merged);
          flush();
          renderSettings();
        } catch (e) { msg(e.message, true); }
      };
      su.onclick = async () => {
        try {
          msg("Creating account…");
          const d = await window.Sync.signUp($("#sync-email").value.trim(), $("#sync-pass").value);
          if (d.access_token) { flush(); renderSettings(); }
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
        S = E.migrate(E.mergeState(S, incoming));
        flush();
        alert("Backup restored and merged! ✅");
        renderHome();
      } catch (e) { alert("That code doesn't look right — paste the whole backup code."); }
    };
    $("#btn-reset").onclick = () => {
      if (!confirm("Really erase ALL progress for " + S.name + "? This cannot be undone.")) return;
      if (!confirm("Last chance — erase everything?")) return;
      const name = S.name, avatar = S.avatar;
      S = E.defaultState();
      S.name = name; S.avatar = avatar;
      flush();
      renderPlacementIntro();
    };
  }
  function applySettings() {
    const name = $("#set-name");
    if (name && name.value.trim()) {
      S.name = name.value.trim();
      const m = window.Sync.meta();
      const p = m.profiles.find((x) => x.id === PID);
      if (p) { p.name = S.name; window.Sync.saveMeta(m); }
    }
    const bn = $("#set-beename"), bd = $("#set-beedate");
    if (bn) S.settings.beeName = bn.value.trim();
    if (bd) S.settings.beeDate = bd.value || null;
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

  window.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden" && S && PID) window.Sync.flush(S, PID);
  });

  // ---------- boot ----------
  (async function boot() {
    const m = window.Sync.meta();
    if (!m.profiles.length) return renderWelcome(false);
    PID = m.active || m.profiles[0].id;
    let loaded = null;
    try { loaded = await window.Sync.load(PID); } catch (e) { loaded = window.Sync.readState(PID); }
    S = E.migrate(loaded || E.defaultState());
    const p = m.profiles.find((x) => x.id === PID);
    if (p) { S.name = S.name || p.name; S.avatar = S.avatar || p.avatar; }
    if (!S.name) renderWelcome(false);
    else if (!S.placementDone && S.totals.answered < 5) renderPlacementIntro();
    else renderHome();
  })();
})();
