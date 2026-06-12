#!/usr/bin/env python3
"""
events.py — two-sided event router (macro/news as RISK *and* OPPORTUNITY).

One event datum is two-sided: a scheduled binary you can't predict is a reason to
stand down (BRACE), but a breaking event that creates a directional move with a
concrete cause is exactly the high-conviction catalyst the momentum books want to
trade WITH (RIDE). After the vol spike, the now-rich index IV favors premium-selling
(FADE_VOL). The router decides which side applies by whether the move's direction is
already known.

Division of labor (house rule): deterministic code DETECTS, MAPS, and sets POSTURE;
the model PICKS the trade within the posture; code PROTECTS (defined-risk, the −$1500
floor, all flag-gated). With EVENT_ROUTER_ENABLED off this module is a no-op.

See docs/EVENT_ROUTER_SPEC.md for the full design.
"""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone

import config as cfg

try:
    import requests
    _OK = True
except Exception:
    _OK = False


# ===========================================================================
# Cache (econ calendar pulled once/day, IV baseline, model-map memo)
# ===========================================================================
def _load_cache() -> dict:
    try:
        return json.loads(cfg.EVENT_STATE_FILE.read_text())
    except Exception:
        return {}


def _save_cache(c: dict):
    try:
        cfg.EVENT_STATE_FILE.write_text(json.dumps(c, indent=2, default=str))
    except Exception:
        pass


def _log(msg: str):
    try:
        import autotrade as at
        at.log(msg)
    except Exception:
        pass


# ===========================================================================
# Detection — breaking news
# ===========================================================================
def _fetch_market_news(limit: int):
    """Latest MARKET-WIDE headlines from Alpaca (no symbol filter), newest first."""
    if not _OK:
        return []
    try:
        r = requests.get(
            "https://data.alpaca.markets/v1beta1/news",
            headers={"APCA-API-KEY-ID": cfg.ALPACA_API_KEY,
                     "APCA-API-SECRET-KEY": cfg.ALPACA_SECRET_KEY},
            params={"limit": limit, "sort": "desc"},
            timeout=10,
        ).json()
        return [{"headline": n.get("headline", ""),
                 "symbols": n.get("symbols", []) or [],
                 "created_at": n.get("created_at", "")} for n in r.get("news", [])]
    except Exception as e:
        _log(f"events: news fetch failed: {e}")
        return []


def _age_min(created_at: str, now) -> float | None:
    """Minutes since a headline's created_at (UTC ISO) relative to `now` (tz-aware)."""
    if not created_at:
        return None
    try:
        ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (now - ts).total_seconds() / 60.0
    except Exception:
        return None


def _classify(headline: str) -> str | None:
    """First EVENT_SIGNATURES theme whose keyword appears in the headline, else None."""
    h = (headline or "").lower()
    for theme, kws in cfg.EVENT_SIGNATURES.items():
        if any(k in h for k in kws):
            return theme
    return None


def _breaking_events(now) -> list[dict]:
    """Fresh, classified headlines, deduped to the freshest per theme."""
    out = {}
    for n in _fetch_market_news(cfg.EVENT_NEWS_LIMIT):
        age = _age_min(n["created_at"], now)
        if age is None or age < 0 or age > cfg.EVENT_FRESH_MIN:
            continue
        theme = _classify(n["headline"])
        if not theme:
            continue
        if theme not in out or age < out[theme]["age_min"]:
            out[theme] = {"headline": n["headline"], "theme": theme,
                          "age_min": round(age), "news_symbols": n["symbols"]}
    return list(out.values())


# ===========================================================================
# Detection — scheduled economic calendar (live feed: Financial Modeling Prep)
# ===========================================================================
def _fetch_econ_calendar(day_iso: str) -> list[dict]:
    """US high-impact events for `day_iso`, via FMP. Cached once/day in EVENT_STATE_FILE.
    Empty if no FMP_API_KEY (the BRACE half just stays dormant)."""
    if not _OK or not cfg.FMP_API_KEY:
        return []
    c = _load_cache()
    econ = c.get("econ") or {}
    if econ.get("date") == day_iso:
        return econ.get("events", [])
    try:
        r = requests.get(
            "https://financialmodelingprep.com/api/v3/economic_calendar",
            params={"from": day_iso, "to": day_iso, "apikey": cfg.FMP_API_KEY},
            timeout=12,
        ).json()
        events = []
        for e in (r or []):
            if e.get("country") not in cfg.EVENT_ECON_COUNTRIES:
                continue
            if (e.get("impact") or "").lower() != cfg.EVENT_ECON_MIN_IMPACT.lower():
                continue
            events.append({"name": e.get("event", ""), "date": e.get("date", ""),
                           "impact": e.get("impact", ""),
                           "actual": e.get("actual"), "estimate": e.get("estimate")})
        c["econ"] = {"date": day_iso, "events": events}
        _save_cache(c)
        return events
    except Exception as e:
        _log(f"events: econ calendar fetch failed: {e}")
        return []


