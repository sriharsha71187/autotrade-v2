"""His actual opening repertoire, mined from his own games.

Three jobs:
  * lines   — what he really plays move by move (not what a book says),
              with results per line.
  * left book — for every game, the ply where the position stops being one
              he has seen before ("his book" = positions reached in 3+ of
              his games). Early exits + bad scores = the repertoire leak.
  * drills  — repeated positions where his habitual move loses win chance
              and the engine knows better, persisted as spaced-repetition
              puzzles (source='repertoire').
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

import chess

from .. import db, util

BOOK_MIN = 3          # position counts as "his book" once seen in 3+ games
MAX_PLIES = 20        # first 10 moves — where a scholastic repertoire lives
LINE_PLIES = 8        # depth of the lines table (4 moves each side)
EARLY_EXIT_PLY = 6    # leaving book by move 3 = "taken out early"
DRILL_MIN_DROP = 8    # avg win-prob loss of his usual move to justify a drill


# PGN replay is the expensive part; a game's moves never change once stored,
# so cache keyed by the PGN text itself — keeps /api/repertoire fast on a
# big history with no invalidation to get wrong.
_paths_cache: dict[tuple, list[str]] = {}


def _paths(game_id: int, pgn: str) -> list[str]:
    """SAN path prefixes for the first MAX_PLIES plies: ['e4', 'e4 e5', ...]."""
    key = (len(pgn), hash(pgn))
    cached = _paths_cache.get(key)
    if cached is not None:
        return cached
    game = util.parse_pgn_game(pgn)
    out: list[str] = []
    if game is not None:
        board = game.board()
        sans = []
        for i, mv in enumerate(game.mainline_moves()):
            if i >= MAX_PLIES:
                break
            try:
                sans.append(board.san(mv))
                board.push(mv)
            except (ValueError, AssertionError):
                break
            out.append(" ".join(sans))
    _paths_cache[key] = out
    return out


def _numbered(path: str) -> str:
    """'e4 e5 Nf3' -> '1.e4 e5 2.Nf3' for display."""
    sans = path.split()
    parts = []
    for i, san in enumerate(sans):
        if i % 2 == 0:
            parts.append(f"{i // 2 + 1}.{san}")
        else:
            parts.append(san)
    return " ".join(parts)


def build(color: str) -> dict:
    games = db.rows(
        """SELECT id, pgn, result, eco, opening_name, played_at, analyzed_at
           FROM games WHERE color=? ORDER BY played_at""", (color,))
    counts: Counter = Counter()
    per_game: dict[int, list[str]] = {}
    for g in games:
        p = _paths(g["id"], g["pgn"])
        per_game[g["id"]] = p
        counts.update(p)

    # --- his lines: deepest well-trodden prefix per game, aggregated
    line_stats: dict[str, dict] = defaultdict(lambda: {"n": 0, "w": 0, "l": 0, "d": 0})
    for g in games:
        p = per_game[g["id"]]
        if not p:
            continue
        key = p[min(LINE_PLIES, len(p)) - 1]
        s = line_stats[key]
        s["n"] += 1
        s["w" if g["result"] == "win" else "l" if g["result"] == "loss" else "d"] += 1
    lines = sorted(
        [{"line": _numbered(k), "plies": len(k.split()), **v,
          "score_pct": round(100 * (v["w"] + 0.5 * v["d"]) / v["n"])}
         for k, v in line_stats.items() if v["n"] >= 2],
        key=lambda x: -x["n"])[:10]

    # --- left-book point per game
    exits: dict[int, int] = {}
    for g in games:
        p = per_game[g["id"]]
        exit_ply = len(p) + 1          # never left book inside the window
        for i, prefix in enumerate(p):
            if counts[prefix] < BOOK_MIN:
                exit_ply = i + 1
                break
        exits[g["id"]] = exit_ply

    n = len(games)
    findings: list[dict] = []
    left_book: dict = {"games": n}
    if n >= BOOK_MIN + 2:
        avg_exit_move = sum((e + 1) // 2 for e in exits.values()) / n
        left_book["avg_exit_move"] = round(avg_exit_move, 1)
        findings.append({
            "kind": "info",
            "text": f"As {color} he is in familiar territory for about the first "
                    f"{avg_exit_move:.0f} moves, on average, across {n} games. "
                    f"After that he is thinking for himself."})

        early = [g for g in games if exits[g["id"]] <= EARLY_EXIT_PLY]
        late = [g for g in games if exits[g["id"]] > EARLY_EXIT_PLY]

        def _score(gs):
            return (sum(1 for g in gs if g["result"] == "win")
                    + 0.5 * sum(1 for g in gs if g["result"] == "draw")) / len(gs)

        if len(early) >= 4 and len(late) >= 4:
            se, sl = _score(early), _score(late)
            left_book["early_exit"] = {"games": len(early), "score_pct": round(100 * se)}
            left_book["deep_book"] = {"games": len(late), "score_pct": round(100 * sl)}
            if sl - se >= 0.15:
                findings.append({
                    "kind": "leak",
                    "text": f"When opponents drag him out of his lines by move 3, he "
                            f"scores {se:.0%} ({len(early)} games) — versus {sl:.0%} when "
                            f"the opening follows his usual paths. Practicing 'what do I "
                            f"do against weird moves' (develop, castle, take the center) "
                            f"is worth more than memorizing deeper lines."})

        # damage right after leaving book, from engine-analyzed games
        # (one query for all games — per-game queries were slow at 1000+ games)
        opening_moves = db.rows(
            """SELECT m.game_id, m.ply, m.winprob_before, m.winprob_after
               FROM moves m JOIN games g ON g.id = m.game_id
               WHERE g.color=? AND g.analyzed_at IS NOT NULL
                 AND m.mover='player' AND m.ply <= ?""",
            (color, MAX_PLIES + 8))
        pre_drops, post_drops = [], []
        for m in opening_moves:
            e = exits.get(m["game_id"])
            if e is None or m["ply"] > e + 7:
                continue
            drop = max(0.0, (m["winprob_before"] or 0) - (m["winprob_after"] or 0))
            (pre_drops if m["ply"] < e else post_drops).append(drop)
        if len(pre_drops) >= 30 and len(post_drops) >= 30:
            pre = sum(pre_drops) / len(pre_drops)
            post = sum(post_drops) / len(post_drops)
            left_book["winprob_loss_in_book"] = round(pre, 2)
            left_book["winprob_loss_after_book"] = round(post, 2)
            if post >= pre * 1.4 and post - pre >= 1.5:
                findings.append({
                    "kind": "leak",
                    "text": f"The four moves right after he leaves known territory cost "
                            f"him {post:.1f}% win chance per move — versus {pre:.1f}% "
                            f"while still on familiar ground. That first "
                            f"'on my own' move is the one to slow down on."})

        # family with the earliest exits and a bad score
        fam_stats: dict[str, dict] = defaultdict(lambda: {"n": 0, "exit": 0, "w": 0, "d": 0})
        for g in games:
            fam = util.opening_family(g["eco"], g["opening_name"])
            s = fam_stats[fam]
            s["n"] += 1
            s["exit"] += (exits[g["id"]] + 1) // 2
            s["w"] += g["result"] == "win"
            s["d"] += g["result"] == "draw"
        worst = None
        for fam, s in fam_stats.items():
            if s["n"] < 4 or fam == "Unknown":
                continue
            score = (s["w"] + 0.5 * s["d"]) / s["n"]
            avg_e = s["exit"] / s["n"]
            if score < 0.45 and (worst is None or avg_e < worst[1]):
                worst = (fam, avg_e, score, s["n"])
        if worst:
            fam, avg_e, score, cnt = worst
            findings.append({
                "kind": "study",
                "text": f"{fam}: he is out of book by move {avg_e:.0f} on average and "
                        f"scores {score:.0%} over {cnt} games — the first line worth "
                        f"studying together. Pick ONE reply and drill it below."})

    return {"color": color, "games": n, "lines": lines,
            "left_book": left_book, "findings": findings}


# ------------------------------------------------------------------ drills

def refresh_drills() -> int:
    """Repeated opening positions where his usual move leaks — as puzzles."""
    rows = db.rows(
        """SELECT m.fen_before, m.san, m.uci, m.best_uci, m.best_san, m.pv,
                  m.winprob_before, m.winprob_after, m.second_gap_cp
           FROM moves m JOIN games g ON g.id = m.game_id
           WHERE m.mover='player' AND m.ply <= ? AND m.best_uci IS NOT NULL
             AND g.engine='stockfish'""", (MAX_PLIES,))
    by_fen: dict[str, list[dict]] = defaultdict(list)
    for m in rows:
        by_fen[m["fen_before"]].append(m)

    created = 0
    for fen, ms in by_fen.items():
        if len(ms) < BOOK_MIN:
            continue
        drops = [max(0.0, (m["winprob_before"] or 0) - (m["winprob_after"] or 0))
                 for m in ms]
        if sum(drops) / len(drops) < DRILL_MIN_DROP:
            continue
        best_mode, best_n = Counter(m["best_uci"] for m in ms).most_common(1)[0]
        if best_n / len(ms) < 0.6:
            continue                     # engine not consistent here — skip
        # his habitual (worst-performing common) reply
        usual, usual_n = Counter(m["san"] for m in ms).most_common(1)[0]
        if usual == next(m["best_san"] for m in ms if m["best_uci"] == best_mode):
            continue                     # his usual move IS the best move
        rep = max((m for m in ms if m["best_uci"] == best_mode),
                  key=lambda m: m["second_gap_cp"] or 0)
        if (rep["second_gap_cp"] or 0) < 80:
            continue                     # not clear-cut enough for a drill
        pv = (rep["pv"] or "").split()[:3]
        if len(pv) % 2 == 0:
            pv = pv[:-1]
        solution = pv or [best_mode]
        if db.scalar("SELECT 1 FROM puzzles WHERE source='repertoire' AND fen=?",
                     (fen,)):
            continue
        board = chess.Board(fen)
        move_no = board.fullmove_number
        with db.tx() as conn:
            conn.execute(
                """INSERT INTO puzzles (source, game_id, ply, fen, solution, themes,
                     phase, difficulty, explanation, created_at, due_at)
                   VALUES ('repertoire', NULL, NULL, ?,?,?,?,?,?,?,?)""",
                (fen, json.dumps(solution), json.dumps(["repertoire"]),
                 "opening", 2,
                 f"You've had this position {len(ms)} times (around move {move_no}) "
                 f"and usually play {usual}. {rep['best_san']} is stronger — "
                 f"make it your line.",
                 util.now_iso(), util.now_iso()))
        created += 1
    return created


def drill_queue(limit: int = 10) -> list[dict]:
    from ..puzzles import scheduler
    ps = db.rows(
        """SELECT * FROM puzzles WHERE source='repertoire' AND retired=0
           ORDER BY due_at LIMIT ?""", (limit,))
    return [scheduler._public(p) for p in ps]
