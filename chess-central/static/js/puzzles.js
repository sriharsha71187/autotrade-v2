// Puzzle player: runs a daily set through the Board component.
// Solution lines are [player, opponent, player, ...] UCI moves.
import { el, post, toast } from "./api.js";
import { Board } from "./board.js";

const THEME_LABELS = {
  missed_mate: "Find the mate", missed_capture: "Win material",
  missed_fork: "Fork!", missed_check_tactic: "Check wins",
  moved_en_prise: "Keep pieces safe", left_piece_hanging: "Spot the hanging piece",
  allowed_fork: "Watch for forks", got_mated: "Defend the king",
  back_rank: "Back rank", rival_prep: "Rival prep",
};

export class PuzzlePlayer {
  constructor(container, { kid = false, onSetDone = null, onSolved = null } = {}) {
    this.root = container;
    this.kid = kid;
    this.onSetDone = onSetDone;
    this.onSolved = onSolved;
    this.queue = [];
    this.idx = 0;
    this._build();
  }

  _build() {
    this.root.innerHTML = "";
    this.boardEl = el("div");
    this.status = el("div", { class: "puzzle-status" });
    this.meta = el("div", { class: "board-meta" });
    this.progress = el("div", { class: "mut" });
    this.hintBtn = el("button", { class: "ghost", onclick: () => this._hint() }, "Hint");
    this.skipBtn = el("button", { class: "ghost", onclick: () => this._fail(true) }, "Skip");
    const side = el("div", {},
      this.progress,
      el("h3", { style: "margin:8px 0 4px" }, this.kid ? "Your move!" : "Find the best move"),
      this.status, this.meta,
      el("div", { class: "row", style: "margin-top:12px" }, this.hintBtn, this.skipBtn),
    );
    this.root.append(el("div", { class: "puzzle-shell" }, this.boardEl, side));
    this.board = new Board(this.boardEl, { onMove: (uci) => this._attempt(uci) });
  }

  start(puzzles) {
    this.queue = puzzles;
    this.idx = 0;
    if (!puzzles.length) {
      this.root.innerHTML = `<div class="empty">No puzzles yet — sync games and run analysis,
        and the queue fills up with positions from real games. 🎯</div>`;
      return;
    }
    this._load();
  }

  _cur() { return this.queue[this.idx]; }

  _load() {
    const p = this._cur();
    this.solIdx = 0;
    this.hadMistake = false;
    this.hintUsed = false;
    this.t0 = Date.now();
    this.board.load(p.fen);
    this.status.textContent = "";
    this.status.className = "puzzle-status";
    const themes = (p.themes || []).map(t => THEME_LABELS[t]).filter(Boolean);
    const who = p.fen.split(" ")[1] === "w" ? "White" : "Black";
    this.meta.textContent = `${who} to move.` + (themes.length ? ` Theme: ${themes[0]}` : "") +
      (p.rival ? ` · Rival prep vs ${p.rival}` : "");
    this.progress.textContent = `Puzzle ${this.idx + 1} of ${this.queue.length}`;
  }

  _attempt(uci) {
    const p = this._cur();
    const expected = p.solution[this.solIdx];
    if (uci === expected || uci === expected.slice(0, 4)) {
      this.board.play(expected);
      this.solIdx++;
      if (this.solIdx >= p.solution.length) return this._solved();
      // auto-play the opponent's forced reply
      const reply = p.solution[this.solIdx];
      this.status.textContent = this.kid ? "Yes! Keep going…" : "Correct — continue.";
      this.status.className = "puzzle-status ok";
      setTimeout(() => {
        this.board.play(reply);
        this.solIdx++;
        if (this.solIdx >= p.solution.length) this._solved();
      }, 450);
    } else {
      this.hadMistake = true;
      this.board.flash(uci.slice(2, 4), "wrong");
      this.status.textContent = this.kid
        ? "Not that one — look again! 🔍"
        : "Not the best move — try again.";
      this.status.className = "puzzle-status bad";
    }
  }

  _hint() {
    const p = this._cur();
    this.hintUsed = true;
    const from = p.solution[this.solIdx].slice(0, 2);
    this.board.render({ [from]: "hint" });
    this.status.textContent = "This piece wants to move…";
    this.status.className = "puzzle-status";
  }

  async _solved() {
    const p = this._cur();
    const correct = !this.hadMistake && !this.hintUsed;
    this.status.textContent = correct
      ? (this.kid ? "🎉 Brilliant! You found it!" : "Solved.")
      : (this.kid ? "Solved — next time first try! 💪" : "Solved (with help).");
    this.status.className = "puzzle-status ok";
    this.board.locked = true;
    try {
      const res = await post(`/puzzles/${p.id}/attempt`,
        { correct, time_ms: Date.now() - this.t0 });
      for (const b of res.new_badges || []) toast(`🏅 New badge earned!`);
      if (this.onSolved) this.onSolved(correct);
    } catch (e) { /* offline attempt — keep playing */ }
    setTimeout(() => this._next(), 1100);
  }

  async _fail(skipped = false) {
    const p = this._cur();
    try { await post(`/puzzles/${p.id}/attempt`, { correct: false, time_ms: Date.now() - this.t0 }); }
    catch (e) {}
    if (skipped) {
      const moves = p.solution.map(u => u).join(", ");
      this.status.textContent = `Answer: ${moves}. ${p.explanation || ""}`;
      this.status.className = "puzzle-status";
      this.board.locked = true;
      setTimeout(() => this._next(), 2600);
    }
  }

  _next() {
    this.idx++;
    if (this.idx >= this.queue.length) {
      this.root.innerHTML = `<div class="empty" style="font-size:18px">
        ${this.kid ? "🌟 Daily set complete! Amazing work!" : "Set complete."}</div>`;
      if (this.onSetDone) this.onSetDone();
      return;
    }
    this._load();
  }
}
