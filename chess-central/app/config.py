"""Central configuration for Nirvaan Chess Central.

Values load from chess-central/config.json if present, else defaults below.
Everything is overridable from the Settings page in the app.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CHESS_CENTRAL_DATA", ROOT / "data"))
CONFIG_PATH = ROOT / "config.json"

DEFAULTS = {
    "player_name": "Nirvaan Thammishetty",
    "lichess_username": "nirvaan0421",
    "chesscom_username": "nirvaan0421",
    "nwsrs_id": "TBKBF05U",
    "uscf_id": "33201208",
    "home_area": "Seattle / Eastside, WA",
    "home_state": "WA",
    # cities considered "near home" for the tournament finder
    "nearby_cities": [
        "seattle", "bellevue", "redmond", "kirkland", "issaquah", "sammamish",
        "renton", "bothell", "mercer island", "newcastle", "woodinville",
        "kenmore", "shoreline", "lynnwood", "everett", "tacoma", "federal way",
        "kent", "auburn", "tukwila", "edmonds", "mill creek", "snoqualmie",
    ],
    "engine_path": "",           # auto-detected if empty (brew stockfish)
    "engine_movetime_ms": 350,   # per-position budget for deep analysis
    "allow_fallback_engine": False,  # never persist non-Stockfish analysis unless opted in
    "engine_multipv": 2,
    "engine_threads": 2,
    "analysis_auto": True,       # analyze new games automatically after sync
    "puzzle_daily_target": 6,    # puzzles in Nirvaan's daily set
    "kid_pin": "",               # optional PIN to open the parent view from kid mode
    "sync_lookback_days": 3650,
    # AI coach (Claude). Key lives only in local config.json (gitignored);
    # ANTHROPIC_API_KEY env var also works.
    "anthropic_api_key": "",
    "llm_model": "claude-opus-5",
    "llm_auto_commentary": False,   # write commentary automatically after analysis
}


def _load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except Exception:
            pass
    return cfg


_config = _load()


def get(key: str):
    return _config.get(key, DEFAULTS.get(key))


def all_config() -> dict:
    return dict(_config)


_INT_FLOORS = {"engine_movetime_ms": 50, "engine_threads": 1,
               "puzzle_daily_target": 1, "sync_lookback_days": 1}


def update(values: dict) -> dict:
    """Persist config changes (only known keys, sane numeric floors)."""
    for k, v in values.items():
        if k not in DEFAULTS:
            continue
        if k in _INT_FLOORS:
            try:
                v = max(_INT_FLOORS[k], int(v))
            except (TypeError, ValueError):
                v = DEFAULTS[k]
        _config[k] = v
    CONFIG_PATH.write_text(json.dumps(_config, indent=2))
    return dict(_config)


def find_engine() -> str:
    """Locate a Stockfish binary."""
    explicit = get("engine_path")
    if explicit and Path(explicit).exists():
        return explicit
    candidates = [
        str(ROOT / "stockfish-bin"),     # binary downloaded into the app folder
        "/opt/homebrew/bin/stockfish",   # Apple Silicon brew
        "/usr/local/bin/stockfish",      # Intel brew
        "/usr/bin/stockfish",
        "/usr/games/stockfish",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    from shutil import which
    return which("stockfish") or ""
