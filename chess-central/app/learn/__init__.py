"""The Academy: a structured curriculum with verified positions and drills.

Content lives in curriculum.json (compiled and engine-validated at build
time; the test suite re-validates every position and claim on every run).
Progress is tracked per lesson in the learn_progress table.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import db, util

CURRICULUM_PATH = Path(__file__).resolve().parent / "curriculum.json"

# Stable chess.com destinations per track — Nirvaan has Premium, so these
# unlock full courses/drills when he's signed in.
CHESSCOM_LINKS = {
    "vision": ("Vision trainer", "https://www.chess.com/vision"),
    "mates": ("Checkmate puzzles", "https://www.chess.com/puzzles"),
    "tactics": ("Rated puzzles", "https://www.chess.com/puzzles/rated"),
    "defense": ("Puzzle practice", "https://www.chess.com/puzzles"),
    "openings": ("Openings explorer", "https://www.chess.com/openings"),
    "endgames": ("Endgame drills", "https://www.chess.com/endgames"),
    "strategy": ("Lessons library", "https://www.chess.com/lessons"),
    "habits": ("Lessons library", "https://www.chess.com/lessons"),
}

# Where the Academy teaches each mistake pattern the engine flags —
# so every recurring mistake links straight to the lesson that fixes it.
MOTIF_LESSONS = {
    "missed_mate": "mates-in-one-gallery",
    "missed_capture": "vision-en-prise",
    "missed_fork": "tactics-knight-forks",
    "missed_check_tactic": "vision-cct-scan",
    "moved_en_prise": "vision-is-it-safe",
    "left_piece_hanging": "vision-loose-pieces",
    "allowed_fork": "defense-escape-early",
    "got_mated": "defense-four-answers",
    "back_rank": "mates-back-rank",
    "repertoire": "openings-three-rules",
}

_cache: list | None = None


def curriculum() -> list[dict]:
    global _cache
    if _cache is None:
        if CURRICULUM_PATH.exists():
            _cache = json.loads(CURRICULUM_PATH.read_text())
        else:
            _cache = []
    return _cache


def overview() -> dict:
    """Track list with per-lesson progress and a recommended level."""
    done = {r["lesson_id"]: r for r in db.rows("SELECT * FROM learn_progress")}
    from ..coach.roadmap import current_stage
    stage_idx = current_stage()["index"]
    rec_level = 1 if stage_idx <= 1 else 2 if stage_idx <= 3 else 3

    tracks = []
    for t in curriculum():
        lessons = []
        for l in t["lessons"]:
            p = done.get(l["id"])
            lessons.append({
                "id": l["id"], "title": l["title"], "level": l["level"],
                "examples": len(l.get("examples", [])),
                "drills": len(l.get("drills", [])),
                "done": bool(p),
                "score": f"{p['correct']}/{p['total']}" if p and p["total"] else None,
            })
        label, url = CHESSCOM_LINKS.get(t["id"], (None, None))
        tracks.append({
            "id": t["id"], "title": t["title"], "emoji": t.get("emoji", "📘"),
            "blurb": t.get("blurb", ""),
            "lessons": lessons,
            "done": sum(1 for l in lessons if l["done"]),
            "total": len(lessons),
            "chesscom": {"label": label, "url": url} if url else None,
        })
    return {"tracks": tracks, "recommended_level": rec_level,
            "lessons_done": len(done),
            "lessons_total": sum(t["total"] for t in tracks)}


def lesson(lesson_id: str) -> dict | None:
    for t in curriculum():
        for l in t["lessons"]:
            if l["id"] == lesson_id:
                label, url = CHESSCOM_LINKS.get(t["id"], (None, None))
                p = db.row("SELECT * FROM learn_progress WHERE lesson_id=?", (lesson_id,))
                return {**l, "track_id": t["id"], "track_title": t["title"],
                        "track_emoji": t.get("emoji", ""),
                        "chesscom": {"label": label, "url": url} if url else None,
                        "done": bool(p)}
    return None


def motif_map() -> dict:
    """motif tag -> {lesson_id, title, track_title} for every mapped lesson
    that actually exists in the installed curriculum."""
    out = {}
    for tag, lesson_id in MOTIF_LESSONS.items():
        l = lesson(lesson_id)
        if l:
            out[tag] = {"lesson_id": lesson_id, "title": l["title"],
                        "track_title": l["track_title"]}
    return out


def complete(lesson_id: str, correct: int = 0, total: int = 0) -> None:
    if lesson(lesson_id) is None:
        raise ValueError("unknown lesson")
    with db.tx() as conn:
        conn.execute(
            """INSERT INTO learn_progress (lesson_id, completed_at, correct, total)
               VALUES (?,?,?,?)
               ON CONFLICT(lesson_id) DO UPDATE SET
                 completed_at=excluded.completed_at,
                 correct=MAX(learn_progress.correct, excluded.correct),
                 total=excluded.total""",
            (lesson_id, util.now_iso(), correct, total))
