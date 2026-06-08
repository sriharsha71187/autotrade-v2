#!/usr/bin/env python3
"""
earnings_crush.py — defined-risk short-premium into single-name earnings (IV crush).

The one return stream UNCORRELATED to market direction. The evidence: stocks move
LESS than their options-implied expected move ~70-75% of the time, and selling the
inflated pre-earnings premium (then buying it back after the announcement, once IV
collapses ~38% on average) is a structural edge — best when VIX is 16-22.

This book sells a DEFINED-RISK iron condor on a name reporting earnings tonight:
short strikes ~1 expected-move OTM (expected move ≈ the front-expiry ATM straddle),
EARNINGS_WING_WIDTH wings so max loss is capped at EARNINGS_MAX_RISK. It is opened
in the afternoon before the report and closed the morning after, harvesting the
crush. The structure is held OVERNIGHT through the print, so — unlike the model's
intraday condors — it is tracked in its OWN state ("earnings"), NOT in
active_multileg, and its leg symbols are added to held_books so the intraday EOD
spread-flatten and reconcile leave it alone. Realized P&L is booked under
"earnings_crush" in the shared ledger.

NOTE: earnings-date detection is via yfinance and is best-effort; on any missing or
ambiguous data the book stands down (no trade). This is the piece most in need of a
live-hours validation before its flag is enabled.
"""

from __future__ import annotations

import config as cfg

try:
    import yfinance as yf
    _YF_OK = True
except Exception:
    _YF_OK = False


def held_symbols(state: dict) -> set:
    h = (state.get("earnings") or {}).get("holding")
    if not h:
        return set()
    return {l["symbol"] for l in h.get("legs", [])}


def held_value(tc, state) -> float:
    syms = held_symbols(state)
    if not syms:
        return 0.0
    total = 0.0
    try:
        for p in tc.get_all_positions():
            if p.symbol in syms:
                total += abs(float(p.market_value or 0))
    except Exception:
        pass
    return total


def _earnings_today(sym, today):
    """True if `sym` reports earnings today (after the close) per yfinance. Best-
    effort: returns False on any missing/ambiguous data."""
    if not _YF_OK:
        return False
    try:
        df = yf.Ticker(sym).get_earnings_dates(limit=12)
        if df is None or df.empty:
            return False
        for ts in df.index:
            try:
                if ts.date() == today:
                    return True
            except Exception:
                continue
    except Exception:
        return False
    return False


def _next_earnings_name(today):
    """First universe name reporting today, or None."""
    for sym in cfg.EARNINGS_UNIVERSE:
        if sym in cfg.BLACKLIST:
            continue
        if _earnings_today(sym, today):
            return sym
    return None


def _front_chain(odc, sym, spot, today):
    """Full near-money chain for the nearest expiry strictly AFTER today (the one
    that prices the post-earnings move). Returns (expiry, rows) or (None, [])."""
    import autotrade as at
    if odc is None or not spot or spot <= 0:
        return None, []
    try:
        from alpaca.data.requests import OptionChainRequest
        chain = odc.get_option_chain(OptionChainRequest(
            underlying_symbol=sym,
            strike_price_gte=round(spot * 0.80, 2),
            strike_price_lte=round(spot * 1.20, 2),
        ))
    except Exception as e:
        at.log(f"earnings: chain fetch {sym} failed: {e}")
        return None, []
    rows = []
    for osym, snap in (chain or {}).items():
        meta = at.parse_occ(osym)
        if not meta:
            continue
        q = getattr(snap, "latest_quote", None)
        bid = float(getattr(q, "bid_price", 0) or 0) if q else 0.0
        ask = float(getattr(q, "ask_price", 0) or 0) if q else 0.0
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else (ask or bid or 0.0)
        rows.append({"symbol": osym, "type": meta["type"], "strike": meta["strike"],
                     "expiry": meta["expiry"], "bid": bid, "ask": ask, "mid": mid})
    future = sorted({r["expiry"] for r in rows if r["expiry"] > today.isoformat()})
    if not future:
        return None, []
    exp = future[0]
    return exp, [r for r in rows if r["expiry"] == exp and r["mid"] > 0]


def _nearest(rows, typ, target):
    grp = [r for r in rows if r["type"] == typ]
    return min(grp, key=lambda r: abs(r["strike"] - target)) if grp else None


def _build_condor(odc, sym, spot, today):
    """Construct a defined-risk condor ~1 expected-move wide. Returns
    (legs, net_credit_per_share, risk$) or (None, 0, None)."""
    exp, rows = _front_chain(odc, sym, spot, today)
    if not rows:
        return None, 0.0, None
    atm_call = _nearest(rows, "call", spot)
    atm_put = _nearest(rows, "put", spot)
    if not atm_call or not atm_put:
        return None, 0.0, None
    em = (atm_call["mid"] + atm_put["mid"])          # expected move ≈ ATM straddle
    if em <= 0:
        return None, 0.0, None
    short_call = _nearest(rows, "call", spot + em)
    short_put = _nearest(rows, "put", spot - em)
    if not short_call or not short_put:
        return None, 0.0, None
    long_call = _nearest(rows, "call", short_call["strike"] + cfg.EARNINGS_WING_WIDTH)
    long_put = _nearest(rows, "put", short_put["strike"] - cfg.EARNINGS_WING_WIDTH)
    if not long_call or not long_put:
        return None, 0.0, None
    legs = [
        {"symbol": short_call["symbol"], "side": "sell"},
        {"symbol": long_call["symbol"], "side": "buy"},
        {"symbol": short_put["symbol"], "side": "sell"},
        {"symbol": long_put["symbol"], "side": "buy"},
    ]
    # Net credit per share = sold mids − bought mids.
    net_credit = (short_call["mid"] + short_put["mid"]
                  - long_call["mid"] - long_put["mid"])
    if net_credit <= 0:
        return None, 0.0, None
    import autotrade as at
    risk = at.multileg_risk(legs, net_credit, 1)     # +credit convention
    return legs, round(net_credit, 2), risk


