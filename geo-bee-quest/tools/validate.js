#!/usr/bin/env node
/* GeoBee Quest content + engine validator.
 * Run from the repo root: node tools/validate.js
 * Loads the real app files in Node and enforces the invariants that keep
 * content bee-calibrated and the Field Book a true textbook. CI-style: exits
 * non-zero with a list of errors if anything regresses.
 */
"use strict";
global.window = {};
const fs = require("fs");
const path = require("path");
const base = path.join(__dirname, "..", "js") + path.sep;
for (const f of ["data.js", "scenes.js", "maps.js", "questions.js", "engine.js"]) {
  eval(fs.readFileSync(base + f, "utf8"));
}
const D = window.GEO_DATA, Q = window.QBank, E = window.Engine;
const errors = [];

// ---------- data shape ----------
const ids = new Set();
for (const f of Q.facts) {
  if (ids.has(f.id)) errors.push("duplicate fact id: " + f.id);
  ids.add(f.id);
  if (!(f.tier >= 1 && f.tier <= 5)) errors.push("bad tier: " + f.id);
}
for (const t of D.TOPICS) {
  if (t.kind !== "items") continue;
  for (const it of t.items) {
    const wantD = it.vs ? 1 : it.odd ? 2 : 3;
    if (!Array.isArray(it.d) || it.d.length !== wantD)
      errors.push(`item needs ${wantD} distractors: ${t.id}: ${String(it.q).slice(0, 50)}`);
    if (it.d && it.d.some((x) => Q.normalize(String(x)) === Q.normalize(String(it.a))))
      errors.push(`distractor equals answer: ${t.id}: ${String(it.q).slice(0, 50)}`);
  }
}
for (const p of D.PYRAMIDS) {
  if (!p.clues || p.clues.length !== 4) errors.push("pyramid clues != 4: " + p.a);
}

// ---------- every fact renders through every form ----------
let variants = 0;
for (const f of Q.facts) {
  for (const form of f.forms) {
    for (const typed of [false, true]) {
      let q;
      try { q = Q.buildQuestion(f, { form, typed }); }
      catch (e) { errors.push(`buildQuestion threw: ${f.id}/${form}: ${e.message}`); continue; }
      variants++;
      if (!q.prompt || q.prompt.length < 8) errors.push(`missing prompt text: ${f.id}/${form}`);
      if (q.kind === "mcq") {
        if (!q.options || q.options.length < 2) errors.push(`too few options: ${f.id}/${form}`);
        if (q.answerIdx < 0) errors.push(`answer not among options: ${f.id}/${form}`);
        const keys = new Set(q.options.map((o) => Q.normalize(o.label) || String(o.label)));
        if (keys.size !== q.options.length) errors.push(`duplicate options: ${f.id}/${form}`);
      }
      if (q.kind === "typed" && (!q.accept || !q.accept.length)) errors.push(`typed with no accept: ${f.id}/${form}`);
    }
  }
}

// ---------- Field Book: sections + textbook prose ----------
for (const t of D.TOPICS) {
  if (t.scene && !["usmap", "worldmap"].includes(t.scene) && !window.GEO_SCENES[t.scene])
    errors.push(`unknown chapter scene: ${t.id}: ${t.scene}`);
  if (!t.intro || t.intro.length < 80) errors.push(`chapter intro missing/short: ${t.id}`);
  if (t.secs) {
    if (!t.items) { errors.push(`secs without items: ${t.id}`); continue; }
    let prev = -1;
    for (let si = 0; si < t.secs.length; si++) {
      const s = t.secs[si];
      if (!s.n || !s.intro || s.intro.length < 40) errors.push(`section missing name/intro: ${t.id}/${s.n}`);
      if (s.scene && !window.GEO_SCENES[s.scene]) errors.push(`unknown scene: ${t.id}/${s.n}: ${s.scene}`);
      if (!(s.from > prev)) errors.push(`section from not increasing: ${t.id}/${s.n}`);
      if (s.from >= t.items.length) errors.push(`section from out of range: ${t.id}/${s.n}`);
      prev = s.from;
      const to = si + 1 < t.secs.length ? t.secs[si + 1].from : t.items.length;
      const secItems = t.items.slice(s.from, to);
      const hasBaseItems = secItems.some((it) => !it.adv);
      const hasAdvItems = secItems.some((it) => it.adv);
      if (!Array.isArray(s.read) || !s.read.length) { errors.push(`section has no read pages: ${t.id}/${s.n}`); continue; }
      let basePages = 0, advPages = 0;
      for (const pg of s.read) {
        const text = typeof pg === "string" ? pg : pg.p;
        const isAdv = typeof pg === "object" && pg.adv;
        if (!text || text.length < 40) errors.push(`read page too short: ${t.id}/${s.n}`);
        if (/\?/.test(text || "")) errors.push(`read page contains a question mark: ${t.id}/${s.n}`);
        if (typeof pg === "object" && pg.art && !window.GEO_SCENES[pg.art]) errors.push(`unknown art: ${t.id}/${s.n}: ${pg.art}`);
        if (isAdv) advPages++; else basePages++;
      }
      if (hasBaseItems && !basePages) errors.push(`section with base items has no base pages: ${t.id}/${s.n}`);
      if (hasAdvItems && !advPages) errors.push(`section with advanced items has no adv pages: ${t.id}/${s.n}`);
      if (!hasAdvItems && advPages) errors.push(`adv pages but no adv items: ${t.id}/${s.n}`);
    }
    if (t.secs[0].from !== 0) errors.push(`first section must start at 0: ${t.id}`);
  } else if (t.kind === "items") {
    errors.push(`item chapter has no sections/prose: ${t.id}`);
  }
}

