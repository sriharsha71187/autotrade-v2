"""Sync record-mapping tests (pure functions, no network) and API smoke tests."""
from fastapi.testclient import TestClient

from app import db
from app.sync import chesscom, lichess


LICHESS_GAME = {
    "id": "abc123",
    "rated": True,
    "speed": "rapid",
    "status": "mate",
    "winner": "black",
    "createdAt": 1754900000000,
    "moves": "e4 e5 Nf3",
    "pgn": "[Event \"x\"]\n\n1. e4 e5 2. Nf3 *",
    "clock": {"initial": 600, "increment": 5},
    "opening": {"eco": "C40", "name": "King's Knight Opening"},
    "players": {
        "white": {"user": {"name": "nirvaan0421"}, "rating": 800},
        "black": {"user": {"name": "somekid"}, "rating": 850},
    },
}


def test_lichess_mapping():
    rec = lichess.game_to_record(LICHESS_GAME, "nirvaan0421")
    assert rec["color"] == "white"
    assert rec["result"] == "loss"
    assert rec["opponent_name"] == "somekid"
    assert rec["player_rating"] == 800
    assert rec["eco"] == "C40"
    assert rec["time_control"] == "600+5"
    assert rec["platform_game_id"] == "abc123"


def test_lichess_mapping_not_player():
    assert lichess.game_to_record(LICHESS_GAME, "someoneelse") is None


CHESSCOM_GAME = {
    "url": "https://www.chess.com/game/live/999",
    "pgn": "[Event \"y\"]\n\n1. d4 *",
    "time_control": "600",
    "end_time": 1754900000,
    "rated": True,
    "time_class": "rapid",
    "rules": "chess",
    "eco": "https://www.chess.com/openings/Queens-Pawn-Opening",
    "white": {"username": "OtherKid", "rating": 700, "result": "agreed"},
    "black": {"username": "Nirvaan0421", "rating": 650, "result": "agreed"},
}


def test_chesscom_mapping_draw_and_case():
    rec = chesscom.game_to_record(CHESSCOM_GAME, "nirvaan0421")
    assert rec["color"] == "black"
    assert rec["result"] == "draw"
    assert rec["opponent_name"] == "OtherKid"
    assert rec["opening_name"] == "Queens Pawn Opening"


def test_chesscom_skips_variants():
    g = dict(CHESSCOM_GAME, rules="crazyhouse")
    assert chesscom.game_to_record(g, "nirvaan0421") is None


def test_api_smoke(missed_tactic_game):
    from app.main import app
    client = TestClient(app)

    assert client.get("/api/summary").status_code == 200
    assert client.get("/api/status").status_code == 200
    assert client.get("/api/games").json()
    assert client.get("/api/roadmap").json()["current"]["name"]
    assert client.get("/api/kid/home").json()["name"]
    ts = client.get("/api/tournaments").json()
    assert len(ts) >= 10          # seeded PNW calendar

    r = client.post("/api/rivals", json={"name": "TestRival"})
    assert r.status_code == 200
    assert client.get("/api/rivals").json()[0]["name"] == "TestRival"

    assert client.post("/api/journal", json={"text": "note"}).status_code == 200
    assert client.get("/api/journal").json()[0]["text"] == "note"

    # puzzle attempt roundtrip
    from app.puzzles import generator
    generator.generate_for_game(missed_tactic_game)
    daily = client.get("/api/puzzles/daily").json()
    assert daily
    res = client.post(f"/api/puzzles/{daily[0]['id']}/attempt",
                      json={"correct": True, "time_ms": 1500})
    assert res.status_code == 200
    assert "new_badges" in res.json()
