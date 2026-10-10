"""OTB rating sync: NWSRS (Northwest Scholastic Rating System) and US Chess.

Both sites are HTML-only, so these are best-effort scrapers with graceful
failure — the dashboard shows the last-known rating with its fetch date.
"""
from __future__ import annotations

import re

import httpx

from .. import config, db, util

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"}

# ----------------------------------------------------------------- NWSRS
# ratingsnw.com retired its per-player search page (playersearch.php -> 404).
# Ratings now live on alphabetical pages (ratings/ratingsT.php) and per-school
# reports (ratings/schoolreport.php?school=TBK); both are HTML tables with
# last name, first name, ID, rating, games, state, status columns.
NWSRS_BASE = "https://ratingsnw.com/ratings"


def sync_nwsrs() -> dict:
    nwsrs_id = (config.get("nwsrs_id") or "").strip()
    name = (config.get("player_name") or "").split()
    first, surname = (name[0], name[-1]) if name else ("", "")
    if not nwsrs_id and not surname:
        return {"ok": False, "reason": "no NWSRS id or player name configured"}

    pages = []
    if surname:
        pages.append(f"{NWSRS_BASE}/ratings{surname[0].upper()}.php")
    if len(nwsrs_id) >= 3:
        pages.append(f"{NWSRS_BASE}/schoolreport.php?school={nwsrs_id[:3].upper()}")

    errors = []
    with httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True) as client:
        for url in pages:
            try:
                r = client.get(url)
                r.raise_for_status()
            except Exception as e:
                errors.append(f"{url}: {_short(e)}")
                continue
            row = find_nwsrs_row(r.text, nwsrs_id, first, surname)
            if row:
                rating = row["rating"]
                if not _store("nwsrs", rating):
                    return {"ok": False, "reason": f"parsed {rating} rejected (implausible jump)"}
                return {"ok": True, "rating": rating, "games": row.get("games"),
                        "id": row.get("id"), "source_url": url}
            errors.append(f"{url}: player not in table")
    return {"ok": False, "reason": "NWSRS: " + "; ".join(errors)}


def _cells(tr_html: str) -> list[str]:
    cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr_html, re.S | re.I)
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip() for c in cells]


def find_nwsrs_row(html: str, nwsrs_id: str, first: str, surname: str) -> dict | None:
    """Locate the player's row (by ID, else by first+last name) and read the
    rating from the column headed 'Rating' (or the first plausible number)."""
    rows = [_cells(tr) for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I)]
    header = next((r for r in rows if any("rating" in c.lower() for c in r)
                   and not any(_int(c) for c in r)), None)
    col: dict[str, int] = {}
    for i, c in enumerate(header or []):
        key = "rating" if "rating" in c.lower() else c.lower()
        col.setdefault(key, i)

    def match(r):
        low = [c.lower() for c in r]
        if nwsrs_id and nwsrs_id.lower() in low:
            return True
        return bool(surname and first and surname.lower() in low
                    and any(c.startswith(first.lower()) for c in low))

    for r in rows:
        if r is header or not match(r):
            continue
        rating = None
        if "rating" in col and col["rating"] < len(r):
            rating = _int(r[col["rating"]])
        if rating is None:
            rating = next((v for v in map(_int, r) if v and _plausible(v)), None)
        if rating is None or not _plausible(rating):
            continue
        games_i = next((i for k, i in col.items() if k.startswith("game")), None)
        id_i = col.get("id") if "id" in col else next(
            (i for k, i in col.items() if k.startswith("id")), None)
        return {"rating": rating,
                "games": (int(r[games_i]) if games_i is not None and games_i < len(r)
                          and r[games_i].isdigit() else None),
                "id": r[id_i] if id_i is not None and id_i < len(r) else None}
    return None


# ----------------------------------------------------------------- US Chess
# The old MSA member pages now answer 403. The new ratings portal (MUIR) is
# backed by a JSON API: /api/v1/members/{id} -> {"ratings": [{"ratingSystem":
# "OverTheBoardRegular", "rating": 311, ...}, ...]}. US Chess calls it
# internal/unsupported, so parsing is deliberately lenient about field names.
USCF_API_HOSTS = ("https://ratings-api.uschess.org", "https://beta-ratings-api.uschess.org")
USCF_SYSTEMS = {
    ("overtheboard", "regular"): "uscf_regular",
    ("overtheboard", "quick"): "uscf_quick",
    ("overtheboard", "blitz"): "uscf_blitz",
    ("online", "regular"): "uscf_online_regular",
    ("online", "quick"): "uscf_online_quick",
    ("online", "blitz"): "uscf_online_blitz",
}


