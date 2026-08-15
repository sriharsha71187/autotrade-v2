"""Tournament finder: OTB events near Seattle/Eastside + national + online.

Sources:
  * seed      — the recurring PNW scholastic calendar (always available, marked
                "verify dates on site"; these events happen every year)
  * nwchess   — scraped from the NW Chess / NWSRS upcoming-events pages
  * uschess   — scraped from the US Chess upcoming-tournaments listing (WA + national)
  * lichess   — online arenas/swisses with rating caps that fit his level
  * manual    — anything you add by hand

Scrapers are best-effort: sites change, so failures degrade to seed + manual
rather than breaking the page.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx

from .. import config, db, util

HEADERS = {"User-Agent": "Mozilla/5.0 (nirvaan-chess-central; personal coaching app)"}

# The recurring Pacific-Northwest scholastic circuit. Month numbers are the
# typical slot; the UI shows "typically <Month> — verify" and the link to check.
SEED_EVENTS = [
    {"name": "Chess4Life Quads (Bellevue)", "month": 0, "city": "Bellevue",
     "url": "https://chess4life.com", "recurring_note":
     "Runs most weekends year-round; beginner-friendly, NWSRS rated. The ideal first OTB events.",
     "sections": "Quads by rating", "online": 0},
    {"name": "Orlov Chess Academy tournaments (Redmond/Bellevue)", "month": 0,
     "city": "Redmond", "url": "https://www.chesscoachseattle.com",
     "recurring_note": "Regular scholastic events on the Eastside, NWSRS rated.",
     "sections": "By grade/rating", "online": 0},
    {"name": "Seattle Chess Club — scholastic & quads", "month": 0, "city": "Seattle",
     "url": "https://www.seattlechess.org", "recurring_note":
     "Frequent weekend events; some USCF rated — good bridge to USCF play.",
     "sections": "Various", "online": 0},
    {"name": "WA State Elementary Chess Championship", "month": 4, "city": "varies (WA)",
     "url": "https://chess.wa-chess.org", "recurring_note":
     "THE spring target for elementary players statewide. Typically late April.",
     "sections": "By grade K-6", "online": 0},
    {"name": "Washington Winter Scholastic Classic", "month": 1, "city": "varies (WA)",
     "url": "https://nwchess.com", "recurring_note": "Typically January.",
     "sections": "By grade/rating", "online": 0},
    {"name": "Washington Open (scholastic side events)", "month": 5, "city": "Redmond",
     "url": "https://nwchess.com", "recurring_note":
     "Memorial Day weekend; big event with sections for all levels. USCF rated.",
     "sections": "Open to U1000+", "online": 0},
    {"name": "Seattle Seafair Open", "month": 7, "city": "Seattle",
     "url": "https://www.seattlechess.org", "recurring_note":
     "Summer USCF event; lower sections are scholastic-friendly.",
     "sections": "By rating", "online": 0},
    {"name": "National Elementary (K-6) Championship", "month": 5, "city": "national — travel",
     "url": "https://new.uschess.org/national-elementary-championship",
     "recurring_note": "USCF nationals, typically May. Needs USCF membership; sections by grade "
     "and rating mean beginners compete against beginners. A great family-trip goal.",
     "sections": "K-1, K-3, K-5, K-6 by rating", "online": 0},
    {"name": "National K-12 Grade Championships", "month": 12, "city": "national — travel",
     "url": "https://new.uschess.org/k-12-grade-championship",
     "recurring_note": "USCF nationals by exact grade, typically December.",
     "sections": "By grade", "online": 0},
    {"name": "Lichess kid-friendly arenas (rating-capped)", "month": 0, "city": "online",
     "url": "https://lichess.org/tournament", "recurring_note":
     "Under-1500/Under-1300 arenas run daily; zero cost, instant practice.",
     "sections": "U1300/U1500", "online": 1},
    {"name": "ChessKid / Chess.com scholastic events", "month": 0, "city": "online",
     "url": "https://www.chesskid.com", "recurring_note":
     "Safe kid-focused online events; good between OTB tournaments.",
     "sections": "By rating", "online": 1},
]


def ensure_seeds() -> None:
    """Insert recurring PNW events, and roll any past annual date to next year
    so yearly events never silently vanish from the list."""
    with db.tx() as conn:
        for i, ev in enumerate(SEED_EVENTS):
            next_date = _next_occurrence(ev["month"])
            conn.execute(
                """INSERT OR IGNORE INTO tournaments
                   (source, external_id, name, starts_at, city, url, sections,
                    online, near_home, recurring_note, fetched_at)
                   VALUES ('seed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (f"seed_{i}", ev["name"], next_date,
                 ev["city"], ev["url"], ev["sections"], ev["online"],
                 1 if ev["city"].lower() not in ("online", "national — travel", "varies (wa)")
                 else 0,
                 ev["recurring_note"], util.now_iso()))
            if next_date:
                conn.execute(
                    """UPDATE tournaments SET starts_at=?, status='new'
                       WHERE source='seed' AND external_id=? AND starts_at < ?""",
                    (next_date, f"seed_{i}", util.now_iso()[:10]))


