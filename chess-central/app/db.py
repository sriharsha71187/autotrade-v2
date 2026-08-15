"""SQLite storage — the app's long-term memory.

Single-file database at chess-central/data/chess.db. Schema is created on
first run; lightweight migrations bump PRAGMA user_version.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from . import config

DB_PATH = Path(config.DATA_DIR) / "chess.db"
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,              -- lichess | chesscom | otb
    platform_game_id TEXT NOT NULL,
    url TEXT,
    pgn TEXT NOT NULL,
    color TEXT NOT NULL,                 -- white | black (Nirvaan's side)
    opponent_name TEXT,
    opponent_rating INTEGER,
    player_rating INTEGER,
    result TEXT NOT NULL,                -- win | loss | draw
    termination TEXT,                    -- mate/resign/time/abandoned/agreed...
    time_class TEXT,                     -- bullet | blitz | rapid | classical | daily
    time_control TEXT,
    rated INTEGER DEFAULT 1,
    eco TEXT,
    opening_name TEXT,
    moves_count INTEGER,
    played_at TEXT NOT NULL,             -- ISO timestamp
    analyzed_at TEXT,                    -- null until engine pass completes
    analysis_error TEXT,
    UNIQUE(platform, platform_game_id)
);
CREATE INDEX IF NOT EXISTS idx_games_played ON games(played_at);
CREATE INDEX IF NOT EXISTS idx_games_opponent ON games(opponent_name);

CREATE TABLE IF NOT EXISTS moves (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    ply INTEGER NOT NULL,                -- 1-based half-move number
    san TEXT NOT NULL,
    uci TEXT NOT NULL,
    mover TEXT NOT NULL,                 -- player | opponent
    fen_before TEXT NOT NULL,
    eval_before INTEGER,                 -- centipawns, white POV, clamped
    eval_after INTEGER,
    winprob_before REAL,                 -- mover's win % before/after the move
    winprob_after REAL,
    best_uci TEXT,
    best_san TEXT,
    pv TEXT,                             -- engine principal variation (uci, space-sep)
    second_uci TEXT,
    second_gap_cp INTEGER,               -- best minus second-best, for puzzle uniqueness
    classification TEXT,                 -- best|good|inaccuracy|mistake|blunder
    phase TEXT,                          -- opening | middlegame | endgame
    motifs TEXT,                         -- JSON list of motif tags
    clock_seconds REAL,                  -- clock after the move, if PGN has %clk
    move_time REAL,                      -- seconds spent on this move, if derivable
    UNIQUE(game_id, ply)
);
CREATE INDEX IF NOT EXISTS idx_moves_game ON moves(game_id);
CREATE INDEX IF NOT EXISTS idx_moves_class ON moves(classification);

CREATE TABLE IF NOT EXISTS puzzles (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,                -- own_blunder | missed_tactic | rival_prep
    game_id INTEGER REFERENCES games(id) ON DELETE CASCADE,
    ply INTEGER,
    fen TEXT NOT NULL,
    solution TEXT NOT NULL,              -- JSON list of uci moves (player, opp, player...)
    themes TEXT,                         -- JSON list: hanging_piece, fork, mate...
    phase TEXT,
    difficulty INTEGER DEFAULT 1,        -- 1 easy .. 5 hard
    rival TEXT,                          -- opponent tag for rival-prep puzzles
    explanation TEXT,
    created_at TEXT NOT NULL,
    -- spaced repetition state
    due_at TEXT,
    interval_days REAL DEFAULT 0,
    ease REAL DEFAULT 2.5,
    reps INTEGER DEFAULT 0,
    lapses INTEGER DEFAULT 0,
    retired INTEGER DEFAULT 0,
    UNIQUE(game_id, ply, source)
);
CREATE INDEX IF NOT EXISTS idx_puzzles_due ON puzzles(due_at);

CREATE TABLE IF NOT EXISTS puzzle_attempts (
    id INTEGER PRIMARY KEY,
    puzzle_id INTEGER NOT NULL REFERENCES puzzles(id) ON DELETE CASCADE,
    attempted_at TEXT NOT NULL,
    correct INTEGER NOT NULL,
    time_ms INTEGER
);

CREATE TABLE IF NOT EXISTS ratings_history (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,                -- lichess_rapid, chesscom_rapid, nwsrs, uscf...
    date TEXT NOT NULL,
    rating INTEGER NOT NULL,
    UNIQUE(source, date)
);

CREATE TABLE IF NOT EXISTS rivals (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    lichess_username TEXT,
    chesscom_username TEXT,
    nwsrs_id TEXT,
    notes TEXT,
    scout_report TEXT,                   -- JSON, refreshed by scouting runs
    scouted_at TEXT,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS tournaments (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,                -- nwsrs | uschess | lichess | manual | seed
    external_id TEXT,
    name TEXT NOT NULL,
    starts_at TEXT,                      -- ISO date or datetime
    city TEXT,
    venue TEXT,
    url TEXT,
    sections TEXT,                       -- free text / JSON
    rating_min INTEGER,
    rating_max INTEGER,
    online INTEGER DEFAULT 0,
    near_home INTEGER DEFAULT 0,
    recurring_note TEXT,                 -- for seed events: typical timing, verify note
    status TEXT DEFAULT 'new',           -- new | interested | registered | played | skipped
    fetched_at TEXT,
    UNIQUE(source, external_id)
);

CREATE TABLE IF NOT EXISTS insights (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                  -- strength | weakness | opportunity
    key TEXT NOT NULL,                   -- stable rule id, e.g. phase_endgame
    title TEXT NOT NULL,
    detail TEXT NOT NULL,
    evidence TEXT,                       -- JSON
    score REAL DEFAULT 0,                -- ranking weight
    generated_at TEXT NOT NULL,
    UNIQUE(key)
);

CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    author TEXT DEFAULT 'coach',
    text TEXT NOT NULL,
    game_id INTEGER REFERENCES games(id) ON DELETE SET NULL,
    tournament_id INTEGER REFERENCES tournaments(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS badges (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    earned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS llm_notes (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                  -- game_commentary | weekly_report | rival_brief
    ref_id TEXT NOT NULL,                -- game id / date / rival id
    content TEXT NOT NULL,               -- markdown
    model TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(kind, ref_id)
);

CREATE TABLE IF NOT EXISTS learn_progress (
    lesson_id TEXT PRIMARY KEY,
    completed_at TEXT NOT NULL,
    correct INTEGER DEFAULT 0,
    total INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS coach_chat (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    role TEXT NOT NULL,                  -- user | assistant
    text TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    """Per-thread connection with sane pragmas."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        _migrate(conn)
        _local.conn = conn
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Lightweight in-place migrations for existing databases."""
    def cols(table):
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}

    if "engine" not in cols("games"):
        # which engine produced the analysis: 'stockfish' | 'fallback' | NULL
        conn.execute("ALTER TABLE games ADD COLUMN engine TEXT")
    if "completed" not in cols("puzzle_attempts"):
        # completed = the puzzle was finished (with or without help);
        # correct = first-try clean solve (drives spaced repetition)
        conn.execute("ALTER TABLE puzzle_attempts ADD COLUMN completed INTEGER DEFAULT 0")
        conn.execute("UPDATE puzzle_attempts SET completed=1 WHERE correct=1")
    # one-time cleanup: rival_prep rows had NULL game_id, which SQLite treats
    # as distinct in the unique index — re-scouting created duplicates
    conn.execute(
        """DELETE FROM puzzles WHERE source='rival_prep' AND id NOT IN (
             SELECT MIN(id) FROM puzzles WHERE source='rival_prep'
             GROUP BY rival, fen)""")
    conn.commit()


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def rows(sql: str, params=()) -> list[dict]:
    cur = connect().execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def row(sql: str, params=()) -> dict | None:
    cur = connect().execute(sql, params)
    r = cur.fetchone()
    return dict(r) if r else None


def scalar(sql: str, params=()):
    cur = connect().execute(sql, params)
    r = cur.fetchone()
    return r[0] if r else None


def kv_get(key: str, default=None):
    r = row("SELECT value FROM kv WHERE key=?", (key,))
    return json.loads(r["value"]) if r else default


def kv_set(key: str, value) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
