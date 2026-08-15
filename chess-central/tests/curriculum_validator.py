#!/usr/bin/env python
"""Curriculum validator — every FEN, move, and claim must check out.

Usage: validate_curriculum.py <file.json> [--strict]
Exit 0 = valid. Prints per-item errors otherwise.

Rules enforced:
- every FEN parses and is a sane position (both kings, side not already mated)
- every example move sequence is legal from its FEN
- every drill solution is legal, non-empty, odd length (ends on solver's move)
- goal "mate": the line ends in checkmate; 1-move mates must be the ONLY mating move
- goal "material": depth-2 material search says solver gains >= 150cp over the line
- goal "best": legality only, but the lesson must give a hint or explain
"""
import json
import sys

import chess

PIECE_VALUES = {chess.PAWN: 100, chess.KNIGHT: 300, chess.BISHOP: 300,
                chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}


def material(b: chess.Board) -> int:
    t = 0
    for pt, val in PIECE_VALUES.items():
        t += val * (len(b.pieces(pt, chess.WHITE)) - len(b.pieces(pt, chess.BLACK)))
    return t


def negamax(b: chess.Board, depth: int) -> int:
    if b.is_checkmate():
        return -100000
    if b.is_stalemate() or b.is_insufficient_material():
        return 0
    base = material(b) if b.turn == chess.WHITE else -material(b)
    if depth == 0:
        return base
    best = None
    for mv in b.legal_moves:
        b.push(mv)
        s = -negamax(b, depth - 1)
        b.pop()
        if best is None or s > best:
            best = s
    return base if best is None else best


def check_fen(fen):
    try:
        b = chess.Board(fen)
    except Exception as e:
        return None, f"bad FEN: {e}"
    if len(b.pieces(chess.KING, chess.WHITE)) != 1 or len(b.pieces(chess.KING, chess.BLACK)) != 1:
        return None, "FEN must have exactly one king per side"
    if not b.is_valid():
        return None, f"illegal position: {b.status()!r}"
    if b.is_game_over():
        return None, "position is already game-over"
    return b, None


def play_line(board, moves):
    b = board.copy()
    for i, u in enumerate(moves):
        try:
            mv = chess.Move.from_uci(u)
        except Exception:
            return None, f"move {i} '{u}' is not UCI"
        if mv not in b.legal_moves:
            return None, f"move {i} '{u}' is illegal in {b.fen()}"
        b.push(mv)
    return b, None


def validate(doc):
    errors = []
    tracks = doc if isinstance(doc, list) else [doc]
    n_lessons = n_drills = 0
    for t in tracks:
        for lesson in t.get("lessons", []):
            n_lessons += 1
            lid = lesson.get("id", "?")
            if not lesson.get("concept"):
                errors.append(f"{lid}: missing concept text")
            if lesson.get("level") not in (1, 2, 3):
                errors.append(f"{lid}: level must be 1, 2, or 3")
            for j, ex in enumerate(lesson.get("examples", [])):
                b, err = check_fen(ex.get("fen", ""))
                if err:
                    errors.append(f"{lid} example {j}: {err}")
                    continue
                _, err = play_line(b, ex.get("moves", []))
                if err:
                    errors.append(f"{lid} example {j}: {err}")
            for j, d in enumerate(lesson.get("drills", [])):
                n_drills += 1
                tag = f"{lid} drill {j}"
                b, err = check_fen(d.get("fen", ""))
                if err:
                    errors.append(f"{tag}: {err}")
                    continue
                sol = d.get("solution", [])
                if not sol:
                    errors.append(f"{tag}: empty solution")
                    continue
                if len(sol) % 2 == 0:
                    errors.append(f"{tag}: solution must end on the solver's move (odd length)")
                    continue
                final, err = play_line(b, sol)
                if err:
                    errors.append(f"{tag}: {err}")
                    continue
                goal = d.get("goal", "best")
                if goal == "mate":
                    if not final.is_checkmate():
                        errors.append(f"{tag}: goal=mate but line does not end in checkmate")
                    elif len(sol) == 1:
                        mates = [m for m in b.legal_moves
                                 if b.copy().__class__ and _mates(b, m)]
                        if len(mates) != 1:
                            errors.append(f"{tag}: mate-in-1 must be unique "
                                          f"({len(mates)} mating moves exist)")
                elif goal == "material":
                    solver_white = b.turn == chess.WHITE
                    before = negamax(b.copy(), 2)
                    after_white_pov = negamax(final.copy(), 2)
                    after = after_white_pov if final.turn == chess.WHITE else -after_white_pov
                    after_solver = after if final.turn == (chess.WHITE if solver_white else chess.BLACK) \
                        else -after
                    gain = after_solver - before
                    if gain < 150:
                        errors.append(f"{tag}: goal=material but depth-2 gain is "
                                      f"{gain}cp (< 150)")
                elif goal == "best":
                    if not (d.get("hint") or d.get("explain")):
                        errors.append(f"{tag}: goal=best needs a hint or explain")
                else:
                    errors.append(f"{tag}: unknown goal '{goal}'")
    return errors, n_lessons, n_drills


def _mates(b, m):
    b2 = b.copy()
    b2.push(m)
    return b2.is_checkmate()


if __name__ == "__main__":
    path = sys.argv[1]
    doc = json.load(open(path))
    errors, nl, nd = validate(doc)
    print(f"{path}: {nl} lessons, {nd} drills")
    for e in errors:
        print("  ERROR:", e)
    print("RESULT:", "PASS" if not errors else f"FAIL ({len(errors)} errors)")
    sys.exit(0 if not errors else 1)
