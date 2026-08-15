"""Per-game annotation: run the engine over every move, classify mistakes,
tag phases and motifs, derive time usage. Writes to the moves table."""
from __future__ import annotations

import json

import chess

from .. import db, util
from . import motifs
from .engine import PositionEval


def annotate_game(game_row: dict, engine, engine_name: str | None = None) -> int:
    """Analyze one game; returns number of move rows written."""
    game = util.parse_pgn_game(game_row["pgn"])
    if game is None:
        raise ValueError("unparseable PGN")
    if engine_name is None:
        from .engine import Engine
        engine_name = "stockfish" if isinstance(engine, Engine) else "fallback"
    increment = util.parse_increment(game_row.get("time_control"))

    board = game.board()
    player_color = chess.WHITE if game_row["color"] == "white" else chess.BLACK

    records = []
    prev_eval: PositionEval = engine.evaluate(board)
    prev_clock = {chess.WHITE: None, chess.BLACK: None}

    for ply, node in enumerate(game.mainline(), start=1):
        move = node.move
        mover = board.turn
        fen_before = board.fen()
        phase = util.phase_of(board)
        san = board.san(move)
        is_player = mover == player_color

        eval_before = prev_eval
        wp_before = util.win_prob(eval_before.cp, mover == chess.WHITE)

        clock = util.clock_from_comment(node.comment)
        move_time = None
        if clock is not None and prev_clock[mover] is not None:
            # clock after move = clock before - think time + increment
            move_time = max(0.0, prev_clock[mover] - clock + increment)
        if clock is not None:
            prev_clock[mover] = clock

        board.push(move)
        eval_after = engine.evaluate(board) if not board.is_game_over() else _terminal_eval(board)
        wp_after = util.win_prob(eval_after.cp, mover == chess.WHITE)

        drop = max(0.0, wp_before - wp_after)
        classification = util.classify_drop(drop)

        board.pop()
        tag_list: list[str] = []
        best = eval_before.best
        if classification in ("inaccuracy", "mistake", "blunder"):
            tag_list = motifs.tags_for_mistake(
                board, move, best, eval_before.cp, eval_after.cp, eval_before.mate_in
            )
        elif classification == "best":
            tag_list = motifs.tags_for_good_move(board, move)

        best_san = board.san(best) if best and best in board.legal_moves else None
        board.push(move)

        second_gap = None
        if eval_before.second_cp is not None:
            gap = eval_before.cp - eval_before.second_cp
            second_gap = gap if mover == chess.WHITE else -gap

        records.append({
            "game_id": game_row["id"],
            "ply": ply,
            "san": san,
            "uci": move.uci(),
            "mover": "player" if is_player else "opponent",
            "fen_before": fen_before,
            "eval_before": eval_before.cp,
            "eval_after": eval_after.cp,
            "winprob_before": wp_before,
            "winprob_after": wp_after,
            "best_uci": best.uci() if best else None,
            "best_san": best_san,
            "pv": " ".join(m.uci() for m in eval_before.pv),
            "second_uci": eval_before.second.uci() if eval_before.second else None,
            "second_gap_cp": second_gap,
            "classification": classification,
            "phase": phase,
            "motifs": json.dumps(tag_list),
            "clock_seconds": clock,
            "move_time": move_time,
        })
        prev_eval = eval_after

    with db.tx() as conn:
        conn.execute("DELETE FROM moves WHERE game_id=?", (game_row["id"],))
        conn.executemany(
            """INSERT INTO moves (game_id, ply, san, uci, mover, fen_before,
                 eval_before, eval_after, winprob_before, winprob_after,
                 best_uci, best_san, pv, second_uci, second_gap_cp,
                 classification, phase, motifs, clock_seconds, move_time)
               VALUES (:game_id,:ply,:san,:uci,:mover,:fen_before,
                 :eval_before,:eval_after,:winprob_before,:winprob_after,
                 :best_uci,:best_san,:pv,:second_uci,:second_gap_cp,
                 :classification,:phase,:motifs,:clock_seconds,:move_time)""",
            records,
        )
        conn.execute(
            "UPDATE games SET analyzed_at=?, analysis_error=NULL, moves_count=?, engine=? "
            "WHERE id=?",
            (util.now_iso(), len(records), engine_name, game_row["id"]),
        )
    return len(records)


def _terminal_eval(board: chess.Board) -> PositionEval:
    if board.is_checkmate():
        cp = -util.MATE_CP if board.turn == chess.WHITE else util.MATE_CP
        return PositionEval(cp=cp, mate_in=0)
    return PositionEval(cp=0)


def game_accuracy(game_id: int) -> dict:
    """Summary stats for one analyzed game (player side only)."""
    ms = db.rows(
        "SELECT classification, phase, winprob_before, winprob_after "
        "FROM moves WHERE game_id=? AND mover='player'", (game_id,))
    if not ms:
        return {}
    drops = [max(0.0, m["winprob_before"] - m["winprob_after"]) for m in ms]
    counts = {c: 0 for c in ("best", "good", "inaccuracy", "mistake", "blunder")}
    for m in ms:
        counts[m["classification"]] = counts.get(m["classification"], 0) + 1
    return {
        "moves": len(ms),
        "avg_winprob_loss": round(sum(drops) / len(drops), 2),
        **counts,
    }
