"""JSON API consumed by both dashboards."""
from __future__ import annotations

import json

from fastapi import APIRouter, Body, HTTPException

from .. import config, db, sync, util
from ..analysis import annotate, worker
from ..coach import badges, insights, roadmap
from ..puzzles import generator, scheduler
from ..rivals import scout
from ..tournaments import finder

router = APIRouter(prefix="/api")


# ------------------------------------------------------------- sync & status

@router.post("/sync")
def run_sync():
    result = sync.sync_all()
    if config.get("analysis_auto"):
        worker.start()
    return result


@router.get("/status")
def status():
    return {
        "last_sync": db.kv_get("last_sync"),
        "analysis": worker.status(),
        "games_total": db.scalar("SELECT COUNT(*) FROM games") or 0,
        "engine_found": bool(config.find_engine()),
    }


@router.post("/analyze")
def start_analysis():
    return worker.start()


@router.get("/analysis/status")
def analysis_status():
    return worker.status()


# ------------------------------------------------------------------ summary

@router.get("/summary")
def summary():
    games = db.scalar("SELECT COUNT(*) FROM games") or 0
    record = db.row(
        "SELECT SUM(result='win') w, SUM(result='loss') l, SUM(result='draw') d FROM games")
    last30 = db.row(
        """SELECT COUNT(*) n, SUM(result='win') w FROM games
           WHERE played_at >= date('now', '-30 days')""")
    ratings = {}
    for r in db.rows(
            """SELECT source, rating, date FROM ratings_history r1
               WHERE date = (SELECT MAX(date) FROM ratings_history r2
                             WHERE r2.source = r1.source)"""):
        ratings[r["source"]] = {"rating": r["rating"], "date": r["date"]}
    acc = db.row(
        """SELECT AVG(MAX(winprob_before - winprob_after, 0)) loss,
                  SUM(classification='blunder') blunders, COUNT(*) moves
           FROM moves WHERE mover='player'""")
    return {
        "player": config.get("player_name"),
        "games_total": games,
        "record": record,
        "last30": last30,
        "ratings": ratings,
        "avg_winprob_loss": round(acc["loss"], 2) if acc and acc["loss"] is not None else None,
        "blunders": acc["blunders"] if acc else 0,
        "moves_analyzed": acc["moves"] if acc else 0,
        "puzzle_stats": scheduler.stats(),
    }


@router.get("/ratings")
def ratings_history():
    return db.rows("SELECT source, date, rating FROM ratings_history ORDER BY date")


# -------------------------------------------------------------------- games

@router.get("/games")
def list_games(limit: int = 50, offset: int = 0, opponent: str | None = None,
               result: str | None = None):
    where, params = ["1=1"], []
    if opponent:
        where.append("LOWER(opponent_name) LIKE ?")
        params.append(f"%{opponent.lower()}%")
    if result in ("win", "loss", "draw"):
        where.append("result=?")
        params.append(result)
    rows = db.rows(
        f"""SELECT id, platform, url, color, opponent_name, opponent_rating,
                  player_rating, result, termination, time_class, eco, opening_name,
                  moves_count, played_at, analyzed_at
           FROM games WHERE {' AND '.join(where)}
           ORDER BY played_at DESC LIMIT ? OFFSET ?""",
        (*params, limit, offset))
    return rows


@router.get("/games/{game_id}")
def game_detail(game_id: int):
    g = db.row("SELECT * FROM games WHERE id=?", (game_id,))
    if not g:
        raise HTTPException(404)
    moves = db.rows("SELECT * FROM moves WHERE game_id=? ORDER BY ply", (game_id,))
    for m in moves:
        m["motifs"] = json.loads(m["motifs"] or "[]")
    return {**g, "moves": moves, "accuracy": annotate.game_accuracy(game_id)}


# ----------------------------------------------------------------- insights

@router.get("/insights")
def get_insights():
    return insights.current()


@router.post("/insights/regenerate")
def regen_insights():
    insights.regenerate()
    return insights.current()


@router.get("/openings")
def openings():
    rows = db.rows(
        """SELECT eco, opening_name, color, result FROM games
           WHERE opening_name IS NOT NULL""")
    from collections import defaultdict
    fams = defaultdict(lambda: {"n": 0, "w": 0, "l": 0, "d": 0})
    for g in rows:
        fam = util.opening_family(g["eco"], g["opening_name"])
        k = (fam, g["color"])
        fams[k]["n"] += 1
        fams[k]["w" if g["result"] == "win" else "l" if g["result"] == "loss" else "d"] += 1
    return sorted(
        [{"opening": fam, "color": color, **v,
          "score_pct": round(100 * (v["w"] + 0.5 * v["d"]) / v["n"])}
         for (fam, color), v in fams.items()],
        key=lambda x: -x["n"])


