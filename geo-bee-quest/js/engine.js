/* GeoBee Quest — adaptive learning engine ("the teacher").
 *
 * Per-fact learner model, Leitner-style spaced repetition:
 *   box 0 = never seen   → introduced with a teach card, then quizzed
 *   box 1-2 = learning   → short review intervals, missed facts return fast
 *   box 3   = known      → medium intervals
 *   box 4-5 = mastered   → long intervals, effectively retired from rotation
 *
 * Selection order each turn: session re-asks (missed minutes ago) →
 * overdue reviews → occasional challenge preview → new material at the
 * topic's current tier. Nothing is chosen at random from the whole bank,
 * so mastered content stops repeating.
 */
window.Engine = (function () {
  const Q = window.QBank;
  const DAY = 24 * 3600 * 1000;
  // Review intervals by box: 4h, 1d, 3d, 7d, 21d
  const INTERVALS = [0, 4 * 3600 * 1000, 1 * DAY, 3 * DAY, 7 * DAY, 21 * DAY];
  const MASTERED_BOX = 4;
  const ROUND_LEN = 10;

  const RANKS = [
    [0, "Rookie Explorer", "🐣"], [300, "Map Reader", "🗺️"],
    [800, "Trail Scout", "🥾"], [1600, "Navigator", "🧭"],
    [2800, "Globetrotter", "🌍"], [4500, "Geo Wizard", "🧙"],
    [7000, "Bee Champion", "🐝"],
  ];

  const BADGES = [
    { id: "first10", emoji: "🎉", name: "First Steps", desc: "Answer 10 questions", test: (s) => s.totals.answered >= 10 },
    { id: "streak10", emoji: "🔥", name: "On Fire", desc: "10 correct in a row", test: (s) => s.totals.bestStreak >= 10 },
    { id: "streak20", emoji: "⚡", name: "Unstoppable", desc: "20 correct in a row", test: (s) => s.totals.bestStreak >= 20 },
    { id: "continents", emoji: "🌍", name: "World Spinner", desc: "Know all continents & oceans facts", test: (s) => topicKnown(s, "continents") >= Q.byTopic.continents.length },
    { id: "states25", emoji: "🏛️", name: "Capital Collector", desc: "Know 25 US states", test: (s) => topicKnown(s, "states") >= 25 },
    { id: "states50", emoji: "🌟", name: "Fifty-State Star", desc: "Know all 50 US states", test: (s) => topicKnown(s, "states") >= 50 },
    { id: "flags25", emoji: "🚩", name: "Flag Finder", desc: "Know 25 flags", test: (s) => topicKnown(s, "flags") >= 25 },
    { id: "world40", emoji: "🏙️", name: "Capital Cruiser", desc: "Know 40 world capitals", test: (s) => topicKnown(s, "capitals") >= 40 },
    { id: "india15", emoji: "🐯", name: "India Explorer", desc: "Know 15 India facts", test: (s) => topicKnown(s, "india") >= 15 },
    { id: "written40", emoji: "📝", name: "Written-Round Ace", desc: "Ace a written mock (22+/25 NSF or 70+ IAC)", test: (s) => (s.best.nsf || 0) >= 22 || (s.best.iac || 0) >= 70 || (s.best.written || 0) >= 40 },
    { id: "oral10", emoji: "🎤", name: "Oral-Round Ace", desc: "Get 10+ in an oral bee", test: (s) => (s.best.oral || 0) >= 10 },
    { id: "summit", emoji: "🏔️", name: "Summit!", desc: "Reach tier 5 in any topic", test: (s) => Object.values(s.topics).some((t) => t.tier >= 5) },
  ];

  // Facts available for this learner: advanced content only in Advanced mode.
  function topicFacts(s, topicId) {
    const list = Q.byTopic[topicId] || [];
    return s.settings.advanced ? list : list.filter((f) => !f.adv);
  }
  // "Known" must be earned, not guessed: box >= 3 AND either two correct
  // answers or one produced (typed/spoken/map-click) correct, which a lucky
  // multiple-choice tap can't fake.
  function solidKnown(r) {
    return !!(r && r.b >= 3 && (r.v || r.c >= 2));
  }
  function topicKnown(s, topicId) {
    let n = 0;
    for (const f of topicFacts(s, topicId)) {
      if (solidKnown(s.facts[f.id])) n++;
    }
    return n;
  }

  function defaultState() {
    const topics = {};
    const enabled = {};
    for (const t of window.GEO_DATA.TOPICS) { topics[t.id] = { tier: 1 }; enabled[t.id] = true; }
    return {
      v: 1, name: "", avatar: "🌍", created: Date.now(), metaUpdated: Date.now(),
      settings: { sound: true, speech: false, typed: false, advanced: false, topics: enabled, beeDate: null, beeName: "" },
      topics, facts: {},
      xp: 0, badges: [], best: { written: null, oral: null, nsf: null, iac: null },
      totals: { answered: 0, correct: 0, bestStreak: 0 },
      log: [], placementDone: false,
      stickers: [], questDone: null, lastBee: null,
      learnPos: {},
    };
  }

  const STICKERS = ["🦁","🐼","🦊","🐨","🦉","🐬","🦋","🐢","🦜","🐙","🦒","🦘","🐧","🦅","🐳","🦭","🐆","🦩","🦔","🐫",
    "🗽","🗼","🏰","🌋","🗻","🏝️","🌈","⛩️","🎡","🚀","⛵","🎢","🛶","🚂","🎠","🌟","🍦","🧁","🏆","🎪"];
  const QUEST_TARGET = 20;
  function awardSticker(s) {
    const left = STICKERS.filter((x) => !(s.stickers || []).includes(x));
    if (!left.length) return null;
    const pick = left[Math.floor(Math.random() * left.length)];
    (s.stickers = s.stickers || []).push(pick);
    return pick;
  }
  function dayStreak(s) {
    if (!s.log || !s.log.length) return 0;
    let streak = 0;
    const d = new Date();
    for (;;) {
      const key = d.toISOString().slice(0, 10);
      if (s.log.some((e) => e.d === key && e.a > 0)) { streak++; d.setDate(d.getDate() - 1); }
      else break;
    }
    return streak;
  }
  function questProgress(s) {
    const day = new Date().toISOString().slice(0, 10);
    const e = (s.log || []).find((x) => x.d === day);
    return { done: e ? Math.min(QUEST_TARGET, e.a) : 0, target: QUEST_TARGET, claimed: s.questDone === day };
  }
  // The study coach: what should today look like, given the bee date?
  function coach(s) {
    const dueN = dueCount(s);
    let unseen = 0;
    for (const t of enabledTopics(s)) {
      for (const f of topicFacts(s, t.id)) {
        const r = s.facts[f.id];
        if (!r || r.b === 0) unseen++;
      }
    }
    let days = null, perDay = null;
    if (s.settings.beeDate) {
      const bd = new Date(s.settings.beeDate + "T12:00:00");
      days = Math.max(0, Math.ceil((bd - Date.now()) / 86400000));
      // aim to see everything with ~10 days to spare for pure review;
      // ~20 new facts/day is the age-appropriate ceiling
      const learnDays = Math.max(1, days - 10);
      perDay = Math.min(20, Math.ceil(unseen / learnDays));
    }
    const mockDue = !s.lastBee || Date.now() - s.lastBee > 6.5 * 86400000;
    return { days, dueN, unseen, perDay, mockDue };
  }

  // Fill in anything a state saved by an older version is missing.
  function migrate(s) {
    if (!s) return s;
    s.topics = s.topics || {};
    s.settings = s.settings || {};
    s.settings.topics = s.settings.topics || {};
    for (const t of window.GEO_DATA.TOPICS) {
      if (!s.topics[t.id]) s.topics[t.id] = { tier: 1 };
      if (s.settings.topics[t.id] === undefined) s.settings.topics[t.id] = true;
    }
    if (s.settings.advanced === undefined) s.settings.advanced = false;
    s.learnPos = s.learnPos || {};
    s.stickers = s.stickers || [];
    s.best = s.best || {};
    if (s.best.nsf === undefined) s.best.nsf = null;
    if (s.best.iac === undefined) s.best.iac = null;
    return s;
  }

  function rank(s) {
    let r = RANKS[0], next = null;
    for (let i = 0; i < RANKS.length; i++) {
      if (s.xp >= RANKS[i][0]) { r = RANKS[i]; next = RANKS[i + 1] || null; }
    }
    return { name: r[1], emoji: r[2], at: r[0], next };
  }

  function enabledTopics(s) {
    return window.GEO_DATA.TOPICS.filter(
      (t) => s.settings.topics[t.id] !== false && (!t.advOnly || s.settings.advanced)
    );
  }

  function rec(s, id) {
    return s.facts[id] || (s.facts[id] = { b: 0, c: 0, w: 0, last: 0, due: 0, forms: {} });
  }

  // Called when a fact card is viewed in Learn mode: mark it introduced so
  // practice quizzes it directly instead of re-teaching it.
  function noteSeen(s, factId) {
    const r = rec(s, factId);
    if (r.b === 0) { r.n = 1; r.last = Date.now(); }
    s.metaUpdated = Date.now();
  }

  // --- session -------------------------------------------------------------
  function newSession(mode, topicId, o) {
    return {
      mode: mode || "practice", topic: topicId || null,
      // noTeach: the learner just browsed these facts in Learn mode —
      // quiz directly instead of re-showing each fact as a teach card
      noTeach: !!(o && o.noTeach),
      // drill: fixed fact-id list (mock debrief / tricky-ones) — the session
      // serves exactly these facts plus their in-session re-asks
      drill: o && o.factIds ? o.factIds.slice() : null,
      asked: [], misses: [], reasks: 0, reviews: 0,
      i: 0, correct: 0, streak: 0, xp: 0, events: [], topicCursor: 0,
    };
  }
  function sessionTopics(s, sess) {
    return enabledTopics(s).filter((t) => !sess || !sess.topic || t.id === sess.topic);
  }

  // Choose the next question. Adventure is quiz-first: new facts are asked
  // cold, bee-style — a correct answer proves he knew it (no repetition),
  // a miss teaches via the answer reveal and earns a later re-ask.
  function nextQuestion(s, sess) {
    const now = Date.now();
    const askedSet = new Set(sess.asked);
    // 1. re-ask a fact missed >=3 questions ago — and at the end of the round
    //    (overtime), re-ask remaining misses immediately so no round ends on
    //    an uncorrected error
    const overtime = sess.i >= ROUND_LEN;
    const due = sess.misses.find((m) => overtime || sess.i - m.at >= 3);
    if (due) {
      sess.misses = sess.misses.filter((m) => m !== due);
      const f = Q.byId[due.factId];
      if (f) return makeQ(s, f, { reask: true });
    }
    // drill session: fixed fact list (e.g. mock debrief), nothing else;
    // once the list is done, immediately clear any remaining misses
    if (sess.drill) {
      const nextId = sess.drill.find((id) => !askedSet.has(id));
      const f = nextId && Q.byId[nextId];
      if (f) return makeQ(s, f, { review: true });
      if (sess.reasks >= 8) return null; // safety: never trap the learner
      const m = sess.misses.shift();
      const mf = m && Q.byId[m.factId];
      if (mf) sess.reasks++;
      return mf ? makeQ(s, mf, { reask: true }) : null;
    }
    // taper: in the last 10 days before the bee, no new material — pure
    // review; in the last 2 days, only confidence-polish (box >= 2)
    let beeDays = null;
    if (s.settings.beeDate) {
      beeDays = Math.ceil((new Date(s.settings.beeDate + "T12:00:00") - now) / 86400000);
    }
    const taper = beeDays !== null && beeDays >= 0 && beeDays <= 10;
    const polish = beeDays !== null && beeDays >= 0 && beeDays <= 2;
    // 2. overdue scheduled reviews — cap grows with the backlog (4..8)
    const dueN = dueCount(s);
    const cap = Math.max(4, Math.min(8, Math.ceil(dueN / 5)));
    if (sess.reviews < cap || taper) {
      const dueFacts = [];
      for (const t of sessionTopics(s, sess)) {
        for (const f of topicFacts(s, t.id)) {
          const r = s.facts[f.id];
          if (r && r.b >= (polish ? 2 : 1) && r.due && r.due <= now && !askedSet.has(f.id)) dueFacts.push([r.due, f]);
        }
      }
      if (dueFacts.length) {
        dueFacts.sort((a, b) => a[0] - b[0]);
        sess.reviews++;
        return makeQ(s, dueFacts[0][1], { review: true });
      }
    }
    // big backlog or bee taper → review-only rounds, no new material
    if (!taper && dueN <= 25) {
      // 3. occasional challenge preview from one tier up
      if (Math.random() < 0.12) {
        const f = pickNew(s, askedSet, +1, sessionTopics(s, sess));
        if (f) return makeQ(s, f, { challenge: true });
      }
      // 4. new material at current tier, asked cold (marked 🆕 in the UI)
      const f = pickNew(s, askedSet, 0, sessionTopics(s, sess));
      if (f) {
        const r = s.facts[f.id];
        const isNew = !r || (r.b === 0 && !r.c && !r.w);
        return makeQ(s, f, isNew && !sess.noTeach ? { fresh: true } : {});
      }
    }
    // 5. fallback: soonest-due or weakest fact
    let best = null, bestScore = Infinity;
    for (const t of sessionTopics(s, sess)) {
      for (const fx of topicFacts(s, t.id)) {
        const r = s.facts[fx.id];
        if (!r || askedSet.has(fx.id)) continue;
        const score = (r.due || 0) - (r.w - r.c) * DAY;
        if (score < bestScore) { bestScore = score; best = fx; }
      }
    }
    return best ? makeQ(s, best, { review: true }) : null;
  }

  function pickNew(s, askedSet, tierOffset, topicList) {
    const topics = topicList || enabledTopics(s);
    if (!topics.length) return null;
    // Round-robin over topics; within a topic take unseen facts,
    // lowest tier first, up to the topic's current tier (+offset).
    for (let k = 0; k < topics.length; k++) {
      const t = topics[(pickNew._cursor + k) % topics.length];
      const maxTier = Math.min(5, (s.topics[t.id].tier || 1) + tierOffset);
      if (tierOffset > 0 && maxTier <= s.topics[t.id].tier) continue;
      const pool = topicFacts(s, t.id)
        .filter((f) => {
          const r = s.facts[f.id];
          return (!r || r.b === 0) && !askedSet.has(f.id) &&
            (tierOffset > 0 ? f.tier === maxTier : f.tier <= maxTier);
        })
        .sort((a, b) => a.tier - b.tier);
      if (pool.length) {
        if (tierOffset === 0) pickNew._cursor = (pickNew._cursor + k + 1) % topics.length;
        // small shuffle within the lowest tier so ordering isn't alphabetical;
        // facts studied in Learn mode come first — quiz what was just studied
        const lowest = pool.filter((f) => f.tier === pool[0].tier);
        const noted = lowest.filter((f) => { const r = s.facts[f.id]; return r && r.n; });
        const pickFrom = noted.length ? noted : lowest;
        return pickFrom[Math.floor(Math.random() * pickFrom.length)];
      }
    }
    return null;
  }
  pickNew._cursor = 0;

  function makeQ(s, fact, flags) {
    const r = rec(s, fact.id);
    const form = Q.nextForm(fact, r.forms);
    // typed recall for well-known facts when spelling mode is on
    const typed = !!(s.settings.typed && r.b >= 3 && fact.forms[0] !== "flagrev");
    const q = Q.buildQuestion(fact, { form, typed });
    q.flags = flags || {};
    return q;
  }

  // --- answering -----------------------------------------------------------
  function record(s, sess, q, correct) {
    const now = Date.now();
    const f = Q.byId[q.factId];
    const r = rec(s, q.factId);
    const events = [];

    r.forms[q.form] = (r.forms[q.form] || 0) + 1;
    r.last = now;
    if (correct) {
      r.c++;
      // produced answers (typed / spoken / map-click) can't be guessed —
      // they verify real recall
      if (q.kind === "typed" || q.kind === "mapclick") r.v = 1;
      const fastGrad = r.b === 0 && f.tier < (s.topics[f.topic].tier || 1);
      // knew a brand-new fact cold → strong evidence, skip the 4h re-check
      // (but one exposure never jumps straight to "known" — that must be earned)
      const coldRight = r.b === 0 && r.c === 1 && r.w === 0;
      r.b = fastGrad || coldRight ? 2 : Math.min(5, r.b + 1);
      r.due = now + INTERVALS[Math.min(r.b, 5)];
    } else {
      r.w++;
      r.b = Math.max(1, r.b - 2);
      r.due = now + INTERVALS[1];
    }

    if (sess) {
      sess.i++;
      sess.asked.push(q.factId);
      if (correct) {
        sess.correct++;
        sess.streak++;
        let gain = 10 * q.tier;
        if (q.flags.challenge) gain *= 2;
        if (sess.streak >= 3) gain += 5;
        sess.xp += gain; s.xp += gain;
        if (sess.streak > s.totals.bestStreak) s.totals.bestStreak = sess.streak;
      } else {
        sess.streak = 0;
        if (sess.mode === "practice") sess.misses.push({ factId: q.factId, at: sess.i });
      }
    }
    s.totals.answered++;
    if (correct) s.totals.correct++;

    // daily log
    const day = new Date().toISOString().slice(0, 10);
    let entry = s.log[s.log.length - 1];
    if (!entry || entry.d !== day) { entry = { d: day, a: 0, c: 0, xp: 0 }; s.log.push(entry); }
    entry.a++; if (correct) entry.c++;
    if (sess && correct) entry.xp += 10 * q.tier;
    if (s.log.length > 90) s.log = s.log.slice(-90);

    // tier promotion: 80% of this tier's facts solidly known → move up
    const t = s.topics[f.topic];
    const tierFacts = topicFacts(s, f.topic).filter((x) => x.tier === t.tier);
    if (tierFacts.length) {
      const known = tierFacts.filter((x) => solidKnown(s.facts[x.id])).length;
      if (known / tierFacts.length >= 0.8 && t.tier < 5) {
        t.tier++;
        events.push({ type: "levelup", topic: f.topic, tier: t.tier });
      }
    }
    // badges
    for (const b of BADGES) {
      if (!s.badges.includes(b.id) && b.test(s)) {
        s.badges.push(b.id);
        events.push({ type: "badge", badge: b });
      }
    }
    // daily quest complete?
    const qp = questProgress(s);
    if (qp.done >= qp.target && !qp.claimed) {
      s.questDone = day;
      s.xp += 30;
      const sticker = awardSticker(s);
      events.push({ type: "quest", sticker });
    }
    s.metaUpdated = now;
    return events;
  }

  // --- placement diagnostic ------------------------------------------------
  function placementPlan(s) {
    const topics = enabledTopics(s).map((t) => t.id);
    return { tier: 2, i: 0, n: 12, results: [], topics, cursor: 0 };
  }
  function placementNext(s, p) {
    if (p.i >= p.n) return null;
    for (let k = 0; k < p.topics.length * 2; k++) {
      const tid = p.topics[(p.cursor + k) % p.topics.length];
      const pool = topicFacts(s, tid).filter((f) => f.tier === p.tier);
      if (pool.length) {
        p.cursor = (p.cursor + k + 1) % p.topics.length;
        const f = pool[Math.floor(Math.random() * pool.length)];
        return Q.buildQuestion(f, { form: Q.nextForm(f, null) });
      }
    }
    return null;
  }
  function placementRecord(s, p, q, correct) {
    p.results.push({ tier: p.tier, correct });
    const r = rec(s, q.factId);
    r.last = Date.now();
    if (correct) { r.c++; r.b = 3; r.due = Date.now() + INTERVALS[3]; }
    else { r.w++; r.b = 1; r.due = Date.now() + INTERVALS[1]; }
    p.tier = Math.max(1, Math.min(5, p.tier + (correct ? 1 : -1)));
    p.i++;
    s.totals.answered++; if (correct) s.totals.correct++;
  }
  function placementFinish(s, p) {
    const tail = p.results.slice(-6);
    const avg = tail.reduce((a, r) => a + r.tier, 0) / Math.max(1, tail.length);
    const start = Math.max(1, Math.min(3, Math.round(avg) - 1));
    for (const t of enabledTopics(s)) s.topics[t.id].tier = start;
    s.placementDone = true;
    s.metaUpdated = Date.now();
    return start;
  }

  // --- bee simulations -----------------------------------------------------
  // Three honest formats, calibrated against the official 2024 JGB sample set:
  //   nsf  — NSF Junior Phase I style: 25 questions, +1 per correct, NO
  //          negative marking (always guess!). Mix mirrors the official
  //          sample: ~12 three-option MCQs, ~8 open-ended (typed), ~5
  //          two-option — with clue-rich stems.
  //   iac  — IAC/IGB qualifying-exam style: 50 four-option MCQs, +2/−1/0
  //          with skip, 30-minute timer (enforced by the UI)
  //   oral — spoken-round practice: typed/spoken answers, 3 strikes,
  //          with progressive-clue "pyramid" questions mixed in (buzzer style)
  const PYRAMID_SLOTS = [2, 6, 10, 14];
  // m = 3-option MCQ, t = open-ended/typed, v = two-option (12/8/5, interleaved)
  const NSF_SLOTS = ["m","t","m","v","m","t","m","m","t","v","m","t","m","m","t","v","m","t","m","v","m","t","t","v","m"];
  function beePlan(kind, s) {
    const topics = enabledTopics(s).map((t) => t.id);
    let tiers;
    if (kind === "nsf") tiers = [1,1,1,2,2,2,2,2,3,3,3,3,3,3,3,3,3,4,4,4,4,4,4,5,5];
    else if (kind === "iac") tiers = [1,1,1,1,1,1,2,2,2,2,2,2,2,2,2,2,3,3,3,3,3,3,3,3,3,3,3,3,3,3,4,4,4,4,4,4,4,4,4,4,4,4,5,5,5,5,5,5,5,5];
    else tiers = [1,1,2,2,2,3,3,3,3,4,4,4,5,5,5];
    return {
      kind, tiers, i: 0, score: 0, correct: 0, strikes: 0,
      asked: new Set(), topics, cursor: 0,
      pyUsed: new Set(), clueBonus: 0,
      timeLimit: kind === "iac" ? 30 * 60 : null,
    };
  }
  function beeNext(s, plan) {
    if (plan.i >= plan.tiers.length) return null;
    if (plan.kind === "oral" && plan.strikes >= 3) return null;
    // oral: mystery pyramid questions at fixed slots
    if (plan.kind === "oral" && PYRAMID_SLOTS.includes(plan.i)) {
      const pool = window.GEO_DATA.PYRAMIDS.map((p, i) => i).filter((i) => !plan.pyUsed.has(i));
      if (pool.length) {
        const idx = pool[Math.floor(Math.random() * pool.length)];
        plan.pyUsed.add(idx);
        return Q.buildPyramid(window.GEO_DATA.PYRAMIDS[idx], idx);
      }
    }
    const written = plan.kind === "nsf" || plan.kind === "iac";
    const tier = plan.tiers[plan.i];
    const search = (slot) => {
      for (let dt = 0; dt <= 4; dt++) {
        for (const off of [0, -dt, dt]) {
          const want = tier + off;
          if (want < 1 || want > 5) continue;
          for (let k = 0; k < plan.topics.length; k++) {
            const tid = plan.topics[(plan.cursor + k) % plan.topics.length];
            let pool = topicFacts(s, tid).filter((f) => f.tier === want && !plan.asked.has(f.id));
            // flag ID isn't a written-exam category in either bee, nor spoken-round material
            if (written || plan.kind === "oral") pool = pool.filter((f) => f.topic !== "flags");
            if (slot === "v") pool = pool.filter((f) => f.item && f.item.vs);
            else pool = pool.filter((f) => !(f.item && f.item.vs));
            // typed slots need an acceptable typed answer (odd items have none)
            if (slot === "t" || plan.kind === "oral") pool = pool.filter((f) => !(f.item && f.item.odd));
            if (pool.length) {
              plan.cursor = (plan.cursor + k + 1) % plan.topics.length;
              const f = pool[Math.floor(Math.random() * pool.length)];
              plan.asked.add(f.id);
              // tap-the-map isn't an exam form; highlighted-map ID is fine
              let form = Q.nextForm(f, (s.facts[f.id] || {}).forms);
              if (form === "find") form = "which";
              return Q.buildQuestion(f, {
                form,
                typed: plan.kind === "oral" || slot === "t",
                optionCount: plan.kind === "nsf" ? 3 : undefined,
                rich: written || plan.kind === "oral",
              });
            }
          }
        }
      }
      return null;
    };
    // NSF: this slot's question style (3-option MCQ / typed / two-option),
    // falling back to a regular MCQ if the two-option pool runs dry.
    const slot = plan.kind === "nsf" ? NSF_SLOTS[plan.i] : null;
    return search(slot) || (slot === "v" ? search("m") : null);
  }
  function beeRecord(s, plan, q, result) {
    // result: 'correct' | 'wrong' | 'skip' (skip only exists in iac)
    plan.i++;
    if (result === "correct") {
      plan.correct++;
      plan.score += plan.kind === "iac" ? 2 : 1;
    } else if (result === "wrong") {
      if (plan.kind === "iac") plan.score -= 1;
      plan.strikes++;
    }
    // debrief list: every non-correct question, for the post-mock review
    if (result !== "correct" && q.kind !== "clues") {
      (plan.review = plan.review || []).push({
        factId: q.factId, prompt: q.prompt, answer: q.answerText, fact: q.fact || null,
      });
    }
    // pyramid questions aren't facts in the learner model
    if (result !== "skip" && q.kind !== "clues") record(s, null, q, result === "correct");
    return plan;
  }
  function beeFinish(s, plan) {
    const events = [];
    const key = plan.kind === "oral" ? "oral" : plan.kind;
    const val = plan.kind === "oral" ? plan.correct : plan.score;
    if (s.best[key] == null || val > s.best[key]) { s.best[key] = val; events.push({ type: "best" }); }
    for (const b of BADGES) {
      if (!s.badges.includes(b.id) && b.test(s)) { s.badges.push(b.id); events.push({ type: "badge", badge: b }); }
    }
    s.lastBee = Date.now();
    s.metaUpdated = Date.now();
    return events;
  }

  // --- dashboard -----------------------------------------------------------
  function topicSummary(s) {
    const out = [];
    for (const t of enabledTopics(s)) {
      const facts = topicFacts(s, t.id);
      let mastered = 0, known = 0, learning = 0, weak = 0, unseen = 0;
      const weakest = [];
      for (const f of facts) {
        const r = s.facts[f.id];
        if (!r || r.b === 0) { unseen++; continue; }
        // dashboard honesty: box alone isn't "known" — it must be solid
        if (r.b >= MASTERED_BOX && solidKnown(r)) mastered++;
        else if (r.b >= 3 && solidKnown(r)) known++;
        else { learning++; if (r.w > 0) { weak++; weakest.push([r.w - r.c, f, r]); } }
      }
      weakest.sort((a, b) => b[0] - a[0]);
      out.push({
        id: t.id, name: t.name, emoji: t.emoji, tier: s.topics[t.id].tier,
        total: facts.length, mastered, known, learning, weak, unseen,
        pct: facts.length ? Math.round(((mastered + known) / facts.length) * 100) : 0,
        weakest: weakest.slice(0, 5).map(([, f, r]) => ({ label: factLabel(f), c: r.c, w: r.w })),
      });
    }
    return out;
  }
  function factLabel(f) {
    if (f.topic === "states") return `${f.src.n} → ${f.src.c}`;
    if (f.topic === "capitals") return `${f.src.n} → ${f.src.c}`;
    if (f.topic === "flags") return `Flag of ${f.src.n}`;
    return f.item ? f.item.q : f.label;
  }
  function dueCount(s) {
    const now = Date.now();
    let n = 0;
    for (const t of enabledTopics(s)) {
      for (const f of topicFacts(s, t.id)) {
        const r = s.facts[f.id];
        if (r && r.b >= 1 && r.due && r.due <= now) n++;
      }
    }
    return n;
  }

  // --- multi-device merge --------------------------------------------------
  function mergeState(a, b) {
    if (!a) return b; if (!b) return a;
    const out = JSON.parse(JSON.stringify(a.metaUpdated >= b.metaUpdated ? a : b));
    const other = a.metaUpdated >= b.metaUpdated ? b : a;
    // facts: latest-touched record wins
    for (const [id, r] of Object.entries(other.facts || {})) {
      const mine = out.facts[id];
      if (!mine || (r.last || 0) > (mine.last || 0)) out.facts[id] = r;
    }
    // topics: keep the higher tier
    for (const [id, t] of Object.entries(other.topics || {})) {
      if (!out.topics[id] || t.tier > out.topics[id].tier) out.topics[id] = t;
    }
    out.xp = Math.max(a.xp || 0, b.xp || 0);
    out.badges = Array.from(new Set([...(a.badges || []), ...(b.badges || [])]));
    out.best = {
      written: maxOrNull(a.best && a.best.written, b.best && b.best.written),
      oral: maxOrNull(a.best && a.best.oral, b.best && b.best.oral),
      nsf: maxOrNull(a.best && a.best.nsf, b.best && b.best.nsf),
      iac: maxOrNull(a.best && a.best.iac, b.best && b.best.iac),
    };
    out.totals = {
      answered: Math.max(a.totals.answered, b.totals.answered),
      correct: Math.max(a.totals.correct, b.totals.correct),
      bestStreak: Math.max(a.totals.bestStreak, b.totals.bestStreak),
    };
    out.placementDone = a.placementDone || b.placementDone;
    out.stickers = Array.from(new Set([...(a.stickers || []), ...(b.stickers || [])]));
    out.lastBee = Math.max(a.lastBee || 0, b.lastBee || 0) || null;
    out.questDone = [a.questDone, b.questDone].filter(Boolean).sort().pop() || null;
    out.learnPos = out.learnPos || {};
    for (const [k, v] of Object.entries({ ...(a.learnPos || {}), ...(b.learnPos || {}) })) {
      out.learnPos[k] = Math.max(v, (a.learnPos || {})[k] || 0, (b.learnPos || {})[k] || 0);
    }
    // log: union by day, keep larger counts
    const byDay = {};
    for (const e of [...(a.log || []), ...(b.log || [])]) {
      const cur = byDay[e.d];
      if (!cur || e.a > cur.a) byDay[e.d] = e;
    }
    out.log = Object.values(byDay).sort((x, y) => x.d < y.d ? -1 : 1).slice(-90);
    return out;
  }
  function maxOrNull(x, y) {
    if (x == null) return y == null ? null : y;
    if (y == null) return x;
    return Math.max(x, y);
  }

  return {
    ROUND_LEN, RANKS, BADGES, STICKERS, defaultState, rank, enabledTopics,
    newSession, nextQuestion, record,
    placementPlan, placementNext, placementRecord, placementFinish,
    beePlan, beeNext, beeRecord, beeFinish,
    topicSummary, dueCount, mergeState, factLabel, topicKnown, topicFacts, migrate,
    awardSticker, dayStreak, questProgress, coach, noteSeen,
  };
})();