def _event_dt(date_str: str, now):
    """Parse an FMP event time. FMP stamps US releases in ET wall-clock; attach `now`'s
    tzinfo so the comparison is apples-to-apples."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=now.tzinfo)
    except Exception:
        return None


def _scheduled(now) -> dict:
    """BRACE if a HIGH-impact US release is within EVENT_PRE_MIN ahead (and hasn't
    posted an `actual` yet). Returns {brace, next_event, post_surprise}."""
    events = _fetch_econ_calendar(now.strftime("%Y-%m-%d"))
    brace, nxt = False, None
    for e in events:
        dt = _event_dt(e.get("date", ""), now)
        if dt is None:
            continue
        mins_until = (dt - now).total_seconds() / 60.0
        # Upcoming, not yet released, inside the pre-window -> BRACE.
        if 0 <= mins_until <= cfg.EVENT_PRE_MIN and e.get("actual") in (None, ""):
            brace = True
            if nxt is None or mins_until < nxt["mins_until"]:
                nxt = {"name": e["name"], "mins_until": round(mins_until), "impact": e["impact"]}
        elif mins_until > cfg.EVENT_PRE_MIN:
            if nxt is None and not brace:
                nxt = {"name": e["name"], "mins_until": round(mins_until), "impact": e["impact"]}
    return {"brace": brace, "next_event": nxt}


# ===========================================================================
# Mapping — theme map (fast) with a cached model fallback for unmapped events
# ===========================================================================
def _map_theme(theme: str) -> dict | None:
    return cfg.EVENT_THEME_MAP.get(theme)


def _model_map(headline: str, news_symbols: list) -> dict | None:
    """Unmapped event: ask the model for the most-affected LIQUID instruments +
    direction, ONCE per headline (memoized in the cache). Falls back to the news
    item's own tagged symbols (as longs) if the model call fails."""
    key = hashlib.sha1((headline or "").encode()).hexdigest()[:16]
    c = _load_cache()
    memo = c.setdefault("model_map", {})
    if key in memo:
        return memo[key]
    result = None
    if _OK and cfg.ANTHROPIC_API_KEY:
        try:
            import autotrade as at
            prompt = (
                "A market event just broke. Name the most-affected LIQUID, optionable US "
                "instruments (large-cap stocks or major ETFs) and the direction the event "
                "pushes each. Return ONLY JSON: "
                '{"long":[tickers],"short":[tickers]} (<=5 each, [] if unsure).\n\n'
                f"Headline: {headline}")
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": cfg.ANTHROPIC_API_KEY,
                         "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": cfg.CLAUDE_MODEL, "max_tokens": 2000,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=60).json()
            if r.get("stop_reason") != "max_tokens" and "error" not in r:
                txt = "".join(b.get("text", "") for b in r.get("content", [])
                              if b.get("type") == "text")
                d = at._parse_model_json(txt)
                result = {"long": [s.upper() for s in (d.get("long") or [])][:5],
                          "short": [s.upper() for s in (d.get("short") or [])][:5],
                          "risk": "off"}
        except Exception as e:
            _log(f"events: model map failed: {e}")
    if not result:
        # Last resort: the news item's own tagged symbols, treated as the move's names
        # (direction unknown -> let the scan's signal decide; we only inject them).
        result = {"long": [s.upper() for s in (news_symbols or [])][:5], "short": [], "risk": "off"}
    memo[key] = result
    _save_cache(c)
    return result


