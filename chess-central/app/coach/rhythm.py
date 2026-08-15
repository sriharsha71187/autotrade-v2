"""The weekly rhythm, made executable.

Takes the current roadmap stage's prescribed weekly plan and turns it into a
live checklist: items we can measure are auto-checked from the database, and
anything can be manually ticked. Manual ticks reset each ISO week.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .. import config, db
from . import roadmap


def week_key() -> str:
    y, w, _ = datetime.now(timezone.utc).isocalendar()
    return f"rhythm_{y}_{w:02d}"


def _auto_metric(text: str) -> dict | None:
    """Map a weekly-plan line to a measurable metric, if we have one."""
    t = text.lower()
    nums = [int(n) for n in re.findall(r"\b(\d+)\b", t) if int(n) < 50]

    if "puzzle" in t:
        days = db.scalar(
            """SELECT COUNT(DISTINCT DATE(attempted_at)) FROM puzzle_attempts
               WHERE completed=1 AND attempted_at >= date('now', '-6 days')""") or 0
        return {"label": "days practiced this week", "count": days,
                "target": 5, "done": days >= 5}

    if "review" in t and "loss" in t:
        losses = db.scalar(
            """SELECT COUNT(*) FROM games WHERE result='loss'
               AND played_at >= date('now', '-6 days')""") or 0
        reviewed = db.scalar(
            """SELECT COUNT(*) FROM games WHERE result='loss'
               AND played_at >= date('now', '-6 days') AND reviewed_at IS NOT NULL""") or 0
        return {"label": "losses reviewed", "count": reviewed, "target": losses,
                "done": losses == 0 or reviewed >= losses}

    if "otb" in t or "tournament" in t or "event" in t:
        recent_otb = db.scalar(
            """SELECT COUNT(*) FROM games WHERE platform='otb'
               AND played_at >= date('now', '-30 days')""") or 0
        played_events = db.scalar(
            """SELECT COUNT(*) FROM tournaments WHERE status='played'
               AND fetched_at >= date('now', '-30 days')""") or 0
        n = recent_otb + played_events
        return {"label": "OTB played this month", "count": n, "target": 1,
                "done": n >= 1}

    if "game" in t:
        target = nums[0] if nums else 3
        games = db.scalar(
            "SELECT COUNT(*) FROM games WHERE played_at >= date('now', '-6 days')") or 0
        return {"label": "games this week", "count": games, "target": target,
                "done": games >= target}
    return None


def status() -> dict:
    stage = roadmap.current_stage()["stage"]
    manual = db.kv_get(week_key(), {})
    items = []
    for i, text in enumerate(stage["weekly"]):
        auto = _auto_metric(text)
        checked = bool(manual.get(str(i)))
        items.append({
            "index": i,
            "text": text,
            "auto": auto,
            "manual_checked": checked,
            "done": checked or bool(auto and auto["done"]),
        })
    return {"stage": stage["name"], "week": week_key()[7:],
            "items": items,
            "done": sum(1 for x in items if x["done"]),
            "total": len(items)}


def set_check(index: int, checked: bool) -> None:
    key = week_key()
    manual = db.kv_get(key, {})
    manual[str(index)] = bool(checked)
    db.kv_set(key, manual)