# ------------------------------------------------------------------ puzzles

@router.get("/puzzles/daily")
def daily_puzzles(rival: str | None = None):
    return scheduler.daily_set(rival=rival)


@router.post("/puzzles/{puzzle_id}/attempt")
def puzzle_attempt(puzzle_id: int, body: dict = Body(...)):
    result = scheduler.record_attempt(
        puzzle_id, bool(body.get("correct")), body.get("time_ms"))
    newly = badges.recompute()
    return {**result, "new_badges": newly}


@router.get("/puzzles/stats")
def puzzle_stats():
    return scheduler.stats()


@router.post("/puzzles/backfill")
def puzzle_backfill():
    return {"created": generator.generate_all_pending()}


# ------------------------------------------------------------------- rivals

@router.get("/rivals")
def list_rivals():
    rows = db.rows("SELECT * FROM rivals ORDER BY name")
    for r in rows:
        r["scout_report"] = json.loads(r["scout_report"]) if r["scout_report"] else None
    return rows


@router.post("/rivals")
def add_rival(body: dict = Body(...)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "name required")
    with db.tx() as conn:
        conn.execute(
            """INSERT INTO rivals (name, lichess_username, chesscom_username, nwsrs_id, notes)
               VALUES (?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 lichess_username=excluded.lichess_username,
                 chesscom_username=excluded.chesscom_username,
                 nwsrs_id=excluded.nwsrs_id, notes=excluded.notes""",
            (name, body.get("lichess_username"), body.get("chesscom_username"),
             body.get("nwsrs_id"), body.get("notes")))
    return db.row("SELECT * FROM rivals WHERE name=?", (name,))


@router.post("/rivals/{rival_id}/scout")
def scout_rival(rival_id: int):
    try:
        return scout.build_report(rival_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.delete("/rivals/{rival_id}")
def delete_rival(rival_id: int):
    with db.tx() as conn:
        conn.execute("DELETE FROM rivals WHERE id=?", (rival_id,))
    return {"ok": True}


# -------------------------------------------------------------- tournaments

@router.get("/tournaments")
def tournaments(online: bool = True):
    return finder.upcoming(include_online=online)


@router.post("/tournaments/refresh")
def tournaments_refresh():
    return finder.refresh()


@router.post("/tournaments")
def tournaments_add(body: dict = Body(...)):
    if not body.get("name"):
        raise HTTPException(422, "name required")
    return {"id": finder.add_manual(body)}


@router.post("/tournaments/{tid}/status")
def tournament_status(tid: int, body: dict = Body(...)):
    finder.set_status(tid, body.get("status", ""))
    return {"ok": True}


# ------------------------------------------------------------------ roadmap

@router.get("/roadmap")
def get_roadmap():
    return roadmap.full()


# ------------------------------------------------------------------ journal

@router.get("/journal")
def journal_list():
    return db.rows("SELECT * FROM journal ORDER BY created_at DESC LIMIT 200")


@router.post("/journal")
def journal_add(body: dict = Body(...)):
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(422, "text required")
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO journal (created_at, author, text, game_id, tournament_id) "
            "VALUES (?,?,?,?,?)",
            (util.now_iso(), body.get("author", "coach"), text,
             body.get("game_id"), body.get("tournament_id")))
    return {"ok": True}


# ----------------------------------------------------------------- kid mode

@router.get("/kid/home")
def kid_home():
    stats = scheduler.stats()
    recent = db.rows(
        "SELECT result, opponent_name, played_at FROM games ORDER BY played_at DESC LIMIT 5")
    ratings = db.rows(
        """SELECT source, date, rating FROM ratings_history
           WHERE source IN ('lichess_rapid','chesscom_rapid','nwsrs')
           ORDER BY date""")
    wins = db.scalar("SELECT COUNT(*) FROM games WHERE result='win'") or 0
    return {
        "name": config.get("player_name").split()[0],
        "streak": stats["streak_days"],
        "solved_today": stats["solved_today"],
        "daily_target": config.get("puzzle_daily_target"),
        "total_wins": wins,
        "badges": badges.all_badges(),
        "recent_games": recent,
        "ratings": ratings,
        "coach_note": _kid_coach_note(),
    }


def _kid_coach_note() -> str:
    ins = db.rows(
        "SELECT * FROM insights WHERE kind='strength' ORDER BY score DESC LIMIT 1")
    if ins:
        return f"Coach says: {ins[0]['title']} — keep it up! 🎉"
    return "Coach says: every puzzle you solve makes you stronger. Let's go! 🚀"


# ----------------------------------------------------------------- settings

@router.get("/settings")
def get_settings():
    return config.all_config()


@router.post("/settings")
def set_settings(body: dict = Body(...)):
    return config.update(body)
