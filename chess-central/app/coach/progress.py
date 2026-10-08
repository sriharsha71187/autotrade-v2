"""Progress dashboard: is he getting better, area by area?

Every metric is a ratio (events / opportunities) computed over a window of
games, so the same definition yields the 12-month trend line, the current
90-day value and the previous 90 days it is compared against. Engine-based
metrics only count Stockfish-analyzed games; a window with too little data
reports None ("not enough data") instead of a misleading number.
"""
from __future__ import annotations

import json
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from .. import db, learn

WINDOW_DAYS = 90
ACTIVITY_DAYS = 28
MONTHS = 12
WINNING_WP = 80          # mover's win% ~ +4 pawns: "clearly winning"
LOSING_WP = 20
FAST_SECONDS = 3.0
TIME_TERMINATIONS = {"timeout", "outoftime", "time", "timevsinsufficient"}
HANG_TAGS = {"moved_en_prise", "left_piece_hanging"}
MISSED_TAGS = {"missed_mate", "missed_fork", "missed_capture", "missed_check_tactic"}
BAD = ("mistake", "blunder")
# opening "traps" that only name an attempt, not something he fell for
TRAP_ATTEMPTS = {"Scholar's Mate attempt", "Wayward Queen Attack"}


@dataclass
class Metric:
    key: str
    group: str
    title: str
    unit: str
    better: str                      # "up" | "down"
    help: str
    num: Callable[[dict], float]
    den: Callable[[dict], float]
    scale: float = 1.0
    min_window: float = 5
    min_month: float = 3
    decimals: int = 1
    lesson: str | None = None
    drill: bool = True               # can list the games behind the number


def _score(g) -> float:
    return 1.0 if g["result"] == "win" else 0.5 if g["result"] == "draw" else 0.0


def _an(g) -> int:
    return 1 if g["analyzed"] else 0


GROUPS = [
    ("vision", "Board vision"),
    ("tactics", "Tactics"),
    ("phases", "Game phases"),
    ("openings", "Openings"),
    ("temperament", "Time & temperament"),
    ("results", "Results"),
    ("habits", "Effort & habits"),
]

