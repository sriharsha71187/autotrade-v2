"""Puzzle generation from Nirvaan's own games.

Two sources, both gold for a developing player:
  * own_blunder  — position before a move where he threw away the game;
                   solving it = playing what he should have played.
  * missed_tactic — opponent just blundered, he had a winning shot and
                    missed it; solving it = taking the shot.

A position becomes a puzzle only when the engine's best move is clearly
better than the alternative (uniqueness gap), so puzzles have one right idea.
"""
from __future__ import annotations

import json

import chess

from .. import db, util

MIN_DROP = 20           # win-prob points thrown away to qualify
UNIQUE_GAP_CP = 150     # best-vs-second gap for a single-solution puzzle
MAX_SOLUTION_PLIES = 3


def generate_for_game(game_id: int) -> int:
    game = db.row("SELECT * FROM games WHERE id=?", (game_id,))
    if not game:
        return 0
    moves = db.rows("SELECT * FROM moves WHERE game_id=? ORDER BY ply", (game_id,))
    created = 0
    to_insert = []
    for i, m in enumerate(moves):
        if m["mover"] != "player":
            continue
        drop = max(0.0, (m["winprob_before"] or 0) - (m["winprob_after"] or 0))
        if drop < MIN_DROP or not m["best_uci"]:
            continue
        # uniqueness: needs a clear gap to the second-best move (when known)
        if m["second_gap_cp"] is not None and m["second_gap_cp"] < UNIQUE_GAP_CP:
            continue
        # was this a missed punishment of an opponent mistake?
        prev = moves[i - 1] if i > 0 else None
        source = "own_blunder"
        if prev and prev["mover"] == "opponent" and prev["classification"] in ("mistake", "blunder"):
            source = "missed_tactic"

        themes = json.loads(m["motifs"] or "[]")
        solution = _solution_line(m)
        if not solution:
            continue
        difficulty = _difficulty(drop, themes)
        explanation = _explain(m, source, themes)
        to_insert.append(
            (source, game_id, m["ply"], m["fen_before"], json.dumps(solution),
             json.dumps(themes), m["phase"], difficulty, explanation,
             util.now_iso(), util.now_iso()))
    if to_insert:
        # one write transaction per game, not per puzzle — the analysis
        # worker calls this constantly and must not hog the write lock
        with db.tx() as conn:
            for row in to_insert:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO puzzles
                       (source, game_id, ply, fen, solution, themes, phase, difficulty,
                        explanation, created_at, due_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""", row)
                created += cur.rowcount
    return created


def _solution_line(m: dict) -> list[str]:
    """Solution = engine PV from the position, trimmed to a kid-sized length."""
    pv = (m["pv"] or "").split()
    if not pv:
        return [m["best_uci"]] if m["best_uci"] else []
    line = pv[:MAX_SOLUTION_PLIES]
    # end on the player's move (odd count) so the last thing solved is his idea
    if len(line) % 2 == 0:
        line = line[:-1]
    return line or [m["best_uci"]]


def _difficulty(drop: float, themes: list[str]) -> int:
    if "missed_mate" in themes:
        return 1 if drop >= 45 else 2
    if drop >= 45:
        return 2
    if drop >= 30:
        return 3
    return 4


def _explain(m: dict, source: str, themes: list[str]) -> str:
    from ..analysis.motifs import MOTIF_LABELS

    what = ", ".join(MOTIF_LABELS.get(t, t) for t in themes[:2]) or "a better move"
    if source == "missed_tactic":
        lead = "Your opponent just made a mistake here"
    else:
        lead = f"In this game you played {m['san']}"
    best = m["best_san"] or m["best_uci"]
    return f"{lead}. Theme: {what}. The strongest move was {best}."


def generate_all_pending() -> int:
    """Backfill puzzles for analyzed games that have none yet."""
    game_ids = [r["id"] for r in db.rows(
        """SELECT g.id FROM games g
           WHERE g.analyzed_at IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM puzzles p
                             WHERE p.game_id = g.id AND p.source != 'rival_prep')""")]
    return sum(generate_for_game(gid) for gid in game_ids)
