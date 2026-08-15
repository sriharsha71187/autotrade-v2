"""The coach's brain: turns analyzed games into ranked strengths,
weaknesses, and opportunities with concrete evidence.

Every rule needs a minimum sample before it speaks, and every insight
carries the numbers behind it so a parent can see why the coach said it.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .. import db, util
from ..analysis.motifs import MOTIF_LABELS

MIN_GAMES = 8            # global gate before insights generate at all
MIN_PHASE_MOVES = 60
MIN_OPENING_GAMES = 5


def regenerate() -> list[dict]:
    out: list[dict] = []
    n_games = db.scalar("SELECT COUNT(*) FROM games WHERE analyzed_at IS NOT NULL") or 0
    if n_games >= MIN_GAMES:
        out += _phase_insights()
        out += _motif_insights()
        out += _opening_insights()
        out += _color_insights()
        out += _time_insights()
        out += _conversion_insights()
        out += _opponent_strength_insights()
        out += _tilt_insights()
    out += _trend_insights()

    with db.tx() as conn:
        conn.execute("DELETE FROM insights")
        for ins in out:
            conn.execute(
                """INSERT OR REPLACE INTO insights
                   (kind, key, title, detail, evidence, score, generated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (ins["kind"], ins["key"], ins["title"], ins["detail"],
                 json.dumps(ins.get("evidence", {})), ins.get("score", 0),
                 util.now_iso()))
    return out


def current() -> list[dict]:
    rows = db.rows("SELECT * FROM insights ORDER BY score DESC")
    for r in rows:
        r["evidence"] = json.loads(r["evidence"] or "{}")
    return rows


def _mk(kind, key, title, detail, evidence=None, score=0.0) -> dict:
    return {"kind": kind, "key": key, "title": title, "detail": detail,
            "evidence": evidence or {}, "score": score}


# ---------------------------------------------------------------- rules

def _phase_insights() -> list[dict]:
    rows = db.rows(
        """SELECT phase,
                  COUNT(*) n,
                  AVG(MAX(winprob_before - winprob_after, 0)) avg_loss,
                  SUM(classification='blunder') blunders
           FROM moves WHERE mover='player' GROUP BY phase""")
    rows = [r for r in rows if r["n"] >= MIN_PHASE_MOVES]
    if len(rows) < 2:
        return []
    out = []
    ranked = sorted(rows, key=lambda r: r["avg_loss"])
    best, worst = ranked[0], ranked[-1]
    if worst["avg_loss"] > best["avg_loss"] * 1.4:
        blunder_rate = round(100 * worst["blunders"] / worst["n"], 1)
        out.append(_mk(
            "weakness", f"phase_{worst['phase']}",
            f"The {worst['phase']} is where points leak",
            f"Average win-chance lost per {worst['phase']} move is "
            f"{worst['avg_loss']:.1f}% — vs {best['avg_loss']:.1f}% in the "
            f"{best['phase']}. Blunder rate there: {blunder_rate}% of moves. "
            f"Focused {worst['phase']} training will pay off fastest.",
            {r["phase"]: {"moves": r["n"], "avg_winprob_loss": round(r["avg_loss"], 2),
                          "blunders": r["blunders"]} for r in rows},
            score=90))
        out.append(_mk(
            "strength", f"phase_strength_{best['phase']}",
            f"Solid {best['phase']} play",
            f"His most accurate phase: only {best['avg_loss']:.1f}% win-chance "
            f"lost per move across {best['n']} moves.",
            score=55))
    return out


def _motif_insights() -> list[dict]:
    rows = db.rows(
        """SELECT m.motifs FROM moves m
           WHERE m.mover='player' AND m.classification IN ('mistake','blunder')""")
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        for t in json.loads(r["motifs"] or "[]"):
            counts[t] += 1
    total_mistakes = len(rows)
    out = []
    negative = {k: v for k, v in counts.items()
                if not k.startswith(("won_", "played_", "delivered_", "back_rank_mate"))}
    for i, (tag, n) in enumerate(sorted(negative.items(), key=lambda kv: -kv[1])[:3]):
        if n < 5:
            continue
        pct = round(100 * n / max(1, total_mistakes))
        out.append(_mk(
            "weakness", f"motif_{tag}",
            MOTIF_LABELS.get(tag, tag),
            f"Appears in {n} of his {total_mistakes} mistakes ({pct}%). "
            f"His puzzle queue is already weighted toward this pattern.",
            {"count": n, "of_mistakes": total_mistakes},
            score=80 - i * 10))
    # positive motifs
    good = db.rows(
        "SELECT motifs FROM moves WHERE mover='player' AND classification='best'")
    gcounts: dict[str, int] = defaultdict(int)
    for r in good:
        for t in json.loads(r["motifs"] or "[]"):
            gcounts[t] += 1
    for tag in ("played_fork", "won_material", "delivered_mate"):
        if gcounts.get(tag, 0) >= 5:
            out.append(_mk(
                "strength", f"motif_good_{tag}", MOTIF_LABELS.get(tag, tag),
                f"He has done this {gcounts[tag]} times when it was the best move — "
                "a real pattern he's spotting.",
                {"count": gcounts[tag]}, score=50))
    return out