METRICS: list[Metric] = [
    # --- board vision
    Metric("hanging", "vision", "Hanging pieces", "per 100 moves", "down",
           "Mistakes where he moved a piece to a losing square or left one undefended.",
           lambda g: g["hang"], lambda g: g["moves"], scale=100, min_window=100,
           min_month=60, lesson="vision-is-it-safe"),
    Metric("blunders", "vision", "Blunders", "per game", "down",
           "Moves that threw away 30%+ of his winning chances.",
           lambda g: g["blunders"], _an, lesson="habits-blunder-check"),
    Metric("mistakes", "vision", "Mistakes", "per game", "down",
           "Moves that cost 20–30% of his winning chances.",
           lambda g: g["mistakes"], _an, lesson="habits-blunder-check"),
    Metric("safe_moves", "vision", "Safe moves", "%", "up",
           "Share of his moves that were not a mistake or blunder.",
           lambda g: g["moves"] - g["mistakes"] - g["blunders"], lambda g: g["moves"],
           scale=100, min_window=100, min_month=60, drill=False),
    Metric("allowed_fork", "vision", "Forks allowed", "per 10 games", "down",
           "Mistakes that let the opponent fork two of his pieces.",
           lambda g: g["allowed_fork"], _an, scale=10, lesson="defense-escape-early"),
    # --- tactics
    Metric("missed_tactics", "tactics", "Missed tactics", "per game", "down",
           "A fork, free piece, mate or winning check was there and he played something worse.",
           lambda g: g["missed"], _an, lesson="vision-cct-scan"),
    Metric("found_rate", "tactics", "Tactics found", "%", "up",
           "When a winning shot existed, how often he played it.",
           lambda g: g["found"], lambda g: g["found"] + g["missed"],
           scale=100, min_window=8, min_month=4, lesson="tactics-knight-forks"),
    Metric("missed_mates", "tactics", "Missed mates", "per 10 games", "down",
           "Times he had a forced checkmate and didn't play it.",
           lambda g: g["missed_mate"], _an, scale=10, lesson="mates-in-one-gallery"),
    # --- phases (avg win% lost per move — lower is more accurate)
    *[Metric(f"phase_{ph}", "phases", f"{ph.capitalize()} accuracy", "win% lost / move",
             "down", f"Average winning chances lost per move in the {ph}.",
             (lambda p: lambda g: g["loss_" + p])(ph), (lambda p: lambda g: g["n_" + p])(ph),
             min_window=60, min_month=30, decimals=1, drill=False,
             lesson={"opening": "openings-three-rules",
                     "middlegame": "strategy-make-a-plan",
                     "endgame": "endgames-opposition"}[ph])
      for ph in ("opening", "middlegame", "endgame")],
    Metric("conversion", "phases", "Converting wins", "%", "up",
           f"Games where he was clearly winning (≥{WINNING_WP}% win chance) that he actually won.",
           lambda g: 1 if g["was_winning"] and g["result"] == "win" else 0,
           lambda g: 1 if g["was_winning"] else 0, scale=100,
           lesson="strategy-trade-when-ahead"),
    Metric("saves", "phases", "Saves from lost positions", "%", "up",
           f"Games where he was clearly losing (≤{LOSING_WP}%) that he still drew or won.",
           lambda g: 1 if g["was_losing"] and g["result"] != "loss" else 0,
           lambda g: 1 if g["was_losing"] else 0, scale=100,
           lesson="defense-stalemate"),
    # --- openings
    Metric("book_depth", "openings", "Stays in his lines until", "move #", "up",
           "Average move where the game leaves positions he has seen in 3+ games.",
           lambda g: g["book_move"] or 0, lambda g: 1 if g["book_move"] else 0,
           drill=False, lesson="openings-three-rules"),
    Metric("early_exit_score", "openings", "Score when knocked out of book early", "%", "up",
           "His score in games where the opponent left his lines by move 3.",
           lambda g: _score(g) if g["early_exit"] else 0,
           lambda g: 1 if g["early_exit"] else 0, scale=100,
           lesson="openings-three-rules"),
    Metric("traps", "openings", "Fell for a named trap", "per 10 games", "down",
           "Scholar's Mate, Fool's Mate, Fried Liver, back-rank/smothered mates and similar.",
           lambda g: 1 if g["trap"] else 0, lambda g: 1, scale=10,
           lesson="openings-traps"),
    # --- time & temperament
    Metric("time_losses", "temperament", "Losses on time", "% of games", "down",
           "Online games he lost because the clock ran out.",
           lambda g: 1 if g["time_loss"] else 0, lambda g: 1 if g["online"] else 0,
           scale=100, lesson="habits-clock-craft"),
    Metric("fast_blunders", "temperament", "Blunders played in under 3s", "% of blunders",
           "down", "High = rushing (slow down). Low = he thought and still missed it (study).",
           lambda g: g["fast_blunders"], lambda g: g["timed_blunders"], scale=100,
           lesson="habits-blunder-check"),
    Metric("tilt", "temperament", "Score after 2 losses in a row", "%", "up",
           "Same-day games played right after two straight losses.",
           lambda g: _score(g) if g["after_two_losses"] else 0,
           lambda g: 1 if g["after_two_losses"] else 0, scale=100, min_window=3,
           min_month=2, lesson="habits-tilt-control"),
    # --- results
    Metric("score_white", "results", "Score as White", "%", "up", "Points per game with White.",
           lambda g: _score(g) if g["color"] == "white" else 0,
           lambda g: 1 if g["color"] == "white" else 0, scale=100, drill=False,
           lesson="openings-italian-game"),
    Metric("score_black", "results", "Score as Black", "%", "up", "Points per game with Black.",
           lambda g: _score(g) if g["color"] == "black" else 0,
           lambda g: 1 if g["color"] == "black" else 0, scale=100, drill=False,
           lesson="openings-black-vs-e4"),
    Metric("score_higher", "results", "Score vs higher-rated", "%", "up",
           "Points per game against opponents rated above him.",
           lambda g: _score(g) if g["vs"] == "higher" else 0,
           lambda g: 1 if g["vs"] == "higher" else 0, scale=100, drill=False),
    Metric("score_lower", "results", "Score vs lower-rated", "%", "up",
           "Points per game against opponents rated below him.",
           lambda g: _score(g) if g["vs"] == "lower" else 0,
           lambda g: 1 if g["vs"] == "lower" else 0, scale=100, drill=False),
    Metric("reviews", "habits", "Losses reviewed", "%", "up",
           "Analyzed losses he walked through in Game detective.",
           lambda g: 1 if g["reviewed"] else 0,
           lambda g: 1 if g["result"] == "loss" and g["analyzed"] else 0,
           scale=100, drill=False),
]
METRIC_BY_KEY = {m.key: m for m in METRICS}


