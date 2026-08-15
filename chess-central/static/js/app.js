// Parent/coach dashboard SPA.
import { get, post, del, el, toast, fmtDate } from "./api.js";
import { lineChart, barChart } from "./charts.js";
import { renderMarkdown } from "./markdown.js";
import { PuzzlePlayer } from "./puzzles.js";

const main = document.getElementById("view");
const tabs = document.querySelectorAll("nav.side a.tab[data-tab]");

const RATING_LABELS = {
  nwsrs: "NWSRS", uscf_regular: "USCF", uscf_quick: "USCF Quick", uscf_blitz: "USCF Blitz",
  lichess_rapid: "Lichess Rapid", lichess_blitz: "Lichess Blitz", lichess_bullet: "Lichess Bullet",
  lichess_classical: "Lichess Classical", lichess_puzzle: "Lichess Puzzles",
  chesscom_rapid: "Chess.com Rapid", chesscom_blitz: "Chess.com Blitz",
  chesscom_bullet: "Chess.com Bullet", chesscom_daily: "Chess.com Daily",
  chesscom_puzzles: "Chess.com Puzzles",
};

const VIEWS = {
  overview, insights, games, openings, puzzles, rivals, tournaments, roadmap,
  journal, settings, aicoach,
};

async function navigate(name) {
  tabs.forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  main.innerHTML = '<div class="empty">Loading…</div>';
  try { await VIEWS[name](); }
  catch (e) { main.innerHTML = `<div class="empty">Failed to load: ${e.message}</div>`; }
  history.replaceState(null, "", `#${name}`);
}
tabs.forEach(t => t.addEventListener("click", (e) => { e.preventDefault(); navigate(t.dataset.tab); }));

document.getElementById("theme-toggle").addEventListener("click", () => {
  const cur = document.documentElement.dataset.theme;
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("theme", next);
});
if (localStorage.getItem("theme")) document.documentElement.dataset.theme = localStorage.getItem("theme");

// ------------------------------------------------------------------ overview

async function overview() {
  const [sum, status] = await Promise.all([get("/summary"), get("/status")]);
  const rec = sum.record || {};
  const r30 = sum.last30 || {};
  const ps = sum.puzzle_stats || {};

  const ratingCards = Object.entries(sum.ratings || {})
    .filter(([k]) => ["nwsrs", "uscf_regular", "lichess_rapid", "chesscom_rapid"].includes(k))
    .map(([k, v]) => tile(RATING_LABELS[k] || k, v.rating, `as of ${fmtDate(v.date)}`));

  main.innerHTML = "";
  main.append(
    el("div", { class: "row", style: "margin-bottom:16px" },
      el("h1", { style: "margin:0;font-size:22px" }, `${sum.player} — Chess Central`),
      el("div", { class: "spacer" }),
      el("button", { class: "ghost", id: "analyzeBtn" }, analysisLabel(status.analysis)),
      el("button", { id: "syncBtn" }, "Sync games"),
    ),
    el("div", { class: "grid cols-4", style: "margin-bottom:14px" },
      tile("Games", sum.games_total, `${rec.w ?? 0}W · ${rec.l ?? 0}L · ${rec.d ?? 0}D`),
      tile("Last 30 days", r30.n ?? 0, `${r30.w ?? 0} wins`),
      tile("Puzzle streak", `${ps.streak_days ?? 0}🔥`, `${ps.solved_today ?? 0} solved today`),
      tile("Avg win% lost/move", sum.avg_winprob_loss ?? "—",
        sum.moves_analyzed ? `${sum.blunders} blunders in ${sum.moves_analyzed} moves` : "run analysis"),
      ...ratingCards,
    ),
    card("Rating progress", el("div", { id: "ratingChart" })),
    !status.engine_found ? el("div", { class: "card", style: "margin-top:14px;border-left:3px solid var(--warning)" },
      el("b", {}, "Stockfish not found. "),
      "Install it with ", el("code", {}, "brew install stockfish"),
      " for real analysis — a simple built-in engine is used until then.") : null,
    el("div", { style: "margin-top:14px" }),
    card("Top insights", el("div", { id: "topInsights" })),
  );

  document.getElementById("syncBtn").addEventListener("click", async (ev) => {
    ev.target.disabled = true; ev.target.textContent = "Syncing…";
    try {
      const r = await post("/sync");
      const n = (r.lichess?.inserted ?? 0) + (r.chesscom?.inserted ?? 0);
      toast(`Sync complete: ${n} new game${n === 1 ? "" : "s"}`);
      navigate("overview");
    } catch (e) { toast(`Sync failed: ${e.message}`); ev.target.disabled = false; ev.target.textContent = "Sync games"; }
  });
  document.getElementById("analyzeBtn").addEventListener("click", async () => {
    await post("/analyze"); toast("Analysis started"); navigate("overview");
  });

  const hist = await get("/ratings");
  const bySource = {};
  for (const r of hist) (bySource[r.source] ??= []).push({ x: new Date(r.date), y: r.rating });
  const prefer = ["lichess_rapid", "chesscom_rapid", "nwsrs", "uscf_regular"];
  const series = prefer.filter(s => bySource[s]?.length)
    .map(s => ({ name: RATING_LABELS[s], points: bySource[s] })).slice(0, 4);
  lineChart(document.getElementById("ratingChart"), series);

  const ins = await get("/insights");
  const top = ins.slice(0, 4);
  const box = document.getElementById("topInsights");
  box.innerHTML = top.length ? "" : '<div class="empty">Sync + analyze games to unlock coaching insights.</div>';
  top.forEach(i => box.append(insightCard(i)));
}

