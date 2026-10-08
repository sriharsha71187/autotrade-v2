"""Progress dashboard: metric math, windows, trends, drill-down, lesson effects."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import db
from conftest import HANG_QUEEN_PGN, MISSED_TACTIC_PGN, insert_game


@pytest.fixture(autouse=True)
def _fresh_cache():
    from app.coach import progress
    progress._cache.update(sig=None, data=None)


def _client():
    from app.main import app
    return TestClient(app)


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _analyzed(pgn, days_ago, gid_str, result="loss"):
    from app.analysis import annotate
    from app.analysis.engine import FakeEngine
    gid = insert_game(pgn, game_id_str=gid_str, played_at=_ago(days_ago), result=result)
    annotate.annotate_game(db.row("SELECT * FROM games WHERE id=?", (gid,)), FakeEngine())
    with db.tx() as conn:
        conn.execute("UPDATE games SET engine='stockfish' WHERE id=?", (gid,))
    return gid


def _metric(data, key):
    for g in data["groups"]:
        for m in g["metrics"]:
            if m["key"] == key:
                return m
    raise KeyError(key)


def _set_moves(gid, n_moves, hang_moves):
    """Make a game's player moves exactly n_moves long with hang_moves hanging mistakes."""
    ms = db.rows("SELECT id FROM moves WHERE game_id=? AND mover='player' ORDER BY ply", (gid,))
    with db.tx() as conn:
        for i, m in enumerate(ms):
            hang = i < hang_moves
            conn.execute("UPDATE moves SET classification=?, motifs=? WHERE id=?",
                         ("blunder" if hang else "best",
                          json.dumps(["left_piece_hanging"] if hang else []), m["id"]))


def test_hanging_rate_trend_improves():
    from app.coach import progress
    old = [_analyzed(HANG_QUEEN_PGN, 100 + i, f"old{i}") for i in range(30)]
    new = [_analyzed(HANG_QUEEN_PGN, 5 + i, f"new{i}") for i in range(30)]
    for gid in old:
        _set_moves(gid, 0, 3)          # lots of hanging pieces before
    for gid in new:
        _set_moves(gid, 0, 1)          # fewer now
    data = progress.dashboard()
    m = _metric(data, "hanging")
    assert m["current"] is not None and m["previous"] is not None
    assert m["current"] < m["previous"]
    assert m["trend"] == "better"           # lower is better for hanging pieces
    assert m["series"] and all("month" in s for s in m["series"])
    assert m["lesson"] is None or m["lesson"]["lesson_id"] == "vision-is-it-safe"

    b = _metric(data, "blunders")
    assert b["current"] < b["previous"] and b["trend"] == "better"


def test_not_enough_data_is_none():
    from app.coach import progress
    _analyzed(HANG_QUEEN_PGN, 3, "solo")
    data = progress.dashboard()
    m = _metric(data, "hanging")            # needs 100 moves in the window
    assert m["current"] is None and m["trend"] is None
    conv = _metric(data, "conversion")
    assert conv["current"] is None


def test_results_and_time_metrics():
    from app.coach import progress
    for i in range(6):
        gid = insert_game(HANG_QUEEN_PGN, game_id_str=f"t{i}", played_at=_ago(10 + i),
                          result="loss" if i < 3 else "win",
                          opponent_rating=700, player_rating=500)
        if i < 3:
            with db.tx() as conn:
                conn.execute("UPDATE games SET termination='outoftime' WHERE id=?", (gid,))
    data = progress.dashboard()
    assert _metric(data, "time_losses")["current"] == 50.0
    assert _metric(data, "score_white")["current"] == 50.0
    assert _metric(data, "score_higher")["current"] == 50.0
    assert _metric(data, "score_lower")["current"] is None
    assert data["games"] == 6 and data["analyzed_games"] == 0


def test_tilt_needs_same_day_back_to_back_losses():
    from app.coach import progress
    base = datetime.now(timezone.utc) - timedelta(days=4)
    results = ["loss", "loss", "win", "loss", "loss", "loss", "loss", "loss", "win"]
    for i, r in enumerate(results):
        insert_game(HANG_QUEEN_PGN, game_id_str=f"tilt{i}", result=r,
                    played_at=(base.replace(hour=1) + timedelta(minutes=20 * i))
                    .strftime("%Y-%m-%dT%H:%M:%SZ"))
    recs = {r["id"]: r for r in progress._game_records()}
    flagged = [r for r in recs.values() if r["after_two_losses"]]
    # games 3, 5, 6, 7, 8 follow two straight losses
    assert len(flagged) == 5


def test_activity_metrics_and_lesson_effects():
    from app.coach import progress
    # mistakes before the lesson, clean games after
    before = [_analyzed(HANG_QUEEN_PGN, 40 + i, f"lb{i}") for i in range(10)]
    after = [_analyzed(HANG_QUEEN_PGN, 5 + i, f"la{i}") for i in range(10)]
    for gid in before:
        _set_moves(gid, 0, 3)
    for gid in after:
        _set_moves(gid, 0, 0)
    with db.tx() as conn:
        conn.execute("INSERT INTO learn_progress VALUES ('vision-loose-pieces', ?, 3, 3)",
                     (_ago(20),))
    from app.puzzles import generator
    generator.generate_for_game(before[0])
    from app.puzzles import scheduler
    pid = db.scalar("SELECT id FROM puzzles LIMIT 1")
    if pid:
        for _ in range(6):
            scheduler.record_attempt(pid, correct=True)

    data = progress.dashboard()
    eff = data["lesson_effects"]
    if eff:                                  # curriculum compiled
        e = next(x for x in eff if x["lesson_id"] == "vision-loose-pieces")
        assert e["before"] > e["after"] and e["verdict"] == "working"
    pd = _metric(data, "practice_days")
    assert pd["current"] > 0
    if pid:
        assert _metric(data, "puzzle_accuracy")["current"] == 100.0


def test_progress_api_and_drill():
    for i in range(6):
        gid = _analyzed(HANG_QUEEN_PGN, 3 + i, f"api{i}")
        _set_moves(gid, 0, 2 if i % 2 else 0)
    client = _client()
    r = client.get("/api/progress")
    assert r.status_code == 200
    body = r.json()
    assert {g["key"] for g in body["groups"]} >= {"vision", "tactics", "phases", "results"}
    drill = client.get("/api/progress/hanging/games").json()
    assert len(drill["games"]) == 3 and all(g["count"] == 2 for g in drill["games"])
    assert client.get("/api/progress/nope/games").status_code == 404


def test_dashboard_cache_invalidates_on_new_game():
    from app.coach import progress
    insert_game(HANG_QUEEN_PGN, game_id_str="c1", played_at=_ago(2))
    assert progress.dashboard()["games"] == 1
    insert_game(MISSED_TACTIC_PGN, game_id_str="c2", played_at=_ago(1))
    assert progress.dashboard()["games"] == 2


def test_named_trap_counts_only_when_he_fell_for_it():
    from app.coach import progress
    from test_phase4 import SCHOLARS_MATE_PGN
    insert_game(SCHOLARS_MATE_PGN, game_id_str="trapme", color="black",
                played_at=_ago(3))
    recs = progress._game_records()
    assert recs[0]["trap"] == "Scholar's Mate"