# ---------------------------------------------------------------- game rows

def _game_records() -> list[dict]:
    from ..analysis import traps
    from . import repertoire

    games = db.rows(
        """SELECT id, pgn, color, result, termination, platform, played_at,
                  opponent_name, opponent_rating, player_rating, engine,
                  analyzed_at, reviewed_at
           FROM games ORDER BY played_at""")
    stats: dict[int, dict] = defaultdict(lambda: defaultdict(float))
    prev_opp: dict[int, dict] = {}
    for m in db.rows(
            """SELECT m.game_id, m.ply, m.mover, m.uci, m.classification, m.phase,
                      m.motifs, m.winprob_before, m.winprob_after, m.move_time
               FROM moves m JOIN games g ON g.id = m.game_id
               WHERE g.engine='stockfish' ORDER BY m.game_id, m.ply"""):
        gid = m["game_id"]
        tags = set(json.loads(m["motifs"] or "[]"))
        if m["mover"] == "opponent":
            prev_opp[gid] = {"to": m["uci"][2:4], "tags": tags}
            continue
        s = stats[gid]
        cls = m["classification"]
        bad = cls in BAD
        s["moves"] += 1
        s["blunders"] += cls == "blunder"
        s["mistakes"] += cls == "mistake"
        s["hang"] += bad and bool(tags & HANG_TAGS)
        s["allowed_fork"] += bad and "allowed_fork" in tags
        s["missed"] += bad and bool(tags & MISSED_TAGS)
        s["missed_mate"] += "missed_mate" in tags
        found = "played_fork" in tags or "delivered_mate" in tags
        if "won_material" in tags:
            # recapturing in an even trade isn't a tactic; grabbing a piece
            # the opponent just blundered onto that square is
            po = prev_opp.get(gid)
            if not po or po["to"] != m["uci"][2:4] or "moved_en_prise" in po["tags"]:
                found = True
        s["found"] += found
        loss = max(0.0, (m["winprob_before"] or 0) - (m["winprob_after"] or 0))
        if m["phase"]:
            s["loss_" + m["phase"]] += loss
            s["n_" + m["phase"]] += 1
        wb = m["winprob_before"] or 50
        s["was_winning"] = s["was_winning"] or wb >= WINNING_WP
        s["was_losing"] = s["was_losing"] or wb <= LOSING_WP
        if cls == "blunder" and m["move_time"] is not None:
            s["timed_blunders"] += 1
            s["fast_blunders"] += m["move_time"] < FAST_SECONDS

    exits = repertoire.exit_plies()
    out = []
    prev: list[dict] = []
    for g in games:
        s = stats.get(g["id"]) or {}
        analyzed = g["engine"] == "stockfish" and s.get("moves", 0) > 0
        opp_color = "black" if g["color"] == "white" else "white"
        trap = traps.detect_cached(g["pgn"], opp_color)
        exit_ply = exits.get(g["id"])
        vs = None
        if g["opponent_rating"] and g["player_rating"]:
            vs = ("higher" if g["opponent_rating"] > g["player_rating"]
                  else "lower" if g["opponent_rating"] < g["player_rating"] else None)
        day = (g["played_at"] or "")[:10]
        after_two = (len(prev) >= 2 and all(p["result"] == "loss" for p in prev[-2:])
                     and all(p["played_at"][:10] == day for p in prev[-2:]))
        rec = {
            "id": g["id"], "played_at": g["played_at"], "month": g["played_at"][:7],
            "color": g["color"], "result": g["result"], "opponent": g["opponent_name"],
            "analyzed": analyzed,
            "online": g["platform"] != "otb",
            "time_loss": (g["result"] == "loss" and
                          (g["termination"] or "").lower() in TIME_TERMINATIONS),
            "trap": trap if trap and trap not in TRAP_ATTEMPTS else None,
            "book_move": (exit_ply + 1) // 2 if exit_ply else None,
            "early_exit": bool(exit_ply and exit_ply <= repertoire.EARLY_EXIT_PLY),
            "vs": vs, "after_two_losses": after_two,
            "reviewed": bool(g["reviewed_at"]),
        }
        for k in ("moves", "blunders", "mistakes", "hang", "allowed_fork", "missed",
                  "missed_mate", "found", "timed_blunders", "fast_blunders",
                  "loss_opening", "n_opening", "loss_middlegame", "n_middlegame",
                  "loss_endgame", "n_endgame"):
            rec[k] = s.get(k, 0) if analyzed else 0
        rec["was_winning"] = bool(analyzed and s.get("was_winning"))
        rec["was_losing"] = bool(analyzed and s.get("was_losing"))
        out.append(rec)
        prev.append(g)
    return out