function analysisLabel(a) {
  if (a?.running) return `Analyzing… (${a.pending} left)`;
  return a?.pending ? `Analyze ${a.pending} games` : "Analysis up to date";
}

function tile(label, value, sub = "") {
  return el("div", { class: "card tile" },
    el("div", { class: "label" }, label),
    el("div", { class: "value" }, String(value ?? "—")),
    sub ? el("div", { class: "sub" }, sub) : null);
}

function card(title, ...body) {
  return el("div", { class: "card" },
    el("h2", { style: "margin:0 0 10px;font-size:16px" }, title), ...body);
}

function insightCard(i) {
  return el("div", { class: `insight ${i.kind}` },
    el("div", { class: "kind" }, i.kind),
    el("h3", {}, i.title),
    el("p", {}, i.detail));
}

// ------------------------------------------------------------------ insights

async function insights() {
  const ins = await get("/insights");
  main.innerHTML = "";
  main.append(el("div", { class: "row", style: "margin-bottom:14px" },
    el("h1", { style: "margin:0;font-size:22px" }, "Strengths · Weaknesses · Opportunities"),
    el("div", { class: "spacer" }),
    el("button", { class: "ghost", onclick: async () => { await post("/insights/regenerate"); navigate("insights"); } }, "Recompute")));
  if (!ins.length) {
    main.append(el("div", { class: "empty" }, "Not enough analyzed games yet — sync, analyze, then check back."));
    return;
  }
  for (const kind of ["weakness", "opportunity", "strength"]) {
    const items = ins.filter(i => i.kind === kind);
    if (!items.length) continue;
    main.append(el("h2", { style: "font-size:17px;text-transform:capitalize" },
      kind === "opportunity" ? "Opportunities" : kind + (kind.endsWith("s") ? "" : "s")));
    items.forEach(i => main.append(insightCard(i)));
  }
}

// --------------------------------------------------------------------- games

