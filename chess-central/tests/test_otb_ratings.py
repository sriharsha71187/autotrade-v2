"""NWSRS / US Chess rating connectors against canned responses (offline)."""
import httpx
import pytest

from app import config, db
from app.sync import ratings

NWSRS_LETTER_PAGE = """<html><body><h1>Northwest Scholastic Ratings Last Names Beginning With 'T'</h1>
<table>
<tr><th>Last Name</th><th>First Name</th><th>ID</th><th>Rating</th><th>Games</th><th>State</th><th>Status</th></tr>
<tr><td>Tanaka</td><td>Aiko</td><td>LMSF0101</td><td>655</td><td>40</td><td>WA</td><td></td></tr>
<tr><td>Thammishetty</td><td>Nirvaan</td><td>TBKBF05U</td><td>612</td><td>58</td><td>WA</td><td></td></tr>
<tr><td>Thammishetty</td><td>Other</td><td>TBKD0002</td><td>900</td><td>12</td><td>WA</td><td>P</td></tr>
</table></body></html>"""

USCF_MEMBER = {
    "id": "33201208", "firstName": "Nirvaan",
    "ratings": [
        {"ratingSystem": "OverTheBoardRegular", "rating": 311, "gamesPlayed": 20,
         "isProvisional": False, "floor": 100},
        {"ratingSystem": "OverTheBoardQuick", "rating": 355, "gamesPlayed": 9},
        {"ratingSystem": "OverTheBoardBlitz", "rating": None},
        {"ratingSystem": "OnlineRegular", "rating": 480},
    ],
}


@pytest.fixture
def fake_http(monkeypatch):
    routes = {}
    real_client = httpx.Client

    def handler(request):
        for prefix, (status, body) in routes.items():
            if str(request.url).startswith(prefix):
                if isinstance(body, dict):
                    return httpx.Response(status, json=body)
                return httpx.Response(status, text=body)
        return httpx.Response(404, text="nope")

    monkeypatch.setattr(ratings.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler),
                                                 **{k: v for k, v in kw.items()
                                                    if k in ("headers", "timeout")}))
    return routes


def test_find_nwsrs_row_by_id_then_name():
    row = ratings.find_nwsrs_row(NWSRS_LETTER_PAGE, "TBKBF05U", "Nirvaan", "Thammishetty")
    assert row == {"rating": 612, "games": 58, "id": "TBKBF05U"}
    # wrong/old ID still finds him by first + last name — not his classmate
    row = ratings.find_nwsrs_row(NWSRS_LETTER_PAGE, "XXXX0000", "Nirvaan", "Thammishetty")
    assert row["rating"] == 612
    assert ratings.find_nwsrs_row(NWSRS_LETTER_PAGE, "", "Nobody", "Here") is None


def test_parse_uscf_ratings_lenient():
    assert ratings.parse_uscf_ratings(USCF_MEMBER) == {
        "uscf_regular": 311, "uscf_quick": 355, "uscf_online_regular": 480}
    assert ratings.parse_uscf_ratings({"data": USCF_MEMBER})["uscf_regular"] == 311
    assert ratings.parse_uscf_ratings({"ratings": []}) == {}
    assert ratings.parse_uscf_ratings("garbage") == {}


def test_sync_both_sources(fake_http):
    config.update({"nwsrs_id": "TBKBF05U", "uscf_id": "33201208",
                   "player_name": "Nirvaan Thammishetty"})
    fake_http["https://ratingsnw.com/ratings/ratingsT.php"] = (200, NWSRS_LETTER_PAGE)
    fake_http["https://ratings-api.uschess.org/api/v1/members/33201208"] = (200, USCF_MEMBER)
    out = ratings.sync_all()
    assert out["nwsrs"]["ok"] and out["nwsrs"]["rating"] == 612
    assert out["uscf"]["ok"] and out["uscf"]["uscf_regular"] == 311
    got = {r["source"]: r["rating"] for r in db.rows("SELECT source, rating FROM ratings_history")}
    assert got["nwsrs"] == 612 and got["uscf_quick"] == 355


def test_sync_falls_back_and_explains(fake_http):
    config.update({"nwsrs_id": "TBKBF05U", "uscf_id": "33201208",
                   "player_name": "Nirvaan Thammishetty"})
    # letter page missing -> school report used; main API 403 -> beta host used
    fake_http["https://ratingsnw.com/ratings/schoolreport.php?school=TBK"] = (200, NWSRS_LETTER_PAGE)
    fake_http["https://ratings-api.uschess.org"] = (403, "forbidden")
    fake_http["https://beta-ratings-api.uschess.org/api/v1/members/33201208"] = (200, USCF_MEMBER)
    out = ratings.sync_all()
    assert out["nwsrs"]["ok"] and "schoolreport" in out["nwsrs"]["source_url"]
    assert out["uscf"]["ok"]

    fake_http.clear()
    out = ratings.sync_all()
    assert not out["nwsrs"]["ok"] and "HTTP 404" in out["nwsrs"]["reason"]
    assert not out["uscf"]["ok"] and "HTTP 404" in out["uscf"]["reason"]


@pytest.mark.parametrize("payload,expected", [
    ({"ratings": [{"ratingSystem": {"name": "Regular", "code": "R"},
                   "rating": {"value": 311}, "gamesPlayed": 5}]}, {"uscf_regular": 311}),
    ({"ratings": [{"system": "R", "value": 311}, {"system": "OB", "value": 400}]},
     {"uscf_regular": 311, "uscf_online_blitz": 400}),
    ({"member": {"ratings": {"regular": {"rating": 311, "games": 12},
                             "quick": {"rating": 350}}}},
     {"uscf_regular": 311, "uscf_quick": 350}),
    ({"overTheBoardRegularRating": 311, "id": "33201208"}, {"uscf_regular": 311}),
    ({"ratings": [{"ratingSystemCode": "R", "ratingValue": 311, "gamesPlayed": 40,
                   "ratingFloor": 100}]}, {"uscf_regular": 311}),
    ({"ratings": [{"ratingSystem": "OverTheBoardRegular", "rating": None}]}, {}),
])
def test_uscf_payload_shapes(payload, expected):
    assert ratings.parse_uscf_ratings(payload) == expected


def test_unparsed_payload_reports_shape(fake_http):
    config.update({"uscf_id": "33201208"})
    fake_http["https://ratings-api.uschess.org/api/v1/members/33201208"] = (
        200, {"id": "33201208", "stuff": [{"weird": 1}]})
    out = ratings.sync_uscf()
    assert not out["ok"] and "stuff" in out["payload_shape"]
