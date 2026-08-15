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

    weaknesses, prep_positions = _engine_scout(their_games[:ENGINE_PASS_GAMES])
    prep_created = _make_prep_puzzles(rival, prep_positions, h2h)

    report = {
        "generated_at": util.now_iso(),
        "head_to_head": h2h,
        "recent_results": results,
        "repertoire": repertoire,
        "typical_mistakes": weaknesses,
        "prep_puzzles_created": prep_created,
        "advice": _advice(h2h, repertoire, weaknesses),
    }
    with db.tx() as conn:
        conn.execute("UPDATE rivals SET scout_report=?, scouted_at=? WHERE id=?",
                     (json.dumps(report), util.now_iso(), rival_id))
    return report


def _engine_scout(games: list[dict]) -> tuple[list[dict], list[dict]]:
    """Quick engine pass over the rival's games: their blunders + punish spots."""
    engine, real = open_engine()
    motif_counts: dict[str, int] = defaultdict(int)
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
                fen_before = board.fen()
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
    return weaknesses, prep_positions[:20]


def _make_prep_puzzles(rival: dict, prep_positions: list[dict], h2h: dict) -> int:
    created = 0
    with db.tx() as conn:
        # from scouting: punish the rival's typical mistakes
        for i, p in enumerate(prep_positions):
            solution = p["solution"]
            if len(solution) % 2 == 0:
                solution = solution[:-1] or p["solution"][:1]
            cur = conn.execute(
                """INSERT OR IGNORE INTO puzzles
                   (source, game_id, ply, fen, solution, themes, phase, difficulty,
                    rival, explanation, created_at, due_at)
                   VALUES ('rival_prep', NULL, ?, ?, ?, ?, NULL, 3, ?, ?, ?, ?)""",
                (i, p["fen"], json.dumps(solution), json.dumps(p["themes"]),
                 rival["name"], p["note"], util.now_iso(), util.now_iso()))
            created += cur.rowcount
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
