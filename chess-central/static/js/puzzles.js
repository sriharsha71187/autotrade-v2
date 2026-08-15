// Puzzle player v2 — fluid flow: animated board, drag or click to move,
// progress dots, streak-style feedback, celebration bursts, auto-advance.
// Solution lines are [player, opponent, player, ...] UCI moves.
import { el, post, toast } from "./api.js";
import { Board } from "./board.js";

const THEME_LABELS = {
  missed_mate: "Find the mate", missed_capture: "Win material",
  missed_fork: "Fork!", missed_check_tactic: "A check wins",
  moved_en_prise: "Piece safety", left_piece_hanging: "Spot the hanging piece",
  allowed_fork: "Watch for forks", got_mated: "Defend the king",
  back_rank: "Back rank",
};

const PRAISE = ["Brilliant!", "Nailed it!", "Sharp eyes!", "Boom!", "Like a grandmaster!", "Perfect!"];
const NUDGE = ["Not that one — look again", "Almost — try another idea", "There's something better"];

export class PuzzlePlayer {
  constructor(container, { kid = false, onSetDone = null, onSolved = null,
                           onFinished = null } = {}) {
    this.root = container;
    this.kid = kid;
    this.onSetDone = onSetDone;
    this.onSolved = onSolved;
    this.onFinished = onFinished;
    this.queue = [];
    this.idx = 0;
    this.solvedCount = 0;
    this.firstTryCount = 0;
  }

  start(puzzles) {
    this.queue = puzzles;
    this.idx = 0;
    this.solvedCount = 0;
    this.firstTryCount = 0;
    if (!puzzles.length) {
      this.root.innerHTML = `<div class="empty">No puzzles here yet — sync games and run
        analysis, and the queue fills with positions from real games. 🎯</div>`;
      return;
    }
    this._build();
    this._load();
  }

  _build() {
    this.root.innerHTML = "";
    this.dots = el("div", { class: "pz-dots" });
    this.boardEl = el("div");
    this.turnPill = el("div", { class: "pz-turn" });
    this.themeChip = el("div", { class: "pz-chip" });
    this.stars = el("div", { class: "pz-stars" });
    this.status = el("div", { class: "pz-status" });
    this.hintBtn = el("button", { class: "ghost", onclick: () => this._hint() }, "💡 Hint");
    this.skipBtn = el("button", { class: "ghost", onclick: () => this._reveal() }, "Show me");

    this.card = el("div", { class: "pz-card" },
      el("div", { class: "pz-board-wrap" }, this.boardEl),
      el("div", { class: "pz-side" },
        this.dots,
        this.turnPill,
        el("div", { class: "row", style: "gap:8px" }, this.themeChip, this.stars),
        this.status,
        el("div", { class: "spacer" }),
        el("div", { class: "row" }, this.hintBtn, this.skipBtn),
      ));
    this.root.append(this.card);
    this.board = new Board(this.boardEl, { onMove: (uci) => this._attempt(uci) });
  }

  _cur() { return this.queue[this.idx]; }

  _renderDots() {
    this.dots.innerHTML = "";
    this.queue.forEach((_, i) => {
      const d = el("span", {
        class: "dot" + (i < this.idx ? " done" : i === this.idx ? " current" : ""),
      });
      this.dots.append(d);
    });
  }

  _load() {
    const p = this._cur();
    this.solIdx = 0;
    this.hadMistake = false;
    this.hintUsed = false;
    this.t0 = Date.now();
    this._renderDots();
    this.card.classList.remove("pz-enter");
    void this.card.offsetWidth;            // restart the entry animation
    this.card.classList.add("pz-enter");

    this.board.load(p.fen);
    const white = p.fen.split(" ")[1] === "w";
    this.turnPill.innerHTML =
      `<span class="turn-dot ${white ? "tw" : "tb"}"></span>${white ? "White" : "Black"} to move`;
    const theme = (p.themes || []).map(t => THEME_LABELS[t]).find(Boolean);
    this.themeChip.textContent = theme || (p.source === "rival_prep" ? "Rival prep" : "Find the best move");
    this.themeChip.style.display = "";
    const diff = Math.max(1, Math.min(5, p.difficulty || 3));
    this.stars.textContent = "★".repeat(diff) + "☆".repeat(5 - diff);
    this._say(p.rival ? `Prep vs ${p.rival} — your move.` : "Your move.", "");
  }

  _say(text, cls) {
    this.status.textContent = text;
    this.status.className = `pz-status ${cls}`;
    this.status.classList.remove("pz-pop");
    void this.status.offsetWidth;
    this.status.classList.add("pz-pop");
  }

