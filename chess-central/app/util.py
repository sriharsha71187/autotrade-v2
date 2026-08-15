"""Shared helpers: PGN parsing into game records, win-probability math."""
from __future__ import annotations

import io
import math
import re
from datetime import datetime, timezone

import chess
import chess.pgn

EVAL_CLAMP = 1000          # centipawns; mates map just beyond this
MATE_CP = 1200

PIECE_VALUES = {
    chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
    chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def win_prob(cp_white: int, pov_white: bool) -> float:
    """Expected score (0-100) from a centipawn eval, lichess curve."""
    cp = max(-EVAL_CLAMP, min(EVAL_CLAMP, cp_white))
    p = 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)
    return round(p if pov_white else 100 - p, 2)


def classify_drop(drop: float) -> str:
    """Classify a move by the mover's win-probability drop (percentage points)."""
    if drop >= 30:
        return "blunder"
    if drop >= 20:
        return "mistake"
    if drop >= 10:
        return "inaccuracy"
    if drop >= 4:
        return "good"
    return "best"


def phase_of(board: chess.Board) -> str:
    """Game phase heuristic: piece count of non-pawn, non-king material."""
    minors_majors = 0
    for pt in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
        minors_majors += len(board.pieces(pt, chess.WHITE)) + len(board.pieces(pt, chess.BLACK))
    if minors_majors <= 6:
        return "endgame"
    if board.fullmove_number <= 10:
        return "opening"
    return "middlegame"


CLK_RE = re.compile(r"\[%clk\s+(\d+):(\d+):(\d+(?:\.\d+)?)\]")


def clock_from_comment(comment: str) -> float | None:
    m = CLK_RE.search(comment or "")
    if not m:
        return None
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + s


def parse_pgn_game(pgn_text: str) -> chess.pgn.Game | None:
    try:
        return chess.pgn.read_game(io.StringIO(pgn_text))
    except Exception:
        return None


def result_for(pgn_result: str, color: str) -> str:
    if pgn_result == "1/2-1/2":
        return "draw"
    if pgn_result == "1-0":
        return "win" if color == "white" else "loss"
    if pgn_result == "0-1":
        return "win" if color == "black" else "loss"
    return "draw"


def time_class_from_control(tc: str) -> str:
    """Rough Lichess-style bucket from a 'base+inc' time control string."""
    try:
        if tc in ("-", "?", ""):
            return "daily"
        base, _, inc = tc.partition("+")
        total = int(base) + 40 * int(inc or 0)
    except ValueError:
        return "rapid"
    if total < 180:
        return "bullet"
    if total < 480:
        return "blitz"
    if total < 1500:
        return "rapid"
    return "classical"


def normalize_result_tag(res: str) -> str:
    return {"1-0": "1-0", "0-1": "0-1", "1/2-1/2": "1/2-1/2"}.get(res, "*")


def opening_family(eco: str | None, name: str | None) -> str:
    """Collapse opening names to a family for aggregation ('Italian Game' etc.)."""
    if name:
        base = re.split(r"[:,]", name)[0].strip()
        if base:
            return base
    return eco or "Unknown"
