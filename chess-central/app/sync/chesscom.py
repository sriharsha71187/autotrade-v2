"""Chess.com game sync via the public monthly-archive API."""
from __future__ import annotations

import httpx

from .. import config, db, util

API = "https://api.chess.com/pub"
HEADERS = {"User-Agent": "nirvaan-chess-central (contact: local app)"}


def fetch_archives(username: str) -> list[str]:
    with httpx.Client(timeout=30, headers=HEADERS) as client:
        r = client.get(f"{API}/player/{username}/games/archives")
        r.raise_for_status()
        return r.json().get("archives", [])


def fetch_archive(url: str) -> list[dict]:
    with httpx.Client(timeout=60, headers=HEADERS) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.json().get("games", [])


def fetch_stats(username: str) -> dict:
    with httpx.Client(timeout=30, headers=HEADERS) as client:
        r = client.get(f"{API}/player/{username}/stats")
        r.raise_for_status()
        return r.json()


def game_to_record(g: dict, username: str) -> dict | None:
    uname = username.lower()
    white, black = g.get("white", {}), g.get("black", {})
    if white.get("username", "").lower() == uname:
        color, me, opp = "white", white, black
    elif black.get("username", "").lower() == uname:
        color, me, opp = "black", black, white
    else:
        return None
    if g.get("rules") not in (None, "chess"):   # skip variants
        return None

    # chess.com result codes: "win", or a loss/draw reason on the loser's side
    my_res = me.get("result")
    draw_codes = {"agreed", "repetition", "stalemate", "insufficient",
                  "50move", "timevsinsufficient"}
    if my_res == "win":
        result = "win"
    elif my_res in draw_codes:
        result = "draw"
    else:
        result = "loss"

    from datetime import datetime, timezone
    played_at = datetime.fromtimestamp(
        g.get("end_time", 0), tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    pgn = g.get("pgn", "")
    eco_name = None
    eco_url = g.get("eco") or ""
    if "/openings/" in eco_url:
        eco_name = eco_url.rsplit("/", 1)[-1].replace("-", " ")

    return {
        "platform": "chesscom",
        "platform_game_id": (g.get("url") or "").rsplit("/", 1)[-1] or g.get("uuid", ""),
        "url": g.get("url"),
        "pgn": pgn,
        "color": color,
        "opponent_name": opp.get("username", "Unknown"),
        "opponent_rating": opp.get("rating"),
        "player_rating": me.get("rating"),
        "result": result,
        "termination": opp.get("result") if result == "win" else my_res,
        "time_class": g.get("time_class"),
        "time_control": g.get("time_control", ""),
        "rated": 1 if g.get("rated") else 0,
        "eco": None,
        "opening_name": eco_name,
        "moves_count": None,
        "played_at": played_at,
    }


def sync() -> dict:
    username = config.get("chesscom_username")
    if not username:
        return {"fetched": 0, "inserted": 0, "skipped": "no username configured"}
    archives = fetch_archives(username)
    done = set(db.kv_get("chesscom_done_archives", []))
    # always re-fetch the most recent archive (current month keeps growing)
    todo = [a for a in archives if a not in done] + (archives[-1:] if archives else [])
    fetched = inserted = 0
    for url in dict.fromkeys(todo):    # dedupe, keep order
        games = fetch_archive(url)
        fetched += len(games)
        with db.tx() as conn:
            for g in games:
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
        if url != (archives[-1] if archives else None):
            done.add(url)
    db.kv_set("chesscom_done_archives", sorted(done))
    _record_ratings(username)
    return {"fetched": fetched, "inserted": inserted}


def _record_ratings(username: str) -> None:
    try:
        stats = fetch_stats(username)
    except Exception:
        return
    today = util.now_iso()[:10]
    mapping = {
        "chess_bullet": "chesscom_bullet",
        "chess_blitz": "chesscom_blitz",
        "chess_rapid": "chesscom_rapid",
        "chess_daily": "chesscom_daily",
    }
    with db.tx() as conn:
        for key, source in mapping.items():
            last = (stats.get(key) or {}).get("last") or {}
            if last.get("rating"):
                conn.execute(
                    "INSERT OR REPLACE INTO ratings_history(source,date,rating) VALUES (?,?,?)",
                    (source, today, last["rating"]),
                )
        puzzles = ((stats.get("tactics") or {}).get("highest") or {})
        if puzzles.get("rating"):
            conn.execute(
                "INSERT OR REPLACE INTO ratings_history(source,date,rating) VALUES (?,?,?)",
                ("chesscom_puzzles", today, puzzles["rating"]),
            )
