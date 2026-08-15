// Nirvaan's view — puzzle-first, encouraging, zero jargon.
import { get, el, fmtDate } from "./api.js";
import { lineChart } from "./charts.js";
import { PuzzlePlayer } from "./puzzles.js";

const main = document.getElementById("view");

async function render() {
  const home = await get("/kid/home");
  main.innerHTML = "";

  const done = Math.min(home.solved_today, home.daily_target);
  const pct = Math.round(100 * done / home.daily_target);

  main.append(
    el("div", { class: "kid-hero" },
      el("div", { class: "rocket" }, "🚀"),
      el("h1", {}, `Let's go, ${home.name}!`),
    ),
    el("div", { class: "kid-streak" },
      el("div", { class: "item" }, el("div", { class: "big" }, `${home.streak}🔥`), el("div", { class: "lbl" }, "day streak")),
      el("div", { class: "item" }, el("div", { class: "big" }, String(home.total_wins)), el("div", { class: "lbl" }, "total wins")),
      el("div", { class: "item" }, el("div", { class: "big" }, `${done}/${home.daily_target}`), el("div", { class: "lbl" }, "today's puzzles")),
    ),
    el("div", { class: "kid-note" }, home.coach_note),
    home.game_note ? el("div", { class: "card", style: "margin-top:14px" },
      el("h2", { style: "margin:0 0 8px;font-size:17px" }, "📝 About your last game"),
      el("div", { style: "white-space:pre-wrap" }, home.game_note)) : null,
    el("div", { style: "margin:14px 0" },
      el("div", { class: "progress-bar" }, el("div", { style: `width:${pct}%` }))),
    el("div", { class: "card", id: "player", style: "margin-bottom:18px" }),
    el("div", { class: "card", style: "margin-bottom:18px" },
      el("h2", { style: "margin:0 0 10px;font-size:17px" }, "Your rating rocket 📈"),
      el("div", { id: "kidChart" })),
    el("div", { class: "card" },
      el("h2", { style: "margin:0 0 10px;font-size:17px" }, "Trophy shelf"),
      el("div", { class: "badge-grid" },
        home.badges.map(b => el("div", { class: `badge ${b.earned_at ? "earned" : ""}` },
          el("div", { class: "e" }, b.emoji),
          el("div", { class: "n" }, b.name),
          el("div", { class: "h" }, b.earned_at ? fmtDate(b.earned_at) : b.how))))),
    el("p", { style: "text-align:center;margin-top:20px" },
      el("a", { href: "/", class: "mut" }, "parent view")),
  );

  const bySource = {};
  for (const r of home.ratings) (bySource[r.source] ??= []).push({ x: new Date(r.date), y: r.rating });
  const series = Object.entries(bySource).map(([s, pts]) => ({
    name: { lichess_rapid: "Lichess", chesscom_rapid: "Chess.com", nwsrs: "Tournaments" }[s] || s,
    points: pts,
  })).slice(0, 3);
  lineChart(document.getElementById("kidChart"), series, { height: 190 });

  const player = new PuzzlePlayer(document.getElementById("player"),
    { kid: true, onSetDone: () => render() });
  player.start(await get("/puzzles/daily"));
}

render();
