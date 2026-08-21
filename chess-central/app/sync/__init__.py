"""Game and rating sync from public sources.

Syncing a full history can stream from lichess/chess.com for minutes, so it
runs as a background job (like analysis): POST /api/sync starts it and
returns immediately; GET /api/sync/status reports progress. A browser tab
must never sit on a minutes-long HTTP request.
"""
from __future__ import annotations

import threading
import traceback

from .. import db, util

_state = {
    "running": False,
    "phase": None,          # lichess | chesscom | ratings
    "result": None,         # last completed run's summary
    "started_at": None,
}
_lock = threading.RLock()   # reentrant on principle — a nested acquire must
                            # never freeze the app (see worker._lock history)


def status() -> dict:
    with _lock:
        return dict(_state)


def start() -> dict:
    """Kick off a background sync (no-op if one is already running)."""
    with _lock:
        if _state["running"]:
            return dict(_state)
        _state.update(running=True, phase="starting", started_at=util.now_iso())
    threading.Thread(target=_run, daemon=True, name="sync-worker").start()
    return status()


def _run() -> None:
    try:
        result = sync_all()
        with _lock:
            _state["result"] = result
    except Exception as e:               # sync_all catches per-source; belt & braces
        traceback.print_exc()
        with _lock:
            _state["result"] = {"error": str(e)}
    finally:
        with _lock:
            _state.update(running=False, phase=None)
        try:
            from .. import config
            from ..analysis import worker
            if config.get("analysis_auto"):
                worker.start()           # analyze whatever the sync brought in
        except Exception:
            traceback.print_exc()


def _phase(name: str) -> None:
    with _lock:
        _state["phase"] = name


def sync_all() -> dict:
    """Run every sync source; each fails independently."""
    from . import chesscom, lichess, ratings

    out: dict = {"at": util.now_iso()}
    for name, fn in (("lichess", lichess.sync), ("chesscom", chesscom.sync)):
        _phase(name)
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = {"error": str(e)}
    _phase("ratings")
    try:
        out["otb_ratings"] = ratings.sync_all()
    except Exception as e:
        out["otb_ratings"] = {"error": str(e)}
    db.kv_set("last_sync", out)
    return out
