"""Spaced-repetition scheduling (SM-2 lite) and daily-set assembly.

The daily set mixes due reviews with fresh puzzles, weighted toward the
motifs the insights engine currently flags as weaknesses — so practice
always points at what needs work (Woodpecker-style repetition included).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .. import config, db, util

AGAIN_MINUTES = 10
FIRST_INTERVALS = [1, 3]      # days after 1st and 2nd successful review


def record_attempt(puzzle_id: int, correct: bool, time_ms: int | None = None) -> dict:
    p = db.row("SELECT * FROM puzzles WHERE id=?", (puzzle_id,))
    if not p:
        raise ValueError("no such puzzle")
    now = datetime.now(timezone.utc)
    ease = p["ease"] or 2.5
    reps, lapses = p["reps"] or 0, p["lapses"] or 0
    interval = p["interval_days"] or 0

    if correct:
        reps += 1
        if reps <= len(FIRST_INTERVALS):
            interval = FIRST_INTERVALS[reps - 1]
        else:
            interval = max(1.0, interval * ease)
        ease = min(3.0, ease + 0.05)
        due = now + timedelta(days=interval)
        retired = 1 if reps >= 6 and lapses == 0 else 0
    else:
        lapses += 1
        reps = 0
        interval = 0
        ease = max(1.3, ease - 0.2)
        due = now + timedelta(minutes=AGAIN_MINUTES)
        retired = 0

    with db.tx() as conn:
        conn.execute(
            """UPDATE puzzles SET reps=?, lapses=?, interval_days=?, ease=?,
               due_at=?, retired=? WHERE id=?""",
            (reps, lapses, interval, ease,
             due.strftime("%Y-%m-%dT%H:%M:%SZ"), retired, puzzle_id))
        conn.execute(
            "INSERT INTO puzzle_attempts(puzzle_id, attempted_at, correct, time_ms) "
            "VALUES (?,?,?,?)",
            (puzzle_id, util.now_iso(), 1 if correct else 0, time_ms))
    return {"next_due": due.isoformat(), "interval_days": interval, "retired": retired}


def _weak_themes(limit: int = 4) -> list[str]:
    """Motifs ranked by how often they cost him points lately."""
    rows = db.rows(
        """SELECT m.motifs FROM moves m JOIN games g ON g.id = m.game_id
           WHERE m.mover='player' AND m.classification IN ('mistake','blunder')
           ORDER BY g.played_at DESC LIMIT 400""")
    counts: dict[str, int] = {}
    for r in rows:
        for t in json.loads(r["motifs"] or "[]"):
            counts[t] = counts.get(t, 0) + 1
    return [t for t, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]]


def daily_set(target: int | None = None, rival: str | None = None) -> list[dict]:
    """Assemble today's puzzle set: due reviews first, then fresh, weakness-weighted."""
    target = target or config.get("puzzle_daily_target")
    now = util.now_iso()

    if rival:
        base = db.rows(
            "SELECT * FROM puzzles WHERE retired=0 AND rival=? ORDER BY due_at LIMIT ?",
            (rival, target))
        return [_public(p) for p in base]

    due = db.rows(
        """SELECT * FROM puzzles WHERE retired=0 AND rival IS NULL
           AND due_at <= ? AND reps > 0 ORDER BY due_at LIMIT ?""",
        (now, target))
    chosen = list(due)

    if len(chosen) < target:
        weak = _weak_themes()
        fresh = db.rows(
            """SELECT * FROM puzzles WHERE retired=0 AND rival IS NULL AND reps=0
               AND due_at <= ? ORDER BY created_at DESC LIMIT 200""", (now,))
        # weakness-themed first, easiest first inside a theme
        def rank(p):
            themes = json.loads(p["themes"] or "[]")
            theme_hit = min((weak.index(t) for t in themes if t in weak), default=99)
            return (theme_hit, p["difficulty"] or 3)
        fresh.sort(key=rank)
        seen = {p["id"] for p in chosen}
        for p in fresh:
            if p["id"] not in seen:
                chosen.append(p)
            if len(chosen) >= target:
                break
    return [_public(p) for p in chosen]


def _public(p: dict) -> dict:
    return {
        "id": p["id"],
        "fen": p["fen"],
        "solution": json.loads(p["solution"]),
        "themes": json.loads(p["themes"] or "[]"),
        "phase": p["phase"],
        "difficulty": p["difficulty"],
        "source": p["source"],
        "explanation": p["explanation"],
        "game_id": p["game_id"],
        "rival": p["rival"],
        "reps": p["reps"],
    }


def stats() -> dict:
    today = util.now_iso()[:10]
    total = db.scalar("SELECT COUNT(*) FROM puzzles WHERE rival IS NULL") or 0
    solved_today = db.scalar(
        "SELECT COUNT(DISTINCT puzzle_id) FROM puzzle_attempts "
        "WHERE attempted_at LIKE ? AND correct=1", (today + "%",)) or 0
    attempts = db.rows(
        "SELECT attempted_at, correct FROM puzzle_attempts ORDER BY attempted_at DESC LIMIT 2000")
    # streak: consecutive days (ending today or yesterday) with >=1 correct solve
    days = sorted({a["attempted_at"][:10] for a in attempts if a["correct"]}, reverse=True)
    streak = 0
    if days:
        cur = datetime.now(timezone.utc).date()
        for d in days:
            dd = datetime.strptime(d, "%Y-%m-%d").date()
            if (cur - dd).days in (0, 1):
                streak += 1
                cur = dd - timedelta(days=1)
            elif (cur - dd).days > 1:
                break
    correct = sum(1 for a in attempts if a["correct"])
    accuracy = round(100 * correct / len(attempts), 1) if attempts else None
    return {"total_puzzles": total, "solved_today": solved_today,
            "streak_days": streak, "accuracy_pct": accuracy,
            "attempts_total": len(attempts)}