# ---------------------------------------------------------------- aggregation

def _value(m: Metric, games: list[dict], minimum: float) -> tuple[float | None, float]:
    num = sum(m.num(g) for g in games)
    den = sum(m.den(g) for g in games)
    if den < minimum or den == 0:
        return None, den
    return round(m.scale * num / den, m.decimals), den


def _trend(cur, prev, better) -> str | None:
    if cur is None or prev is None:
        return None
    delta = cur - prev
    if abs(delta) < max(0.05 * abs(prev), 0.05):
        return "flat"
    improving = delta < 0 if better == "down" else delta > 0
    return "better" if improving else "worse"


def _months(now: datetime, n: int = MONTHS) -> list[str]:
    y, mth = now.year, now.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{mth:02d}")
        mth -= 1
        if mth == 0:
            y, mth = y - 1, 12
    return out[::-1]


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _game_metrics(records: list[dict], now: datetime) -> list[dict]:
    cut1 = _iso(now - timedelta(days=WINDOW_DAYS))
    cut2 = _iso(now - timedelta(days=2 * WINDOW_DAYS))
    cur_games = [g for g in records if g["played_at"] >= cut1]
    prev_games = [g for g in records if cut2 <= g["played_at"] < cut1]
    by_month: dict[str, list[dict]] = defaultdict(list)
    for g in records:
        by_month[g["month"]].append(g)
    months = _months(now)

    out = []
    for m in METRICS:
        cur, n_cur = _value(m, cur_games, m.min_window)
        prev, _ = _value(m, prev_games, m.min_window)
        series = []
        for mo in months:
            v, n = _value(m, by_month.get(mo, []), m.min_month)
            if v is not None:
                series.append({"month": mo, "value": v, "n": n})
        out.append(_pack(m.key, m.group, m.title, m.unit, m.better, m.help,
                         cur, prev, n_cur, series, m.lesson, m.drill, WINDOW_DAYS))
    return out


def _pack(key, group, title, unit, better, help_, cur, prev, n_cur, series,
          lesson_id, drill, window) -> dict:
    lesson = None
    if lesson_id:
        l = learn.lesson(lesson_id)
        if l:
            lesson = {"lesson_id": lesson_id, "title": l["title"]}
    return {
        "key": key, "group": group, "title": title, "unit": unit, "better": better,
        "help": help_, "current": cur, "previous": prev, "n": n_cur,
        "delta": round(cur - prev, 1) if cur is not None and prev is not None else None,
        "trend": _trend(cur, prev, better), "series": series,
        "lesson": lesson, "drill": drill, "window_days": window,
    }


# ---------------------------------------------------------------- activity

