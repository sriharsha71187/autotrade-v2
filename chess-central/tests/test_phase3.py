"""Phase-3 tests: repertoire + left book, drills, coach packet, backups,
recency-windowed insights."""
from fastapi.testclient import TestClient

from app import db
from conftest import HANG_QUEEN_PGN, MISSED_TACTIC_PGN, insert_game


def _client():
    from app.main import app
    return TestClient(app)


def _seed_repertoire(analyzed=False):
    """4 games down his usual line, 2 where the opponent deviates at move 2."""
    from app.analysis import annotate
    from app.analysis.engine import FakeEngine

    ids = []
    for i in range(4):
        ids.append(insert_game(HANG_QUEEN_PGN, game_id_str=f"rep_a{i}",
                               played_at=f"2026-08-0{i + 1}T10:00:00Z"))
    for i in range(2):
        ids.append(insert_game(MISSED_TACTIC_PGN, opponent="opponent2",
                               game_id_str=f"rep_b{i}",
                               played_at=f"2026-08-1{i}T10:00:00Z"))
    if analyzed:
        for gid in ids:
            g = db.row("SELECT * FROM games WHERE id=?", (gid,))
            annotate.annotate_game(g, FakeEngine())
        # drills only build from trusted analysis
        with db.tx() as conn:
            conn.execute("UPDATE games SET engine='stockfish'")
    return ids


def test_repertoire_lines_and_left_book():
    from app.coach import repertoire
    _seed_repertoire()
    rep = repertoire.build("white")
    assert rep["games"] == 6
    # his main line is the 4x-repeated one, rendered with move numbers
    assert rep["lines"][0]["n"] == 4
    assert rep["lines"][0]["line"].startswith("1.e4 e5 2.Qh5")
    # the two deviation games leave his book at ply 3 (2.Nf3 is rare for him)
    assert rep["left_book"]["avg_exit_move"] > 0
    assert any(f["kind"] == "info" for f in rep["findings"])
    # empty color still returns a sane shape
    assert repertoire.build("black")["games"] == 0


def test_repertoire_drills_and_queue():
    from app.coach import repertoire
    _seed_repertoire(analyzed=True)
    # FakeEngine can't attest uniqueness (second_gap), so stamp the repeated
    # leaky position (2...Nc6 3.Qxe5+??) with realistic Stockfish numbers.
    with db.tx() as conn:
        conn.execute(
            """UPDATE moves SET second_gap_cp=220, best_uci='h5f5', best_san='Qf5',
               pv='h5f5 g8f6 f1c4' WHERE ply=5 AND mover='player'""")
    created = repertoire.refresh_drills()
    assert created >= 1
    assert repertoire.refresh_drills() == 0        # dedupe by position

    queue = repertoire.drill_queue()
    assert queue and queue[0]["source"] == "repertoire"
    assert len(queue[0]["solution"]) % 2 == 1
    assert "usually play" in queue[0]["explanation"]

    client = _client()
    api = client.get("/api/repertoire?color=white").json()
    assert api["games"] == 6 and api["lines"]
    assert client.get("/api/repertoire/queue").json()
    assert client.get("/api/repertoire?color=purple").status_code == 422


def test_coach_packet_renders():
    _seed_repertoire(analyzed=True)
    client = _client()
    r = client.get("/packet")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Coach packet" in r.text
    assert "Recent games" in r.text


def test_backups(tmp_path):
    from app import backup
    insert_game(HANG_QUEEN_PGN, game_id_str="bk1")
    r1 = backup.run()
    assert r1["created"] is True
    assert backup.run()["created"] is False        # once per day
    assert backup.run(force=True)["created"] is True

    lst = backup.list_backups()
    assert len(lst) == 1 and lst[0]["bytes"] > 0

    # the backup is a valid database containing the game
    import sqlite3
    conn = sqlite3.connect(r1["path"])
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    conn.close()

    client = _client()
    assert client.get("/api/backups").json()["backups"]
    assert client.post("/api/backups").json()["created"] is True


def test_insights_recency_window():
    from app.coach import insights
    for i in range(8):
        insert_game(HANG_QUEEN_PGN, game_id_str=f"w{i}",
                    played_at=f"2026-08-{i + 1:02d}T10:00:00Z")
    with db.tx() as conn:
        conn.execute("UPDATE games SET analyzed_at=played_at, engine='stockfish'")

    insights.regenerate(window_days=90)
    m = insights.meta()
    assert m["window_used"] == 90 and m["games"] == 8

    # push everything into the distant past — the window is too thin, so it
    # falls back to all-time instead of going silent
    with db.tx() as conn:
        conn.execute("UPDATE games SET played_at=replace(played_at,'2026','2020')")
    insights.regenerate(window_days=90)
    m = insights.meta()
    assert m["window_days"] == 90 and m["window_used"] == 0 and m["games"] == 8

    client = _client()
    api = client.get("/api/insights").json()
    assert isinstance(api["items"], list) and api["meta"]["games"] == 8
    r = client.post("/api/insights/regenerate", json={"window_days": 30}).json()
    assert r["meta"]["window_days"] == 30