async function games() {
  main.innerHTML = "";
  const list = el("div");
  const oppInput = el("input", { placeholder: "Filter by opponent…" });
  const resSel = el("select", {},
    el("option", { value: "" }, "All results"),
    el("option", { value: "win" }, "Wins"),
    el("option", { value: "loss" }, "Losses"),
    el("option", { value: "draw" }, "Draws"));
  const reload = async () => {
    const q = new URLSearchParams();
    if (oppInput.value) q.set("opponent", oppInput.value);
    if (resSel.value) q.set("result", resSel.value);
    const rows = await get(`/games?limit=100&${q}`);
    list.innerHTML = "";
    if (!rows.length) { list.append(el("div", { class: "empty" }, "No games — hit Sync on the Overview tab.")); return; }
    const table = el("table", { class: "data" },
      el("thead", {}, el("tr", {},
        ...["Date", "Opponent", "", "Result", "Opening", "Time", "Analysis"].map(h => el("th", {}, h)))),
      el("tbody", {}, rows.map(g => el("tr", { style: "cursor:pointer", onclick: () => gameDetail(g.id) },
        el("td", {}, fmtDate(g.played_at)),
        el("td", {}, `${g.opponent_name ?? "?"} (${g.opponent_rating ?? "?"})`),
        el("td", {}, g.color === "white" ? "⚪" : "⚫"),
        el("td", {}, el("span", { class: `pill ${g.result}` }, g.result)),
        el("td", {}, g.opening_name || g.eco || "—"),
        el("td", {}, g.time_class || "—"),
        el("td", {}, g.analyzed_at ? "✓" : "…"),
      ))));
    list.append(table);
  };
  oppInput.addEventListener("change", reload);
  resSel.addEventListener("change", reload);
  main.append(
    el("div", { class: "row", style: "margin-bottom:12px" },
      el("h1", { style: "margin:0;font-size:22px" }, "Games"),
      el("div", { class: "spacer" }), oppInput, resSel),
    list);
  await reload();
}

async function gameDetail(id) {
  const g = await get(`/games/${id}`);
  main.innerHTML = "";
  const acc = g.accuracy || {};
  const mistakes = (g.moves || []).filter(m => m.mover === "player" &&
    ["inaccuracy", "mistake", "blunder"].includes(m.classification));
  main.append(
    el("div", { class: "row", style: "margin-bottom:12px" },
      el("button", { class: "ghost", onclick: () => navigate("games") }, "← Games"),
      el("h1", { style: "margin:0;font-size:20px" },
        `vs ${g.opponent_name} (${g.opponent_rating ?? "?"}) — ${g.result}`),
      el("div", { class: "spacer" }),
      g.url ? el("a", { href: g.url, target: "_blank" }, "View on " + g.platform) : null),
    el("div", { class: "grid cols-4", style: "margin-bottom:14px" },
      tile("Date", fmtDate(g.played_at), g.time_class),
      tile("Opening", g.opening_name || g.eco || "—"),
      tile("Avg win% loss", acc.avg_winprob_loss ?? "—"),
      tile("Mistakes", `${acc.mistake ?? 0} + ${acc.blunder ?? 0}`, "mistakes + blunders")),
    card("Key moments",
      mistakes.length
        ? el("table", { class: "data" },
            el("thead", {}, el("tr", {}, ...["Move", "Played", "Type", "Better was", "Why it mattered"].map(h => el("th", {}, h)))),
            el("tbody", {}, mistakes.map(m => el("tr", {},
              el("td", {}, String(Math.ceil(m.ply / 2))),
              el("td", {}, m.san),
              el("td", {}, el("span", { class: `pill ${m.classification === "blunder" ? "loss" : "draw"}` }, m.classification)),
              el("td", {}, m.best_san || m.best_uci || "—"),
              el("td", {}, (m.motifs || []).join(", ") || `−${Math.round(m.winprob_before - m.winprob_after)}% win chance`)))))
        : el("div", { class: "empty" }, g.analyzed_at ? "Clean game — no player mistakes flagged. 🎉" : "Not analyzed yet.")),
    el("div", { style: "margin-top:14px" }),
    card("Coach's commentary", el("div", { id: "commentaryBox" })));

  const box = document.getElementById("commentaryBox");
  const showNote = (content) => {
    box.innerHTML = renderMarkdown(content);
  };
  const existing = await get(`/llm/game/${id}/commentary`);
  if (existing.content) {
    showNote(existing.content);
  } else if (g.analyzed_at) {
    const btn = el("button", { class: "ghost" }, "✨ Write commentary");
    btn.addEventListener("click", async () => {
      btn.disabled = true; btn.textContent = "Coach is writing…";
      try {
        const r = await post(`/llm/game/${id}/commentary`);
        showNote(r.content);
      } catch (e) { toast(e.message); btn.disabled = false; btn.textContent = "✨ Write commentary"; }
    });
    box.append(btn, el("span", { class: "mut", style: "margin-left:10px" },
      "AI commentary grounded in the engine analysis above."));
  } else {
    box.append(el("span", { class: "mut" }, "Analyze the game first."));
  }
}

