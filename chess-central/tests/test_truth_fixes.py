"""Phase-1 'fix the truth' regression tests: engine provenance, re-analysis,
dedupe, increment math, scraper guards, honest streak/solved accounting."""
import json

from fastapi.testclient import TestClient

from app import db, util
from app.puzzles import scheduler
from app.sync import ratings


def test_parse_increment():
    assert util.parse_increment("600+5") == 5
    assert util.parse_increment("180+0") == 0
    assert util.parse_increment("600") == 0
    assert util.parse_increment("1/86400") == 0    # daily
    assert util.parse_increment(None) == 0
    assert util.parse_increment("-") == 0


def test_engine_provenance_recorded(analyzed_game):
    g = db.row("SELECT engine FROM games WHERE id=?", (analyzed_game,))
    assert g["engine"] == "fallback"          # tests analyze with FakeEngine


def test_reanalyze_resets_fake_games(analyzed_game, missed_tactic_game):
    from app.puzzles import generator
    generator.generate_for_game(missed_tactic_game)
    assert db.scalar("SELECT COUNT(*) FROM puzzles WHERE game_id=?",
                     (missed_tactic_game,)) >= 1

    from app.main import app
    client = TestClient(app)
    st = client.get("/api/status").json()
    assert st["fake_analyzed"] == 2

    r = client.post("/api/analyze/reset", json={"scope": "fake"})
    assert r.status_code == 200
    assert r.json()["reset"] == 2
    assert db.scalar("SELECT COUNT(*) FROM games WHERE analyzed_at IS NOT NULL") == 0
    assert db.scalar("SELECT COUNT(*) FROM moves") == 0
    assert db.scalar("SELECT COUNT(*) FROM puzzles WHERE game_id IS NOT NULL") == 0


def test_worker_refuses_fallback_by_default(monkeypatch):
    from tests.conftest import HANG_QUEEN_PGN, insert_game
    from app.analysis import worker
    from app.analysis.engine import FakeEngine

    insert_game(HANG_QUEEN_PGN, game_id_str="pending1")
    monkeypatch.setattr(worker, "open_engine", lambda: (FakeEngine(), False))
    worker._state.update(running=True)        # simulate start()'s claim
    worker._run()
    # nothing analyzed, and the reason is surfaced
    assert db.scalar("SELECT COUNT(*) FROM games WHERE analyzed_at IS NOT NULL") == 0
    assert "Stockfish" in (worker._state["last_error"] or "")


def test_rival_prep_dedupe_on_rescout():
    from app.rivals import scout
    rival = {"name": "DupKid"}
    prep = [{"fen": "8/8/8/8/8/8/8/K6k w - - 0 1", "solution": ["a1a2"],
             "themes": [], "note": "x"}]
    h2h = {"games": []}
    scout._make_prep_puzzles(rival, prep, h2h)
    scout._make_prep_puzzles(rival, prep, h2h)   # re-scout
    assert db.scalar(
        "SELECT COUNT(*) FROM puzzles WHERE rival='DupKid'") == 1


def test_rating_jump_guard():
    assert ratings._store("nwsrs_test", 350) is True
    # backdate so the guard compares against a *previous* day
    with db.tx() as conn:
        conn.execute("UPDATE ratings_history SET date='2026-08-01' WHERE source='nwsrs_test'")
    assert ratings._store("nwsrs_test", 2026) is False   # year parsed as rating
    assert ratings._store("nwsrs_test", 410) is True     # plausible move
    rows = db.rows("SELECT rating FROM ratings_history WHERE source='nwsrs_test' ORDER BY date")
    assert [r["rating"] for r in rows] == [350, 410]


def test_first_int_near_rejects_years():
    html = "<td>Nirvaan</td><td>2026</td><td>311</td>"
    assert ratings._first_int_near(html, "Nirvaan") == 311


def test_completed_vs_clean_accounting(missed_tactic_game):
    from app.puzzles import generator
    generator.generate_for_game(missed_tactic_game)
    p = db.row("SELECT id FROM puzzles LIMIT 1")

    # solved WITH help: completed but not clean
    scheduler.record_attempt(p["id"], correct=False, completed=True)
    s = scheduler.stats()
    assert s["solved_today"] == 1        # it counts — he finished it
    assert s["streak_days"] == 1
    assert s["accuracy_pct"] == 0.0      # but not first-try

    # revealed answer: not completed
    scheduler.record_attempt(p["id"], correct=False, completed=False)
    s2 = scheduler.stats()
    assert s2["solved_today"] == 1       # unchanged


