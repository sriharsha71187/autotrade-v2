"""Background analysis — chews through unanalyzed games until none remain.

Runs several engine workers in parallel (each with its own Stockfish process
and DB connection), so a big first-sync backlog completes in a fraction of
the single-engine time. Progress and an ETA are visible at
/api/analysis/status, and the dashboard enriches itself as games complete.
"""
from __future__ import annotations

import os
import threading
import time
import traceback
from collections import deque

from .. import db, util
from . import annotate
from .engine import open_engine

_state = {
    "running": False,
    "current": None,
    "done_this_run": 0,
    "errors": 0,
    "real_engine": None,
    "workers": None,
    "last_error": None,
}
_lock = threading.Lock()
_thread: threading.Thread | None = None
_durations: deque = deque(maxlen=30)   # recent per-game seconds, for the ETA


def status() -> dict:
    # DB reads happen OUTSIDE the lock — a slow query here must never
    # stall the worker threads (which take the lock between games).
    pending = db.scalar(
        "SELECT COUNT(*) FROM games WHERE analyzed_at IS NULL AND analysis_error IS NULL")
    analyzed = db.scalar("SELECT COUNT(*) FROM games WHERE analyzed_at IS NOT NULL")
    with _lock:
        eta = None
        if _durations and pending and _state["running"]:
            avg = sum(_durations) / len(_durations)
            eta = int(pending * avg / max(1, _state["workers"] or 1))
        return {**_state, "pending": pending, "analyzed": analyzed,
                "eta_seconds": eta}


def start() -> dict:
    global _thread
    with _lock:
        if _state["running"]:
            return status()
        _state.update(running=True, done_this_run=0, errors=0, last_error=None)
        _durations.clear()
    _thread = threading.Thread(target=_run, daemon=True, name="analysis-worker")
    _thread.start()
    return status()


def _worker_count() -> int:
    from .. import config
    try:
        n = int(config.get("analysis_workers") or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:                       # auto: leave headroom for the app + OS
        n = max(1, min(4, (os.cpu_count() or 4) // 2 - 1))
    return max(1, min(8, n))


def _run() -> None:
    engine, real = open_engine()     # probe which engine we'd get
    with _lock:
        _state["real_engine"] = real
    from .. import config
    if not real and not config.get("allow_fallback_engine"):
        # Never persist fallback analysis as truth — wait for Stockfish instead.
        engine.close()
        with _lock:
            _state.update(running=False, current=None,
                          last_error="Stockfish not available — analysis is waiting. "
                                     "Install it (see Settings) and press Analyze again.")
        return
    engine.close()                   # each worker opens its own engine
    engine_name = "stockfish" if real else "fallback"
    n = _worker_count()
    with _lock:
        _state["workers"] = n

    claimed: set[int] = set()        # game ids currently in flight (≤ n)

    def claim() -> int | None:
        """Pick the next unanalyzed game no other worker holds."""
        with _lock:
            rows = db.rows(
                "SELECT id, opponent_name, played_at FROM games "
                "WHERE analyzed_at IS NULL AND analysis_error IS NULL "
                "ORDER BY played_at DESC LIMIT ?", (n + 1,))
            for r in rows:
                if r["id"] not in claimed:
                    claimed.add(r["id"])
                    _state["current"] = {"id": r["id"], "opponent": r["opponent_name"],
                                         "played_at": r["played_at"]}
                    return r["id"]
        return None

    def work() -> None:
        eng, _ = open_engine()
        try:
            while True:
                gid = claim()
                if gid is None:
                    break
                game = db.row("SELECT * FROM games WHERE id=?", (gid,))
                t0 = time.time()
                try:
                    if game and not game["analyzed_at"]:
                        annotate.annotate_game(game, eng, engine_name=engine_name)
                        _post_game_hooks(gid)
                    with _lock:
                        _state["done_this_run"] += 1
                        _durations.append(time.time() - t0)
                except Exception as e:
                    with db.tx() as conn:
                        conn.execute("UPDATE games SET analysis_error=? WHERE id=?",
                                     (str(e)[:500], gid))
                    with _lock:
                        _state["errors"] += 1
                        _state["last_error"] = f"game {gid}: {e}"
                    traceback.print_exc()
                finally:
                    with _lock:
                        claimed.discard(gid)
        finally:
            eng.close()

    threads = [threading.Thread(target=work, daemon=True, name=f"analysis-{i}")
               for i in range(n)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        with _lock:
            _state.update(running=False, current=None)
        _post_run_hooks()


def _post_game_hooks(game_id: int) -> None:
    """Generate puzzles (and optionally AI commentary) once a game is analyzed."""
    try:
        from ..puzzles import generator
        generator.generate_for_game(game_id)
    except Exception:
        traceback.print_exc()
    try:
        from .. import config
        if config.get("llm_auto_commentary"):
            from ..coach import llm
            if llm.is_configured():
                llm.game_commentary(game_id)
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
