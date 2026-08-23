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
    """Start a background sync and return immediately — a full-history pull
    can stream for minutes, far too long for a browser to wait on."""
    return sync.start()


@router.get("/sync/status")
def sync_status():
    return sync.status()


@router.get("/status")
def status():
    return {
        "last_sync": db.kv_get("last_sync"),
        "analysis": worker.status(),
        "games_total": db.scalar("SELECT COUNT(*) FROM games") or 0,
        "engine_found": bool(config.find_engine()),
        "fake_analyzed": db.scalar(
            "SELECT COUNT(*) FROM games WHERE analyzed_at IS NOT NULL "
            "AND (engine IS NULL OR engine != 'stockfish')") or 0,
        "analysis_errors": db.scalar(
            "SELECT COUNT(*) FROM games WHERE analysis_error IS NOT NULL") or 0,
    }


@router.post("/analyze")
def start_analysis():
    return worker.start()


@router.post("/analyze/reset")
def reset_analysis(body: dict = Body(default={})):
    """Queue games for re-analysis.

    scope: 'fake' (analyzed without Stockfish), 'errors', or 'all'.
    Their derived moves and own-game puzzles are discarded and rebuilt.
    """
    scope = body.get("scope", "fake")
    where = {
        "fake": "analyzed_at IS NOT NULL AND (engine IS NULL OR engine != 'stockfish')",
        "errors": "analysis_error IS NOT NULL",
        "all": "analyzed_at IS NOT NULL OR analysis_error IS NOT NULL",
    }.get(scope)
    if not where:
        raise HTTPException(422, "scope must be fake, errors, or all")
    ids = [r["id"] for r in db.rows(f"SELECT id FROM games WHERE {where}")]
    if ids:
        ph = ",".join("?" for _ in ids)
        with db.tx() as conn:
            conn.execute(f"DELETE FROM moves WHERE game_id IN ({ph})", ids)
            conn.execute(
                f"DELETE FROM puzzles WHERE game_id IN ({ph}) AND source != 'rival_prep'", ids)
            conn.execute(
                f"UPDATE games SET analyzed_at=NULL, analysis_error=NULL, engine=NULL "
                f"WHERE id IN ({ph})", ids)
    worker.start()
    return {"reset": len(ids)}


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

# what the opponent hit him with, in plain words
OPP_TAG_LABELS = {
    "played_fork": "Fork",
    "won_material": "Won material",
    "delivered_mate": "Checkmate",
    "back_rank_mate_win": "Back-rank mate",
}
_trap_cache: dict[tuple, str | None] = {}


