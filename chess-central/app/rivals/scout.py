"""Rival preparation — real tournament prep, scaled to scholastic chess.

For a named rival (with lichess and/or chess.com usernames):
  1. Head-to-head record and critical moments from our own database.
  2. Scout their recent public games: opening repertoire by color, results,
     and — with a quick engine pass — the mistakes they habitually make.
  3. Prep puzzles: positions from head-to-head games, plus positions where
     the rival blundered against others and Nirvaan gets to find the punish
     (playing the side the rival's opponent had).
"""
from __future__ import annotations

import json
from collections import defaultdict

import chess

from .. import config, db, util
from ..analysis.engine import open_engine
from ..sync import chesscom, lichess

SCOUT_GAMES_PER_PLATFORM = 60
ENGINE_PASS_GAMES = 12         # deep-ish pass only on the most recent few
PREP_MIN_DROP = 22


def head_to_head(rival: dict) -> dict:
    names = {n.lower() for n in
             [rival.get("lichess_username"), rival.get("chesscom_username"), rival.get("name")]
             if n}
    if not names:
        return {"games": []}
    placeholders = ",".join("?" for _ in names)
    games = db.rows(
        f"SELECT * FROM games WHERE LOWER(opponent_name) IN ({placeholders}) "
        "ORDER BY played_at DESC", tuple(names))
    w = sum(1 for g in games if g["result"] == "win")
    l = sum(1 for g in games if g["result"] == "loss")
    return {
        "games": [{k: g[k] for k in ("id", "url", "played_at", "color", "result",
                                     "opening_name", "time_class")} for g in games],
        "wins": w, "losses": l, "draws": len(games) - w - l,
    }


def fetch_rival_games(rival: dict) -> list[dict]:
    """Recent public games (raw PGN + meta) from both platforms."""
    out = []
    lu = rival.get("lichess_username")
    cu = rival.get("chesscom_username")
    if lu:
        try:
            for g in lichess.fetch_games(lu, max_games=SCOUT_GAMES_PER_PLATFORM):
                rec = lichess.game_to_record(g, lu)
                if rec:
                    rec["scout_side"] = rec.pop("color")
                    out.append(rec)
        except Exception:
            pass
    if cu:
        try:
            archives = chesscom.fetch_archives(cu)[-3:]
            for url in archives:
                for g in chesscom.fetch_archive(url):
                    rec = chesscom.game_to_record(g, cu)
                    if rec:
                        rec["scout_side"] = rec.pop("color")
                        out.append(rec)
        except Exception:
            pass
    out.sort(key=lambda r: r["played_at"], reverse=True)
    return out[: 2 * SCOUT_GAMES_PER_PLATFORM]


def build_report(rival_id: int) -> dict:
    rival = db.row("SELECT * FROM rivals WHERE id=?", (rival_id,))
    if not rival:
        raise ValueError("no such rival")

    h2h = head_to_head(rival)
    their_games = fetch_rival_games(rival)

    # opening repertoire by color
    rep: dict[str, dict] = {"white": defaultdict(lambda: {"n": 0, "w": 0}),
                            "black": defaultdict(lambda: {"n": 0, "w": 0})}
    for g in their_games:
        fam = util.opening_family(g.get("eco"), g.get("opening_name"))
        side = g["scout_side"]
        rep[side][fam]["n"] += 1
        if g["result"] == "win":
            rep[side][fam]["w"] += 1
    repertoire = {
        side: sorted(
            [{"opening": fam, **v, "win_pct": round(100 * v["w"] / v["n"])}
             for fam, v in fams.items() if v["n"] >= 2],
            key=lambda x: -x["n"])[:6]
        for side, fams in rep.items()
    }

    results = {"n": len(their_games),
               "wins": sum(1 for g in their_games if g["result"] == "win"),
               "losses": sum(1 for g in their_games if g["result"] == "loss")}

    weaknesses, prep_positions, extra = _engine_scout(their_games[:ENGINE_PASS_GAMES])
    weapons = extra["weapons"]
    prep_created = _make_prep_puzzles(rival, prep_positions, h2h)
    patterns = _style_patterns(their_games)
    findings = _findings_english(rival["name"], weapons, weaknesses, patterns,
                                 repertoire, h2h)
    plan = _learn_plan(rival["name"], weapons, weaknesses, repertoire, h2h)

    report = {
        "generated_at": util.now_iso(),
        "head_to_head": h2h,
        "recent_results": results,
        "repertoire": repertoire,
        "typical_mistakes": weaknesses,
        "weapons": weapons,
        "style_patterns": patterns,
        "findings": findings,
        "learn_plan": plan,
        "prep_puzzles_created": prep_created,
        "advice": _advice(h2h, repertoire, weaknesses),
    }
    with db.tx() as conn:
        conn.execute("UPDATE rivals SET scout_report=?, scouted_at=? WHERE id=?",
                     (json.dumps(report), util.now_iso(), rival_id))
    return report


