"""Phase-2 tests: OTB entry, weekly rhythm, guided loss review, secrets gate."""
from fastapi.testclient import TestClient

from app import db

OTB_PGN = """[Event "NWSRS Quads"]
[White "Nirvaan Thammishetty"]
[Black "Some Kid"]
[Result "0-1"]

1. e4 e5 2. Qh5 Nc6 3. Qxe5+ Nxe5 4. Nf3 Nxf3+ 5. gxf3 d5 0-1
"""


def _client():
    from app.main import app
    return TestClient(app)


def test_otb_game_entry_full_pipeline():
    client = _client()
    r = client.post("/api/games/otb", json={
        "pgn": OTB_PGN, "color": "white", "opponent_rating": 450,
        "time_control": "G/30;d5", "played_at": "2026-08-10",
    })
    assert r.status_code == 200, r.text
    gid = r.json()["id"]
    g = db.row("SELECT * FROM games WHERE id=?", (gid,))
    assert g["platform"] == "otb"
    assert g["result"] == "loss"          # derived from the 0-1 header
    assert g["opponent_name"] == "Some Kid"
    assert g["time_class"] == "classical"
    # same pipeline: analyzable like any other game
    from app.analysis import annotate
    from app.analysis.engine import FakeEngine
    assert annotate.annotate_game(g, FakeEngine()) > 0

    # duplicates rejected
    r2 = client.post("/api/games/otb", json={"pgn": OTB_PGN, "color": "white"})
    assert r2.status_code == 409


def test_otb_movetext_only_and_errors():
    client = _client()
    r = client.post("/api/games/otb", json={
        "pgn": "1. d4 d5 2. c4 e6 3. Nc3 Nf6",
        "color": "black", "result": "draw"})
    assert r.status_code == 200

    assert client.post("/api/games/otb", json={
        "pgn": "1. e4 e9??", "color": "white", "result": "win"}).status_code == 422
    assert client.post("/api/games/otb", json={
        "pgn": "1. e4 e5", "color": "white", "result": "win"}).status_code == 422  # too short
    assert client.post("/api/games/otb", json={
        "pgn": OTB_PGN, "color": "purple"}).status_code == 422


def test_rhythm_checklist(analyzed_game):
    from app.coach import rhythm
    s = rhythm.status()
    assert s["total"] >= 3
    games_item = next((i for i in s["items"] if i["auto"] and
                       "games" in i["auto"]["label"]), None)
    assert games_item is not None

    rhythm.set_check(0, True)
    s2 = rhythm.status()
    assert s2["items"][0]["manual_checked"] is True
    assert s2["items"][0]["done"] is True

    client = _client()
    api = client.get("/api/rhythm").json()
    assert api["items"][0]["done"] is True


def test_loss_review_flow(analyzed_game):
    from app.coach import review
    q = review.queue()
    assert len(q) == 1                      # the hang-queen loss
    item = q[0]
    assert item["played"] == "Qxe5+"        # the turning point found
    assert item["drop_pct"] >= 10
    assert len(item["puzzle"]["solution"]) % 2 == 1

    client = _client()
    assert client.get("/api/review/queue").json()
    assert client.post(f"/api/review/{item['game_id']}/done").status_code == 200
    assert review.queue() == []
    kid = client.get("/api/kid/home").json()
    assert kid["review_queue"] == 0
    assert "practiced_days_7" in kid and "skills_trend" in kid


def test_settings_secrets_redacted():
    from app import config
    config.update({"anthropic_api_key": "sk-secret-123", "kid_pin": "1234"})
    client = _client()
    cfg = client.get("/api/settings").json()
    assert cfg["anthropic_api_key"] == ""
    assert cfg["anthropic_api_key_set"] is True
    assert cfg["kid_pin"] == ""

    # empty secret on save keeps the stored value
    client.post("/api/settings", json={"anthropic_api_key": "", "player_name": "X"})
    assert config.get("anthropic_api_key") == "sk-secret-123"
    # non-empty replaces it
    client.post("/api/settings", json={"anthropic_api_key": "sk-new"})
    assert config.get("anthropic_api_key") == "sk-new"

    # gate
    assert client.get("/api/gate").json()["pin_required"] is True
    assert client.post("/api/gate", json={"pin": "9999"}).status_code == 403
    assert client.post("/api/gate", json={"pin": "1234"}).status_code == 200

    # cleanup (config.update clears directly; the skip-empty rule is API-level)
    config.update({"anthropic_api_key": "", "kid_pin": ""})
