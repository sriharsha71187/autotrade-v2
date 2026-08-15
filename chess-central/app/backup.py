"""Automatic SQLite backups — years of games and notes deserve a safety net.

A dated copy of the database lands in data/backups/ on every app start
(at most once per day), using SQLite's online backup API so a mid-write
snapshot is still consistent. Old copies are pruned; ~2 weeks are kept.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import db

KEEP = 14


def _backup_dir():
    return db.DB_PATH.parent / "backups"


def run(force: bool = False, keep: int = KEEP) -> dict:
    bdir = _backup_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    dest = bdir / f"chess-{datetime.now(timezone.utc):%Y%m%d}.db"
    if dest.exists() and not force:
        return {"created": False, "path": str(dest), "reason": "today's backup exists"}
    src = db.connect()
    out = sqlite3.connect(dest)
    try:
        with out:
            src.backup(out)
    finally:
        out.close()
    pruned = 0
    for old in sorted(bdir.glob("chess-*.db"))[:-keep]:
        old.unlink()
        pruned += 1
    return {"created": True, "path": str(dest), "pruned": pruned}


def list_backups() -> list[dict]:
    bdir = _backup_dir()
    if not bdir.exists():
        return []
    out = []
    for f in sorted(bdir.glob("chess-*.db"), reverse=True):
        st = f.stat()
        out.append({"name": f.name, "bytes": st.st_size,
                    "date": f.stem.replace("chess-", "")})
    return out