# ===========================================================================
# IV rank (index proxy from a rolling VIX baseline we accumulate ourselves)
# ===========================================================================
def iv_rank(vix) -> float | None:
    """Percentile (0-100) of today's VIX within our stored trailing VIX history — the
    index IV-rank proxy that drives FADE_VOL. Updated once/day. None until enough
    history. Honest limit: a true per-name historical-IV rank needs paid data; this is
    the index-level proxy (good enough for the index premium-selling books)."""
    if vix is None:
        return None
    c = _load_cache()
    hist = c.get("vix_hist") or []
    today = None
    try:
        import autotrade as at
        today = at.et_now().strftime("%Y-%m-%d")
    except Exception:
        pass
    # Append at most once per day.
    if not hist or (today and hist[-1].get("d") != today):
        hist.append({"d": today, "v": round(float(vix), 2)})
        hist = hist[-120:]                       # ~6 months of trading days
        c["vix_hist"] = hist
        _save_cache(c)
    vals = [h["v"] for h in hist]
    if len(vals) < 20:
        return None                              # not enough history to rank
    rank = sum(1 for v in vals if v <= vix) / len(vals) * 100.0
    return round(rank, 1)


# ===========================================================================
# Top-level: assess the cycle's event posture
# ===========================================================================
def assess(state, scan, vix, now, dry) -> dict | None:
    """Resolve this cycle's event posture + RIDE targets + inject set. None when the
    router is off (callers treat None as 'no event influence')."""
    if not cfg.EVENT_ROUTER_ENABLED:
        return None
    breaking = _breaking_events(now)
    sched = _scheduled(now)

    ride = {}             # {symbol: {"dir": "long"|"short", "theme":..., "headline":...}}
    for ev in breaking:
        m = _map_theme(ev["theme"]) or _model_map(ev["headline"], ev["news_symbols"])
        if not m:
            continue
        for s in (m.get("long") or []):
            ride.setdefault(s, {"dir": "long", "theme": ev["theme"], "headline": ev["headline"]})
        for s in (m.get("short") or []):
            ride.setdefault(s, {"dir": "short", "theme": ev["theme"], "headline": ev["headline"]})

    ivr = iv_rank(vix)
    posture = "RIDE" if ride else ("BRACE" if sched["brace"] else
                                   ("FADE_VOL" if (ivr is not None and ivr >= cfg.EVENT_IV_RANK_HIGH) else "NEUTRAL"))
    return {
        "posture": posture,
        "ride": ride,
        "inject": sorted(ride.keys()),
        "brace": sched["brace"],
        "next_event": sched["next_event"],
        "iv_rank": ivr,
        "fade_vol": (ivr is not None and ivr >= cfg.EVENT_IV_RANK_HIGH),
        "breaking": [{"theme": e["theme"], "age_min": e["age_min"],
                      "headline": e["headline"][:140]} for e in breaking],
    }


# --- helpers the engine uses ------------------------------------------------
def ride_direction(event_state, symbol: str) -> str | None:
    """'long'/'short' if `symbol` is a current RIDE catalyst name, else None. Used to
    relax anti-chase ONLY for a name whose move matches the event direction."""
    if not event_state:
        return None
    r = (event_state.get("ride") or {}).get(symbol)
    return r["dir"] if r else None


def is_brace(event_state) -> bool:
    return bool(event_state and event_state.get("brace"))


def fade_vol(event_state) -> bool:
    return bool(event_state and event_state.get("fade_vol"))


def summary(event_state) -> dict | None:
    """Compact, model-facing view for the context block."""
    if not event_state:
        return None
    nxt = event_state.get("next_event")
    bits = []
    if event_state["posture"] == "RIDE":
        names = ", ".join(f"{s}({v['dir'][0].upper()})" for s, v in event_state["ride"].items())
        bits.append(f"RIDE {names} — fresh catalyst, anti-chase relaxed, trade WITH the move (defined-risk)")
    if event_state.get("brace") and nxt:
        bits.append(f"BRACE: {nxt['name']} in {nxt['mins_until']}m — no new short-premium into it")
    if event_state.get("fade_vol"):
        bits.append(f"FADE_VOL: IV rank {event_state['iv_rank']:.0f} — index premium-selling favored")
    if not bits and nxt:
        bits.append(f"next event: {nxt['name']} in {nxt['mins_until']}m")
    return {
        "posture": event_state["posture"],
        "iv_rank": event_state.get("iv_rank"),
        "ride": {s: v["dir"] for s, v in (event_state.get("ride") or {}).items()},
        "next_event": nxt,
        "breaking": event_state.get("breaking"),
        "guidance": " | ".join(bits) or "no live events",
    }
