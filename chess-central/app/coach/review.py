"""Guided loss review — the five-minute ritual that matters most at his level.

For each analyzed loss, find the turning point (the player move that threw
away the most win probability) and turn it into a find-the-better-move
exercise on the board. Completing it marks the game reviewed.
"""
from __future__ import annotations

import json

from .. import db, util


def queue(limit: int = 5) -> list[dict]:
    losses = db.rows(
        """SELECT * FROM games WHERE result='loss' AND analyzed_at IS NOT NULL
           AND reviewed_at IS NULL ORDER BY played_at DESC LIMIT ?""", (limit,))
    out = []
    for g in losses:
        tp = db.row(
            """SELECT *, (winprob_before - winprob_after) AS wp_drop FROM moves
               WHERE game_id=? AND mover='player' AND best_uci IS NOT NULL
               ORDER BY (winprob_before - winprob_after) DESC LIMIT 1""",
            (g["id"],))
        if not tp or (tp["wp_drop"] or 0) < 10:
            # nothing decisive to learn from — count it reviewed
            mark(g["id"])
            continue
        pv = (tp["pv"] or "").split()
        solution = pv[:3] if pv else [tp["best_uci"]]
        if len(solution) % 2 == 0:
            solution = solution[:-1] or [tp["best_uci"]]
        out.append({
            "game_id": g["id"],
            "opponent": g["opponent_name"],
            "played_at": g["played_at"],
            "platform": g["platform"],
            "move_number": (tp["ply"] + 1) // 2,
            "played": tp["san"],
            "better": tp["best_san"] or tp["best_uci"],
            "drop_pct": round(tp["wp_drop"]),
            "motifs": json.loads(tp["motifs"] or "[]"),
            "puzzle": {
                "id": None,
                "fen": tp["fen_before"],
                "solution": solution,
                "themes": json.loads(tp["motifs"] or "[]"),
                "difficulty": 2,
                "source": "loss_review",
                "explanation": f"In the game you played {tp['san']}. "
                               f"{tp['best_san'] or tp['best_uci']} was the move — "
                               "this is the moment the game turned.",
                "rival": None, "reps": 0,
            },
        })
    return out


def mark(game_id: int) -> None:
    with db.tx() as conn:
        conn.execute("UPDATE games SET reviewed_at=? WHERE id=?",
                     (util.now_iso(), game_id))
