"""Stockfish wrapper with a pluggable interface so tests can run engine-free."""
from __future__ import annotations

from dataclasses import dataclass, field

import chess
import chess.engine

from .. import config
from ..util import EVAL_CLAMP, MATE_CP


@dataclass
class PositionEval:
    """Engine verdict on one position, from White's point of view."""
    cp: int                              # clamped centipawns (mate → ±MATE_CP)
    mate_in: int | None = None           # signed; + means White mates
    best: chess.Move | None = None
    pv: list[chess.Move] = field(default_factory=list)
    second: chess.Move | None = None
    second_cp: int | None = None


def score_to_cp(score: chess.engine.PovScore) -> tuple[int, int | None]:
    """White-POV clamped centipawns plus mate distance."""
    white = score.white()
    mate = white.mate()
    if mate is not None:
        return (MATE_CP if mate > 0 else -MATE_CP), mate
    cp = white.score(mate_score=MATE_CP)
    return max(-EVAL_CLAMP, min(EVAL_CLAMP, cp or 0)), None


class Engine:
    """Real Stockfish via UCI."""

    def __init__(self, path: str | None = None, threads: int | None = None):
        path = path or config.find_engine()
        if not path:
            raise RuntimeError(
                "Stockfish not found. Install with `brew install stockfish` "
                "or set engine_path in config.json / Settings."
            )
        self._engine = chess.engine.SimpleEngine.popen_uci(path)
        self._engine.configure({"Threads": threads or config.get("engine_threads")})

    def evaluate(self, board: chess.Board, movetime_ms: int | None = None,
                 multipv: int = 2) -> PositionEval:
        limit = chess.engine.Limit(
            time=(movetime_ms or config.get("engine_movetime_ms")) / 1000
        )
        infos = self._engine.analyse(board, limit, multipv=multipv)
        if isinstance(infos, dict):
            infos = [infos]
        cp, mate = score_to_cp(infos[0]["score"])
        pv = infos[0].get("pv", [])
        ev = PositionEval(cp=cp, mate_in=mate, best=pv[0] if pv else None, pv=pv[:8])
        if len(infos) > 1 and infos[1].get("pv"):
            ev.second = infos[1]["pv"][0]
            ev.second_cp, _ = score_to_cp(infos[1]["score"])
        return ev

    def close(self):
        try:
            self._engine.quit()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeEngine:
    """Deterministic 2-ply material minimax for tests and engine-less demo mode.

    Deep enough to see hung pieces and simple recaptures, so the annotation
    pipeline behaves realistically without Stockfish installed. Not a chess
    coach — the app warns when running on this.
    """

    DEPTH = 2

    def evaluate(self, board: chess.Board, movetime_ms=None, multipv: int = 2) -> PositionEval:
        from ..util import PIECE_VALUES

        def material(b: chess.Board) -> int:
            total = 0
            for pt, val in PIECE_VALUES.items():
                total += val * (len(b.pieces(pt, chess.WHITE)) - len(b.pieces(pt, chess.BLACK)))
            return total * 100

        def negamax(b: chess.Board, depth: int) -> int:
            """Score from the side-to-move's perspective."""
            if b.is_checkmate():
                return -MATE_CP
            if b.is_stalemate() or b.is_insufficient_material():
                return 0
            base = material(b) if b.turn == chess.WHITE else -material(b)
            if depth == 0:
                return base
            best = None
            for move in b.legal_moves:
                b.push(move)
                score = -negamax(b, depth - 1)
                b.pop()
                if best is None or score > best:
                    best = score
            return base if best is None else best

        if board.is_checkmate():
            cp = -MATE_CP if board.turn == chess.WHITE else MATE_CP
            return PositionEval(cp=cp, mate_in=0)

        scored: list[tuple[int, chess.Move]] = []
        for move in board.legal_moves:
            board.push(move)
            score = -negamax(board, self.DEPTH - 1)
            board.pop()
            scored.append((score, move))
        if not scored:
            return PositionEval(cp=0)
        scored.sort(key=lambda t: -t[0])
        best_score, best_move = scored[0]
        cp_white = best_score if board.turn == chess.WHITE else -best_score
        cp_white = max(-EVAL_CLAMP, min(EVAL_CLAMP, cp_white))
        ev = PositionEval(cp=cp_white, best=best_move, pv=[best_move])
        if len(scored) > 1:
            second_score, second_move = scored[1]
            s_white = second_score if board.turn == chess.WHITE else -second_score
            ev.second = second_move
            ev.second_cp = max(-EVAL_CLAMP, min(EVAL_CLAMP, s_white))
        return ev

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def open_engine():
    """Real engine if available, else FakeEngine (flagged so UI can warn)."""
    try:
        return Engine(), True
    except Exception:
        return FakeEngine(), False