def _activity_metrics(now: datetime) -> list[dict]:
    months = _months(now)
    first = months[0] + "-01"
    cut1 = (now - timedelta(days=ACTIVITY_DAYS)).strftime("%Y-%m-%d")
    cut2 = (now - timedelta(days=2 * ACTIVITY_DAYS)).strftime("%Y-%m-%d")

    attempts = db.rows(
        """SELECT substr(attempted_at,1,10) AS d, correct, completed
           FROM puzzle_attempts WHERE attempted_at >= ?""", (cut2 if cut2 < first else first,))
    lessons = db.rows("SELECT substr(completed_at,1,10) AS d FROM learn_progress")
    game_days = db.rows("SELECT DISTINCT substr(played_at,1,10) AS d FROM games")

    def month_days(mo: str) -> int:
        y, m = int(mo[:4]), int(mo[5:])
        if mo == now.strftime("%Y-%m"):
            return now.day
        nxt = datetime(y + (m == 12), m % 12 + 1, 1)
        return (nxt - datetime(y, m, 1)).days

    out = []

    # puzzle first-try accuracy (among completed puzzles)
    def acc(rows):
        done = [a for a in rows if a["completed"]]
        if len(done) < 5:
            return None, len(done)
        return round(100 * sum(a["correct"] for a in done) / len(done), 1), len(done)
    series = []
    for mo in months:
        v, n = acc([a for a in attempts if a["d"].startswith(mo)])
        if v is not None:
            series.append({"month": mo, "value": v, "n": n})
    cur, n = acc([a for a in attempts if a["d"] >= cut1])
    prev, _ = acc([a for a in attempts if cut2 <= a["d"] < cut1])
    mastered = db.scalar("SELECT COUNT(*) FROM puzzles WHERE interval_days >= 21") or 0
    m = _pack("puzzle_accuracy", "habits", "Puzzle first-try accuracy", "%", "up",
              f"Puzzles solved cleanly on the first try. {mastered} puzzles mastered "
              "(reviewed out to 3+ weeks).", cur, prev, n, series, None, False, ACTIVITY_DAYS)
    m["extra"] = {"mastered": mastered}
    out.append(m)

    # practice days per week: any puzzle, lesson or game
    days = ({a["d"] for a in attempts if a["completed"]} | {r["d"] for r in lessons}
            | {r["d"] for r in game_days if r["d"]})

    def per_week(lo, hi, span):
        return round(7 * sum(1 for d in days if lo <= d < hi) / span, 1)
    series = [{"month": mo, "value": round(7 * sum(1 for d in days if d.startswith(mo))
                                            / month_days(mo), 1), "n": month_days(mo)}
              for mo in months]
    today = now.strftime("%Y-%m-%d") + "~"
    out.append(_pack("practice_days", "habits", "Practice days", "per week", "up",
                     "Days with at least one puzzle, lesson or game.",
                     per_week(cut1, today, ACTIVITY_DAYS), per_week(cut2, cut1, ACTIVITY_DAYS),
                     ACTIVITY_DAYS, _trim_leading_zeros(series), "habits-tournament-day",
                     False, ACTIVITY_DAYS))

    # puzzles solved per week
    solved = [a["d"] for a in attempts if a["completed"]]
    series = [{"month": mo, "value": round(7 * sum(1 for d in solved if d.startswith(mo))
                                            / month_days(mo), 1), "n": month_days(mo)}
              for mo in months]
    out.append(_pack("puzzles_per_week", "habits", "Puzzles solved", "per week", "up",
                     "Completed puzzles (with or without a hint).",
                     round(7 * sum(1 for d in solved if d >= cut1) / ACTIVITY_DAYS, 1),
                     round(7 * sum(1 for d in solved if cut2 <= d < cut1) / ACTIVITY_DAYS, 1),
                     ACTIVITY_DAYS, _trim_leading_zeros(series), None, False, ACTIVITY_DAYS))

    # Academy lessons completed
    ld = [r["d"] for r in lessons]
    total = sum(len(t["lessons"]) for t in learn.curriculum())
    series = [{"month": mo, "value": sum(1 for d in ld if d.startswith(mo)), "n": 1}
              for mo in months]
    m = _pack("lessons", "habits", "Academy lessons", "completed", "up",
              f"{len(ld)} of {total} lessons finished so far.",
              sum(1 for d in ld if d >= cut1), sum(1 for d in ld if cut2 <= d < cut1),
              ACTIVITY_DAYS, _trim_leading_zeros(series), None, False, ACTIVITY_DAYS)
    m["extra"] = {"done": len(ld), "total": total}
    out.append(m)
    return out


def _trim_leading_zeros(series: list[dict]) -> list[dict]:
    i = 0
    while i < len(series) and not series[i]["value"]:
        i += 1
    return series[i:]