def _opponent_tactics(games_rows: list[dict]) -> dict[int, dict]:
    """Per game: named trap (from the PGN) + the opponent's tactical strikes
    (from analyzed moves). Traps show even before analysis runs."""
    from ..analysis import traps

    out: dict[int, dict] = {}
    ids = [g["id"] for g in games_rows]
    if not ids:
        return out
    ph = ",".join("?" for _ in ids)

    strikes: dict[int, list[dict]] = {gid: [] for gid in ids}
    for m in db.rows(
            f"""SELECT game_id, ply, san, motifs FROM moves
               WHERE game_id IN ({ph}) AND mover='opponent'
                 AND motifs != '[]' ORDER BY ply""", ids):
        for tag in json.loads(m["motifs"] or "[]"):
            if tag in OPP_TAG_LABELS:
                strikes[m["game_id"]].append(
                    {"move_number": (m["ply"] + 1) // 2, "san": m["san"],
                     "label": OPP_TAG_LABELS[tag]})

    pgns = {r["id"]: r["pgn"] for r in db.rows(
        f"SELECT id, pgn FROM games WHERE id IN ({ph})", ids)}
    for g in games_rows:
        gid = g["id"]
        key = (gid, len(pgns.get(gid) or ""))
        if key not in _trap_cache:
            opp_color = "black" if g["color"] == "white" else "white"
            _trap_cache[key] = traps.detect(pgns.get(gid) or "", opp_color)
        trap = _trap_cache[key]
        labels = ([trap] if trap else [])
        for s in strikes[gid]:
            if s["label"] not in labels:
                labels.append(s["label"])
        out[gid] = {"labels": labels[:3], "trap": trap, "strikes": strikes[gid]}
    return out

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
                  moves_count, played_at, analyzed_at, analysis_error, engine
           FROM games WHERE {' AND '.join(where)}
           ORDER BY played_at DESC LIMIT ? OFFSET ?""",
        (*params, limit, offset))
    tactics = _opponent_tactics(rows)
    for r in rows:
        r["opp_tactics"] = tactics.get(r["id"], {}).get("labels", [])
    return rows


@router.post("/games/otb")
def add_otb_game(body: dict = Body(...)):
    """Enter an over-the-board (tournament) game — feeds the same pipeline."""
    import hashlib

    import chess.pgn

    pgn_text = (body.get("pgn") or "").strip()
    if not pgn_text:
        raise HTTPException(422, "PGN (or a movetext line like '1. e4 e5 …') is required")
    if "[" not in pgn_text:   # bare movetext — wrap it
        pgn_text = '[Event "OTB"]\n[Result "*"]\n\n' + pgn_text
    game = util.parse_pgn_game(pgn_text)
    if game is None or game.errors:
        detail = str(game.errors[0]) if game and game.errors else "could not parse the moves"
        raise HTTPException(422, f"That PGN has a problem: {detail}")
    n_moves = len(list(game.mainline_moves()))
    if n_moves < 4:
        raise HTTPException(422, "That game has fewer than 4 moves — check the movetext")

    color = body.get("color")
    if color not in ("white", "black"):
        raise HTTPException(422, "color must be 'white' or 'black' (Nirvaan's side)")
    result = body.get("result")
    if result not in ("win", "loss", "draw"):
        header = game.headers.get("Result", "*")
        if header in ("1-0", "0-1", "1/2-1/2"):
            result = util.result_for(header, color)
        else:
            raise HTTPException(422, "result is required (win/loss/draw)")

    played_at = (body.get("played_at") or game.headers.get("Date", "").replace(".", "-")
                 or util.now_iso()[:10])
    if len(played_at) == 10:
        played_at += "T12:00:00Z"
    gid = "otb_" + hashlib.sha1(pgn_text.encode()).hexdigest()[:12]
    rec = {
        "platform": "otb", "platform_game_id": gid, "url": None, "pgn": pgn_text,
        "color": color,
        "opponent_name": body.get("opponent_name") or game.headers.get(
            "Black" if color == "white" else "White") or "Unknown",
        "opponent_rating": body.get("opponent_rating") or None,
        "player_rating": body.get("player_rating") or None,
        "result": result, "termination": body.get("termination"),
        "time_class": "classical",
        "time_control": body.get("time_control") or game.headers.get("TimeControl", ""),
        "rated": 1, "eco": game.headers.get("ECO"),
        "opening_name": game.headers.get("Opening"),
        "moves_count": n_moves, "played_at": played_at,
    }
    with db.tx() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO games
               (platform, platform_game_id, url, pgn, color, opponent_name,
                opponent_rating, player_rating, result, termination, time_class,
                time_control, rated, eco, opening_name, moves_count, played_at)
               VALUES (:platform,:platform_game_id,:url,:pgn,:color,:opponent_name,
                :opponent_rating,:player_rating,:result,:termination,:time_class,
                :time_control,:rated,:eco,:opening_name,:moves_count,:played_at)""",
            rec)
        if cur.rowcount == 0:
            raise HTTPException(409, "This game is already in the database")
    if config.get("analysis_auto"):
        worker.start()
    new_id = db.scalar("SELECT id FROM games WHERE platform_game_id=?", (gid,))
    return {"id": new_id, "moves": n_moves}


@router.post("/games/otb/scan")
def scan_scoresheet(body: dict = Body(...)):
    """Photo of a handwritten scoresheet -> transcribed, validated movetext.

    Returns a draft for the OTB form — never inserts a game directly. The
    human reviews the movetext (and any flagged issues) before submitting.
    """
    from ..coach import llm

    image = (body.get("image") or "").strip()
    if image.startswith("data:"):          # dataURL from the browser
        header, _, image = image.partition(",")
        media_type = header.split(";")[0].split(":")[1] if ":" in header else ""
    else:
        media_type = body.get("media_type") or "image/jpeg"
    if not image:
        raise HTTPException(422, "image required (base64 or data URL)")
    if media_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        raise HTTPException(422, f"unsupported image type: {media_type}")
    if len(image) > 7_000_000:             # ~5MB decoded, the API image limit
        raise HTTPException(422, "Image too large — the app resizes photos "
                                 "automatically, so this shouldn't happen")

    try:
        scan = llm.scan_scoresheet(image, media_type)
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    checked = util.validate_movetext(scan.get("moves_san") or [])
    if scan.get("notes"):
        checked["issues"].append(f"Reader's notes: {scan['notes']}")
    return {
        **checked,
        "white_name": scan.get("white_name"),
        "black_name": scan.get("black_name"),
        "date": scan.get("date"),
        "result": scan.get("result"),
        "event": scan.get("event"),
    }


