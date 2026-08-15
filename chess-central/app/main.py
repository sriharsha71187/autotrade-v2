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
    yield


app = FastAPI(title="Nirvaan Chess Central", lifespan=lifespan)
app.include_router(router)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/kid")
def kid():
    return FileResponse(STATIC / "kid.html")


@app.get("/packet")
def coach_packet():
    from .coach import packet
    return HTMLResponse(packet.render())


app.mount("/static", StaticFiles(directory=STATIC), name="static")