// ------------------------------------------------------------------ openings

async function openings() {
  const rows = await get("/openings");
  main.innerHTML = "";
  main.append(el("h1", { style: "margin:0 0 14px;font-size:22px" }, "Opening repertoire"));
  for (const color of ["white", "black"]) {
    const data = rows.filter(r => r.color === color && r.n >= 2).slice(0, 12)
      .map(r => ({ label: r.opening, value: r.score_pct, sub: `${r.n} games — ${r.w}W ${r.l}L ${r.d}D`,
        color: r.score_pct >= 55 ? "var(--series-3)" : r.score_pct <= 45 ? "var(--series-2)" : "var(--series-1)" }));
    main.append(card(`As ${color} — score % by opening (min 2 games)`,
      el("div", { id: `op-${color}` })));
    main.append(el("div", { style: "height:12px" }));
  }
  for (const color of ["white", "black"]) {
    const data = rows.filter(r => r.color === color && r.n >= 2).slice(0, 12)
      .map(r => ({ label: r.opening, value: r.score_pct, sub: `${r.n} games — ${r.w}W ${r.l}L ${r.d}D`,
        color: r.score_pct >= 55 ? "var(--series-3)" : r.score_pct <= 45 ? "var(--series-2)" : "var(--series-1)" }));
    barChart(document.getElementById(`op-${color}`), data, { unit: "%", max: 100 });
  }
}

// ------------------------------------------------------------------- puzzles

async function puzzles() {
  const stats = await get("/puzzles/stats");
  main.innerHTML = "";
  main.append(
    el("div", { class: "row", style: "margin-bottom:12px" },
      el("h1", { style: "margin:0;font-size:22px" }, "Puzzle trainer"),
      el("div", { class: "spacer" }),
      el("button", { class: "ghost", onclick: async () => {
        const r = await post("/puzzles/backfill"); toast(`${r.created} new puzzles generated`); navigate("puzzles");
      } }, "Generate from games")),
    el("div", { class: "grid cols-4", style: "margin-bottom:14px" },
      tile("Queue", stats.total_puzzles), tile("Streak", `${stats.streak_days}🔥`),
      tile("Solved today", stats.solved_today), tile("Accuracy", stats.accuracy_pct != null ? `${stats.accuracy_pct}%` : "—")),
    el("div", { class: "card", id: "player" }));
  const player = new PuzzlePlayer(document.getElementById("player"));
  player.start(await get("/puzzles/daily"));
}

// -------------------------------------------------------------------- rivals