def sync_uscf() -> dict:
    uscf_id = (config.get("uscf_id") or "").strip()
    if not uscf_id:
        return {"ok": False, "reason": "no USCF id configured"}
    headers = {**HEADERS, "Accept": "application/json",
               "Origin": "https://ratings.uschess.org",
               "Referer": "https://ratings.uschess.org/"}
    errors = []
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        for host in USCF_API_HOSTS:
            url = f"{host}/api/v1/members/{uscf_id}"
            try:
                r = client.get(url)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                errors.append(f"{url}: {_short(e)}")
                continue
            found = parse_uscf_ratings(data)
            if not found:
                return {"ok": False, "reason": "US Chess: member found but no published "
                        "ratings (unrated yet, or the API format changed)"}
            results = {}
            for source, rating in found.items():
                if _store(source, rating):
                    results[source] = rating
            return {"ok": True, **results}
    return {"ok": False, "reason": "US Chess: " + "; ".join(errors)}


def parse_uscf_ratings(data) -> dict[str, int]:
    """{'uscf_regular': 311, ...} from a members payload, tolerant of shape."""
    entries = []
    if isinstance(data, dict):
        for key in ("ratings", "Ratings", "memberRatings"):
            if isinstance(data.get(key), list):
                entries = data[key]
                break
        else:
            inner = data.get("data") or data.get("member")
            if isinstance(inner, dict):
                return parse_uscf_ratings(inner)
    out: dict[str, int] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        label = ""
        for k in ("ratingSystem", "ratingSystemCode", "system", "type", "name"):
            v = e.get(k)
            if isinstance(v, dict):
                v = v.get("name") or v.get("code")
            if isinstance(v, str):
                label = v
                break
        norm = re.sub(r"[^a-z]", "", label.lower())
        rating = None
        for k in ("rating", "value", "ratingValue", "currentRating"):
            rating = _int(e.get(k))
            if rating is not None:
                break
        if not norm or rating is None or not (100 <= rating <= 3000):
            continue
        venue = "online" if "online" in norm else "overtheboard"
        for (v, speed), source in USCF_SYSTEMS.items():
            if v == venue and speed in norm:
                out[source] = rating
    return out


def _int(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        m = re.match(r"\s*(\d{3,4})(?:/\d+)?\s*[PpE*]?\s*$", v)
        return int(m.group(1)) if m else None
    return None


def _plausible(v: int) -> bool:
    return 100 <= v <= 2400 and not (1900 <= v <= 2100)


def _short(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    return str(e).split("\n")[0][:120]


def _first_int_near(html: str, anchor: str, window: int = 600) -> int | None:
    """Find a plausible rating (3-4 digit int) near an anchor string.

    Scholastic/OTB ratings live in 100-2400; years (1900-2100) are excluded
    because a page full of dates is the classic false positive.
    """
    idx = html.lower().find(anchor.lower())
    if idx < 0:
        return None
    chunk = html[idx:idx + window]

    def plausible(v: int) -> bool:
        return 100 <= v <= 2400 and not (1900 <= v <= 2100)

    for m in re.finditer(r">(\d{3,4})<", chunk):
        if plausible(int(m.group(1))):
            return int(m.group(1))
    for m in re.finditer(r"\b(\d{3,4})\b", re.sub(r"<[^>]+>", " ", chunk)):
        if plausible(int(m.group(1))):
            return int(m.group(1))
    return None


MAX_RATING_JUMP = 400   # a single-day OTB move bigger than this is a parse error


def _store(source: str, rating: int) -> bool:
    """Store today's rating unless it's an implausible jump from the last known."""
    last = db.row(
        "SELECT rating, date FROM ratings_history WHERE source=? AND date < ? "
        "ORDER BY date DESC LIMIT 1", (source, util.now_iso()[:10]))
    if last and abs(rating - last["rating"]) > MAX_RATING_JUMP:
        return False   # keep the history clean; a real jump will persist next scrape
    with db.tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO ratings_history(source,date,rating) VALUES (?,?,?)",
            (source, util.now_iso()[:10], rating),
        )
    return True


def sync_all() -> dict:
    return {"nwsrs": sync_nwsrs(), "uscf": sync_uscf()}


if __name__ == "__main__":     # python -m app.sync.ratings  -> live diagnostic
    import json
    print(json.dumps(sync_all(), indent=2))
