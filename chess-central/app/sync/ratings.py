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
    _store("nwsrs", rating)
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
            results[source] = rating
            _store(source, rating)
    if not results:
        return {"ok": False, "reason": "no published ratings found (unrated or page changed)"}
    return {"ok": True, **results}


def _first_int_near(html: str, anchor: str, window: int = 600) -> int | None:
    """Find a plausible rating (3-4 digit int, 100-3000) near an anchor string."""
    idx = html.lower().find(anchor.lower())
    if idx < 0:
        return None
    chunk = html[idx:idx + window]
    for m in re.finditer(r">(\d{3,4})<", chunk):
        v = int(m.group(1))
        if 100 <= v <= 3000:
            return v
    for m in re.finditer(r"\b(\d{3,4})\b", re.sub(r"<[^>]+>", " ", chunk)):
        v = int(m.group(1))
        if 100 <= v <= 3000:
            return v
    return None


def _store(source: str, rating: int) -> None:
    with db.tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO ratings_history(source,date,rating) VALUES (?,?,?)",
            (source, util.now_iso()[:10], rating),
        )


def sync_all() -> dict:
    return {"nwsrs": sync_nwsrs(), "uscf": sync_uscf()}
