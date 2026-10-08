// Nirvaan's view — puzzle-first, effort-framed, and self-contained
// (the Academy and loss review live INSIDE kid mode; the parent app is gated).
import { get, post, el, fmtDate } from "./api.js";
import { academyView, openLesson } from "./academy.js";
import { sparkline } from "./charts.js";
import { PuzzlePlayer } from "./puzzles.js";

const main = document.getElementById("view");

const navigate = (name) => {
  if (name === "academy") renderAcademy();
  else render();
};

async function renderAcademy() {
  main.innerHTML = "";
  const back = el("button", { class: "ghost", style: "margin-bottom:12px",
    onclick: () => render() }, "← Back to my page");
  const box = el("div");
  main.append(back, box);
  await academyView(box, navigate);
}

let MOTIF_MAP = null;

async function renderReview() {
  if (MOTIF_MAP === null) {
    try { MOTIF_MAP = await get("/learn/motif-map"); } catch (e) { MOTIF_MAP = {}; }
  }
  const queue = await get("/review/queue");
  main.innerHTML = "";
  main.append(el("button", { class: "ghost", style: "margin-bottom:12px",
    onclick: () => render() }, "← Back to my page"));
  if (!queue.length) {
    main.append(el("div", { class: "empty", style: "font-size:18px" },
      "All your games are reviewed — nice work! 🕵️"));
    return;
  }
  const item = queue[0];
  const lesson = (item.motifs || []).map(t => MOTIF_MAP[t]).find(Boolean);
  main.append(
    el("div", { class: "card", style: "margin-bottom:14px" },
      el("h2", { style: "margin:0 0 6px;font-size:19px" }, "🕵️ Game detective"),
      el("p", { style: "margin:0" },
        `Your game vs ${item.opponent} (${fmtDate(item.played_at)}) — around move `,
        el("b", {}, String(item.move_number)),
        ", something went wrong. Can you find the better move?"),
      lesson ? el("p", { style: "margin:8px 0 0" },
        "Want to master this pattern? ",
        el("a", { href: "#", onclick: (e) => {
          e.preventDefault(); openLesson(main, navigate, lesson.lesson_id);
        } }, `🎓 ${lesson.title}`)) : null),
    el("div", { class: "card", id: "reviewPlayer" }));
  const player = new PuzzlePlayer(document.getElementById("reviewPlayer"), {
    kid: true,
    onFinished: async () => { await post(`/review/${item.game_id}/done`); },
    onSetDone: () => renderReview(),
  });
  player.start([item.puzzle]);
}

const POWER_STATUS = {
  growing: ["kp-growing", "🚀 Getting stronger!"],
  mission: ["kp-mission", "🎯 Your next mission"],
  steady: ["kp-steady", "➡️ Holding steady"],
  locked: ["kp-locked", "🔒 Play more games to unlock"],
};

