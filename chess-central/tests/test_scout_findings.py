"""Rival scouting: plain-English findings and learning-plan generation."""
import json

from app import db
from app.rivals import scout


def _games(n_wins=6, n_losses=4, mate_wins=4, time_losses=2, moves=20):
    out = []
    for i in range(n_wins):
        out.append({"scout_side": "white", "result": "win",
                    "termination": "mate" if i < mate_wins else "resign",
                    "moves_count": moves * 2, "opening_name": "Italian Game",
                    "eco": "C50", "pgn": "", "played_at": "2026-08-01T00:00:00Z"})
    for i in range(n_losses):
        out.append({"scout_side": "black", "result": "loss",
                    "termination": "outoftime" if i < time_losses else "resign",
                    "moves_count": moves * 2, "opening_name": "Sicilian Defense",
                    "eco": "B20", "pgn": "", "played_at": "2026-08-01T00:00:00Z"})
    return out


def test_style_patterns():
    p = scout._style_patterns(_games())
    assert p["games_seen"] == 10
    assert p["win_pct"] == 60
    assert p["wins_by_mate_pct"] == 67       # 4 of 6
    assert p["losses_on_time_pct"] == 50     # 2 of 4
    assert p["avg_game_moves"] == 20
    assert p["white_score_pct"] == 100
    assert p["black_score_pct"] == 0


def test_findings_english():
    weapons = [{"motif": "played_fork", "label": "Played a fork", "count": 5}]
    weaknesses = [{"motif": "left_piece_hanging", "label": "Left a piece hanging", "count": 7}]
    patterns = scout._style_patterns(_games())
    repertoire = {"white": [{"opening": "Italian Game", "n": 6, "w": 5, "win_pct": 83}],
                  "black": []}
    h2h = {"games": [1, 2], "wins": 0, "losses": 2, "draws": 0}
    out = scout._findings_english("TestKid", weapons, weaknesses, patterns, repertoire, h2h)

    kinds = {f["kind"] for f in out}
    assert kinds == {"weapon", "weakness", "pattern"}
    weapon = next(f for f in out if f["kind"] == "weapon")
    assert "fork" in weapon["title"].lower()
    assert "5×" in weapon["detail"]
    titles = " | ".join(f["title"] for f in out)
    assert "checkmate" in titles.lower()          # mate-heavy wins pattern
    assert "Italian Game" in titles               # repertoire pattern
    assert "Head-to-head" in titles


def test_learn_plan_orders_and_counts():
    with db.tx() as conn:
        for i in range(3):
            conn.execute(
                """INSERT INTO puzzles (source, game_id, ply, fen, solution, themes,
                     difficulty, rival, created_at, due_at)
                   VALUES ('rival_prep', NULL, ?, 'fen', '[]',
                           '["left_piece_hanging"]', 3, 'TestKid',
                           '2026-08-15T00:00:00Z', '2026-08-15T00:00:00Z')""", (i,))
    weaknesses = [{"motif": "left_piece_hanging", "label": "Left a piece hanging", "count": 7}]
    weapons = [{"motif": "played_fork", "label": "Played a fork", "count": 5}]
    repertoire = {"white": [{"opening": "Italian Game", "n": 6, "w": 5, "win_pct": 83}],
                  "black": []}
    plan = scout._learn_plan("TestKid", weapons, weaknesses, repertoire,
                             {"games": [1], "wins": 0, "losses": 1, "draws": 0})

    steps = [s["step"] for s in plan]
    assert steps[0] == "Openings"
    assert "Punish drill" in steps and "Defense drill" in steps
    assert steps[-1] == "Game day"
    drill = next(s for s in plan if s["step"] == "Punish drill")
    assert drill["action"] == {"type": "puzzles", "theme": "left_piece_hanging"}
    assert drill["ready"] == 3
    full = next(s for s in plan if s["step"] == "Full prep set")
    assert full["ready"] == 3


def test_theme_filtered_rival_puzzles():
    from app.puzzles import scheduler
    with db.tx() as conn:
        conn.execute(
            """INSERT INTO puzzles (source, ply, fen, solution, themes, difficulty,
                 rival, created_at, due_at)
               VALUES ('rival_prep', 0, 'fen', '["e2e4"]', '["allowed_fork"]', 3,
                       'Kid', '2026-08-15T00:00:00Z', '2026-08-15T00:00:00Z')""")
        conn.execute(
            """INSERT INTO puzzles (source, ply, fen, solution, themes, difficulty,
                 rival, created_at, due_at)
               VALUES ('rival_prep', 1, 'fen', '["d2d4"]', '["back_rank"]', 3,
                       'Kid', '2026-08-15T00:00:00Z', '2026-08-15T00:00:00Z')""")
    assert len(scheduler.daily_set(rival="Kid")) == 2
    only = scheduler.daily_set(rival="Kid", theme="allowed_fork")
    assert len(only) == 1
    assert only[0]["themes"] == ["allowed_fork"]
