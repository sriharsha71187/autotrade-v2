"""OTB rating sync: NWSRS (Northwest Scholastic Rating System) and US Chess.

Both sites are HTML-only, so these are best-effort scrapers with graceful
failure — the dashboard shows the last-known rating with its fetch date.
"""
from __future__ import annotations

import re

import httpx

from .. import config, db, util

HEADERS = {"User-Agent": "Mozilla/5.0 (nirvaan-chess-central; personal coaching app)"}


def sync_nwsrs() -> dict:
    """Look up the player's NWSRS rating by ID on ratingsnw.com."""
    nwsrs_id = (config.get("nwsrs_id") or "").strip()
    if not nwsrs_id:
        return {"ok": False, "reason": "no NWSRS id configured"}
    try:
        with httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True) as client:
            r = client.get("https://ratingsnw.com/playersearch.php",
                           params={"id": nwsrs_id})
            r.raise_for_status()
            html = r.text
    except Exception as e:
        return {"ok": False, "reason": f"NWSRS unreachable: {e}"}

    rating = _first_int_near(html, nwsrs_id)
    if rating is None:
        # fallback: search full ratings list by last name
        rating = _nwsrs_ratings_page(config.get("player_name"))
    if rating is None:
        return {"ok": False, "reason": "rating not found on page"}
    if not _store("nwsrs", rating):
        return {"ok": False, "reason": f"parsed {rating} rejected (implausible jump)"}
    return {"ok": True, "rating": rating}


def _nwsrs_ratings_page(player_name: str) -> int | None:
    """Scan the alphabetical ratings page for the player's surname."""
    surname = (player_name or "").split()[-1]
    if not surname:
        return None
    letter = surname[0].upper()
    try:
        with httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True) as client:
            r = client.get(f"https://ratingsnw.com/ratings/ratings{letter}.php")
            r.raise_for_status()
            html = r.text
    except Exception:
        return None
    return _first_int_near(html, surname)


def sync_uscf() -> dict:
    """Fetch the player's published US Chess ratings from the MSA page."""
    uscf_id = (config.get("uscf_id") or "").strip()
    if not uscf_id:
        return {"ok": False, "reason": "no USCF id configured"}
    try:
        with httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True) as client:
            r = client.get(f"https://www.uschess.org/msa/MbrDtlMain.php?{uscf_id}")
            r.raise_for_status()
            html = r.text
    except Exception as e:
        return {"ok": False, "reason": f"US Chess unreachable: {e}"}

    results = {}
    for label, source in (("Regular Rating", "uscf_regular"),
                          ("Quick Rating", "uscf_quick"),
                          ("Blitz Rating", "uscf_blitz")):
        m = re.search(label + r".{0,400}?(\d{3,4})", html, re.S)
        if m:
            rating = int(m.group(1))
            if 100 <= rating <= 2400 and _store(source, rating):
                results[source] = rating
    if not results:
        return {"ok": False, "reason": "no published ratings found (unrated or page changed)"}
    return {"ok": True, **results}


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
