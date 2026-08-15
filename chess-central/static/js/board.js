// Animated, draggable chessboard.
// Squares form the background grid; pieces live in an absolutely-positioned
// layer so moves glide, captures fade, and drags follow the pointer.
// It does NOT validate full chess legality — the puzzle solution is the referee.

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

export class Board {
  constructor(container, { onMove = null } = {}) {
    this.el = container;
    this.onMove = onMove;
    this.sel = null;
    this.locked = false;
    this.flipped = false;
    this.pos = null;
    this.pieces = new Map();   // square -> piece element
    this.cells = new Map();    // square -> background cell
    this.el.classList.add("board2");
    this._buildGrid();
    this.layer = document.createElement("div");
    this.layer.className = "piece-layer";
    this.el.append(this.layer);
    this.el.addEventListener("pointerdown", (e) => this._pointerDown(e));
  }

  _buildGrid() {
    this.grid = document.createElement("div");
    this.grid.className = "board-grid";
    this.el.append(this.grid);
  }

  _squares() {
    const files = this.flipped ? "hgfedcba" : "abcdefgh";
    const ranks = this.flipped ? "12345678" : "87654321";
    const out = [];
    for (const r of ranks) for (const f of files) out.push(f + r);
    return out;
  }

  _xy(sq) {
    // percentage position of a square's top-left in current orientation
    let file = sq.charCodeAt(0) - 97;      // 0..7
    let rank = +sq[1] - 1;                 // 0..7
    if (this.flipped) { file = 7 - file; rank = 7 - rank; }
    return { x: file * 12.5, y: (7 - rank) * 12.5 };
  }

  _renderGrid() {
    this.grid.innerHTML = "";
    this.cells.clear();
    for (const sq of this._squares()) {
      const dark = (sq.charCodeAt(0) - 97 + +sq[1]) % 2 === 0;
      const cell = document.createElement("div");
      cell.className = `bsq ${dark ? "dark" : "light"}`;
      cell.dataset.sq = sq;
      this.grid.append(cell);
      this.cells.set(sq, cell);
    }
    // coordinates on the edge squares
    const files = this.flipped ? "hgfedcba" : "abcdefgh";
    const bottomRank = this.flipped ? "8" : "1";
    for (const f of files) {
      const c = this.cells.get(f + bottomRank);
      const lbl = document.createElement("span");
      lbl.className = "coord file";
      lbl.textContent = f;
      c.append(lbl);
    }
    const leftFile = this.flipped ? "h" : "a";
    for (const r of "12345678") {
      const c = this.cells.get(leftFile + r);
      const lbl = document.createElement("span");
      lbl.className = "coord rank";
      lbl.textContent = r;
      c.append(lbl);
    }
  }

  load(fen, { flipped = null } = {}) {
    this.pos = parseFen(fen);
    this.flipped = flipped !== null ? flipped : this.pos.turn === "b";
    this.sel = null;
    this.locked = false;
    this._renderGrid();
    this.layer.innerHTML = "";
    this.pieces.clear();
    for (const [sq, piece] of Object.entries(this.pos.board)) {
      this._addPiece(sq, piece, true);
    }
    this.clearMarks();
  }

  _addPiece(sq, piece, popIn = false) {
    const elp = document.createElement("div");
    const isWhite = piece === piece.toUpperCase();
    elp.className = `pc ${isWhite ? "w" : "b"}${popIn ? " pop" : ""}`;
    elp.textContent = GLYPHS[piece];
    elp.dataset.piece = piece;
    const { x, y } = this._xy(sq);
    elp.style.left = `${x}%`;
    elp.style.top = `${y}%`;
    this.layer.append(elp);
    this.pieces.set(sq, elp);
    return elp;
  }

  // ---- marks -------------------------------------------------------------

  clearMarks() {
    for (const c of this.cells.values())
      c.classList.remove("sel", "hint", "last-from", "last-to", "good");
  }

  mark(sq, cls) { this.cells.get(sq)?.classList.add(cls); }

  markLastMove(from, to) {
    this.clearMarks();
    this.mark(from, "last-from");
    this.mark(to, "last-to");
  }

  shake(sq) {
    const p = this.pieces.get(sq);
    const c = this.cells.get(sq);
    c?.classList.add("bad-flash");
    p?.classList.add("shake");
    setTimeout(() => { p?.classList.remove("shake"); c?.classList.remove("bad-flash"); }, 500);
  }

  glow(sq) {
    const c = this.cells.get(sq);
    c?.classList.add("good");
    setTimeout(() => c?.classList.remove("good"), 900);
  }

  // ---- moves -------------------------------------------------------------

