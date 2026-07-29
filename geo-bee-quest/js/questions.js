/* GeoBee Quest — question bank.
 * Compiles the curated fact data into fact records, and renders each fact
 * into varied question forms (MCQ or typed) with plausible distractors
 * (same region / same continent / similar difficulty), mirroring how bee
 * questions are written. Deterministic logic only — no AI at runtime.
 */
window.QBank = (function () {
  const D = window.GEO_DATA;

  // --- utilities -----------------------------------------------------------
  function hash(s) {
    let h = 5381;
    for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
    return h.toString(36);
  }
  function normalize(s) {
    return String(s || "")
      .toLowerCase()
      .normalize("NFD").replace(/[̀-ͯ]/g, "")
      .replace(/[^a-z0-9 ]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/^the /, "");
  }
  function shuffle(arr) {
    const a = arr.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }
  // normalize() strips emoji to "" — fall back to the raw string so flag
  // options still deduplicate correctly.
  function optKey(v) { return normalize(v) || String(v); }
  function pickDistinct(pools, n, excludeLabels) {
    // pools: array of candidate arrays in priority order
    const out = [];
    const seen = new Set(excludeLabels.map(optKey));
    for (const pool of pools) {
      for (const cand of shuffle(pool)) {
        if (out.length >= n) return out;
        const key = optKey(cand.label !== undefined ? cand.label : cand);
        if (seen.has(key)) continue;
        seen.add(key);
        out.push(cand);
      }
    }
    return out;
  }
  function flagCode(emoji) {
    // Two regional-indicator symbols -> ISO 3166-1 alpha-2 code
    const pts = Array.from(emoji);
    if (pts.length < 2) return null;
    const A = 0x1f1e6;
    const c1 = pts[0].codePointAt(0), c2 = pts[1].codePointAt(0);
    if (c1 < A || c1 > A + 25 || c2 < A || c2 > A + 25) return null;
    return String.fromCharCode(97 + c1 - A, 97 + c2 - A);
  }

  // --- fact compilation ----------------------------------------------------
  const facts = [];
  const byId = {};
  const byTopic = {};

  function addFact(f) {
    facts.push(f);
    byId[f.id] = f;
    (byTopic[f.topic] = byTopic[f.topic] || []).push(f);
  }

  // US states
  for (const s of D.STATES) {
    addFact({
      id: "st:" + s.n, topic: "states", tier: s.t, src: s,
      forms: s.nick ? ["cap", "rev", "nick"] : ["cap", "rev"],
      label: s.n, teachText: `${s.c} is the capital of ${s.n} — "${s.nick}"`,
      fact: s.x || `${s.c} is the capital of ${s.n}, in the ${s.r} region. Its nickname is "${s.nick}".`,
    });
  }
  // Countries whose names take "the" mid-sentence
  const NEEDS_THE = new Set(["United States", "United Kingdom", "Netherlands", "Philippines", "United Arab Emirates", "Democratic Republic of the Congo"]);
  const art = (n) => (NEEDS_THE.has(n) ? "the " + n : n);

  // World capitals
  for (const c of D.COUNTRIES) {
    addFact({
      id: "cc:" + c.n, topic: "capitals", tier: c.ct || c.t, src: c,
      forms: ["cap", "rev"], label: c.n,
      teachText: `${c.c} is the capital of ${art(c.n)} ${c.f}`,
      fact: c.x || `${c.c} is the capital of ${art(c.n)}, a country in ${c.k}.`,
    });
  }
  // Flags (ft overrides tier: flag recognizability ≠ country fame)
  for (const c of D.COUNTRIES) {
    addFact({
      id: "fl:" + c.n, topic: "flags", tier: c.ft || c.t, src: c,
      forms: ["flag", "flagrev"], label: c.n,
      teachText: `This is the flag of ${art(c.n)} ${c.f}`,
      fact: `${c.f} is the flag of ${art(c.n)}, in ${c.k}. Its capital is ${c.c}.`,
    });
  }
  // Map topics (need js/maps.js — skipped gracefully if absent)
  if (window.GEO_MAPS) {
    const MU = window.GEO_MAPS.us.states;
    const bySize = Object.entries(MU).sort((a, b) => b[1].area - a[1].area).map(([n]) => n);
    const famous = new Set(["Washington", "California", "Texas", "Florida", "Alaska", "Hawaii", "New York"]);
    for (const s of D.STATES) {
      const m = MU[s.n];
      if (!m) continue;
      const tier = famous.has(s.n) ? 1 : Math.min(5, Math.floor(bySize.indexOf(s.n) / 10) + 1);
      addFact({
        id: "um:" + s.n, topic: "usmap", tier, src: s,
        // tiny states can't be tapped reliably — identify-only for them
        forms: m.area >= 900 ? ["find", "which"] : ["which"],
        label: s.n, teachQ: "Here it is on the map:", teachA: s.n,
        teachText: "This is " + s.n + " on the map!",
        teachMap: { kind: "us", highlight: s.n },
        fact: `${s.n} is in the ${s.r} region. Its capital is ${s.c}.`,
      });
    }
    const MW = window.GEO_MAPS.world.countries;
    for (const c of D.COUNTRIES) {
      if (!MW[c.n]) continue;
      addFact({
        id: "wm:" + c.n, topic: "worldmap", tier: c.t, src: c,
        forms: ["which"], label: c.n,
        teachQ: "Here it is on the map:", teachA: c.n + " " + c.f,
        teachText: "This is " + c.n + " on the map!",
        teachMap: { kind: "world", highlight: c.n },
        fact: `${c.n} is in ${c.k}. Its capital is ${c.c}.`,
      });
    }
  }

  // Curated item topics
  for (const t of D.TOPICS) {
    if (t.kind !== "items") continue;
    for (const it of t.items) {
      addFact({
        id: t.id + ":" + hash(it.q), topic: t.id, tier: it.t, src: it,
        forms: ["item"], label: it.a, adv: !!it.adv,
        teachQ: it.q, teachA: it.a,
        teachText: it.q + " " + it.a + "!",
        fact: it.x || null, item: it,
      });
    }
  }

  // --- distractor pools ----------------------------------------------------
  const stateCapsByRegion = {}, stateNamesByRegion = {};
  for (const s of D.STATES) {
    (stateCapsByRegion[s.r] = stateCapsByRegion[s.r] || []).push(s.c);
    (stateNamesByRegion[s.r] = stateNamesByRegion[s.r] || []).push(s.n);
  }
  const allStateCaps = D.STATES.map((s) => s.c);
  const allStateNames = D.STATES.map((s) => s.n);
  const countriesByCont = {};
  for (const c of D.COUNTRIES) (countriesByCont[c.k] = countriesByCont[c.k] || []).push(c);
  const allCountries = D.COUNTRIES;

  // --- question rendering --------------------------------------------------
  // Returns: { factId, topic, tier, kind, prompt, media, options, answerIdx,
  //            accept, fact, speak }
  // options: [{ label, media? }]; media: {type:'flag', code, emoji}
  function buildQuestion(fact, opts) {
    opts = opts || {};
    const form = opts.form || fact.forms[0];
    const typed = !!opts.typed;
    const s = fact.src;
    let prompt, answer, accept = [], media = null, optionMedia = null, pools = [];

    if (fact.topic === "usmap" && form === "find") {
      return {
        factId: fact.id, topic: fact.topic, tier: fact.tier, form,
        kind: "mapclick", prompt: `Find ${s.n}! Tap it on the map.`,
        map: { kind: "us", target: s.n }, answerText: s.n,
        fact: fact.fact, speak: `Find ${s.n} on the map.`,
      };
    }
    if (fact.topic === "usmap") {
      prompt = "Which state is highlighted on the map?";
      media = { type: "map", kind: "us", highlight: s.n };
      answer = s.n; accept = [s.n];
      pools = [stateNamesByRegion[s.r].filter((n) => n !== s.n), allStateNames];
    } else if (fact.topic === "worldmap") {
      prompt = "Which country is highlighted on the map?";
      media = { type: "map", kind: "world", highlight: s.n };
      answer = s.n; accept = countryAccept(s.n);
      const cont = countriesByCont[s.k].filter((c) => c.n !== s.n && window.GEO_MAPS.world.countries[c.n]);
      pools = [cont.map((c) => c.n), allCountries.map((c) => c.n)];
    } else if (fact.topic === "states") {
      const sameRegion = stateCapsByRegion[s.r].filter((c) => c !== s.c);
      const sameRegionNames = stateNamesByRegion[s.r].filter((n) => n !== s.n);
      if (form === "cap") {
        // rich stems (bee mode) embed a locating clue, NSF-style
        prompt = opts.rich ? `What is the capital of ${s.n}, a state in the ${s.r}?` : `What is the capital of ${s.n}?`;
        answer = s.c; pools = [sameRegion, allStateCaps];
        accept = [s.c];
      } else if (form === "rev") {
        prompt = opts.rich ? `${s.c} is the capital of which state in the ${s.r}?` : `${s.c} is the capital of which state?`;
        answer = s.n; pools = [sameRegionNames, allStateNames];
        accept = [s.n];
      } else {
        prompt = `Which state is nicknamed "${s.nick}"?`;
        answer = s.n; pools = [sameRegionNames, allStateNames];
        accept = [s.n];
      }
      if (s.n === "Washington" && form === "cap") accept.push("olympia washington");
    } else if (fact.topic === "capitals") {
      const cont = countriesByCont[s.k].filter((c) => c.n !== s.n);
      const near = cont.filter((c) => Math.abs((c.ct || c.t) - (s.ct || s.t)) <= 1);
      if (form === "cap") {
        prompt = opts.rich ? `What is the capital of ${art(s.n)}, a country in ${s.k}?` : `What is the capital of ${art(s.n)}?`;
        answer = s.c;
        pools = [near.map((c) => c.c), cont.map((c) => c.c), allCountries.map((c) => c.c)];
        accept = [s.c];
        if (s.c === "Washington, D.C.") accept.push("washington dc", "washington", "dc");
        if (s.c === "New Delhi") accept.push("delhi");
        if (s.c === "Mexico City") accept.push("mexico");
        if (s.c === "Ulaanbaatar") accept.push("ulan bator");
        if (s.c === "Kyiv") accept.push("kiev");
        if (s.c === "Nukuʻalofa") accept.push("nukualofa");
      } else {
        prompt = opts.rich ? `${s.c} is the capital of which country in ${s.k}?` : `${s.c} is the capital of which country?`;
        answer = s.n;
        pools = [near.map((c) => c.n), cont.map((c) => c.n), allCountries.map((c) => c.n)];
        accept = countryAccept(s.n);
      }
    } else if (fact.topic === "flags") {
      const cont = countriesByCont[s.k].filter((c) => c.n !== s.n);
      const near = cont.filter((c) => Math.abs(c.t - s.t) <= 1);
      if (form === "flag") {
        prompt = "Which country does this flag belong to?";
        media = { type: "flag", code: flagCode(s.f), emoji: s.f };
        answer = s.n;
        pools = [near.map((c) => c.n), cont.map((c) => c.n), allCountries.map((c) => c.n)];
        accept = countryAccept(s.n);
      } else {
        prompt = `Which of these is the flag of ${art(s.n)}?`;
        answer = s.f;
        optionMedia = (label) => {
          const c = allCountries.find((x) => x.f === label);
          return { type: "flag", code: flagCode(label), emoji: label, alt: c ? c.n : "" };
        };
        pools = [near.map((c) => c.f), cont.map((c) => c.f), allCountries.map((c) => c.f)];
        accept = []; // never typed
      }
    } else {
      // curated item
      const it = fact.item;
      prompt = it.q;
      answer = it.a;
      pools = [it.d];
      // odd-item-out only makes sense with visible options — never typed
      accept = it.odd ? [] : [it.a].concat(it.alt || []);
    }

    const q = {
      factId: fact.id, topic: fact.topic, tier: fact.tier, form,
      prompt, fact: fact.fact, media,
      speak: prompt,
    };

    if (typed && accept.length) {
      q.kind = "typed";
      q.answerText = answer;
      q.accept = accept.map(normalize);
    } else {
      q.kind = "mcq";
      // vs = two-option comparative; odd = three-option odd-item-out.
      // opts.optionCount caps total options (NSF mocks use 3, like the real exam).
      let nOpts = fact.item && fact.item.vs ? 1 : fact.item && fact.item.odd ? 2 : 3;
      if (opts.optionCount && !(fact.item && fact.item.vs)) nOpts = Math.min(nOpts, opts.optionCount - 1);
      const distractors = pickDistinct(pools, nOpts, [answer]);
      const options = shuffle(
        [answer].concat(distractors).map((label) => {
          const o = { label };
          if (optionMedia) o.media = optionMedia(label);
          return o;
        })
      );
      q.options = options;
      q.answerIdx = options.findIndex((o) => optKey(o.label) === optKey(answer));
      q.answerText = answer;
    }
    return q;
  }

  function countryAccept(name) {
    const extra = {
      "United States": ["usa", "us", "united states of america", "america"],
      "United Kingdom": ["uk", "great britain", "britain", "england"],
      "United Arab Emirates": ["uae", "emirates"],
      "Czechia": ["czech republic"],
      "Democratic Republic of the Congo": ["dr congo", "drc", "congo"],
      "The Bahamas": ["bahamas"],
      "Ivory Coast": ["cote divoire", "cote d ivoire"],
      "Papua New Guinea": ["png"],
    };
    return [name].concat(extra[name] || []);
  }

  // Progressive-clue pyramid question (IAC buzzer-round style).
  function buildPyramid(p, idx) {
    return {
      factId: null, topic: "pyramid", tier: p.t, form: "clues",
      kind: "clues", clues: p.clues,
      prompt: "Mystery place! Answer as early as you dare…",
      speak: p.clues[0],
      answerText: p.a,
      accept: [p.a].concat(p.alt || []).map(normalize),
      fact: null, pyIdx: idx,
    };
  }

  // Pick the least-practiced form for variety, preferring recall directions
  // the learner hasn't seen lately.
  function nextForm(fact, seenForms) {
    let best = fact.forms[0], bestCount = Infinity;
    for (const f of fact.forms) {
      const c = (seenForms && seenForms[f]) || 0;
      if (c < bestCount) { best = f; bestCount = c; }
    }
    return best;
  }

  return { facts, byId, byTopic, buildQuestion, buildPyramid, nextForm, normalize, flagCode };
})();