def _next_occurrence(month: int) -> str | None:
    """Next future occurrence (1st of month) for a yearly seed; None = always-on."""
    if not month:
        return None
    today = datetime.now(timezone.utc)
    year = today.year if month > today.month else today.year + 1
    return f"{year}-{month:02d}-01"


def refresh() -> dict:
    """Re-scrape live sources. Each source fails independently."""
    ensure_seeds()
    out = {}
    for name, fn in (("nwchess", _scrape_nwchess),
                     ("uschess", _scrape_uschess),
                     ("lichess_online", _fetch_lichess_arenas)):
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = {"error": str(e)}
    db.kv_set("tournaments_refreshed", util.now_iso())
    return out


def _is_near_home(city: str | None) -> int:
    if not city:
        return 0
    c = city.lower()
    return int(any(n in c for n in config.get("nearby_cities")))


def _upsert(source: str, external_id: str, **fields) -> int:
    with db.tx() as conn:
        cur = conn.execute(
            """INSERT INTO tournaments (source, external_id, name, starts_at, city,
                 venue, url, sections, rating_min, rating_max, online, near_home, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source, external_id) DO UPDATE SET
                 name=excluded.name, starts_at=excluded.starts_at, city=excluded.city,
                 url=excluded.url, sections=excluded.sections, fetched_at=excluded.fetched_at""",
            (source, external_id, fields.get("name"), fields.get("starts_at"),
             fields.get("city"), fields.get("venue"), fields.get("url"),
             fields.get("sections"), fields.get("rating_min"), fields.get("rating_max"),
             fields.get("online", 0),
             fields.get("near_home", _is_near_home(fields.get("city"))),
             util.now_iso()))
        return cur.rowcount


DATE_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})(?:\s*[-–]\s*\d{1,2})?,?\s+(\d{4})",
    re.I)
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _parse_date(text: str) -> str | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    mon = MONTHS[m.group(1)[:3].lower()]
    return f"{m.group(3)}-{mon:02d}-{int(m.group(2)):02d}"


def _scrape_nwchess() -> dict:
    """NW Chess 'Upcoming Events' page: rows of <a>event</a> with dates nearby."""
    found = 0
    with httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True) as client:
        r = client.get("https://nwchess.com/calendar/")
        r.raise_for_status()
        html = r.text
    # anchor + surrounding text chunks
    for m in re.finditer(r"<a[^>]+href=\"([^\"]+)\"[^>]*>([^<]{8,120})</a>", html):
        url, name = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
        if not re.search(r"(chess|open|scholastic|quad|championship|classic|swiss)", name, re.I):
            continue
        ctx = html[max(0, m.start() - 300): m.end() + 300]
        date = _parse_date(ctx)
        if not date:
            continue
        city = None
        cm = re.search(r"(Seattle|Bellevue|Redmond|Kirkland|Tacoma|Everett|Renton|"
                       r"Issaquah|Sammamish|Bothell|Portland|Spokane|Vancouver)", ctx, re.I)
        if cm:
            city = cm.group(1)
        if not url.startswith("http"):
            url = "https://nwchess.com" + (url if url.startswith("/") else "/" + url)
        found += _upsert("nwchess", f"{name}_{date}", name=name, starts_at=date,
                         city=city, url=url)
    return {"found": found}


