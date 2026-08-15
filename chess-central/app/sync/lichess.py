"""Lichess game sync via the public export API (no auth needed for public games).

Incremental: remembers the newest game timestamp and only fetches later games.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from .. import config, db, util

API = "https://lichess.org/api"


def _headers():
    return {"Accept": "application/x-ndjson", "User-Agent": "nirvaan-chess-central"}


def fetch_games(username: str, since_ms: int | None = None, max_games: int = 3000) -> list[dict]:
    """Stream NDJSON games, newest first."""
    params = {
        "max": max_games,
        "pgnInJson": "true",
        "clocks": "true",
        "evals": "false",
        "opening": "true",
    }
    if since_ms:
        params["since"] = since_ms
    out = []
    with httpx.Client(timeout=60) as client:
        with client.stream("GET", f"{API}/games/user/{username}", params=params,
                           headers=_headers()) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line.strip():
                    out.append(json.loads(line))
    return out


def fetch_profile(username: str) -> dict:
    with httpx.Client(timeout=30) as client:
        r = client.get(f"{API}/user/{username}",
                       headers={"User-Agent": "nirvaan-chess-central"})
        r.raise_for_status()
        return r.json()


def game_to_record(g: dict, username: str) -> dict | None:
    """Convert a lichess NDJSON game to our games-table record."""
    players = g.get("players", {})
    white = (players.get("white", {}).get("user") or {}).get("name", "")
    black = (players.get("black", {}).get("user") or {}).get("name", "")
    uname = username.lower()
    if white.lower() == uname:
        color, opp = "white", players.get("black", {})
        me = players.get("white", {})
        opp_name = black or "Anonymous"
    elif black.lower() == uname:
        color, opp = "black", players.get("white", {})
        me = players.get("black", {})
        opp_name = white or "Anonymous"
    else:
        return None

    winner = g.get("winner")  # "white" | "black" | None
    if winner is None:
        result = "draw"
    else:
        result = "win" if winner == color else "loss"

    played_at = datetime.fromtimestamp(
        g.get("createdAt", 0) / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    opening = g.get("opening") or {}
    clock = g.get("clock") or {}
    tc = f"{clock.get('initial', '')}+{clock.get('increment', '')}" if clock else "-"

    pgn = g.get("pgn", "")
    moves_count = len((g.get("moves") or "").split()) if g.get("moves") else None

    return {
        "platform": "lichess",
        "platform_game_id": g["id"],
        "url": f"https://lichess.org/{g['id']}",
        "pgn": pgn,
        "color": color,
        "opponent_name": opp_name,
        "opponent_rating": opp.get("rating"),
        "player_rating": me.get("rating"),
        "result": result,
        "termination": g.get("status"),
        "time_class": g.get("speed"),
        "time_control": tc,
        "rated": 1 if g.get("rated") else 0,
        "eco": opening.get("eco"),
        "opening_name": opening.get("name"),
        "moves_count": moves_count,
        "played_at": played_at,
    }


def sync() -> dict:
    """Pull new games; returns {'fetched': n, 'inserted': n}."""
    username = config.get("lichess_username")
    if not username:
        return {"fetched": 0, "inserted": 0, "skipped": "no username configured"}
    since = db.kv_get("lichess_since_ms")
    games = fetch_games(username, since_ms=since)
    inserted = 0
    newest = since or 0
    with db.tx() as conn:
        for g in games:
            newest = max(newest, g.get("createdAt", 0))
            rec = game_to_record(g, username)
            if not rec or not rec["pgn"]:
                continue
            cur = conn.execute(
                """INSERT OR IGNORE INTO games
                   (platform, platform_game_id, url, pgn, color, opponent_name,
                    opponent_rating, player_rating, result, termination, time_class,
                    time_control, rated, eco, opening_name, moves_count, played_at)
                   VALUES (:platform,:platform_game_id,:url,:pgn,:color,:opponent_name,
                    :opponent_rating,:player_rating,:result,:termination,:time_class,
                    :time_control,:rated,:eco,:opening_name,:moves_count,:played_at)""",
                rec,
            )
            inserted += cur.rowcount
    if newest:
        db.kv_set("lichess_since_ms", newest + 1)
    _record_ratings(username)
    return {"fetched": len(games), "inserted": inserted}


def _record_ratings(username: str) -> None:
    """Snapshot current ratings into ratings_history (one point per day)."""
    try:
        prof = fetch_profile(username)
    except Exception:
        return
    today = util.now_iso()[:10]
    perfs = prof.get("perfs", {})
    with db.tx() as conn:
        for cat in ("bullet", "blitz", "rapid", "classical", "puzzle"):
            p = perfs.get(cat)
            if p and p.get("games", 0) > 0 and not p.get("prov"):
                conn.execute(
                    "INSERT OR REPLACE INTO ratings_history(source,date,rating) VALUES (?,?,?)",
                    (f"lichess_{cat}", today, p["rating"]),
                )