async function rivals() {
  const list = await get("/rivals");
  main.innerHTML = "";
  const form = el("div", { class: "card", style: "margin-bottom:14px" },
    el("h2", { style: "margin:0 0 10px;font-size:16px" }, "Add a rival"),
    el("div", { class: "row" },
      el("input", { id: "rvName", placeholder: "Name (required)" }),
      el("input", { id: "rvLi", placeholder: "Lichess username" }),
      el("input", { id: "rvCc", placeholder: "Chess.com username" }),
      el("button", { onclick: async () => {
        const name = document.getElementById("rvName").value.trim();
        if (!name) return toast("Name required");
        await post("/rivals", { name,
          lichess_username: document.getElementById("rvLi").value.trim() || null,
          chesscom_username: document.getElementById("rvCc").value.trim() || null });
        navigate("rivals");
      } }, "Add")));
  main.append(el("h1", { style: "margin:0 0 14px;font-size:22px" }, "Rival prep"), form);
  if (!list.length) main.append(el("div", { class: "empty" },
    "Add the kids he faces repeatedly at tournaments — the app scouts their public games and builds prep puzzles."));
  for (const r of list) main.append(rivalCard(r));
}

function rivalCard(r) {
  const rep = r.scout_report;
  const body = el("div", {});
  if (rep) {
    const h2h = rep.head_to_head || {};
    body.append(el("p", { class: "mut" }, `Scouted ${fmtDate(r.scouted_at)} · prep puzzles: ${rep.prep_puzzles_created ?? 0}`));
    (rep.advice || []).forEach(a => body.append(el("p", { style: "margin:4px 0" }, "• " + a)));
    if ((rep.typical_mistakes || []).length)
      body.append(el("p", { style: "margin:6px 0 0" },
        el("b", {}, "Their habits: "),
        rep.typical_mistakes.map(m => `${m.label} (${m.count}×)`).join(" · ")));
  } else {
    body.append(el("p", { class: "mut" }, "Not scouted yet."));
  }
  return el("div", { class: "card", style: "margin-bottom:12px" },
    el("div", { class: "row" },
      el("h3", { style: "margin:0" }, r.name),
      el("span", { class: "mut" }, [r.lichess_username && `lichess:${r.lichess_username}`,
        r.chesscom_username && `chess.com:${r.chesscom_username}`].filter(Boolean).join(" · ")),
      el("div", { class: "spacer" }),
      el("button", { class: "ghost", onclick: async (ev) => {
        ev.target.disabled = true; ev.target.textContent = "Scouting…";
        try { await post(`/rivals/${r.id}/scout`); toast("Scout report ready"); navigate("rivals"); }
        catch (e) { toast("Scouting failed: " + e.message); ev.target.disabled = false; ev.target.textContent = "Scout"; }
      } }, rep ? "Re-scout" : "Scout"),
      el("button", { class: "ghost", onclick: async () => {
        const puzzlesForRival = await get(`/puzzles/daily?rival=${encodeURIComponent(r.name)}`);
        showRivalPuzzles(r, puzzlesForRival);
      } }, "Prep puzzles"),
      rep ? el("button", { class: "ghost", onclick: async (ev) => {
        ev.target.disabled = true; ev.target.textContent = "Writing…";
        try {
          const brief = await post(`/llm/rival/${r.id}/brief`);
          const d = el("div", { class: "card", style: "margin-top:10px" });
          d.innerHTML = renderMarkdown(brief.content);
          ev.target.closest(".card").append(d);
          ev.target.textContent = "✨ Pep talk";
        } catch (e) { toast(e.message); ev.target.textContent = "✨ Pep talk"; }
        ev.target.disabled = false;
      } }, "✨ Pep talk") : null,
      el("button", { class: "ghost", onclick: async () => { await del(`/rivals/${r.id}`); navigate("rivals"); } }, "✕")),
    body);
}

function showRivalPuzzles(r, list) {
  main.innerHTML = "";
  main.append(
    el("div", { class: "row", style: "margin-bottom:12px" },
      el("button", { class: "ghost", onclick: () => navigate("rivals") }, "← Rivals"),
      el("h1", { style: "margin:0;font-size:20px" }, `Prep vs ${r.name}`)),
    el("div", { class: "card", id: "player" }));
  new PuzzlePlayer(document.getElementById("player")).start(list);
}

// --------------------------------------------------------------- tournaments