def _opening_insights() -> list[dict]:
    games = db.rows(
        """SELECT eco, opening_name, color, result FROM games
           WHERE analyzed_at IS NOT NULL AND opening_name IS NOT NULL""")
    fams: dict[tuple, dict] = defaultdict(lambda: {"n": 0, "w": 0, "l": 0})
    for g in games:
        fam = util.opening_family(g["eco"], g["opening_name"])
        k = (fam, g["color"])
        fams[k]["n"] += 1
        if g["result"] == "win":
            fams[k]["w"] += 1
        elif g["result"] == "loss":
            fams[k]["l"] += 1
    out = []
    scored = [(k, v, (v["w"] + 0.5 * (v["n"] - v["w"] - v["l"])) / v["n"])
              for k, v in fams.items() if v["n"] >= MIN_OPENING_GAMES]
    if not scored:
        return out
    scored.sort(key=lambda t: t[2])
    (fam, color), v, pct = scored[0]
    if pct < 0.45:
        out.append(_mk(
            "opportunity", f"opening_fix_{fam}_{color}",
            f"Repertoire gap: {fam} as {color}",
            f"Scoring {pct:.0%} over {v['n']} games ({v['w']}W {v['l']}L). "
            f"Either study the main ideas of this line or switch to a simpler "
            f"system as {color}.",
            {"family": fam, "color": color, **v, "score_pct": round(100 * pct)},
            score=70))
    (fam, color), v, pct = scored[-1]
    if pct > 0.6:
        out.append(_mk(
            "strength", f"opening_best_{fam}_{color}",
            f"{fam} as {color} is working",
            f"Scoring {pct:.0%} over {v['n']} games. Keep playing it and "
            f"deepen it a few moves further.",
            {"family": fam, "color": color, **v}, score=45))
    return out


def _color_insights() -> list[dict]:
    rows = db.rows(
        """SELECT color, COUNT(*) n, SUM(result='win') w, SUM(result='loss') l
           FROM games GROUP BY color""")
    if len(rows) < 2 or any(r["n"] < 10 for r in rows):
        return []
    by = {r["color"]: r for r in rows}
    sw = (by["white"]["w"] + 0.5 * (by["white"]["n"] - by["white"]["w"] - by["white"]["l"])) / by["white"]["n"]
    sb = (by["black"]["w"] + 0.5 * (by["black"]["n"] - by["black"]["w"] - by["black"]["l"])) / by["black"]["n"]
    gap = abs(sw - sb)
    if gap >= 0.12:
        weaker = "black" if sb < sw else "white"
        return [_mk(
            "weakness", "color_gap",
            f"Much weaker with {weaker}",
            f"White score {sw:.0%} vs Black score {sb:.0%}. His {weaker} "
            f"repertoire needs a reliable setup he actually understands.",
            {"white_score": round(100 * sw), "black_score": round(100 * sb)},
            score=65)]
    return []


def _time_insights() -> list[dict]:
    out = []
    flagged = db.scalar(
        """SELECT COUNT(*) FROM games WHERE result='loss'
           AND termination IN ('outoftime','timeout','time')""") or 0
    losses = db.scalar("SELECT COUNT(*) FROM games WHERE result='loss'") or 0
    if losses >= 10 and flagged / losses >= 0.2:
        out.append(_mk(
            "weakness", "time_losses",
            "Losing on the clock",
            f"{flagged} of {losses} losses ({100 * flagged // losses}%) were on time. "
            "Practice a steady pace: same time control in training, and a rule of "
            "thumb like 'never under 30 seconds before move 20'.",
            {"time_losses": flagged, "total_losses": losses}, score=75))
    fast = db.rows(
        """SELECT COUNT(*) n, SUM(classification IN ('mistake','blunder')) bad
           FROM moves WHERE mover='player' AND move_time IS NOT NULL AND move_time < 2""")
    if fast and fast[0]["n"] and fast[0]["n"] >= 50:
        rate = fast[0]["bad"] / fast[0]["n"]
        if rate >= 0.15:
            out.append(_mk(
                "weakness", "fast_moves",
                "Fast moves are costing games",
                f"On moves played in under 2 seconds, {rate:.0%} are mistakes or "
                "blunders. One habit fixes this: sit on your hands, check checks, "
                "captures and threats before every move.",
                {"fast_moves": fast[0]["n"], "bad": fast[0]["bad"]}, score=78))
    return out


