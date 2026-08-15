"""Tactical motif detection — the vocabulary the coach uses to explain mistakes.

These are static-board heuristics (no engine needed) applied around moves the
engine flagged. Tags feed the insights dashboard and puzzle theming.
"""
from __future__ import annotations

import chess

from ..util import PIECE_VALUES


def _value(board: chess.Board, sq: int) -> int:
    p = board.piece_at(sq)
    return PIECE_VALUES[p.piece_type] if p else 0


def _defenders(board: chess.Board, sq: int, color: bool) -> list[int]:
    return sorted(board.attackers(color, sq),
                  key=lambda s: _value(board, s))


def hanging_pieces(board: chess.Board, color: bool) -> list[int]:
    """Squares of `color` pieces the opponent can win material on right now.

    A piece hangs if it's attacked and either undefended, or its cheapest
    attacker is worth less than the piece (simple 1-ply exchange logic).
    """
    out = []
    for sq in chess.SQUARES:
        piece = board.piece_at(sq)
        if not piece or piece.color != color or piece.piece_type == chess.KING:
            continue
        attackers = _defenders(board, sq, not color)
        if not attackers:
            continue
        defenders = _defenders(board, sq, color)
        val = PIECE_VALUES[piece.piece_type]
        cheapest_attacker = _value(board, attackers[0])
        if not defenders or cheapest_attacker < val:
            out.append(sq)
    return out


def is_fork(board: chess.Board, move: chess.Move) -> bool:
    """Does `move` land a piece attacking 2+ valuable enemy targets?"""
    b = board.copy(stack=False)
    mover_color = b.turn
    b.push(move)
    to_sq = move.to_square
    piece = b.piece_at(to_sq)
    if piece is None:
        return False
    attacker_val = PIECE_VALUES[piece.piece_type]
    targets = 0
    for sq in b.attacks(to_sq):
        target = b.piece_at(sq)
        if not target or target.color == mover_color:
            continue
        tval = PIECE_VALUES[target.piece_type]
        defended = bool(b.attackers(not mover_color, sq))
        if target.piece_type == chess.KING or tval > attacker_val or not defended:
            targets += 1
    return targets >= 2


def is_back_rank_mate(board_after: chess.Board) -> bool:
    if not board_after.is_checkmate():
        return False
    loser = board_after.turn
    ksq = board_after.king(loser)
    if ksq is None:
        return False
    back = 7 if loser == chess.BLACK else 0
    return chess.square_rank(ksq) == back


def moved_en_prise(board_before: chess.Board, move: chess.Move) -> bool:
    """Did the mover put the moved piece itself on a losing square?"""
    b = board_before.copy(stack=False)
    color = b.turn
    b.push(move)
    return move.to_square in hanging_pieces(b, color)


def left_piece_hanging(board_before: chess.Board, move: chess.Move) -> bool:
    """Did the move leave (or ignore) another piece that can now be won?"""
    b = board_before.copy(stack=False)
    color = b.turn
    b.push(move)
    hangs = set(hanging_pieces(b, color)) - {move.to_square}
    return bool(hangs)


def tags_for_mistake(board_before: chess.Board, played: chess.Move,
                     best: chess.Move | None,
                     eval_before_cp: int, eval_after_cp: int,
                     best_mate_in: int | None) -> list[str]:
    """Motif tags for a move the engine disliked (mover = board_before.turn)."""
    tags: list[str] = []
    mover_white = board_before.turn == chess.WHITE

    # missed mate: engine had a forced mate for the mover before the move
    if best_mate_in is not None and (best_mate_in > 0) == mover_white and best_mate_in != 0:
        tags.append("missed_mate")

    if best is not None:
        if board_before.is_capture(best):
            captured = board_before.piece_at(best.to_square)
            if captured and PIECE_VALUES[captured.piece_type] >= 3:
                tags.append("missed_capture")
        if is_fork(board_before, best):
            tags.append("missed_fork")
        b2 = board_before.copy(stack=False)
        b2.push(best)
        if b2.is_check():
            tags.append("missed_check_tactic")

    if moved_en_prise(board_before, played):
        tags.append("moved_en_prise")
    elif left_piece_hanging(board_before, played):
        tags.append("left_piece_hanging")

    # allowed a fork: opponent has a strong fork available after our move
    b3 = board_before.copy(stack=False)
    b3.push(played)
    if not b3.is_game_over():
        for reply in b3.legal_moves:
            if is_fork(b3, reply):
                tags.append("allowed_fork")
                break

    # walked into mate
    if b3.is_checkmate():
        tags.append("got_mated")
        if is_back_rank_mate(b3):
            tags.append("back_rank")

    return tags


def tags_for_good_move(board_before: chess.Board, played: chess.Move) -> list[str]:
    """Positive motifs, used for strengths ('you find forks!')."""
    tags = []
    if board_before.is_capture(played):
        captured = board_before.piece_at(played.to_square)
        if captured and PIECE_VALUES[captured.piece_type] >= 3:
            tags.append("won_material")
    if is_fork(board_before, played):
        tags.append("played_fork")
    b = board_before.copy(stack=False)
    b.push(played)
    if b.is_checkmate():
        tags.append("delivered_mate")
        if is_back_rank_mate(b):
            tags.append("back_rank_mate_win")
    return tags


MOTIF_LABELS = {
    "missed_mate": "Missed a checkmate",
    "missed_capture": "Missed a free capture",
    "missed_fork": "Missed a fork",
    "missed_check_tactic": "Missed a checking tactic",
    "moved_en_prise": "Moved a piece to a losing square",
    "left_piece_hanging": "Left a piece hanging",
    "allowed_fork": "Allowed a fork",
    "got_mated": "Walked into checkmate",
    "back_rank": "Back-rank weakness",
    "won_material": "Won material",
    "played_fork": "Played a fork",
    "delivered_mate": "Delivered checkmate",
    "back_rank_mate_win": "Back-rank mate (win)",
}
