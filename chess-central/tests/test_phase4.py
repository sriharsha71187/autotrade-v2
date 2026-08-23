"""Phase-4 tests: scoresheet scan (transcription validation + endpoint)
and Academy links for mistake motifs."""
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import util

CURRICULUM = (pathlib.Path(__file__).resolve().parent.parent
              / "app" / "learn" / "curriculum.json")


def _client():
    from app.main import app
    return TestClient(app)


def test_validate_movetext():
    r = util.validate_movetext(["e4", "e5", "Nf3", "Nc6", "Bb5"])
    assert r["movetext"] == "1. e4 e5 2. Nf3 Nc6 3. Bb5"
    assert r["valid_plies"] == 5 and not r["issues"]

    # kid spellings normalized; annotations stripped; blanks skipped
    r = util.validate_movetext(["e4", "e5", "Nf3", "", "Nc6!", "Bb5", "0-0"])
    assert r["valid_plies"] == 5          # 0-0 -> O-O but castling isn't legal yet
    assert r["issues"] and "not a legal move" in r["issues"][0]
    assert r["movetext"].endswith("3. Bb5")

    assert util.validate_movetext([])["valid_plies"] == 0


def test_scan_endpoint(monkeypatch):
    from app.coach import llm
    monkeypatch.setattr(llm, "scan_scoresheet", lambda img, mt: {
        "moves_san": ["e4", "e5", "Qh5", "Nc6", "Qxf7+"],
        "white_name": "Nirvaan Thammishetty", "black_name": "Some Kid",
        "date": "2026-08-12", "result": "1-0", "event": "Quads",
        "notes": "move 3 was hard to read",
    })
    client = _client()
    r = client.post("/api/games/otb/scan",
                    json={"image": "data:image/jpeg;base64,eHh4eA=="})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["movetext"].startswith("1. e4 e5 2. Qh5 Nc6")
    assert body["valid_plies"] == 5 and body["total_plies"] == 5
    assert any("Reader's notes" in i for i in body["issues"])
    assert body["white_name"] == "Nirvaan Thammishetty"
    assert body["result"] == "1-0"

    assert client.post("/api/games/otb/scan", json={}).status_code == 422
    assert client.post("/api/games/otb/scan",
                       json={"image": "eHh4", "media_type": "image/tiff"}
                       ).status_code == 422


def test_scan_endpoint_llm_error(monkeypatch):
    from app.coach import llm

    def no_key(img, mt):
        raise llm.LLMError("No Anthropic API key configured.")
    monkeypatch.setattr(llm, "scan_scoresheet", no_key)
    r = _client().post("/api/games/otb/scan", json={"image": "eHh4eA=="})
    assert r.status_code == 400
    assert "API key" in r.json()["detail"]


SCHOLARS_MATE_PGN = """[Event "T"]
[White "Bully"]
[Black "nirvaan0421"]
[Result "1-0"]

1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0
"""

FOOLS_MATE_PGN = """[Event "T"]
[White "nirvaan0421"]
[Black "Speedy"]
[Result "0-1"]

1. f3 e5 2. g4 Qh4# 0-1
"""


def test_trap_detection():
    from app.analysis import traps
    # opponent was White and delivered the classic Qxf7#
    assert traps.detect(SCHOLARS_MATE_PGN, "white") == "Scholar's Mate"
    # from Black's-opponent perspective there is no trap by Black here
    assert traps.detect(SCHOLARS_MATE_PGN, "black") is None
    assert traps.detect(FOOLS_MATE_PGN, "black") == "Fool's Mate pattern"
    # a quiet game names nothing
    quiet = "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7"
    assert traps.detect(f'[Event "T"]\n\n{quiet}', "white") is None

    fried = ('[Event "T"]\n\n'
             "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Ng5 d5 5. exd5 Nxd5 6. Nxf7 Kxf7")
    assert traps.detect(fried, "white") == "Fried Liver Attack"


def test_mate_pattern_names():
    import chess

    from app.analysis import traps
    # smothered: Nf7#, king boxed in by its own rook and pawns
    assert traps.mate_pattern_name(
        chess.Board("6rk/5Npp/8/8/8/8/8/6K1 b - - 0 1")) == "Smothered Mate"
    # back-rank: rook mates along the 8th behind an unmoved pawn shield
    assert traps.mate_pattern_name(
        chess.Board("R5k1/5ppp/8/8/8/8/8/6K1 b - - 0 1")) == "Back-rank Mate"
    # ladder: rook checks on the 1st rank, queen seals the 2nd
    assert traps.mate_pattern_name(
        chess.Board("6k1/8/8/8/8/8/1q6/r6K w - - 0 1")) == "Ladder Mate"
    # not checkmate -> no name
    assert traps.mate_pattern_name(chess.Board()) is None


def test_opponent_tactics_in_games_api(analyzed_game):
    # in the fixture game the opponent captures the hung queen (won material)
    client = _client()
    rows = client.get("/api/games?limit=10").json()
    row = next(r for r in rows if r["id"] == analyzed_game)
    assert "opp_tactics" in row
    assert "Won material" in row["opp_tactics"]

    detail = client.get(f"/api/games/{analyzed_game}").json()
    assert "Won material" in detail["opp_tactics"]
    assert any(s["label"] == "Won material" for s in detail["opp_strikes"])

    # trap detection shows even without analysis
    from tests.conftest import insert_game
    gid = insert_game(SCHOLARS_MATE_PGN, opponent="Bully", game_id_str="trap1",
                      color="black", played_at="2026-08-03T10:00:00Z")
    rows = client.get("/api/games?limit=10").json()
    row = next(r for r in rows if r["id"] == gid)
    assert row["opp_tactics"][0] == "Scholar's Mate"


@pytest.mark.skipif(not CURRICULUM.exists(), reason="curriculum not compiled")
def test_motif_lesson_links():
    from app import learn
    m = learn.motif_map()
    # every mapped lesson must actually exist in the compiled curriculum
    assert set(m) == set(learn.MOTIF_LESSONS)
    assert m["missed_fork"]["lesson_id"] == "tactics-knight-forks"
    assert m["left_piece_hanging"]["title"]
    assert m["back_rank"]["track_title"]

    api = _client().get("/api/learn/motif-map").json()
    assert api["moved_en_prise"]["lesson_id"] == "vision-is-it-safe"
