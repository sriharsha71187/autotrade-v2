"""AI coach tests — the Anthropic call is mocked; everything else is real."""
import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.coach import llm

CANNED = "## For the coach\nSolid game analysis here.\n\n## For Nirvaan\nGreat job! Watch your queen next time. Keep going!"


@pytest.fixture
def mock_llm(monkeypatch):
    calls = []

    def fake_call(user_content, effort="high", max_tokens=8000):
        calls.append({"content": user_content, "effort": effort})
        return CANNED

    monkeypatch.setattr(llm, "_call", fake_call)
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    return calls


def test_game_facts_grounding(analyzed_game):
    facts = llm._game_facts(analyzed_game)
    assert facts["game"]["opponent_name"] == "opponent1"
    # the known blunder must be in the key moments Claude sees
    sans = [m["played"] for m in facts["key_moments"]]
    assert "Qxe5+" in sans
    assert facts["accuracy"]["blunder"] >= 1


def test_game_facts_requires_analysis():
    from tests.conftest import HANG_QUEEN_PGN, insert_game
    gid = insert_game(HANG_QUEEN_PGN, game_id_str="unanalyzed")
    with pytest.raises(llm.LLMError):
        llm._game_facts(gid)


def test_game_commentary_caches(analyzed_game, mock_llm):
    r1 = llm.game_commentary(analyzed_game)
    assert r1["cached"] is False
    assert "For Nirvaan" in r1["content"]
    r2 = llm.game_commentary(analyzed_game)
    assert r2["cached"] is True
    assert len(mock_llm) == 1          # second call served from llm_notes

    r3 = llm.game_commentary(analyzed_game, force=True)
    assert r3["cached"] is False
    assert len(mock_llm) == 2


def test_kid_note_extraction(analyzed_game, mock_llm):
    llm.game_commentary(analyzed_game)
    note = llm.kid_note_for_latest_game()
    assert note is not None
    assert note.startswith("Great job!")
    assert "For the coach" not in note


def test_weekly_report_includes_data(analyzed_game, mock_llm):
    # the report covers the last 14 days — the fixture's fixed date must not
    # silently age out of that window as real time passes
    with db.tx() as conn:
        conn.execute("UPDATE games SET played_at=datetime('now')")
    r = llm.weekly_report()
    assert r["content"] == CANNED
    sent = mock_llm[0]["content"]
    assert "opponent1" in sent           # game data made it into the prompt
    assert "puzzle_training" in sent


def test_rival_brief_requires_scout(mock_llm):
    with db.tx() as conn:
        conn.execute("INSERT INTO rivals (name) VALUES ('SomeKid')")
    rid = db.scalar("SELECT id FROM rivals WHERE name='SomeKid'")
    with pytest.raises(llm.LLMError):
        llm.rival_brief(rid)
    with db.tx() as conn:
        conn.execute("UPDATE rivals SET scout_report=? WHERE id=?",
                     (json.dumps({"advice": ["play e4"]}), rid))
    r = llm.rival_brief(rid)
    assert r["content"] == CANNED


def test_chat_persists_history(analyzed_game, monkeypatch):
    class FakeBlock:
        type = "text"
        text = "Grounded coaching answer."

    class FakeResponse:
        stop_reason = "end_turn"
        content = [FakeBlock()]

    class FakeMessages:
        def create(self, **kwargs):
            # grounding snapshot must be the first message
            assert "Current data snapshot" in kwargs["messages"][0]["content"]
            assert kwargs["messages"][-1]["content"] == "why did he lose?"
            return FakeResponse()

    class FakeBeta:
        messages = FakeMessages()

    class FakeClient:
        def __init__(self, **kw): self.beta = FakeBeta()

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    r = llm.chat("why did he lose?")
    assert r["reply"] == "Grounded coaching answer."
    hist = llm.chat_history()
    assert [h["role"] for h in hist] == ["user", "assistant"]
    llm.chat_clear()
    assert llm.chat_history() == []


def test_llm_api_routes(analyzed_game, mock_llm):
    from app.main import app
    client = TestClient(app)

    assert client.get("/api/llm/status").json()["configured"] is True
    r = client.post(f"/api/llm/game/{analyzed_game}/commentary")
    assert r.status_code == 200
    assert "For Nirvaan" in r.json()["content"]
    r2 = client.get(f"/api/llm/game/{analyzed_game}/commentary")
    assert r2.json()["content"] == r.json()["content"]
    assert client.post("/api/llm/weekly-report").status_code == 200


def test_llm_unconfigured_is_friendly(analyzed_game, monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: False)
    from app.main import app
    client = TestClient(app)
    r = client.post(f"/api/llm/game/{analyzed_game}/commentary")
    assert r.status_code == 400
    assert "API key" in r.json()["detail"]