def _enter(tc, odc, state, dry, vix):
    import autotrade as at
    e = state.setdefault("earnings", {})
    now = at.et_now()
    today = now.date()
    today_s = today.isoformat()
    if e.get("holding"):
        return
    if e.get("last_entry_date") == today_s:
        return
    # Afternoon only (let pre-earnings IV inflate), and the right VIX band.
    if now.hour < 15:
        return
    if vix is None or not (cfg.EARNINGS_VIX_MIN <= vix <= cfg.EARNINGS_VIX_MAX):
        return
    sym = _next_earnings_name(today)
    if not sym:
        return
    e["last_entry_date"] = today_s                   # attempt at most once/day
    spot = None
    if _YF_OK:
        try:
            spot = float(yf.Ticker(sym).fast_info.last_price)
        except Exception:
            spot = None
    legs, net_credit, risk = _build_condor(odc, sym, spot, today)
    if not legs or risk is None:
        at.log(f"earnings: no defined-risk condor available for {sym} — standing down")
        at.save_state(state)
        return
    if risk > cfg.EARNINGS_MAX_RISK:
        at.log(f"earnings: {sym} condor risk ${risk:.0f} > cap ${cfg.EARNINGS_MAX_RISK:.0f} — skip")
        at.save_state(state)
        return
    if dry:
        at.log(f"[DRY] earnings would SELL condor {sym} net≈{net_credit} risk≈${risk:.0f} "
               f"legs={[l['symbol'] for l in legs]}")
        at.save_state(state)
        return
    try:
        o = at.place_multi_leg(tc, legs, net_credit, 1, dry)
        e["holding"] = {"underlying": sym, "legs": legs,
                        "entry_net": round(net_credit * 100, 2),  # $ credit received
                        "qty": 1, "risk": risk, "entry_date": today_s,
                        "order_id": str(getattr(o, "id", "")) or None,
                        "opened": now.isoformat()}
        at.log(f"EARNINGS condor SELL {sym} net≈{net_credit} risk≈${risk:.0f} "
               f"id={getattr(o,'id',None)}")
        at.tg_send(f"📅 Earnings IV-crush: sold a defined-risk condor on {sym} "
                   f"(credit ≈ ${net_credit*100:,.0f}, max risk ${risk:,.0f}).")
        at.save_state(state)
    except Exception as ex:
        at.log(f"earnings: condor submit {sym} failed: {ex}")


def _exit(tc, odc, state, dry):
    """The session AFTER entry (post-announcement), close the condor on the crush."""
    import autotrade as at
    e = state.setdefault("earnings", {})
    h = e.get("holding")
    if not h:
        return
    now = at.et_now()
    today_s = now.date().isoformat()
    if h.get("entry_date") == today_s:
        return                                       # earnings is tonight; hold
    # Post-earnings: close once the open has settled.
    if now.hour < 9 or (now.hour == 9 and now.minute < 35):
        return
    legs = h.get("legs", [])
    if dry:
        at.log(f"[DRY] earnings would CLOSE {h.get('underlying')} condor (post-crush)")
        return
    # Cost to close ≈ current net debit to buy it back; realized = credit − cost.
    cost = 0.0
    try:
        for l in legs:
            bid, ask, mid = at.option_quote(odc, l["symbol"])
            m = mid or 0.0
            cost += m if l["side"] == "sell" else -m   # buy back shorts, sell longs
    except Exception:
        cost = 0.0
    try:
        for l in legs:
            try:
                tc.close_position(l["symbol"])
            except Exception:
                pass
        realized = float(h.get("entry_net", 0.0)) - cost * 100
        at.record_strategy_realized(state, "earnings_crush", realized)
        e["holding"] = None
        e["last_realized"] = round(realized, 2)
        at.log(f"EARNINGS close {h.get('underlying')} realized≈{realized:+.0f}")
        at.tg_send(f"📅 Earnings IV-crush on {h.get('underlying')} closed: "
                   f"P&L ≈ ${realized:+,.0f}.")
        at.save_state(state)
    except Exception as ex:
        at.log(f"earnings: close failed: {ex}")


def run(tc, state, dry: bool, vix=None):
    if not cfg.EARNINGS_CRUSH_ENABLED:
        return
    import autotrade as at
    try:
        odc = at.option_data_client() if at._ALPACA_OK else None
        if vix is None:
            try:
                vix = at.get_vix()
            except Exception:
                vix = None
        _exit(tc, odc, state, dry)
        _enter(tc, odc, state, dry, vix)
    except Exception as e:
        at.log(f"earnings: run failed: {e}")


def summary(state: dict) -> dict:
    e = state.get("earnings", {}) or {}
    h = e.get("holding")
    return {
        "holding": ({"underlying": h["underlying"], "risk": h.get("risk"),
                     "entry_net": h.get("entry_net"), "entry_date": h.get("entry_date")}
                    if h else None),
        "last_realized": e.get("last_realized"),
        "last_entry_date": e.get("last_entry_date"),
    }
