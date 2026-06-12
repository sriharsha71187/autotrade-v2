#!/usr/bin/env python3
"""
options_intel.py — free options-derived signals from Alpaca's per-contract IV + greeks.

No paid flow data (Unusual Whales etc.); this builds the "options sentiment/positioning"
signal from data we already pull. Per underlying it computes:

  - ATM_IV     : at-the-money implied vol (the real underlying IV, not the VIX proxy).
  - IV_RANK    : percentile of today's ATM IV within a rolling history we accumulate
                 ourselves (daily) — upgrades the event router's FADE_VOL from a
                 VIX-based proxy to the actual name's IV.
  - SKEW       : 25-delta risk reversal = IV(25Δ call) − IV(25Δ put). Negative => puts
                 richer => downside fear / defensive positioning; positive => upside
                 demand / call-chasing. A directional sentiment read.

Honest limits: Alpaca exposes IV + greeks but NOT open interest or trade-level flow, so
this is positioning-by-vol, not true "unusual options activity" (volume≫OI, sweeps).
For that you'd need a paid flow feed.
"""

from __future__ import annotations

import json

import config as cfg

try:
    from alpaca.data.requests import OptionChainRequest
    _OK = True
except Exception:
    _OK = False


def _load() -> dict:
    try:
        return json.loads(cfg.OPTIONS_INTEL_FILE.read_text())
    except Exception:
        return {}


def _save(c: dict):
    try:
        cfg.OPTIONS_INTEL_FILE.write_text(json.dumps(c, default=str))
    except Exception:
        pass


def _rows(odc, underlying: str, spot: float):
    """Near-money contracts for `underlying` as [{type, strike, expiry, iv, delta}]."""
    import autotrade as at
    try:
        chain = odc.get_option_chain(OptionChainRequest(
            underlying_symbol=underlying,
            strike_price_gte=round(spot * 0.80, 2),
            strike_price_lte=round(spot * 1.20, 2)))
    except Exception as e:
        at.log(f"options_intel: chain {underlying} failed: {e}")
        return []
    out = []
    for osym, snap in (chain or {}).items():
        meta = at.parse_occ(osym)
        iv = getattr(snap, "implied_volatility", None)
        g = getattr(snap, "greeks", None)
        delta = getattr(g, "delta", None) if g else None
        if not meta or iv is None or delta is None:
            continue
        out.append({"type": meta["type"], "strike": meta["strike"],
                    "expiry": meta["expiry"], "iv": float(iv), "delta": float(delta)})
    return out


def _target_expiry(rows, today, target_dte=30):
    """The listed expiry whose DTE is closest to target_dte (skip 0DTE noise)."""
    from datetime import date
    exps = sorted({r["expiry"] for r in rows if r["expiry"] > today})
    if not exps:
        return None
    def dte(e):
        y, m, d = (int(x) for x in e.split("-"))
        return abs((date(y, m, d) - date(*[int(x) for x in today.split("-")])).days - target_dte)
    return min(exps, key=dte)


def iv_rank(underlying: str, atm_iv: float, today: str) -> float | None:
    """Percentile (0-100) of today's ATM IV within our stored rolling history for this
    name. Appended at most once/day; None until enough history (≥20 days)."""
    c = _load()
    hist = (c.get("iv_hist") or {}).get(underlying) or []
    if not hist or hist[-1].get("d") != today:
        hist.append({"d": today, "v": round(atm_iv, 4)})
        hist = hist[-120:]
        c.setdefault("iv_hist", {})[underlying] = hist
        _save(c)
    vals = [h["v"] for h in hist]
    if len(vals) < 20:
        return None
    return round(sum(1 for v in vals if v <= atm_iv) / len(vals) * 100.0, 1)


def compute(odc, underlying: str, spot: float, now) -> dict | None:
    """Full intel for one underlying, or None if the chain can't be read."""
    if not _OK or odc is None or not spot or spot <= 0:
        return None
    today = now.date().isoformat()
    rows = _rows(odc, underlying, spot)
    if not rows:
        return None
    exp = _target_expiry(rows, today)
    if not exp:
        return None
    leg = [r for r in rows if r["expiry"] == exp]
    calls = [r for r in leg if r["type"] == "call"]
    puts = [r for r in leg if r["type"] == "put"]
    if not calls or not puts:
        return None
    # ATM IV: average of the nearest-strike call and put.
    atm_c = min(calls, key=lambda r: abs(r["strike"] - spot))
    atm_p = min(puts, key=lambda r: abs(r["strike"] - spot))
    atm_iv = (atm_c["iv"] + atm_p["iv"]) / 2.0
    # 25-delta risk reversal (skew).
    c25 = min(calls, key=lambda r: abs(r["delta"] - 0.25))
    p25 = min(puts, key=lambda r: abs(abs(r["delta"]) - 0.25))
    skew = c25["iv"] - p25["iv"]                       # <0 => put skew (fear)
    if skew <= -cfg.OPTIONS_SKEW_THRESHOLD:
        label = "put skew (downside fear / defensive positioning)"
    elif skew >= cfg.OPTIONS_SKEW_THRESHOLD:
        label = "call skew (upside demand / call-chasing)"
    else:
        label = "flat"
    return {
        "underlying": underlying,
        "atm_iv": round(atm_iv * 100, 1),             # as a % (e.g. 19.8)
        "iv_rank": iv_rank(underlying, atm_iv, today),
        "skew": round(skew * 100, 1),                 # vol points (call − put)
        "skew_label": label,
        "expiry": exp,
    }
