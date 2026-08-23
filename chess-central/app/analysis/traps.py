"""Named trap/attack detection — the openings kids actually get hit with.

Deterministic pattern checks over the PGN (no engine needed), from the
opponent's side of a given game. Honest by design: a name is returned only
when the concrete pattern is on the board.
"""
from __future__ import annotations

import chess

from .. import util

MAX_PLIES = 20   # traps live in the first ~10 moves


def detect(pgn: str, opponent_color: str) -> str | None:
    """Name the trap/attack the opponent used in this game, if any."""
    game = util.parse_pgn_game(pgn)
    if game is None:
        return None
    opp_white = opponent_color == "white"
    board = game.board()

    queen_out_early = False      # opponent queen developed in their first 3 moves
    scholars_bishop = False      # opponent bishop eyeing f7/f2 (c4 or c5)
    mate_ply = mate_to = None
    mate_by_opp = mate_with_queen = False

    for i, mv in enumerate(game.mainline_moves()):
        if i >= MAX_PLIES:
            break
        is_opp = board.turn == (chess.WHITE if opp_white else chess.BLACK)
        piece = board.piece_at(mv.from_square)
        if is_opp and piece:
            if piece.piece_type == chess.QUEEN and i < 6:
                queen_out_early = True
            if piece.piece_type == chess.BISHOP and \
                    chess.square_name(mv.to_square) in ("c4", "c5"):
                scholars_bishop = True
        board.push(mv)
        if board.is_checkmate():
            mate_ply, mate_to, mate_by_opp = i + 1, mv.to_square, is_opp
            mate_with_queen = bool(piece and piece.piece_type == chess.QUEEN)
            break

    if mate_by_opp and mate_with_queen:
        if mate_ply <= 6:
            return "Fool's Mate pattern"
        if chess.square_name(mate_to) in ("f7", "f2") and mate_ply <= 16:
            return "Scholar's Mate"
        if queen_out_early and mate_ply <= 12:
            return "Early queen mate"
    if queen_out_early and scholars_bishop:
        return "Scholar's Mate attempt"
    if queen_out_early:
        return "Early queen attack"
    return None