  play(uci, { animate = true } = {}) {
    const from = uci.slice(0, 2), to = uci.slice(2, 4), promo = uci[4];
    const piece = this.pos.board[from];
    if (!piece) return;

    // en passant: pawn moves diagonally onto an empty square
    if (piece.toLowerCase() === "p" && from[0] !== to[0] && !this.pos.board[to]) {
      const epSq = to[0] + from[1];
      delete this.pos.board[epSq];
      this._removePiece(epSq);
    }
    // capture
    if (this.pos.board[to]) this._removePiece(to);

    delete this.pos.board[from];
    this.pos.board[to] = promo
      ? (piece === piece.toUpperCase() ? promo.toUpperCase() : promo.toLowerCase())
      : piece;

    const elp = this.pieces.get(from);
    this.pieces.delete(from);
    if (elp) {
      this.pieces.set(to, elp);
      const { x, y } = this._xy(to);
      if (!animate) elp.classList.add("no-anim");
      elp.style.left = `${x}%`;
      elp.style.top = `${y}%`;
      if (!animate) requestAnimationFrame(() => elp.classList.remove("no-anim"));
      if (promo) setTimeout(() => { elp.textContent = GLYPHS[this.pos.board[to]]; }, 180);
    }

    // castling: king moved two files -> slide the rook
    if (piece.toLowerCase() === "k" && Math.abs(from.charCodeAt(0) - to.charCodeAt(0)) === 2) {
      const rank = from[1];
      const [rFrom, rTo] = to[0] === "g" ? ["h" + rank, "f" + rank] : ["a" + rank, "d" + rank];
      const rook = this.pos.board[rFrom];
      if (rook) {
        delete this.pos.board[rFrom];
        this.pos.board[rTo] = rook;
        const relp = this.pieces.get(rFrom);
        this.pieces.delete(rFrom);
        if (relp) {
          this.pieces.set(rTo, relp);
          const { x, y } = this._xy(rTo);
          relp.style.left = `${x}%`;
          relp.style.top = `${y}%`;
        }
      }
    }
    this.pos.turn = this.pos.turn === "w" ? "b" : "w";
    this.markLastMove(from, to);
  }

  _removePiece(sq) {
    const elp = this.pieces.get(sq);
    if (elp) {
      this.pieces.delete(sq);
      elp.classList.add("captured");
      setTimeout(() => elp.remove(), 260);
    }
  }

  // ---- interaction (click + drag) ---------------------------------------

  _sqFromPoint(clientX, clientY) {
    const r = this.el.getBoundingClientRect();
    let fx = Math.floor(((clientX - r.left) / r.width) * 8);
    let fy = Math.floor(((clientY - r.top) / r.height) * 8);
    if (fx < 0 || fx > 7 || fy < 0 || fy > 7) return null;
    if (this.flipped) { fx = 7 - fx; fy = 7 - fy; }
    return "abcdefgh"[fx] + (8 - fy);
  }

  _isMine(piece) {
    return piece && ((this.pos.turn === "w") === (piece === piece.toUpperCase()));
  }

  _pointerDown(e) {
    if (this.locked) return;
    const sq = this._sqFromPoint(e.clientX, e.clientY);
    if (!sq) return;
    const piece = this.pos.board[sq];

    // click-move: second click on a target square
    if (this.sel && !this._isMine(piece)) {
      const from = this.sel;
      this._deselect();
      if (this.onMove) this.onMove(from + sq);
      return;
    }
    if (!this._isMine(piece)) { this._deselect(); return; }

    // select + begin possible drag
    this._deselect();
    this.sel = sq;
    this.mark(sq, "sel");
    const elp = this.pieces.get(sq);
    if (!elp) return;
    e.preventDefault();

    const startX = e.clientX, startY = e.clientY;
    let dragging = false;
    const r = this.el.getBoundingClientRect();

    const onMove = (ev) => {
      const dx = ev.clientX - startX, dy = ev.clientY - startY;
      if (!dragging && Math.hypot(dx, dy) > 5) {
        dragging = true;
        elp.classList.add("dragging");
      }
      if (dragging) {
        const px = ((ev.clientX - r.left) / r.width) * 100 - 6.25;
        const py = ((ev.clientY - r.top) / r.height) * 100 - 6.25;
        elp.style.left = `${Math.max(-6, Math.min(94, px))}%`;
        elp.style.top = `${Math.max(-6, Math.min(94, py))}%`;
      }
    };
    const onUp = (ev) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      if (!dragging) return;               // plain click — wait for second click
      elp.classList.remove("dragging");
      const target = this._sqFromPoint(ev.clientX, ev.clientY);
      const from = this.sel;
      this._deselect();
      if (target && target !== from) {
        // snap home first; the game logic replays it (or shakes it) from truth
        this._snapHome(from, elp);
        if (this.onMove) this.onMove(from + target);
      } else {
        this._snapHome(from, elp);
      }
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }

  _snapHome(sq, elp) {
    const { x, y } = this._xy(sq);
    elp.style.left = `${x}%`;
    elp.style.top = `${y}%`;
  }

  _deselect() {
    if (this.sel) this.cells.get(this.sel)?.classList.remove("sel");
    this.sel = null;
  }

  hintFrom(sq) {
    this.mark(sq, "hint");
  }
}