async function tournaments() {
  const rows = await get("/tournaments");
  main.innerHTML = "";
  main.append(el("div", { class: "row", style: "margin-bottom:12px" },
    el("h1", { style: "margin:0;font-size:22px" }, "Tournaments"),
    el("div", { class: "spacer" }),
    el("button", { class: "ghost", onclick: async (ev) => {
      ev.target.disabled = true; ev.target.textContent = "Refreshing…";
      await post("/tournaments/refresh"); navigate("tournaments");
    } }, "Refresh listings")));

  const groups = [
    ["Near home (Seattle / Eastside)", rows.filter(t => t.near_home && !t.online)],
    ["Regional & national (travel)", rows.filter(t => !t.near_home && !t.online)],
    ["Online events", rows.filter(t => t.online)],
  ];
  for (const [title, list] of groups) {
    main.append(el("h2", { style: "font-size:17px;margin:18px 0 8px" }, title));
    if (!list.length) { main.append(el("div", { class: "empty" }, "Nothing listed — try Refresh.")); continue; }
    const table = el("table", { class: "data" },
      el("thead", {}, el("tr", {}, ...["Date", "Event", "Where", "Sections", "Status", ""].map(h => el("th", {}, h)))),
      el("tbody", {}, list.map(t => el("tr", {},
        el("td", {}, t.starts_at ? fmtDate(t.starts_at) : "ongoing"),
        el("td", {}, t.url ? el("a", { href: t.url, target: "_blank" }, t.name) : t.name,
          t.recurring_note ? el("div", { class: "mut" }, t.recurring_note) : null),
        el("td", {}, t.city || "—"),
        el("td", {}, t.sections || "—"),
        el("td", {}, statusSel(t)),
        el("td", { class: "mut" }, t.source)))));
    main.append(el("div", { class: "card", style: "padding:6px 10px" }, table));
  }
  main.append(el("div", { class: "card", style: "margin-top:16px" },
    el("h2", { style: "margin:0 0 10px;font-size:16px" }, "Add manually"),
    el("div", { class: "row" },
      el("input", { id: "tName", placeholder: "Event name" }),
      el("input", { id: "tDate", type: "date" }),
      el("input", { id: "tCity", placeholder: "City" }),
      el("input", { id: "tUrl", placeholder: "Link (optional)" }),
      el("button", { onclick: async () => {
        const name = document.getElementById("tName").value.trim();
        if (!name) return toast("Name required");
        await post("/tournaments", { name, starts_at: document.getElementById("tDate").value || null,
          city: document.getElementById("tCity").value || null, url: document.getElementById("tUrl").value || null });
        navigate("tournaments");
      } }, "Add"))));
}

function statusSel(t) {
  const sel = el("select", {},
    ...["new", "interested", "registered", "played", "skipped"].map(s =>
      el("option", { value: s, ...(t.status === s ? { selected: "" } : {}) }, s)));
  sel.addEventListener("change", async () => {
    await post(`/tournaments/${t.id}/status`, { status: sel.value });
    if (sel.value === "skipped") navigate("tournaments");
  });
  return sel;
}

// ------------------------------------------------------------------- roadmap

async function roadmap() {
  const r = await get("/roadmap");
  main.innerHTML = "";
  main.append(
    el("h1", { style: "margin:0 0 6px;font-size:22px" }, "The road to Grandmaster"),
    el("p", { class: "mut", style: "max-width:760px" }, r.honest_note),
    el("div", { class: "card", style: "margin:14px 0" },
      el("h2", { style: "margin:0 0 6px;font-size:16px" }, `Now: ${r.current.name}`),
      el("div", { class: "mut" }, r.current.band),
      el("p", { style: "margin:8px 0" }, el("b", {}, "Goal: "), r.current.goal),
      el("b", {}, "This stage's weekly rhythm:"),
      el("ul", {}, r.current.weekly.map(w => el("li", {}, w))),
      el("p", { style: "margin:6px 0 0" }, el("b", {}, "Graduates when: "), r.current.graduate_when)));

  r.stages.forEach((s, i) => {
    const cls = i < r.current_index ? "done" : i === r.current_index ? "current" : "";
    main.append(el("div", { class: `stage ${cls}` },
      el("div", { class: "dot" }, i < r.current_index ? "✓" : String(i + 1)),
      el("div", {},
        el("h3", {}, s.name),
        el("div", { class: "band" }, s.band),
        el("p", { style: "margin:4px 0 0;font-size:14px" }, s.goal),
        el("details", {}, el("summary", {}, "Skills & weekly plan"),
          el("b", { style: "font-size:13px" }, "Skills to build:"),
          el("ul", {}, s.skills.map(k => el("li", {}, k))),
          el("b", { style: "font-size:13px" }, "Weekly rhythm:"),
          el("ul", {}, s.weekly.map(k => el("li", {}, k)))))));
  });
}