@router.get("/games/{game_id}")
def game_detail(game_id: int):
    g = db.row("SELECT * FROM games WHERE id=?", (game_id,))
    if not g:
        raise HTTPException(404)
    moves = db.rows("SELECT * FROM moves WHERE game_id=? ORDER BY ply", (game_id,))
    for m in moves:
        m["motifs"] = json.loads(m["motifs"] or "[]")
    opp = _opponent_tactics([g]).get(game_id, {})
    return {**g, "moves": moves, "accuracy": annotate.game_accuracy(game_id),
            "opp_tactics": opp.get("labels", []), "opp_trap": opp.get("trap"),
            "opp_strikes": opp.get("strikes", [])}


# ----------------------------------------------------------------- insights

@router.get("/insights")
def get_insights():
    return {"items": insights.current(), "meta": insights.meta()}


@router.post("/insights/regenerate")
def regen_insights(body: dict = Body(default={})):
    if "window_days" in body:
        config.update({"insights_window_days": body["window_days"]})
    insights.regenerate()
    return {"items": insights.current(), "meta": insights.meta()}


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


# --------------------------------------------------------------- repertoire

@router.get("/repertoire")
def get_repertoire(color: str = "white"):
    from ..coach import repertoire
    if color not in ("white", "black"):
        raise HTTPException(422, "color must be white or black")
    return repertoire.build(color)


@router.post("/repertoire/drills")
def repertoire_drills():
    from ..coach import repertoire
    return {"created": repertoire.refresh_drills()}


@router.get("/repertoire/queue")
def repertoire_queue():
    from ..coach import repertoire
    return repertoire.drill_queue()


# ------------------------------------------------------------------ puzzles

@router.get("/puzzles/daily")
def daily_puzzles(rival: str | None = None, theme: str | None = None):
    return scheduler.daily_set(rival=rival, theme=theme)


@router.post("/puzzles/{puzzle_id}/attempt")
def puzzle_attempt(puzzle_id: int, body: dict = Body(...)):
    result = scheduler.record_attempt(
        puzzle_id, bool(body.get("correct")), body.get("time_ms"),
        completed=body.get("completed"))
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


# ---------------------------------------------------------- rhythm & review

@router.get("/rhythm")
def rhythm_status():
    from ..coach import rhythm
    return rhythm.status()


@router.post("/rhythm/check")
def rhythm_check(body: dict = Body(...)):
    from ..coach import rhythm
    rhythm.set_check(int(body.get("index", -1)), bool(body.get("checked")))
    return rhythm.status()


@router.get("/review/queue")
def review_queue():
    from ..coach import review
    return review.queue()


@router.post("/review/{game_id}/done")
def review_done(game_id: int):
    from ..coach import review
    review.mark(game_id)
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
    from ..coach import review
    stats = scheduler.stats()
    recent = db.rows(
        "SELECT result, opponent_name, played_at FROM games ORDER BY played_at DESC LIMIT 5")
    wins = db.scalar("SELECT COUNT(*) FROM games WHERE result='win'") or 0
    practiced7 = db.scalar(
        """SELECT COUNT(DISTINCT DATE(attempted_at)) FROM puzzle_attempts
           WHERE completed=1 AND attempted_at >= date('now', '-6 days')""") or 0
    # skills trend: % of his moves that weren't mistakes/blunders, by month —
    # a chart where up always means "getting stronger"
    skills = db.rows(
        """SELECT substr(g.played_at, 1, 7) AS month,
                  COUNT(*) AS moves,
                  SUM(m.classification IN ('mistake','blunder')) AS bad
           FROM moves m JOIN games g ON g.id = m.game_id
           WHERE m.mover='player' AND g.engine='stockfish'
           GROUP BY month HAVING moves >= 50 ORDER BY month""")
    skills_trend = [
        {"month": s["month"], "safe_pct": round(100 * (1 - s["bad"] / s["moves"]), 1),
         "moves": s["moves"]}
        for s in skills
    ]
    return {
        "name": config.get("player_name").split()[0],
        "streak": stats["streak_days"],
        "practiced_days_7": practiced7,
        "solved_today": stats["solved_today"],
        "daily_target": config.get("puzzle_daily_target"),
        "total_wins": wins,
        "badges": badges.all_badges(),
        "recent_games": recent,
        "skills_trend": skills_trend,
        "review_queue": len(review.queue(limit=3)),
        "coach_note": _kid_coach_note(),
        "game_note": _kid_game_note(),
    }


