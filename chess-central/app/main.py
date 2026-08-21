"""Nirvaan Chess Central — FastAPI entrypoint.

    ./run.sh          # or: uvicorn app.main:app --port 8425

Parent dashboard:  http://localhost:8425/
Nirvaan's view:    http://localhost:8425/kid
"""
from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import backup, db
from .api.routes import router
from .tournaments import finder

STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.connect()           # create schema
    finder.ensure_seeds()  # PNW tournament calendar available immediately
    try:
        backup.run()       # daily safety copy of the database
    except Exception:
        pass               # a failed backup must never block the app
    try:
        _resume_analysis() # finish any backlog without being asked
    except Exception:
        pass
    _start_auto_sync()     # fetch new games on a schedule — fully hands-off
    yield


_auto_sync_started = False


def _start_auto_sync() -> None:
    global _auto_sync_started
    if _auto_sync_started:
        return
    _auto_sync_started = True
    threading.Thread(target=_auto_sync_loop, daemon=True, name="auto-sync").start()


def _auto_sync_loop() -> None:
    """He plays -> games appear -> analysis runs. Nobody presses anything."""
    from . import config, sync
    time.sleep(90)                       # let the server settle after boot
    while True:
        try:
            minutes = int(config.get("sync_interval_minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        if minutes > 0:
            try:
                sync.start()             # no-op if a sync is already running;
            except Exception:            # analysis auto-chains when it lands
                pass
        time.sleep(max(15, minutes or 60) * 60)


def _resume_analysis() -> None:
    """If unanalyzed games are waiting and the engine is present, keep going —
    the always-on server should complete the backlog by itself."""
    from . import config
    from .analysis import worker
    if not config.get("analysis_auto") or not config.find_engine():
        return
    pending = db.scalar(
        "SELECT COUNT(*) FROM games WHERE analyzed_at IS NULL AND analysis_error IS NULL")
    if pending:
        worker.start()


app = FastAPI(title="Nirvaan Chess Central", lifespan=lifespan)
app.include_router(router)


# ---- slow-request watchdog: if any request runs >15s, dump every thread's
# stack to the log so a hang is diagnosable from data/server.log alone.
_inflight: dict[int, tuple] = {}
_watchdog_started = False


@app.middleware("http")
async def _watch_requests(request, call_next):
    global _watchdog_started
    if not _watchdog_started:
        _watchdog_started = True
        threading.Thread(target=_watchdog, daemon=True, name="watchdog").start()
    key = id(request)
    _inflight[key] = (request.url.path, time.time())
    try:
        return await call_next(request)
    finally:
        _inflight.pop(key, None)


def _watchdog() -> None:
    import faulthandler
    reported: set[int] = set()
    while True:
        time.sleep(5)
        now = time.time()
        for key, (path, t0) in list(_inflight.items()):
            if now - t0 > 15 and key not in reported:
                reported.add(key)
                print(f"⚠️  WATCHDOG: {path} stuck for {now - t0:.0f}s — "
                      "thread stacks follow", flush=True)
                faulthandler.dump_traceback()
        reported &= set(_inflight)

NO_CACHE = {"Cache-Control": "no-cache"}   # revalidate every load (cheap 304s
# on a LAN) — a browser must never keep running old JS against a new server


class FreshStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        resp = await super().get_response(path, scope)
        if path.rsplit(".", 1)[-1] in ("js", "css", "html", "json", "webmanifest"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers=NO_CACHE)


@app.get("/kid")
def kid():
    return FileResponse(STATIC / "kid.html", headers=NO_CACHE)


@app.get("/packet")
def coach_packet():
    from .coach import packet
    return HTMLResponse(packet.render())


@app.get("/api/version")
def version():
    """Which build is actually serving — for debugging 'did the update take?'."""
    return {"build": BUILD}


BUILD = "2026-08-21-auto-sync"


app.mount("/static", FreshStaticFiles(directory=STATIC), name="static")
