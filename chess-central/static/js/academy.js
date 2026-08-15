// The Academy — curriculum browser: tracks → lessons → interactive lesson page.
import { get, post, el, toast } from "./api.js";
import { Board } from "./board.js";
import { renderMarkdown } from "./markdown.js";
import { PuzzlePlayer } from "./puzzles.js";

const LEVEL_NAMES = { 1: "Rookie", 2: "Improver", 3: "Advanced" };

// Deep link: open one lesson directly (used by "learn this" links elsewhere).
export async function openLesson(main, navigate, lessonId) {
  const l = await get(`/learn/lesson/${lessonId}`);
  await lessonView(lessonId,
    { id: l.track_id, title: l.track_title, emoji: l.track_emoji }, main, navigate);
}

export async function academyView(main, navigate) {
  const data = await get("/learn");
  main.innerHTML = "";
  main.append(
    el("div", { class: "row", style: "margin-bottom:6px" },
      el("h1", { style: "margin:0;font-size:22px" }, "🎓 The Academy"),
      el("div", { class: "spacer" }),
      el("span", { class: "mut" },
        `${data.lessons_done}/${data.lessons_total} lessons complete`)),
    el("p", { class: "mut", style: "margin:0 0 16px" },
      `A full course in every part of the game. Recommended level right now: `,
      el("b", {}, LEVEL_NAMES[data.recommended_level]),
      ` — every position is engine-verified.`));

  if (!data.tracks.length) {
    main.append(el("div", { class: "empty" }, "Curriculum not installed — run git pull."));
    return;
  }
  const grid = el("div", { class: "grid cols-2" });
  for (const t of data.tracks) grid.append(trackCard(t, data.recommended_level, main, navigate));
  main.append(grid);
}

function trackCard(t, recLevel, main, navigate) {
  const pct = t.total ? Math.round(100 * t.done / t.total) : 0;
  const hasRec = t.lessons.some(l => l.level === recLevel && !l.done);
  const card = el("div", { class: "card track-card", style: "cursor:pointer" },
    el("div", { class: "row" },
      el("div", { style: "font-size:30px" }, t.emoji),
      el("div", { style: "flex:1" },
        el("h3", { style: "margin:0;font-size:16px" }, t.title,
          hasRec ? el("span", { class: "pz-chip", style: "margin-left:8px;font-size:11px" }, "recommended") : null),
        el("div", { class: "mut" }, t.blurb)),
      el("div", { class: "mut", style: "font-variant-numeric:tabular-nums" }, `${t.done}/${t.total}`)),
    el("div", { class: "progress-bar", style: "height:8px;margin-top:10px" },
      el("div", { style: `width:${pct}%` })));
  card.addEventListener("click", () => trackView(t, main, navigate));
  return card;
}

async function trackView(t, main, navigate) {
  const data = await get("/learn");                    // fresh progress
  const track = data.tracks.find(x => x.id === t.id) || t;
  main.innerHTML = "";
  main.append(
    el("div", { class: "row", style: "margin-bottom:14px" },
      el("button", { class: "ghost", onclick: () => navigate("academy") }, "← Academy"),
      el("h1", { style: "margin:0;font-size:20px" }, `${track.emoji} ${track.title}`),
      el("div", { class: "spacer" }),
      track.chesscom ? el("a", { href: track.chesscom.url, target: "_blank", class: "mut" },
        `↗ ${track.chesscom.label} (chess.com Premium)`) : null));

  const list = el("div", { class: "card", style: "padding:6px 14px" });
  track.lessons.forEach((l, i) => {
    const row = el("div", { class: "plan-step", style: "cursor:pointer" },
      el("div", { class: "num" }, l.done ? "✓" : String(i + 1)),
      el("div", { style: "flex:1" },
        el("h4", {}, l.title),
        el("p", {}, `Level: ${LEVEL_NAMES[l.level]}` +
          (l.drills ? ` · ${l.drills} drill${l.drills === 1 ? "" : "s"}` : "") +
          (l.score ? ` · best ${l.score}` : ""))),
      el("div", { class: "mut", style: "align-self:center" }, "→"));
    if (l.done) row.querySelector(".num").style.background = "var(--good)";
    if (l.done) row.querySelector(".num").style.color = "#fff";
    row.addEventListener("click", () => lessonView(l.id, track, main, navigate));
    list.append(row);
  });
  main.append(list);
}