# ---------------------------------------------------------------- lessons → games

def lesson_effects(records: list[dict] | None = None) -> list[dict]:
    """For each finished lesson that targets a mistake motif: did that mistake
    get rarer in the games after the lesson than in the games before it?"""
    records = records if records is not None else _game_records()
    tags_for: dict[str, set] = defaultdict(set)
    for tag, lid in learn.MOTIF_LESSONS.items():
        tags_for[lid].add(tag)
    done = db.rows("SELECT lesson_id, completed_at FROM learn_progress")
    if not done:
        return []
    moves = db.rows(
        """SELECT m.game_id, m.motifs, m.classification FROM moves m
           JOIN games g ON g.id = m.game_id
           WHERE g.engine='stockfish' AND m.mover='player'""")
    events_by_game: dict[int, list[set]] = defaultdict(list)
    for m in moves:
        events_by_game[m["game_id"]].append(
            set(json.loads(m["motifs"] or "[]")) if m["classification"] != "best" else set())
    played = {g["id"]: g["played_at"] for g in records if g["analyzed"]}

    out = []
    for d in done:
        tags = tags_for.get(d["lesson_id"])
        lesson = learn.lesson(d["lesson_id"])
        if not tags or not lesson:
            continue
        at = d["completed_at"]
        lo = _iso(datetime.strptime(at[:10], "%Y-%m-%d") - timedelta(days=60))
        hi = _iso(datetime.strptime(at[:10], "%Y-%m-%d") + timedelta(days=60))

        def rate(ids):
            mv = [t for gid in ids for t in events_by_game.get(gid, [])]
            if len(mv) < 60:
                return None, len(mv)
            return round(100 * sum(1 for t in mv if t & tags) / len(mv), 2), len(mv)
        before, nb = rate([gid for gid, p in played.items() if lo <= p < at])
        after, na = rate([gid for gid, p in played.items() if at <= p < hi])
        out.append({
            "lesson_id": d["lesson_id"], "title": lesson["title"],
            "completed_at": at, "motifs": sorted(tags),
            "before": before, "after": after, "moves_before": nb, "moves_after": na,
            "verdict": (None if before is None or after is None else
                        "working" if after < before * 0.8 else
                        "worse" if after > before * 1.2 else "no change yet"),
        })
    out.sort(key=lambda x: x["completed_at"], reverse=True)
    return out


# ---------------------------------------------------------------- ratings

