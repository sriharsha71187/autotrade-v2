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