def test_streak_requires_consecutive_days(missed_tactic_game):
    from app.puzzles import generator
    generator.generate_for_game(missed_tactic_game)
    p = db.row("SELECT id FROM puzzles LIMIT 1")
    scheduler.record_attempt(p["id"], correct=True)
    today = util.now_iso()[:10]
    with db.tx() as conn:
        # solves today, 2 days ago, 3 days ago — gap breaks the streak at 1
        conn.execute("INSERT INTO puzzle_attempts(puzzle_id, attempted_at, correct, completed) "
                     "VALUES (?, date(?, '-2 days') || 'T10:00:00Z', 1, 1)", (p["id"], today))
        conn.execute("INSERT INTO puzzle_attempts(puzzle_id, attempted_at, correct, completed) "
                     "VALUES (?, date(?, '-3 days') || 'T10:00:00Z', 1, 1)", (p["id"], today))
    assert scheduler.stats()["streak_days"] == 1


def test_increment_aware_move_time():
    """In a 600+5 game a 3s think leaves the clock higher — must not read as 0s blitzing."""
    from app.analysis import annotate
    from app.analysis.engine import FakeEngine
    from tests.conftest import insert_game

    pgn = """[Event "Clk"]
[White "nirvaan0421"]
[Black "opp"]
[Result "*"]

1. e4 {[%clk 0:10:00]} e5 {[%clk 0:10:00]} 2. Nf3 {[%clk 0:10:02]} Nc6 {[%clk 0:09:30]} *
"""
    gid = insert_game(pgn, game_id_str="clkgame")
    game = db.row("SELECT * FROM games WHERE id=?", (gid,))   # time_control 600+5
    annotate.annotate_game(game, FakeEngine())
    moves = db.rows("SELECT ply, move_time FROM moves WHERE game_id=? ORDER BY ply", (gid,))
    # white's 2nd move: 600 -> 602 with +5 inc = 3s thought (not 0!)
    assert moves[2]["move_time"] == 3.0
    # black's 2nd move: 600 -> 570 with +5 inc = 35s thought
    assert moves[3]["move_time"] == 35.0


def test_seed_calendar_rolls_forward():
    from app.tournaments import finder
    finder.ensure_seeds()
    with db.tx() as conn:
        conn.execute("""UPDATE tournaments SET starts_at='2020-01-01', status='played'
                        WHERE source='seed' AND external_id='seed_3'""")
    finder.ensure_seeds()
    row = db.row("SELECT starts_at, status FROM tournaments WHERE external_id='seed_3'")
    assert row["starts_at"] >= util.now_iso()[:10]
    assert row["status"] == "new"


def test_chat_history_starts_on_user_turn(monkeypatch):
    from app.coach import llm
    with db.tx() as conn:
        conn.execute("INSERT INTO coach_chat (created_at, role, text) VALUES ('t1','user','q1')")
        conn.execute("INSERT INTO coach_chat (created_at, role, text) VALUES ('t2','assistant','a1')")
        conn.execute("INSERT INTO coach_chat (created_at, role, text) VALUES ('t3','user','q2')")
        conn.execute("INSERT INTO coach_chat (created_at, role, text) VALUES ('t4','assistant','a2')")
    monkeypatch.setattr(llm, "MAX_CHAT_TURNS", 3)   # window would start on 'assistant'

    captured = {}

    class FakeBlock:
        type = "text"; text = "ok"

    class FakeResponse:
        stop_reason = "end_turn"; content = [FakeBlock()]

    class FakeMessages:
        def create(self, **kwargs):
            captured["messages"] = kwargs["messages"]
            return FakeResponse()

    class FakeClient:
        def __init__(self, **kw):
            self.beta = type("B", (), {"messages": FakeMessages()})()

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    llm.chat("q3")

    roles = [m["role"] for m in captured["messages"]]
    for i in range(1, len(roles)):
        assert roles[i] != roles[i - 1], f"non-alternating roles: {roles}"


def test_config_floors():
    from app import config
    out = config.update({"engine_movetime_ms": 0, "puzzle_daily_target": "abc"})
    assert out["engine_movetime_ms"] == 50
    assert out["puzzle_daily_target"] == config.DEFAULTS["puzzle_daily_target"]
    config.update({"engine_movetime_ms": 350, "puzzle_daily_target": 6})
