// Nirvaan's view — puzzle-first, effort-framed, and self-contained
// (the Academy and loss review live INSIDE kid mode; the parent app is gated).
import { get, post, el, fmtDate } from "./api.js";
import { academyView } from "./academy.js";
import { lineChart } from "./charts.js";
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

async function renderReview() {
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
  main.append(
    el("div", { class: "card", style: "margin-bottom:14px" },
      el("h2", { style: "margin:0 0 6px;font-size:19px" }, "🕵️ Game detective"),
      el("p", { style: "margin:0" },
        `Your game vs ${item.opponent} (${fmtDate(item.played_at)}) — around move `,
        el("b", {}, String(item.move_number)),
        ", something went wrong. Can you find the better move?")),
    el("div", { class: "card", id: "reviewPlayer" }));
  const player = new PuzzlePlayer(document.getElementById("reviewPlayer"), {
    kid: true,
    onFinished: async () => { await post(`/review/${item.game_id}/done`); },
    onSetDone: () => renderReview(),
  });
  player.start([item.puzzle]);
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

    el("div", { class: "card", style: "margin-bottom:18px" },
      el("h2", { style: "margin:0 0 4px;font-size:17px" }, "Your skills are growing 🌱"),
      el("div", { class: "mut", style: "margin-bottom:8px" },
        "Safe moves — how often you keep your pieces out of trouble. Up = stronger!"),
      el("div", { id: "kidChart" })),

    el("div", { class: "card" },
      el("h2", { style: "margin:0 0 10px;font-size:17px" }, "Trophy shelf"),
      el("div", { class: "badge-grid" },
        home.badges.map(b => el("div", { class: `badge ${b.earned_at ? "earned" : ""}` },
          el("div", { class: "e" }, b.emoji),
          el("div", { class: "n" }, b.name),
          el("div", { class: "h" }, b.earned_at ? fmtDate(b.earned_at) : b.how))))),
  );

  const chartBox = document.getElementById("kidChart");
  if ((home.skills_trend || []).length >= 2) {
    lineChart(chartBox, [{
      name: "Safe moves %",
      points: home.skills_trend.map(s => ({ x: new Date(s.month + "-15"), y: s.safe_pct })),
    }], { height: 180 });
  } else {
    chartBox.innerHTML = '<div class="empty">Play and analyze games for a couple of months and your growth line appears here 🌱</div>';
  }

  const player = new PuzzlePlayer(document.getElementById("player"),
    { kid: true, onSetDone: () => render() });
  player.start(await get("/puzzles/daily"));
}

render();
