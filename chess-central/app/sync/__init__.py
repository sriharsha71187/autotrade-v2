"""Game and rating sync from public sources."""
from __future__ import annotations

from .. import db, util


def sync_all() -> dict:
    """Run every sync source; each fails independently."""
    from . import chesscom, lichess, ratings

    out: dict = {"at": util.now_iso()}
    for name, fn in (("lichess", lichess.sync), ("chesscom", chesscom.sync)):
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = {"error": str(e)}
    try:
        out["otb_ratings"] = ratings.sync_all()
    except Exception as e:
        out["otb_ratings"] = {"error": str(e)}
    db.kv_set("last_sync", out)
    return out