def _scrape_uschess() -> dict:
    """US Chess upcoming-tournaments listing, WA page + national events."""
    found = 0
    state = config.get("home_state") or "WA"
    with httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True) as client:
        r = client.get("https://new.uschess.org/upcoming-tournaments",
                       params={"state": state})
        r.raise_for_status()
        html = r.text
    for m in re.finditer(
            r"<a[^>]+href=\"(/tournaments?/[^\"]+|https://new\.uschess\.org/[^\"]+)\"[^>]*>([^<]{8,140})</a>",
            html):
        url, name = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
        ctx = html[max(0, m.start() - 400): m.end() + 400]
        date = _parse_date(ctx)
        if not date:
            continue
        cm = re.search(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)?),\s*" + state + r"\b", ctx)
        city = cm.group(1) if cm else None
        if url.startswith("/"):
            url = "https://new.uschess.org" + url
        found += _upsert("uschess", f"{name}_{date}", name=name, starts_at=date,
                         city=city, url=url)
    return {"found": found}


def _fetch_lichess_arenas() -> dict:
    """Current/upcoming lichess arenas whose rating cap fits his level."""
    my_rating = db.scalar(
        "SELECT rating FROM ratings_history WHERE source='lichess_rapid' "
        "ORDER BY date DESC LIMIT 1") or 800
    with httpx.Client(timeout=30, headers={"User-Agent": HEADERS["User-Agent"],
                                           "Accept": "application/json"}) as client:
        r = client.get("https://lichess.org/api/tournament")
        r.raise_for_status()
        data = r.json()
    found = 0
    for bucket in ("created", "started"):
        for t in data.get(bucket, []):
            cap = ((t.get("conditions") or {}).get("maxRating") or {}).get("rating")
            if cap and cap <= max(1500, my_rating + 400):
                starts = t.get("startsAt")
                if isinstance(starts, (int, float)):
                    starts = datetime.fromtimestamp(
                        starts / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                found += _upsert(
                    "lichess", t["id"], name=f"{t.get('fullName', 'Arena')} (U{cap})",
                    starts_at=starts, city="online",
                    url=f"https://lichess.org/tournament/{t['id']}",
                    sections=f"U{cap} · {t.get('minutes')}min",
                    rating_max=cap, online=1)
    return {"found": found}


def upcoming(include_online: bool = True) -> list[dict]:
    ensure_seeds()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    rows = db.rows(
        """SELECT * FROM tournaments
           WHERE status != 'skipped' AND (starts_at IS NULL OR starts_at >= ?)
           ORDER BY online, starts_at IS NULL, starts_at""", (cutoff,))
    if not include_online:
        rows = [r for r in rows if not r["online"]]
    return rows


def add_manual(fields: dict) -> int:
    ext = f"manual_{fields.get('name', '')}_{fields.get('starts_at', '')}"
    _upsert("manual", ext, **fields)
    return db.scalar("SELECT id FROM tournaments WHERE source='manual' AND external_id=?",
                     (ext,))


def set_status(tid: int, status: str) -> None:
    if status not in ("new", "interested", "registered", "played", "skipped"):
        raise ValueError("bad status")
    with db.tx() as conn:
        conn.execute("UPDATE tournaments SET status=? WHERE id=?", (status, tid))