def _ratings(now: datetime) -> list[dict]:
    cut = (now - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    out = []
    for src in db.rows("SELECT DISTINCT source FROM ratings_history"):
        hist = db.rows("SELECT date, rating FROM ratings_history WHERE source=? ORDER BY date",
                       (src["source"],))
        if not hist:
            continue
        before = [h for h in hist if h["date"] < cut]
        base = before[-1]["rating"] if before else None
        out.append({"source": src["source"], "rating": hist[-1]["rating"],
                    "date": hist[-1]["date"],
                    "change": hist[-1]["rating"] - base if base is not None else None,
                    "series": [{"date": h["date"], "value": h["rating"]} for h in hist[-60:]]})
    return out


# ---------------------------------------------------------------- public API

_lock = threading.Lock()
_cache: dict = {"sig": None, "data": None}


def _signature() -> tuple:
    return (
        db.scalar("SELECT COUNT(*) || '|' || IFNULL(MAX(played_at),'') || '|' || "
                  "IFNULL(MAX(analyzed_at),'') || '|' || COUNT(reviewed_at) FROM games"),
        db.scalar("SELECT COUNT(*) FROM puzzle_attempts"),
        db.scalar("SELECT COUNT(*) || IFNULL(MAX(completed_at),'') FROM learn_progress"),
        db.scalar("SELECT COUNT(*) FROM ratings_history"),
        datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )


def dashboard() -> dict:
    sig = _signature()
    with _lock:
        if _cache["sig"] == sig:
            return _cache["data"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    records = _game_records()
    metrics = _game_metrics(records, now) + _activity_metrics(now)
    by_group: dict[str, list] = defaultdict(list)
    for m in metrics:
        by_group[m["group"]].append(m)
    data = {
        "window_days": WINDOW_DAYS,
        "activity_days": ACTIVITY_DAYS,
        "games": len(records),
        "analyzed_games": sum(1 for g in records if g["analyzed"]),
        "groups": [{"key": k, "title": t, "metrics": by_group[k]}
                   for k, t in GROUPS if by_group.get(k)],
        "ratings": _ratings(now),
        "lesson_effects": lesson_effects(records),
        "summary": {
            "better": sum(1 for m in metrics if m["trend"] == "better"),
            "worse": sum(1 for m in metrics if m["trend"] == "worse"),
            "flat": sum(1 for m in metrics if m["trend"] == "flat"),
        },
    }
    with _lock:
        _cache.update(sig=sig, data=data)
    return data


def metric_games(key: str, limit: int = 30) -> dict:
    """The games behind a metric's current value (where it 'happened')."""
    m = METRIC_BY_KEY.get(key)
    if m is None:
        raise KeyError(key)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cut = _iso(now - timedelta(days=WINDOW_DAYS))
    rows = [g for g in _game_records() if g["played_at"] >= cut and m.den(g)]
    hits = [g for g in rows if m.num(g)]
    # for "higher is better" rates, the instructive games are the misses
    if m.better == "up":
        hits = [g for g in rows if m.num(g) < m.den(g)]
    hits.sort(key=lambda g: g["played_at"], reverse=True)
    return {
        "key": key, "title": m.title,
        "games": [{"id": g["id"], "played_at": g["played_at"], "opponent": g["opponent"],
                   "color": g["color"], "result": g["result"],
                   "count": m.num(g), "trap": g["trap"]} for g in hits[:limit]],
    }


# ---------------------------------------------------------------- kid view

# The kid sees effort-framed versions of a subset of metrics: no tilt or
# review-compliance numbers, and nothing labeled "slipping" — a dip is
# presented as his next mission with the lesson that fixes it.
KID_METRICS = {
    "hanging": ("🛡️", "Keeping pieces safe", "Pieces left hanging, per 100 moves"),
    "blunders": ("💥", "Fewer big oopsies", "Blunders per game"),
    "safe_moves": ("✅", "Safe moves", "How often your move keeps everything safe"),
    "found_rate": ("🎯", "Spotting tactics", "When there was a trick, how often you found it"),
    "missed_mates": ("👑", "Finding checkmates", "Checkmates you missed, per 10 games"),
    "allowed_fork": ("🍴", "Dodging forks", "Forks you allowed, per 10 games"),
    "conversion": ("🏁", "Finishing the job", "Winning the games where you were way ahead"),
    "saves": ("🦸", "Comeback hero", "Saving games that looked lost"),
    "traps": ("🪤", "Trap-proof", "Named traps you fell for, per 10 games"),
    "fast_blunders": ("🐢", "Taking your time", "Oopsies made in under 3 seconds"),
    "score_higher": ("🧗", "Beating stronger players", "Your score vs higher-rated players"),
    "puzzle_accuracy": ("🧩", "Puzzle first-try", "Puzzles solved on the first try"),
    "practice_days": ("📅", "Practice days", "Days you practiced, per week"),
    "lessons": ("🎓", "Academy lessons", "Lessons finished in the last 4 weeks"),
}
_KID_STATUS = {"better": "growing", "worse": "mission", "flat": "steady", None: "locked"}


def kid_view() -> dict:
    data = dashboard()
    by_key = {m["key"]: m for g in data["groups"] for m in g["metrics"]}
    skills = []
    for key, (emoji, title, help_) in KID_METRICS.items():
        m = by_key.get(key)
        if not m:
            continue
        status = _KID_STATUS[m["trend"]] if m["current"] is not None else "locked"
        skills.append({
            "key": key, "emoji": emoji, "title": title, "help": help_,
            "value": m["current"], "previous": m["previous"],
            "unit": m["unit"], "lower_is_better": m["better"] == "down",
            "status": status, "series": m["series"], "lesson": m["lesson"],
        })
    order = {"growing": 0, "mission": 1, "steady": 2, "locked": 3}
    skills.sort(key=lambda s: order[s["status"]])
    return {
        "growing": sum(1 for s in skills if s["status"] == "growing"),
        "missions": [s for s in skills if s["status"] == "mission"][:2],
        "skills": skills,
    }