  _attempt(uci) {
    if (this.board.locked) return;
    const p = this._cur();
    const expected = p.solution[this.solIdx];
    if (uci === expected || uci === expected.slice(0, 4)) {
      this.board.play(expected);
      this.board.glow(expected.slice(2, 4));
      this.solIdx++;
      if (this.solIdx >= p.solution.length) return this._solved();
      this._say(this.kid ? "Yes! Keep going…" : "Correct — keep going.", "ok");
      const reply = p.solution[this.solIdx];
      this.board.locked = true;
      setTimeout(() => {
        this.board.play(reply);
        this.board.locked = false;
        this.solIdx++;
        if (this.solIdx >= p.solution.length) this._solved();
      }, 420);
    } else {
      this.hadMistake = true;
      // replay the piece back by shaking the origin square's piece
      this.board.shake(uci.slice(0, 2));
      this._say(NUDGE[Math.floor(Math.random() * NUDGE.length)] +
        (this.kid ? " 🔍" : "."), "bad");
    }
  }

  _hint() {
    const p = this._cur();
    this.hintUsed = true;
    this.board.hintFrom(p.solution[this.solIdx].slice(0, 2));
    this._say("This piece wants to move…", "");
  }

  async _solved() {
    const p = this._cur();
    const clean = !this.hadMistake && !this.hintUsed;
    this.solvedCount++;
    if (clean) this.firstTryCount++;
    this.board.locked = true;
    this._say(clean
      ? PRAISE[Math.floor(Math.random() * PRAISE.length)]
      : (this.kid ? "Solved! Next one first try 💪" : "Solved (with help)."), "ok big");
    if (clean) this._burst();
    if (p.id != null) {
      try {
        const res = await post(`/puzzles/${p.id}/attempt`,
          { correct: clean, time_ms: Date.now() - this.t0 });
        for (const _ of res.new_badges || []) toast("🏅 New badge earned!");
      } catch (e) { /* offline — keep playing */ }
    }
    if (this.onSolved) this.onSolved(clean);
    setTimeout(() => this._next(), clean ? 950 : 1250);
  }

  async _reveal() {
    const p = this._cur();
    this.hadMistake = true;
    this.board.locked = true;
    // play the whole remaining solution slowly so it teaches
    const rest = p.solution.slice(this.solIdx);
    this._say("Watch the idea…", "");
    for (let i = 0; i < rest.length; i++) {
      await new Promise(r => setTimeout(r, 650));
      this.board.play(rest[i]);
    }
    this._say(p.explanation || "That was the idea.", "");
    if (p.id != null) {
      try { await post(`/puzzles/${p.id}/attempt`, { correct: false, time_ms: Date.now() - this.t0 }); }
      catch (e) {}
    }
    setTimeout(() => this._next(), 2400);
  }

  _burst() {
    const wrap = this.card.querySelector(".pz-board-wrap");
    const emojis = ["✨", "⭐", "🎉", "💥"];
    for (let i = 0; i < 14; i++) {
      const s = document.createElement("span");
      s.className = "spark";
      s.textContent = emojis[i % emojis.length];
      s.style.left = `${35 + Math.random() * 30}%`;
      s.style.top = `${35 + Math.random() * 30}%`;
      s.style.setProperty("--dx", `${(Math.random() - 0.5) * 240}px`);
      s.style.setProperty("--dy", `${-40 - Math.random() * 200}px`);
      s.style.setProperty("--rot", `${(Math.random() - 0.5) * 240}deg`);
      wrap.append(s);
      setTimeout(() => s.remove(), 1000);
    }
  }

  _next() {
    this.idx++;
    if (this.idx >= this.queue.length) return this._finish();
    this._load();
  }

  _finish() {
    const total = this.queue.length;
    if (this.onFinished) this.onFinished(this.firstTryCount, total);
    this.root.innerHTML = "";
    this.root.append(el("div", { class: "pz-finish" },
      el("div", { class: "big-emoji" }, this.firstTryCount === total ? "🏆" : "🌟"),
      el("h2", {}, this.kid ? "Set complete — amazing work!" : "Set complete"),
      el("p", {}, `${this.solvedCount}/${total} solved · ${this.firstTryCount} first-try`),
      el("button", { onclick: () => { if (this.onSetDone) this.onSetDone(); } },
        this.kid ? "What's next?" : "Done"),
    ));
    if (!this.onSetDone) this.root.querySelector("button").style.display = "none";
  }
}