def _kid_game_note() -> str | None:
    try:
        from ..coach import llm
        return llm.kid_note_for_latest_game()
    except Exception:
        return None


def _kid_coach_note() -> str:
    ins = db.rows(
        "SELECT * FROM insights WHERE kind='strength' ORDER BY score DESC LIMIT 1")
    if ins:
        return f"Coach says: {ins[0]['title']} — keep it up! 🎉"
    return "Coach says: every puzzle you solve makes you stronger. Let's go! 🚀"


# ----------------------------------------------------------------- academy

@router.get("/learn")
def learn_overview():
    from .. import learn
    return learn.overview()


@router.get("/learn/motif-map")
def learn_motif_map():
    from .. import learn
    return learn.motif_map()


@router.get("/learn/lesson/{lesson_id}")
def learn_lesson(lesson_id: str):
    from .. import learn
    l = learn.lesson(lesson_id)
    if not l:
        raise HTTPException(404)
    return l


@router.post("/learn/lesson/{lesson_id}/complete")
def learn_complete(lesson_id: str, body: dict = Body(default={})):
    from .. import learn
    try:
        learn.complete(lesson_id, int(body.get("correct", 0)), int(body.get("total", 0)))
    except ValueError:
        raise HTTPException(404)
    return {"ok": True}


# ---------------------------------------------------------------- AI coach

@router.get("/llm/status")
def llm_status():
    from ..coach import llm
    return {"configured": llm.is_configured(), "model": config.get("llm_model")}


@router.post("/llm/game/{game_id}/commentary")
def llm_game_commentary(game_id: int, force: bool = False):
    from ..coach import llm
    try:
        return llm.game_commentary(game_id, force=force)
    except llm.LLMError as e:
        raise HTTPException(400, str(e))


@router.get("/llm/game/{game_id}/commentary")
def llm_game_commentary_get(game_id: int):
    from ..coach import llm
    note = llm._note_get("game_commentary", game_id)
    return note or {"content": None}


@router.post("/llm/weekly-report")
def llm_weekly_report(force: bool = False):
    from ..coach import llm
    try:
        return llm.weekly_report(force=force)
    except llm.LLMError as e:
        raise HTTPException(400, str(e))


@router.post("/llm/rival/{rival_id}/brief")
def llm_rival_brief(rival_id: int, force: bool = False):
    from ..coach import llm
    try:
        return llm.rival_brief(rival_id, force=force)
    except llm.LLMError as e:
        raise HTTPException(400, str(e))


@router.get("/llm/chat")
def llm_chat_history():
    from ..coach import llm
    return llm.chat_history()


@router.post("/llm/chat")
def llm_chat(body: dict = Body(...)):
    from ..coach import llm
    try:
        return llm.chat(body.get("message", ""))
    except llm.LLMError as e:
        raise HTTPException(400, str(e))


@router.delete("/llm/chat")
def llm_chat_clear():
    from ..coach import llm
    llm.chat_clear()
    return {"ok": True}


# ------------------------------------------------------------------ backups

@router.get("/backups")
def backups_list():
    from .. import backup
    return {"backups": backup.list_backups()}


@router.post("/backups")
def backups_run():
    from .. import backup
    return backup.run(force=True)


# ----------------------------------------------------------------- settings

SECRET_KEYS = ("anthropic_api_key", "kid_pin")


def _redact(cfg: dict) -> dict:
    out = dict(cfg)
    for k in SECRET_KEYS:
        out[f"{k}_set"] = bool(out.get(k))
        out[k] = ""            # secrets never leave the server
    return out


@router.get("/settings")
def get_settings():
    return _redact(config.all_config())


@router.post("/settings")
def set_settings(body: dict = Body(...)):
    # an empty secret field means "keep what's saved", not "clear it"
    for k in SECRET_KEYS:
        if not (body.get(k) or "").strip():
            body.pop(k, None)
    return _redact(config.update(body))


# --------------------------------------------------------------- parent gate

@router.get("/gate")
def gate_status():
    return {"pin_required": bool(config.get("kid_pin"))}


@router.post("/gate")
def gate_check(body: dict = Body(...)):
    ok = (body.get("pin") or "") == (config.get("kid_pin") or "")
    if not ok:
        raise HTTPException(403, "Wrong PIN")
    return {"ok": True}