def _engine_scout(games: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """Engine pass over the rival's games.

    Returns (their_mistake_motifs, punish_positions, their_weapon_motifs) —
    weapons are tactics the rival successfully lands (forks, mates, material
    wins), the things Nirvaan must defend against.
    """
    engine, real = open_engine()
    motif_counts: dict[str, int] = defaultdict(int)
    weapon_counts: dict[str, int] = defaultdict(int)
    prep_positions: list[dict] = []
    try:
        for g in games:
            game = util.parse_pgn_game(g["pgn"])
            if game is None:
                continue
            rival_color = chess.WHITE if g["scout_side"] == "white" else chess.BLACK
            board = game.board()
            prev = engine.evaluate(board, movetime_ms=120)
            for node in game.mainline():
                move = node.move
                mover = board.turn
                if mover == rival_color:
                    # positive motifs need only the pre-move board
                    from ..analysis.motifs import tags_for_good_move
                    for t in tags_for_good_move(board, move):
                        weapon_counts[t] += 1
                wp_before = util.win_prob(prev.cp, mover == chess.WHITE)
                board.push(move)
                if board.is_game_over():
                    break
                cur = engine.evaluate(board, movetime_ms=120)
                wp_after = util.win_prob(cur.cp, mover == chess.WHITE)
                drop = wp_before - wp_after
                if mover == rival_color and drop >= PREP_MIN_DROP:
                    from ..analysis.motifs import tags_for_mistake
                    board.pop()
                    tags = tags_for_mistake(board, move, prev.best, prev.cp, cur.cp, prev.mate_in)
                    board.push(move)
                    for t in tags:
                        motif_counts[t] += 1
                    # the punish position: opponent (Nirvaan's side) to move after
                    # the rival's mistake — engine best is the refutation
                    refute = engine.evaluate(board, movetime_ms=200)
                    if refute.best:
                        prep_positions.append({
                            "fen": board.fen(),
                            "solution": [m.uci() for m in refute.pv[:3]] or [refute.best.uci()],
                            "themes": tags,
                            "note": f"{'White' if mover==chess.WHITE else 'Black'} "
                                    f"just played {node.san() if hasattr(node,'san') else move.uci()} — punish it!",
                        })
                prev = cur
    finally:
        engine.close()
    from ..analysis.motifs import MOTIF_LABELS
    weaknesses = [
        {"motif": t, "label": MOTIF_LABELS.get(t, t), "count": n}
        for t, n in sorted(motif_counts.items(), key=lambda kv: -kv[1])[:5]
    ]
    weapons = [
        {"motif": t, "label": MOTIF_LABELS.get(t, t), "count": n}
        for t, n in sorted(weapon_counts.items(), key=lambda kv: -kv[1])[:5]
    ]
    return weaknesses, prep_positions[:20], {"weapons": weapons}


def _make_prep_puzzles(rival: dict, prep_positions: list[dict], h2h: dict) -> int:
    created = 0
    with db.tx() as conn:
        # from scouting: punish the rival's typical mistakes.
        # dedupe on (rival, fen) — the unique index doesn't apply to NULL game_id
        for i, p in enumerate(prep_positions):
            exists = conn.execute(
                "SELECT 1 FROM puzzles WHERE source='rival_prep' AND rival=? AND fen=?",
                (rival["name"], p["fen"])).fetchone()
            if exists:
                continue
            solution = p["solution"]
            if len(solution) % 2 == 0:
                solution = solution[:-1] or p["solution"][:1]
            conn.execute(
                """INSERT INTO puzzles
                   (source, game_id, ply, fen, solution, themes, phase, difficulty,
                    rival, explanation, created_at, due_at)
                   VALUES ('rival_prep', NULL, ?, ?, ?, ?, NULL, 3, ?, ?, ?, ?)""",
                (i, p["fen"], json.dumps(solution), json.dumps(p["themes"]),
                 rival["name"], p["note"], util.now_iso(), util.now_iso()))
            created += 1
    # from head-to-head games: their critical moments already have puzzles via
    # the normal pipeline; tag them with the rival name so they surface in prep
    ids = [g["id"] for g in h2h.get("games", [])]
    if ids:
        placeholders = ",".join("?" for _ in ids)
        with db.tx() as conn:
            conn.execute(
                f"UPDATE puzzles SET rival=? WHERE game_id IN ({placeholders}) "
                "AND source != 'rival_prep'",
                (rival["name"], *ids))
    return created


def _style_patterns(their_games: list[dict]) -> dict:
    """Cheap style stats from game metadata — no engine needed."""
    if not their_games:
        return {}
    wins = [g for g in their_games if g["result"] == "win"]
    losses = [g for g in their_games if g["result"] == "loss"]
    mate_terms = {"mate", "checkmated"}
    time_terms = {"outoftime", "timeout", "time"}
    lengths = [g["moves_count"] for g in their_games if g.get("moves_count")]
    return {
        "games_seen": len(their_games),
        "win_pct": round(100 * len(wins) / len(their_games)),
        "wins_by_mate_pct": round(100 * sum(
            1 for g in wins if (g.get("termination") or "") in mate_terms) / len(wins)) if wins else 0,
        "losses_on_time_pct": round(100 * sum(
            1 for g in losses if (g.get("termination") or "") in time_terms) / len(losses)) if losses else 0,
        "avg_game_moves": round(sum(lengths) / (2 * len(lengths))) if lengths else None,
        "white_score_pct": _score_pct(their_games, "white"),
        "black_score_pct": _score_pct(their_games, "black"),
    }


def _score_pct(games: list[dict], side: str) -> int | None:
    gs = [g for g in games if g["scout_side"] == side]
    if not gs:
        return None
    pts = sum(1 if g["result"] == "win" else 0.5 if g["result"] == "draw" else 0 for g in gs)
    return round(100 * pts / len(gs))


# Plain-English templates: what the tactic means and what Nirvaan should do.
WEAPON_ENGLISH = {
    "played_fork": ("Forks are their favorite weapon",
                    "they land knight/pawn forks hitting two pieces at once. "
                    "Keep pieces defended and off squares a knight can hit together."),
    "won_material": ("They punish free pieces",
                     "when a piece is left hanging, they take it. No freebies — "
                     "check every piece is safe before each move."),
    "delivered_mate": ("They hunt the king",
                       "they finish games with checkmate rather than waiting. "
                       "King safety first: castle early, keep defenders home."),
    "back_rank_mate_win": ("Back-rank mates are in their toolkit",
                           "they exploit a trapped king on the back rank. "
                           "Make an escape square (h3/h6) once rooks come off."),
}
WEAKNESS_ENGLISH = {
    "moved_en_prise": ("They move pieces to unsafe squares",
                       "when attacking, they park pieces where they can be taken. "
                       "After every move they make, ask: can I just capture that?"),
    "left_piece_hanging": ("They forget their own pieces",
                           "mid-plan, their other pieces go undefended. Scan their "
                           "whole army each turn — free material is often sitting there."),
    "missed_capture": ("They miss free captures",
                       "material left in reach goes untaken — so an escape often "
                       "goes unpunished, but don't rely on it."),
    "missed_fork": ("They miss forks",
                    "fork chances go unplayed — but don't leave them available."),
    "missed_mate": ("They miss checkmates",
                    "they've let winning attacks slip. Defending stubbornly pays "
                    "off against them — never resign early."),
    "allowed_fork": ("They walk into forks",
                     "their pieces end up on forkable squares. Knight-fork drills "
                     "will pay off directly in this matchup."),
    "got_mated": ("Their king gets caught",
                  "they neglect king safety under pressure. A direct, safe attack "
                  "often works."),
    "back_rank": ("Weak back rank",
                  "they leave the back rank undefended — rook to the open file late "
                  "in the game and look for the classic mate."),
    "missed_check_tactic": ("They miss checking tactics",
                            "forcing checks surprise them — look at every check."),
}


def _findings_english(name: str, weapons: list[dict], weaknesses: list[dict],
                      patterns: dict, repertoire: dict, h2h: dict) -> list[dict]:
    """The scout report in plain English: weapons, weaknesses, other patterns."""
    out: list[dict] = []
    for w in weapons[:3]:
        title, detail = WEAPON_ENGLISH.get(
            w["motif"], (w["label"], "seen repeatedly in their games."))
        out.append({"kind": "weapon", "title": title,
                    "detail": f"{detail} (seen {w['count']}× in scouted games)",
                    "motif": w["motif"], "count": w["count"]})
    for w in weaknesses[:3]:
        title, detail = WEAKNESS_ENGLISH.get(
            w["motif"], (w["label"], "shows up repeatedly in their games."))
        out.append({"kind": "weakness", "title": title,
                    "detail": f"{detail} (seen {w['count']}× in scouted games)",
                    "motif": w["motif"], "count": w["count"]})

    p = patterns or {}
    if p.get("games_seen"):
        if p.get("wins_by_mate_pct", 0) >= 50:
            out.append({"kind": "pattern", "title": "They play for checkmate",
                        "detail": f"{p['wins_by_mate_pct']}% of their wins end in mate — "
                                  "an attacker. Expect pressure on the king; trade "
                                  "attackers off and they run out of ideas."})
        if p.get("losses_on_time_pct", 0) >= 25:
            out.append({"kind": "pattern", "title": "They get in time trouble",
                        "detail": f"{p['losses_on_time_pct']}% of their losses are on time. "
                                  "In a level position, playing solidly and quickly "
                                  "puts the clock on Nirvaan's side."})
        if p.get("avg_game_moves") and p["avg_game_moves"] <= 25:
            out.append({"kind": "pattern", "title": "Short, sharp games",
                        "detail": f"Their games average ~{p['avg_game_moves']} moves — they go "
                                  "for quick knockouts. Survive the opening carefully and "
                                  "the game tilts toward Nirvaan."})
        ws, bs = p.get("white_score_pct"), p.get("black_score_pct")
        if ws is not None and bs is not None and ws - bs >= 15:
            out.append({"kind": "pattern", "title": "Much stronger with White",
                        "detail": f"They score {ws}% as White but only {bs}% as Black. "
                                  "The game where Nirvaan has White is the one to press."})
    for side in ("white", "black"):
        top = (repertoire.get(side) or [])
        if top:
            t = top[0]
            out.append({"kind": "pattern",
                        "title": f"As {side}: {t['opening']}",
                        "detail": f"Their go-to as {side} — played {t['n']}× "
                                  f"({t['win_pct']}% wins). Prepare a line against it "
                                  "so the first 6 moves are automatic."})
    if h2h.get("games"):
        out.append({"kind": "pattern", "title": "Head-to-head",
                    "detail": f"{h2h['wins']}W-{h2h['losses']}L-{h2h['draws']}D against them. "
                              + ("Review the losses — same mistake twice is the thing "
                                 "to avoid." if h2h["losses"] else "Nirvaan owns this matchup — "
                                 "play his game, not theirs.")})
    return out


def _learn_plan(name: str, weapons: list[dict], weaknesses: list[dict],
                repertoire: dict, h2h: dict) -> list[dict]:
    """Ordered training plan; puzzle steps carry a theme filter for the trainer."""
    plan: list[dict] = []

    def puzzles_ready(theme: str | None) -> int:
        if theme:
            return db.scalar(
                "SELECT COUNT(*) FROM puzzles WHERE rival=? AND retired=0 AND themes LIKE ?",
                (name, f'%"{theme}"%')) or 0
        return db.scalar(
            "SELECT COUNT(*) FROM puzzles WHERE rival=? AND retired=0", (name,)) or 0

    top_white = (repertoire.get("white") or [{}])[0].get("opening")
    top_black = (repertoire.get("black") or [{}])[0].get("opening")
    if top_white or top_black:
        lines = [f"vs their White: know your setup against the {top_white}" if top_white else None,
                 f"with White: they usually answer with the {top_black}" if top_black else None]
        plan.append({"step": "Openings", "title": "Lock in the first 6 moves",
                     "detail": "; ".join(x for x in lines if x) + ".",
                     "action": None})

    for wk in weaknesses[:2]:
        theme = wk["motif"]
        n = puzzles_ready(theme)
        title, _ = WEAKNESS_ENGLISH.get(theme, (wk["label"], ""))
        plan.append({"step": "Punish drill",
                     "title": f"Exploit: {title.lower()}",
                     "detail": f"Puzzles built from real positions where they made this "
                               f"exact mistake — train the punishment until it's instant.",
                     "action": {"type": "puzzles", "theme": theme},
                     "ready": n})
    total = puzzles_ready(None)
    if total:
        plan.append({"step": "Full prep set",
                     "title": f"All {total} prep puzzles vs {name}",
                     "detail": "Mixed set from head-to-head games and their scouted habits.",
                     "action": {"type": "puzzles", "theme": None},
                     "ready": total})

    for wp in weapons[:1]:
        title, detail = WEAPON_ENGLISH.get(wp["motif"], (wp["label"], ""))
        plan.append({"step": "Defense drill",
                     "title": f"Defend against: {title.lower()}",
                     "detail": f"Their main weapon. {detail}",
                     "action": None})

    if h2h.get("losses"):
        plan.append({"step": "Review",
                     "title": f"Re-watch the {h2h['losses']} head-to-head loss"
                              + ("es" if h2h["losses"] != 1 else ""),
                     "detail": "Open each loss in Games and find the one moment it turned.",
                     "action": {"type": "games"}})
    plan.append({"step": "Game day",
                 "title": "Read the pep talk before the round",
                 "detail": "Generate it with the ✨ Pep talk button once scouting is done.",
                 "action": None})
    return plan


def _advice(h2h: dict, repertoire: dict, weaknesses: list[dict]) -> list[str]:
    advice = []
    n = len(h2h.get("games", []))
    if n:
        advice.append(
            f"Head-to-head: {h2h['wins']}W-{h2h['losses']}L-{h2h['draws']}D "
            f"over {n} games. Review the losses in the Games tab before the rematch.")
    for side_label, side in (("With White they open", "white"), ("With Black they answer", "black")):
        top = (repertoire.get(side) or [])
        if top:
            names = ", ".join(f"{t['opening']} ({t['n']}x)" for t in top[:2])
            advice.append(f"{side_label}: {names}. Prepare your line against these.")
    if weaknesses:
        advice.append(
            f"Their most common lapse: {weaknesses[0]['label'].lower()} — "
            "the prep puzzles drill exactly how to punish it.")
    if not advice:
        advice.append("No public data found yet — add their lichess/chess.com usernames.")
    return advice