// ------------------------------------------------------------------ AI coach

async function aicoach() {
  const status = await get("/llm/status");
  main.innerHTML = "";
  main.append(el("div", { class: "row", style: "margin-bottom:12px" },
    el("h1", { style: "margin:0;font-size:22px" }, "AI Coach"),
    el("span", { class: "mut" }, status.configured ? status.model : ""),
    el("div", { class: "spacer" }),
    el("button", { class: "ghost", id: "reportBtn" }, "Weekly report"),
    el("button", { class: "ghost", onclick: async () => {
      if (confirm("Clear the chat history?")) { await del("/llm/chat"); navigate("aicoach"); }
    } }, "Clear chat")));

  if (!status.configured) {
    main.append(el("div", { class: "card", style: "border-left:3px solid var(--warning)" },
      el("b", {}, "No Anthropic API key configured. "),
      "Add one in ", el("a", { href: "#settings", onclick: (e) => { e.preventDefault(); navigate("settings"); } }, "Settings"),
      " — it stays in local config.json on this Mac. Get a key at ",
      el("a", { href: "https://platform.claude.com", target: "_blank" }, "platform.claude.com"), "."));
    return;
  }

  const reportBox = el("div");
  const chatBox = el("div", { style: "max-height:52vh;overflow-y:auto;padding:4px 2px" });
  const input = el("textarea", { rows: 2, style: "flex:1",
    placeholder: "Ask the coach… e.g. \"why does he keep losing with black?\" or \"what should we train before the next tournament?\"" });
  const sendBtn = el("button", {}, "Ask");

  const bubble = (role, text) => el("div", {
    style: `margin:8px 0;padding:10px 14px;border-radius:12px;max-width:85%;` +
      (role === "user"
        ? "background:color-mix(in oklab, var(--accent) 14%, var(--surface));margin-left:auto"
        : "background:var(--surface);border:1px solid var(--border)"),
  }, role === "user" ? text : rawHtml(renderMarkdown(text)));

  function rawHtml(html) {
    const d = document.createElement("div");
    d.innerHTML = html;
    return d;
  }

  const history = await get("/llm/chat");
  history.forEach(h => chatBox.append(bubble(h.role, h.text)));
  if (!history.length) {
    chatBox.append(el("div", { class: "empty" },
      "Ask anything about his chess — answers are grounded in his actual games, analysis, and training data."));
  }

  const send = async () => {
    const msg = input.value.trim();
    if (!msg) return;
    input.value = "";
    sendBtn.disabled = true;
    chatBox.append(bubble("user", msg));
    const thinking = el("div", { class: "mut", style: "padding:8px 14px" }, "Coach is thinking…");
    chatBox.append(thinking);
    chatBox.scrollTop = chatBox.scrollHeight;
    try {
      const r = await post("/llm/chat", { message: msg });
      thinking.remove();
      chatBox.append(bubble("assistant", r.reply));
    } catch (e) {
      thinking.remove();
      chatBox.append(el("div", { class: "mut", style: "color:var(--critical);padding:8px 14px" }, e.message));
    }
    sendBtn.disabled = false;
    chatBox.scrollTop = chatBox.scrollHeight;
  };
  sendBtn.addEventListener("click", send);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });

  main.append(reportBox,
    el("div", { class: "card" }, chatBox,
      el("div", { class: "row", style: "margin-top:10px" }, input, sendBtn)));

  document.getElementById("reportBtn").addEventListener("click", async (ev) => {
    ev.target.disabled = true; ev.target.textContent = "Writing report…";
    try {
      const r = await post("/llm/weekly-report");
      reportBox.innerHTML = "";
      reportBox.append(el("div", { class: "card", style: "margin-bottom:14px" },
        el("div", { class: "mut" },
          `Coach's report · ${fmtDate(r.created_at)}${r.cached ? " (cached — " : " ("}`,
          el("a", { href: "#", onclick: async (e) => {
            e.preventDefault(); await post("/llm/weekly-report?force=true"); navigate("aicoach");
          } }, "regenerate"), ")"),
        rawHtml(renderMarkdown(r.content))));
    } catch (e) { toast(e.message); }
    ev.target.disabled = false; ev.target.textContent = "Weekly report";
  });
}

