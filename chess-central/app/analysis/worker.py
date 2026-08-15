"""Background analysis worker — chews through unanalyzed games one at a time.

Runs in a daemon thread inside the FastAPI process. Progress is visible at
/api/analysis/status, and the dashboard enriches itself as games complete.
"""
from __future__ import annotations

import threading
import traceback

from .. import db, util
from . import annotate
from .engine import open_engine

_state = {
    "running": False,
    "current": None,
    "done_this_run": 0,
    "errors": 0,
    "real_engine": None,
    "last_error": None,
}
_lock = threading.Lock()
_thread: threading.Thread | None = None


def status() -> dict:
    with _lock:
        pending = db.scalar(
            "SELECT COUNT(*) FROM games WHERE analyzed_at IS NULL AND analysis_error IS NULL")
        analyzed = db.scalar("SELECT COUNT(*) FROM games WHERE analyzed_at IS NOT NULL")
        return {**_state, "pending": pending, "analyzed": analyzed}


def start() -> dict:
    global _thread
    with _lock:
        if _state["running"]:
            return status()
        _state.update(running=True, done_this_run=0, errors=0, last_error=None)
    _thread = threading.Thread(target=_run, daemon=True, name="analysis-worker")
    _thread.start()
    return status()


def _run() -> None:
    engine, real = open_engine()
    with _lock:
        _state["real_engine"] = real
    try:
        while True:
            game = db.row(
                "SELECT * FROM games WHERE analyzed_at IS NULL AND analysis_error IS NULL "
                "ORDER BY played_at DESC LIMIT 1")
            if game is None:
                break
            with _lock:
                _state["current"] = {"id": game["id"], "opponent": game["opponent_name"],
                                     "played_at": game["played_at"]}
            try:
                annotate.annotate_game(game, engine)
                _post_game_hooks(game["id"])
                with _lock:
                    _state["done_this_run"] += 1
            except Exception as e:
                with db.tx() as conn:
                    conn.execute("UPDATE games SET analysis_error=? WHERE id=?",
                                 (str(e)[:500], game["id"]))
                with _lock:
                    _state["errors"] += 1
                    _state["last_error"] = f"game {game['id']}: {e}"
                traceback.print_exc()
    finally:
        engine.close()
        with _lock:
            _state.update(running=False, current=None)
        _post_run_hooks()


def _post_game_hooks(game_id: int) -> None:
    """Generate puzzles from this game as soon as it's analyzed."""
    try:
        from ..puzzles import generator
        generator.generate_for_game(game_id)
    except Exception:
        traceback.print_exc()


def _post_run_hooks() -> None:
    """Refresh derived data once a batch finishes."""
    try:
        from ..coach import badges, insights
        insights.regenerate()
        badges.recompute()
        db.kv_set("last_analysis_finished", util.now_iso())
    except Exception:
        traceback.print_exc()