async function lessonView(lessonId, track, main, navigate) {
  const l = await get(`/learn/lesson/${lessonId}`);
  main.innerHTML = "";
  main.append(
    el("div", { class: "row", style: "margin-bottom:14px" },
      el("button", { class: "ghost", onclick: () => trackView(track, main, navigate) },
        `← ${l.track_title}`),
      el("h1", { style: "margin:0;font-size:20px" }, l.title),
      l.done ? el("span", { class: "pill win" }, "completed") : null));

  const concept = el("div", { class: "card lesson-concept" });
  concept.innerHTML = renderMarkdown(l.concept || "");
  main.append(concept);

  for (const [i, ex] of (l.examples || []).entries()) {
    main.append(exampleCard(ex, i));
  }

  if ((l.drills || []).length) {
    const drillCard = el("div", { class: "card", style: "margin-top:14px" },
      el("h3", { style: "margin:0 0 12px;font-size:16px" }, "🎯 Your turn — drills"),
      el("div", { id: "lessonDrills" }));
    main.append(drillCard);
    const player = new PuzzlePlayer(drillCard.querySelector("#lessonDrills"), {
      onFinished: async (firstTry, total) => {
        try {
          await post(`/learn/lesson/${l.id}/complete`, { correct: firstTry, total });
          toast("Lesson complete! 🎓");
        } catch (e) {}
      },
      onSetDone: () => trackView(track, main, navigate),
    });
    player.start(l.drills.map((d, i) => ({
      id: null, fen: d.fen, solution: d.solution, themes: [],
      difficulty: l.level + 1, source: "lesson",
      explanation: d.explain || d.hint || "", rival: null, reps: 0,
      _hint: d.hint,
    })));
  } else {
    const btn = el("button", { style: "margin-top:14px" }, l.done ? "✓ Completed" : "Mark as complete");
    btn.disabled = l.done;
    btn.addEventListener("click", async () => {
      await post(`/learn/lesson/${l.id}/complete`, {});
      toast("Lesson complete! 🎓");
      trackView(track, main, navigate);
    });
    main.append(btn);
  }

  if (l.chesscom) {
    main.append(el("p", { class: "mut", style: "margin-top:14px" },
      "Extra practice: ",
      el("a", { href: l.chesscom.url, target: "_blank" }, l.chesscom.label),
      " on chess.com — your Premium unlocks the full set."));
  }
}

function exampleCard(ex, i) {
  const boardEl = el("div");
  const caption = el("div", { class: "mut", style: "margin-top:8px" }, ex.caption || "");
  const card = el("div", { class: "card", style: "margin-top:14px" },
    el("div", { class: "pz-card" },
      el("div", { class: "pz-board-wrap" }, boardEl),
      el("div", { class: "pz-side" },
        el("h3", { style: "margin:0;font-size:15px" }, `Watch: example ${i + 1}`),
        caption,
        el("div", { class: "spacer" }),
        el("div", { class: "row" },
          el("button", { class: "ghost", id: `play${i}` }, "▶ Play"),
          el("button", { class: "ghost", id: `reset${i}` }, "↺ Reset")))));
  const board = new Board(boardEl, {});
  board.load(ex.fen, { flipped: ex.fen.split(" ")[1] === "b" });
  board.locked = true;
  let timer = null;
  card.querySelector(`#play${i}`).addEventListener("click", () => {
    clearInterval(timer);
    board.load(ex.fen, { flipped: ex.fen.split(" ")[1] === "b" });
    board.locked = true;
    let j = 0;
    timer = setInterval(() => {
      if (j >= ex.moves.length) { clearInterval(timer); return; }
      board.play(ex.moves[j++]);
    }, 850);
  });
  card.querySelector(`#reset${i}`).addEventListener("click", () => {
    clearInterval(timer);
    board.load(ex.fen, { flipped: ex.fen.split(" ")[1] === "b" });
    board.locked = true;
  });
  return card;
}