// ---------- official 2024 NSF JGB sample: 15/15 must stay answerable ----------
const OFFICIAL = [
  ["Q1 Pontchartrain", (f) => /Pontchartrain/.test(f.teachQ || "")],
  ["Q2 Porto/Algarve", (f) => /Porto|Algarve/.test(f.teachQ || "")],
  ["Q3 Gateway of India", (f) => /Gateway of India/.test(f.teachQ || "")],
  ["Q4 Luzon", (f) => /Luzon/.test(f.teachQ || "")],
  ["Q5 Altiplano/Bolivia", (f) => /Altiplano|two capitals/.test(f.teachQ || "")],
  ["Q6 Transylvania", (f) => /Transylvania/.test(f.teachQ || "")],
  ["Q7 East Siberian Sea", (f) => /East Siberian/.test(f.teachQ || "")],
  ["Q8 Geneva/Switzerland", (f) => /Geneva/.test(f.teachQ || "")],
  ["Q9 Rann of Kutch", (f) => /Rann of Kutch/.test(f.teachQ || "")],
  ["Q10 Guantanamo", (f) => /Guantanamo/.test(f.teachQ || "")],
  ["Q11 Tiber/Rome", (f) => /Tiber/.test(f.teachQ || "")],
  ["Q12 global warming vs El Nino", (f) => /main cause/.test(f.teachQ || "")],
  ["Q13 Arctic warming faster", (f) => /warming faster/.test(f.teachQ || "")],
  ["Q14 glacier", (f) => /slow-moving (river|mass) of ice/.test(f.teachQ || "")],
  ["Q15 lake", (f) => /surrounded by land/.test(f.teachQ || "")],
];
for (const [label, test] of OFFICIAL) {
  if (!Q.facts.some(test)) errors.push(`official sample question no longer answerable: ${label}`);
}

// ---------- engine flows ----------
{
  const s = E.migrate(E.defaultState());
  s.placementDone = true;
  // practice round: terminates, no teach cards, no in-round repeats of unmissed facts
  const sess = E.newSession("practice");
  const seenIds = new Set(), seenAnswers = new Set();
  for (let i = 0; i < 20; i++) {
    const q = E.nextQuestion(s, sess);
    if (!q) break;
    if (q.teach) errors.push("practice served a teach card");
    if (!q.flags.reask && seenIds.has(q.factId)) errors.push("unmissed fact repeated in round: " + q.factId);
    const aKey = Q.normalize(q.answerText || "");
    if (!q.flags.reask && aKey && seenAnswers.has(aKey)) errors.push("answer repeated in round: " + q.answerText);
    seenIds.add(q.factId); seenAnswers.add(aKey);
    E.record(s, sess, q, true);
    if (sess.i >= E.ROUND_LEN && !sess.misses.length) break;
  }
  if (sess.i < E.ROUND_LEN) errors.push(`practice round ended early: ${sess.i}/${E.ROUND_LEN}`);

  // NSF mock: 25 questions, slot mix, never 4 options, always answerable
  const plan = E.beePlan("nsf", s);
  const mix = { mcq3: 0, mcq4: 0, mcq2: 0, typed: 0 };
  for (let i = 0; i < 25; i++) {
    const q = E.beeNext(s, plan);
    if (!q) { errors.push(`nsf mock ran dry at ${i}`); break; }
    if (q.kind === "typed") mix.typed++;
    else if (q.options.length === 4) mix.mcq4++;
    else if (q.options.length === 3) mix.mcq3++;
    else mix.mcq2++;
    E.beeRecord(s, plan, q, "correct");
  }
  if (mix.mcq4) errors.push("nsf mock served a 4-option MCQ");
  if (!mix.typed) errors.push("nsf mock served no typed questions");
  if (!plan.timeLimit) errors.push("nsf mock has no time limit");
  E.beeFinish(s, plan);

  // iac + oral basic liveness
  const iac = E.beePlan("iac", s);
  if (!iac.timeLimit) errors.push("iac mock has no time limit");
  if (!E.beeNext(s, iac)) errors.push("iac mock served nothing");
  const oral = E.beePlan("oral", s);
  if (!E.beeNext(s, oral)) errors.push("oral bee served nothing");

  // advanced gating: adv facts only in advanced mode
  const advOff = E.topicFacts(s, "concepts").some((f) => f.adv);
  if (advOff) errors.push("advanced facts leak with advanced off");
  s.settings.advanced = true;
  const advOn = E.topicFacts(s, "concepts").some((f) => f.adv);
  if (!advOn) errors.push("advanced facts missing with advanced on");
}

// ---------- report ----------
const facts = Q.facts.length;
const perTopic = D.TOPICS.map((t) => `${t.id}=${(Q.byTopic[t.id] || []).length}`).join(", ");
console.log(`facts: ${facts} · variants built: ${variants} · pyramids: ${D.PYRAMIDS.length}`);
console.log("topics: " + perTopic);
if (errors.length) {
  console.log(`\n${errors.length} ERROR(S):`);
  for (const e of errors.slice(0, 50)) console.log(" - " + e);
  if (errors.length > 50) console.log(` … and ${errors.length - 50} more`);
  process.exit(1);
}
console.log("\nALL CHECKS PASSED");
