import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Every test gets an isolated database."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    # drop any cached per-thread connection
    if hasattr(db._local, "conn"):
        del db._local.conn
    db.connect()
    yield
    if hasattr(db._local, "conn"):
        db._local.conn.close()
        del db._local.conn


HANG_QUEEN_PGN = """[Event "Test"]
[White "nirvaan0421"]
[Black "opponent1"]
[Result "0-1"]

1. e4 e5 2. Qh5 Nc6 3. Qxe5+ Nxe5 4. Nf3 Nxf3+ 5. gxf3 d5 6. exd5 Qxd5 7. Nc3 Qe5+ 8. Be2 Bh3 0-1
"""

MISSED_TACTIC_PGN = """[Event "Test2"]
[White "nirvaan0421"]
[Black "opponent2"]
[Result "0-1"]

1. e4 e5 2. Nf3 Qg5 3. Bc4 Qxg2 4. Rg1 Qxf3 5. Qxf3 Nc6 0-1
"""


def insert_game(pgn, opponent="opponent1", game_id_str="g1", result="loss",
                color="white", played_at="2026-08-01T10:00:00Z",
                opponent_rating=600, player_rating=550):
    with db.tx() as conn:
        cur = conn.execute(
            """INSERT INTO games (platform, platform_game_id, url, pgn, color,
                 opponent_name, opponent_rating, player_rating, result, termination,
                 time_class, time_control, rated, eco, opening_name, moves_count, played_at)
               VALUES ('lichess', ?, 'http://x', ?, ?, ?, ?, ?, ?, 'resign',
                 'rapid', '600+5', 1, 'C20', 'Kings Pawn Game', 16, ?)""",
            (game_id_str, pgn, color, opponent, opponent_rating, player_rating,
             result, played_at))
        return cur.lastrowid


@pytest.fixture
def analyzed_game():
    from app.analysis import annotate
    from app.analysis.engine import FakeEngine

    gid = insert_game(HANG_QUEEN_PGN)
    game = db.row("SELECT * FROM games WHERE id=?", (gid,))
    annotate.annotate_game(game, FakeEngine())
    return gid


@pytest.fixture
def missed_tactic_game():
    from app.analysis import annotate
    from app.analysis.engine import FakeEngine

    gid = insert_game(MISSED_TACTIC_PGN, opponent="opponent2", game_id_str="g2",
                      played_at="2026-08-02T10:00:00Z")
    game = db.row("SELECT * FROM games WHERE id=?", (gid,))
    annotate.annotate_game(game, FakeEngine())
    return gid