// ------------------------------------------------------------------- journal

async function journal() {
  const rows = await get("/journal");
  main.innerHTML = "";
  const ta = el("textarea", { rows: 3, style: "width:100%",
    placeholder: "Coach's note — what did we learn this week? Tournament observations, mindset, goals…" });
  main.append(
    el("h1", { style: "margin:0 0 14px;font-size:22px" }, "Coach's journal"),
    el("div", { class: "card", style: "margin-bottom:14px" }, ta,
      el("div", { class: "row", style: "margin-top:8px" },
        el("div", { class: "spacer" }),
        el("button", { onclick: async () => {
          if (!ta.value.trim()) return;
          await post("/journal", { text: ta.value.trim() }); navigate("journal");
        } }, "Save note"))),
    ...rows.map(r => el("div", { class: "card", style: "margin-bottom:10px" },
      el("div", { class: "mut" }, `${fmtDate(r.created_at)} · ${r.author}`),
      el("p", { style: "margin:6px 0 0;white-space:pre-wrap" }, r.text))));
  if (!rows.length) main.append(el("div", { class: "empty" }, "First note goes here — after the next tournament, write down three things."));
}

// ------------------------------------------------------------------ settings

async function settings() {
  const cfg = await get("/settings");
  main.innerHTML = "";
  const fields = [
    ["player_name", "Player name"], ["lichess_username", "Lichess username"],
    ["chesscom_username", "Chess.com username"], ["nwsrs_id", "NWSRS ID"],
    ["uscf_id", "USCF ID"], ["home_area", "Home area"],
    ["engine_path", "Stockfish path (blank = auto-detect)"],
    ["engine_movetime_ms", "Engine ms per move"], ["puzzle_daily_target", "Daily puzzle target"],
    ["anthropic_api_key", "Anthropic API key (stays in local config.json)"],
    ["llm_model", "AI coach model"],
  ];
  const inputs = {};
  main.append(el("h1", { style: "margin:0 0 14px;font-size:22px" }, "Settings"),
    el("div", { class: "card" },
      ...fields.map(([k, label]) => el("div", { style: "margin-bottom:10px" },
        el("div", { class: "mut" }, label),
        inputs[k] = el("input", { value: cfg[k] ?? "", style: "width:100%;max-width:420px" }))),
      el("button", { onclick: async () => {
        const body = {};
        for (const [k] of fields) {
          let v = inputs[k].value;
          if (["engine_movetime_ms", "puzzle_daily_target"].includes(k)) v = parseInt(v || "0", 10);
          body[k] = v;
        }
        await post("/settings", body); toast("Saved");
      } }, "Save")),
    el("p", { class: "mut", style: "margin-top:12px" },
      "Nirvaan's view lives at ", el("a", { href: "/kid" }, "/kid"), " — bookmark it on his device."));
}

navigate(location.hash.slice(1) || "overview");