function missionGo(m) {
  if (m.lesson) { main.innerHTML = ""; openLesson(main, navigate, m.lesson.lesson_id); }
  else if (m.key === "lessons") renderAcademy();
  else document.getElementById("player")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function renderPowers(box) {
  let p;
  try { p = await get("/kid/progress"); }
  catch (e) { box.append(el("div", { class: "mut" }, "Your powers will show up soon!")); return; }
  box.append(el("div", { class: "mut", style: "margin:4px 0 12px" },
    p.growing
      ? `You're getting stronger at ${p.growing} thing${p.growing === 1 ? "" : "s"}! 🎉 Lines going up = you're improving.`
      : "Keep playing and practicing — your growth lines show up here. Lines going up = you're improving."));

  if (p.missions.length) {
    const ms = el("div", { class: "kp-missions" });
    for (const m of p.missions) {
      ms.append(el("div", { class: "kp-mission-card" },
        el("div", { style: "font-size:28px" }, m.emoji),
        el("div", { style: "flex:1" },
          el("div", { style: "font-weight:700" }, `Mission: ${m.title}`),
          el("div", { class: "mut" }, m.help)),
        el("button", { onclick: () => missionGo(m) }, "Practice →")));
    }
    box.append(ms);
  }

  const grid = el("div", { class: "kp-grid" });
  for (const s of p.skills) {
    const [cls, label] = POWER_STATUS[s.status];
    const spark = el("div");
    grid.append(el("div", { class: `kp ${cls}` },
      el("div", { class: "row", style: "gap:8px;flex-wrap:nowrap" },
        el("div", { style: "font-size:24px" }, s.emoji),
        el("div", { style: "font-weight:700;line-height:1.2" }, s.title)),
      s.value != null ? el("div", { class: "kp-val" }, String(s.value),
        el("span", { class: "unit" }, s.unit === "move #" ? "" : ` ${s.unit}`)) : null,
      el("div", { class: "kp-help" }, s.help, s.lower_is_better ? " — fewer is better" : ""),
      spark,
      el("div", { class: "kp-status" }, label)));
    if (s.series.length >= 2) {
      const pts = s.series.map(x => ({
        label: new Date(`${x.month}-15T00:00:00`).toLocaleDateString(undefined, { month: "short" }),
        value: x.value }));
      sparkline(spark, pts, { invert: s.lower_is_better, height: 40,
        color: s.status === "growing" ? "var(--good)" : "var(--series-1)",
        unit: s.unit.startsWith("%") ? "%" : "" });
    }
  }
  box.append(grid);
}

async function render() {
  const home = await get("/kid/home");
  main.innerHTML = "";

  const done = Math.min(home.solved_today, home.daily_target);
  const pct = Math.round(100 * done / Math.max(1, home.daily_target));

  main.append(
    el("div", { class: "kid-hero" },
      el("div", { class: "rocket" }, "🚀"),
      el("h1", {}, `Let's go, ${home.name}!`),
    ),
    el("div", { class: "kid-streak" },
      el("div", { class: "item" }, el("div", { class: "big" }, `${home.practiced_days_7}/7`),
        el("div", { class: "lbl" }, "days practiced this week")),
      el("div", { class: "item" }, el("div", { class: "big" }, String(home.total_wins)),
        el("div", { class: "lbl" }, "total wins")),
      el("div", { class: "item" }, el("div", { class: "big" }, `${done}/${home.daily_target}`),
        el("div", { class: "lbl" }, "today's puzzles")),
    ),
    el("div", { class: "kid-note" }, home.coach_note),
    el("div", { style: "margin:14px 0" },
      el("div", { class: "progress-bar" }, el("div", { style: `width:${pct}%` }))),

    home.review_queue > 0 ? el("div", { class: "card", style: "margin-bottom:18px;cursor:pointer",
      onclick: () => renderReview() },
      el("div", { class: "row" },
        el("div", { style: "font-size:34px" }, "🕵️"),
        el("div", {},
          el("h2", { style: "margin:0;font-size:17px" }, "Game detective"),
          el("div", { class: "mut" },
            `${home.review_queue} game${home.review_queue === 1 ? "" : "s"} to investigate — find the moment it turned!`)),
        el("div", { class: "spacer" }),
        el("div", { style: "font-size:22px" }, "→"))) : null,

    home.game_note ? el("div", { class: "card", style: "margin-bottom:18px" },
      el("h2", { style: "margin:0 0 8px;font-size:17px" }, "📝 About your last game"),
      el("div", { style: "white-space:pre-wrap" }, home.game_note)) : null,

    el("div", { class: "card", id: "player", style: "margin-bottom:18px" }),

    el("div", { class: "card", style: "display:block;margin-bottom:18px;cursor:pointer",
      onclick: () => renderAcademy() },
      el("div", { class: "row" },
        el("div", { style: "font-size:34px" }, "🎓"),
        el("div", {},
          el("h2", { style: "margin:0;font-size:17px" }, "The Academy"),
          el("div", { class: "mut" }, "Lessons on checkmates, tactics, openings and more — with drills!")),
        el("div", { class: "spacer" }),
        el("div", { style: "font-size:22px" }, "→"))),

    el("div", { class: "card", id: "kidPowers", style: "margin-bottom:18px" },
      el("h2", { style: "margin:0;font-size:17px" }, "My chess powers 💪")),

    el("div", { class: "card" },
      el("h2", { style: "margin:0 0 10px;font-size:17px" }, "Trophy shelf"),
      el("div", { class: "badge-grid" },
        home.badges.map(b => el("div", { class: `badge ${b.earned_at ? "earned" : ""}` },
          el("div", { class: "e" }, b.emoji),
          el("div", { class: "n" }, b.name),
          el("div", { class: "h" }, b.earned_at ? fmtDate(b.earned_at) : b.how))))),
  );

  renderPowers(document.getElementById("kidPowers"));

  const player = new PuzzlePlayer(document.getElementById("player"),
    { kid: true, onSetDone: () => render() });
  player.start(await get("/puzzles/daily"));
}

render();
