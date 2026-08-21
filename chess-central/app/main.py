"""Nirvaan Chess Central — FastAPI entrypoint.

    ./run.sh          # or: uvicorn app.main:app --port 8425

Parent dashboard:  http://localhost:8425/
Nirvaan's view:    http://localhost:8425/kid
"""
from __future__ import annotations

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
    yield


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


BUILD = "2026-08-19-complete-analysis"


app.mount("/static", FreshStaticFiles(directory=STATIC), name="static")
