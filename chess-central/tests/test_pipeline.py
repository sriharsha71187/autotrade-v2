"""Core pipeline tests: annotation, motifs, puzzles, scheduling, badges, insights.
All offline — FakeEngine, no network, no Stockfish."""
import json

import chess

from app import db, util
from app.analysis import annotate
from app.analysis.motifs import hanging_pieces, is_fork, tags_for_mistake
from app.puzzles import generator, scheduler


def test_win_prob_symmetry():
    assert util.win_prob(0, True) == 50
    assert util.win_prob(300, True) > 70
    assert util.win_prob(300, False) < 30
    assert util.win_prob(300, True) + util.win_prob(300, False) == 100


def test_classify_drop_bands():
    assert util.classify_drop(0) == "best"
    assert util.classify_drop(12) == "inaccuracy"
    assert util.classify_drop(22) == "mistake"
    assert util.classify_drop(35) == "blunder"


def test_phase_heuristic():
    assert util.phase_of(chess.Board()) == "opening"
    endgame = chess.Board("8/5k2/8/8/8/3K4/4P3/8 w - - 0 50")
    assert util.phase_of(endgame) == "endgame"


def test_hanging_piece_detection():
    # black queen on g5 attacked by Nf3, undefended
    board = chess.Board("rnb1kbnr/pppp1ppp/8/4p1q1/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3")
    hangs = hanging_pieces(board, chess.BLACK)
    assert chess.G5 in hangs


def test_fork_detection():
    # white knight c3->d5 forking undefended queen e7 + c7 pawn/king targets
    board = chess.Board("rnb1kbnr/ppppqppp/8/8/8/2N5/PPPPPPPP/R1BQKBNR w KQkq - 0 4")
    move = chess.Move.from_uci("c3d5")
    assert is_fork(board, move)


def test_annotation_flags_blunder(analyzed_game):
    rows = db.rows(
        "SELECT * FROM moves WHERE game_id=? AND mover='player' "
        "AND classification='blunder'", (analyzed_game,))
    assert rows, "Qxe5+ should be flagged as a blunder"
    assert any("moved_en_prise" in (r["motifs"] or "") for r in rows)


def test_missed_tactic_motifs(missed_tactic_game):
    rows = db.rows(
        "SELECT * FROM moves WHERE game_id=? AND mover='player' "
        "AND classification IN ('mistake','blunder')", (missed_tactic_game,))
    tags = [t for r in rows for t in json.loads(r["motifs"] or "[]")]
    assert "missed_capture" in tags


def test_puzzle_generation_and_solution(missed_tactic_game):
    created = generator.generate_for_game(missed_tactic_game)
    assert created >= 1
    p = db.row("SELECT * FROM puzzles WHERE source='missed_tactic'")
    assert p is not None
    solution = json.loads(p["solution"])
    assert solution[0] == "f3g5"          # Nxg5 wins the queen
    assert len(solution) % 2 == 1         # ends on the player's move


def test_no_puzzle_without_unique_solution(analyzed_game):
    # "don't grab the pawn" has many fine alternatives -> no puzzle
    generator.generate_for_game(analyzed_game)
    assert db.scalar(
        "SELECT COUNT(*) FROM puzzles WHERE game_id=?", (analyzed_game,)) == 0


def test_spaced_repetition_progression(missed_tactic_game):
    generator.generate_for_game(missed_tactic_game)
    p = db.row("SELECT * FROM puzzles LIMIT 1")
    r1 = scheduler.record_attempt(p["id"], True)
    assert r1["interval_days"] == 1
    r2 = scheduler.record_attempt(p["id"], True)
    assert r2["interval_days"] == 3
    r3 = scheduler.record_attempt(p["id"], False)
    assert r3["interval_days"] == 0       # lapse resets
    p2 = db.row("SELECT * FROM puzzles WHERE id=?", (p["id"],))
    assert p2["lapses"] == 1
    assert p2["ease"] < 2.5


def test_daily_set_and_stats(missed_tactic_game):
    generator.generate_for_game(missed_tactic_game)
    ds = scheduler.daily_set()
    assert len(ds) == 1
    scheduler.record_attempt(ds[0]["id"], True, 3000)
    s = scheduler.stats()
    assert s["solved_today"] == 1
    assert s["streak_days"] == 1
    assert s["accuracy_pct"] == 100.0


def test_badges(analyzed_game, missed_tactic_game):
    from app.coach import badges
    generator.generate_for_game(missed_tactic_game)
    ds = scheduler.daily_set()
    scheduler.record_attempt(ds[0]["id"], True)
    newly = badges.recompute()
    assert "first_puzzle" in newly


def test_insights_generate(analyzed_game, missed_tactic_game, monkeypatch):
    from app.coach import insights
    monkeypatch.setattr(insights, "MIN_GAMES", 1)
    monkeypatch.setattr(insights, "MIN_PHASE_MOVES", 2)
    insights.regenerate()
    rows = insights.current()
    # small sample: at minimum the structure holds and rows are well-formed
    for r in rows:
        assert r["kind"] in ("strength", "weakness", "opportunity")
        assert r["title"] and r["detail"]


def test_game_accuracy_summary(analyzed_game):
    acc = annotate.game_accuracy(analyzed_game)
    assert acc["moves"] == 8
    assert acc["blunder"] >= 1
