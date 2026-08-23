"""Named trap & mate-pattern detection — if it has a name, name it.

Deterministic checks over the PGN (no engine needed), from the opponent's
side of a given game. Two families:
  * opening traps, from the move sequence (Scholar's Mate, Fried Liver,
    Wayward Queen attack)
  * mate patterns, from the final position (Smothered, Back-rank, Ladder)
Honest by design: a name is returned only when the concrete pattern is on
the board.
"""
from __future__ import annotations

import chess

from .. import util
from . import motifs

MAX_PLIES = 24


def mate_pattern_name(board: chess.Board) -> str | None:
    """Name the mate pattern on a checkmated board, if it's a classic."""
    if not board.is_checkmate():
        return None
    loser = board.turn
    winner = not loser
    king_sq = board.king(loser)
    checkers = list(board.checkers())
    if len(checkers) != 1:
        return None
    checker = board.piece_at(checkers[0])

    if checker.piece_type == chess.KNIGHT:
        neighbors = list(board.attacks(king_sq))   # king's adjacent squares
        if neighbors and all(
                (p := board.piece_at(s)) and p.color == loser for s in neighbors):
            return "Smothered Mate"

    if checker.piece_type in (chess.ROOK, chess.QUEEN):
        # Ladder (two-rook) mate first — it's the more specific pattern
        # (needs a second heavy piece sealing the adjacent line)
        k_rank, k_file = chess.square_rank(king_sq), chess.square_file(king_sq)
        c_rank, c_file = chess.square_rank(checkers[0]), chess.square_file(checkers[0])
        heavies = [s for pt in (chess.ROOK, chess.QUEEN)
                   for s in board.pieces(pt, winner) if s != checkers[0]]
        if k_rank in (0, 7) and c_rank == k_rank:
            seal = k_rank + (1 if k_rank == 0 else -1)
            if any(chess.square_rank(s) == seal for s in heavies):
                return "Ladder Mate"
        if k_file in (0, 7) and c_file == k_file:
            seal = k_file + (1 if k_file == 0 else -1)
            if any(chess.square_file(s) == seal for s in heavies):
                return "Ladder Mate"
        if motifs.is_back_rank_mate(board):
            return "Back-rank Mate"
    return None


def detect(pgn: str, opponent_color: str) -> str | None:
    """Name the trap/attack/mate pattern the opponent used, if any."""
    game = util.parse_pgn_game(pgn)
    if game is None:
        return None
    opp_white = opponent_color == "white"
    opp = chess.WHITE if opp_white else chess.BLACK
    board = game.board()

    queen_out_early = False      # opponent queen developed in their first 3 moves
    scholars_bishop = False      # opponent bishop eyeing f7/f2 (c4 or c5)
    knight_hop = False           # opponent knight to g5/g4 (the Fried Liver hop)
    fried_liver = False          # ...followed by that side capturing on f7/f2
    mate_ply = None
    mate_by_opp = mate_with_queen = False
    final_board = None

    target = "f7" if opp_white else "f2"
    hop_sq = "g5" if opp_white else "g4"

    for i, mv in enumerate(game.mainline_moves()):
        if i >= MAX_PLIES:
            break
        is_opp = board.turn == opp
        piece = board.piece_at(mv.from_square)
        to_name = chess.square_name(mv.to_square)
        if is_opp and piece:
            if piece.piece_type == chess.QUEEN and i < 6:
                queen_out_early = True
            if piece.piece_type == chess.BISHOP and to_name in ("c4", "c5"):
                scholars_bishop = True
            if piece.piece_type == chess.KNIGHT and to_name == hop_sq:
                knight_hop = True
            if piece.piece_type == chess.KNIGHT and to_name == target \
                    and knight_hop and board.is_capture(mv):
                fried_liver = True
        board.push(mv)
        if board.is_checkmate():
            mate_ply, mate_by_opp = i + 1, is_opp
            mate_with_queen = bool(piece and piece.piece_type == chess.QUEEN)
            final_board = board
            break

    if mate_by_opp:
        if mate_ply <= 6:
            return "Fool's Mate pattern"
        if mate_with_queen and mate_ply <= 16 and \
                chess.square_name(final_board.peek().to_square) in ("f7", "f2"):
            return "Scholar's Mate"
        named = mate_pattern_name(final_board)
        if named:
            return named
        if queen_out_early and mate_with_queen and mate_ply <= 12:
            return "Wayward Queen mate"
    if fried_liver:
        return "Fried Liver Attack"
    if queen_out_early and scholars_bishop:
        return "Scholar's Mate attempt"
    if queen_out_early:
        return "Wayward Queen Attack"
    return None