def _conversion_insights() -> list[dict]:
    """Did winning positions (+3 or better) convert to wins?"""
    rows = db.rows(
        """SELECT g.id, g.result, MAX(CASE WHEN m.mover='player'
                    THEN CASE WHEN g.color='white' THEN m.eval_before
                              ELSE -m.eval_before END END) AS best_eval
           FROM games g JOIN moves m ON m.game_id = g.id
           WHERE g.analyzed_at IS NOT NULL GROUP BY g.id""")
    winning = [r for r in rows if (r["best_eval"] or 0) >= 300]
    if len(winning) < 8:
        return []
    converted = sum(1 for r in winning if r["result"] == "win")
    rate = converted / len(winning)
    if rate < 0.7:
        return [_mk(
            "weakness", "conversion",
            "Winning positions slipping away",
            f"He reached a clearly winning position (+3 or more) in "
            f"{len(winning)} games but only won {converted} ({rate:.0%}). "
            "Conversion technique: trade pieces when ahead, keep pawns safe, "
            "watch for counterplay. His puzzle queue includes these moments.",
            {"winning_positions": len(winning), "converted": converted},
            score=85)]
    return [_mk(
        "strength", "conversion_ok",
        "Closes out winning games",
        f"Converted {converted} of {len(winning)} clearly winning positions "
        f"({rate:.0%}) — good technique for his level.",
        score=50)]


def _opponent_strength_insights() -> list[dict]:
    rows = db.rows(
        """SELECT (opponent_rating - player_rating) diff, result FROM games
           WHERE opponent_rating IS NOT NULL AND player_rating IS NOT NULL""")
    if len(rows) < 20:
        return []
    up = [r for r in rows if r["diff"] >= 100]
    down = [r for r in rows if r["diff"] <= -100]
    out = []
    if len(down) >= 10:
        lost_down = sum(1 for r in down if r["result"] == "loss")
        if lost_down / len(down) >= 0.3:
            out.append(_mk(
                "weakness", "vs_lower",
                "Dropping points to lower-rated players",
                f"Lost {lost_down} of {len(down)} games against players rated "
                "100+ below him. Usually a focus issue: treat every opponent "
                "like a rival, play the board not the rating.",
                {"games": len(down), "losses": lost_down}, score=60))
    if len(up) >= 10:
        beat_up = sum(1 for r in up if r["result"] == "win")
        if beat_up / len(up) >= 0.35:
            out.append(_mk(
                "strength", "giant_slayer",
                "Punches above his rating",
                f"Beat {beat_up} of {len(up)} opponents rated 100+ above him — "
                "a great sign; his rating has room to run.",
                {"games": len(up), "wins": beat_up}, score=52))
    return out


def _tilt_insights() -> list[dict]:
    games = db.rows(
        "SELECT played_at, result FROM games ORDER BY played_at DESC LIMIT 300")
    by_day: dict[str, list] = defaultdict(list)
    for g in games:
        by_day[g["played_at"][:10]].append(g["result"])
    tilt_days = 0
    for day, results in by_day.items():
        chron = list(reversed(results))
        run = best_run = 0
        for r in chron:
            run = run + 1 if r == "loss" else 0
            best_run = max(best_run, run)
        if best_run >= 4:
            tilt_days += 1
    if tilt_days >= 3:
        return [_mk(
            "opportunity", "tilt",
            "Long losing streaks in single sessions",
            f"{tilt_days} days show 4+ losses in a row. A house rule helps: "
            "after 2 losses in a row, stop and do 5 puzzles instead. "
            "Protects both rating and love of the game.",
            {"tilt_days": tilt_days}, score=58)]
    return []


def _trend_insights() -> list[dict]:
    out = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=45)).strftime("%Y-%m-%d")
    for source in ("lichess_rapid", "chesscom_rapid", "nwsrs", "uscf_regular"):
        pts = db.rows(
            "SELECT date, rating FROM ratings_history WHERE source=? AND date>=? ORDER BY date",
            (source, cutoff))
        if len(pts) >= 2:
            delta = pts[-1]["rating"] - pts[0]["rating"]
            if delta >= 50:
                out.append(_mk(
                    "strength", f"trend_{source}",
                    f"{source.replace('_', ' ').title()} climbing",
                    f"Up {delta} points in the last ~6 weeks "
                    f"({pts[0]['rating']} → {pts[-1]['rating']}). Momentum!",
                    {"from": pts[0]["rating"], "to": pts[-1]["rating"]}, score=48))
            elif delta <= -50:
                out.append(_mk(
                    "opportunity", f"trend_{source}",
                    f"{source.replace('_', ' ').title()} dipped",
                    f"Down {abs(delta)} points recently ({pts[0]['rating']} → "
                    f"{pts[-1]['rating']}). Normal after a growth spurt — check "
                    "the weakness list and grind the puzzle queue.",
                    score=56))
    return out
