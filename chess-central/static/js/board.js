// Interactive chessboard for puzzle solving. Renders a FEN, lets the solver
// click from-square then to-square, and applies UCI moves (incl. castling,
// en passant, promotion) well enough to play out a known solution line.
// It does NOT validate full chess legality — the solution line is the referee.

const GLYPHS = {
  K: "♔", Q: "♕", R: "♖", B: "♗", N: "♘", P: "♙",
  k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟",
};

export function parseFen(fen) {
  const [placement, turn] = fen.split(" ");
  const board = {};
  let rank = 8, file = 0;
  for (const ch of placement) {
    if (ch === "/") { rank--; file = 0; }
    else if (/\d/.test(ch)) file += +ch;
    else { board["abcdefgh"[file] + rank] = ch; file++; }
  }
  return { board, turn: turn || "w" };
}

export function applyUci(pos, uci) {
  const from = uci.slice(0, 2), to = uci.slice(2, 4), promo = uci[4];
  const piece = pos.board[from];
  if (!piece) return pos;
  delete pos.board[from];
  // en passant: pawn moves diagonally onto empty square
  if (piece.toLowerCase() === "p" && from[0] !== to[0] && !pos.board[to]) {
    delete pos.board[to[0] + from[1]];
  }
  pos.board[to] = promo
    ? (piece === piece.toUpperCase() ? promo.toUpperCase() : promo.toLowerCase())
    : piece;
  // castling: king moved two files → bring the rook across
  if (piece.toLowerCase() === "k" && Math.abs(from.charCodeAt(0) - to.charCodeAt(0)) === 2) {
    const rank = from[1];
    if (to[0] === "g") { pos.board["f" + rank] = pos.board["h" + rank]; delete pos.board["h" + rank]; }
    if (to[0] === "c") { pos.board["d" + rank] = pos.board["a" + rank]; delete pos.board["a" + rank]; }
  }
  pos.turn = pos.turn === "w" ? "b" : "w";
  return pos;
}

export class Board {
  constructor(container, { onMove = null } = {}) {
    this.el = container;
    this.onMove = onMove;
    this.sel = null;
    this.locked = false;
    this.flipped = false;
    this.pos = null;
    this.el.classList.add("board");
    this.el.addEventListener("click", (ev) => this._click(ev));
  }

  load(fen, { flipped = null } = {}) {
    this.pos = parseFen(fen);
    // orient so the side to move is at the bottom (it's always "your move")
    this.flipped = flipped !== null ? flipped : this.pos.turn === "b";
    this.sel = null;
    this.locked = false;
    this.render();
  }

  play(uci) { applyUci(this.pos, uci); this.render(); }

  render(marks = {}) {
    this.el.innerHTML = "";
    const files = this.flipped ? "hgfedcba" : "abcdefgh";
    const ranks = this.flipped ? "12345678" : "87654321";
    for (const r of ranks) {
      for (const f of files) {
        const sq = f + r;
        const dark = (f.charCodeAt(0) - 97 + +r) % 2 === 0;
        const div = document.createElement("div");
        div.className = `sq ${dark ? "dark" : "light"}`;
        div.dataset.sq = sq;
        if (this.sel === sq) div.classList.add("sel");
        if (marks[sq]) div.classList.add(marks[sq]);
        const piece = this.pos.board[sq];
        if (piece) {
          const span = document.createElement("span");
          const isWhite = piece === piece.toUpperCase();
          span.className = `p ${isWhite ? "w" : "b"}`;
          span.textContent = GLYPHS[piece];
          div.append(span);
        }
        this.el.append(div);
      }
    }
  }

  flash(sq, cls, ms = 700) {
    this.render({ [sq]: cls });
    setTimeout(() => this.render(), ms);
  }

  _click(ev) {
    if (this.locked) return;
    const cell = ev.target.closest(".sq");
    if (!cell) return;
    const sq = cell.dataset.sq;
    const piece = this.pos.board[sq];
    const mine = piece && ((this.pos.turn === "w") === (piece === piece.toUpperCase()));
    if (this.sel === null || mine) {
      this.sel = mine ? sq : null;
      this.render();
      return;
    }
    const from = this.sel;
    this.sel = null;
    this.render();
    if (this.onMove) this.onMove(from + sq);
  }
}
