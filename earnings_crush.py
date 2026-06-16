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


def _holdings(state: dict) -> list:
    """Open earnings condors as a list (AUDIT_ROADMAP #23 broadened the book from a single
    nightly condor to up to EARNINGS_MAX_CONCURRENT). Reads the new 'holdings' list and
    transparently lifts a legacy single 'holding' dict into the list so an in-flight
    position from the old single-condor format is never orphaned."""
    e = state.get("earnings") or {}
    out = list(e.get("holdings") or [])
    legacy = e.get("holding")
    if legacy:
        out.append(legacy)
    return out


def held_symbols(state: dict) -> set:
    syms = set()
    for h in _holdings(state):
        syms.update(l["symbol"] for l in h.get("legs", []))
    return syms


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
    """True if `sym` reports AFTER THE CLOSE (AMC) today per yfinance — i.e. there is
    still an UPCOMING binary tonight. A morning (BMO) report already happened: its IV
    already crushed at the open, so it is neither a premium-selling setup nor a reason
    to block an afternoon momentum hold. Best-effort: False on missing data, but
    conservative (True) when the report is today with an unknown time."""
    if not _YF_OK:
        return False
    try:
        df = yf.Ticker(sym).get_earnings_dates(limit=12)
        if df is None or df.empty:
            return False
        for ts in df.index:
            try:
                if ts.date() != today:
                    continue
                hour = getattr(ts, "hour", 0)
                # AMC ~16:00+ ET counts; BMO (~4:00–12:00) already passed -> skip.
                # hour == 0 usually means "time not supplied" -> treat as unknown AMC.
                if hour >= 16 or hour == 0:
                    return True
            except Exception:
                continue
    except Exception:
        return False
    return False


def _earnings_names_tonight(today, exclude=None):
    """ALL universe names reporting AMC tonight (AUDIT_ROADMAP #23), excluding blacklisted
    names and any already in `exclude` (names we already hold a condor on). Order follows
    EARNINGS_UNIVERSE. Replaces the old _next_earnings_name (which stopped at the first)."""
    exclude = exclude or set()
    out = []
    for sym in cfg.EARNINGS_UNIVERSE:
        if sym in cfg.BLACKLIST or sym in exclude:
            continue
        if _earnings_today(sym, today):
            out.append(sym)
    return out


def _legs_tight(legs, rows_by_sym):
    """True if EVERY leg quotes a TIGHT two-sided market (AUDIT_ROADMAP #23): bid/ask within
    EARNINGS_MAX_LEG_SPREAD_PCT of mid. A wide condor donates the spread on all four legs at
    entry AND exit, so reject the name rather than sell into a thin market."""
    for l in legs:
        r = rows_by_sym.get(l["symbol"])
        if not r:
            return False
        bid, ask, mid = r.get("bid") or 0.0, r.get("ask") or 0.0, r.get("mid") or 0.0
        if bid <= 0 or ask <= 0 or mid <= 0:
            return False
        if (ask - bid) / mid > cfg.EARNINGS_MAX_LEG_SPREAD_PCT:
            return False
    return True


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
    from datetime import timedelta
    future = sorted({r["expiry"] for r in rows if r["expiry"] > today.isoformat()})
    if not future:
        return None, []
    # Prefer an expiry with a DTE buffer so a single missed post-earnings close doesn't
    # let the condor expire ITM and assign. Fall back to the nearest if nothing further
    # out is listed (still defined-risk; just less buffer).
    min_exp = (today + timedelta(days=cfg.EARNINGS_MIN_DTE)).isoformat()
    buffered = [e for e in future if e >= min_exp]
    exp = buffered[0] if buffered else future[0]
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
    # Tight-market eligibility (#23): all four legs must quote a tight two-sided market,
    # else the condor donates the spread four times over at entry and exit.
    rows_by_sym = {r["symbol"]: r for r in rows}
    if not _legs_tight(legs, rows_by_sym):
        import autotrade as at
        at.log(f"earnings: {sym} condor legs too wide (> {cfg.EARNINGS_MAX_LEG_SPREAD_PCT:.0%}) — skip")
        return None, 0.0, None
    # Net credit per share = sold mids − bought mids.
    net_credit = (short_call["mid"] + short_put["mid"]
                  - long_call["mid"] - long_put["mid"])
    if net_credit <= 0:
        return None, 0.0, None
    import autotrade as at
    risk = at.multileg_risk(legs, net_credit, 1)     # +credit convention
    return legs, round(net_credit, 2), risk


def eligible_tonight(odc, state, today, vix):
    """Deterministic eligibility (AUDIT_ROADMAP #23), broken out so it is unit-testable
    without placing orders. Returns the list of {underlying, legs, net_credit, risk} for
    every name reporting tonight that:
      * isn't already held / blacklisted,
      * builds a defined-risk, TIGHT-spread condor (per _build_condor),
      * is within EARNINGS_MAX_RISK individually, AND
      * still fits the shared EARNINGS_NIGHT_RISK_BUDGET alongside open + already-chosen
        condors, capped at EARNINGS_MAX_CONCURRENT total open.
    VIX-band + afternoon gating is applied by the caller (_enter); pass vix=None to skip
    the band here (the test exercises eligibility directly)."""
    import autotrade as at
    held = _holdings(state)
    if len(held) >= cfg.EARNINGS_MAX_CONCURRENT:
        return []
    used_risk = sum(float(h.get("risk") or 0.0) for h in held)
    held_names = {h.get("underlying") for h in held}
    chosen = []
    for sym in _earnings_names_tonight(today, exclude=held_names):
        if len(held) + len(chosen) >= cfg.EARNINGS_MAX_CONCURRENT:
            break
        spot = None
        if _YF_OK:
            try:
                spot = float(yf.Ticker(sym).fast_info.last_price)
            except Exception:
                spot = None
        legs, net_credit, risk = _build_condor(odc, sym, spot, today)
        if not legs or risk is None:
            continue
        if risk > cfg.EARNINGS_MAX_RISK:
            at.log(f"earnings: {sym} condor risk ${risk:.0f} > per-trade cap "
                   f"${cfg.EARNINGS_MAX_RISK:.0f} — skip")
            continue
        if used_risk + risk > cfg.EARNINGS_NIGHT_RISK_BUDGET:
            at.log(f"earnings: {sym} risk ${risk:.0f} would exceed night budget "
                   f"${cfg.EARNINGS_NIGHT_RISK_BUDGET:.0f} (used ${used_risk:.0f}) — skip")
            continue
        used_risk += risk
        chosen.append({"underlying": sym, "legs": legs,
                       "net_credit": round(net_credit, 2), "risk": risk})
    return chosen


def _enter(tc, odc, state, dry, vix):
    import autotrade as at
    e = state.setdefault("earnings", {})
    e.setdefault("holdings", [])
    now = at.et_now()
    today = now.date()
    today_s = today.isoformat()
    if len(_holdings(state)) >= cfg.EARNINGS_MAX_CONCURRENT:
        return
    if e.get("last_entry_date") == today_s:
        return
    # Afternoon only (let pre-earnings IV inflate), and the right VIX band.
    if now.hour < 15:
        return
    if vix is None or not (cfg.EARNINGS_VIX_MIN <= vix <= cfg.EARNINGS_VIX_MAX):
        return
    e["last_entry_date"] = today_s                   # attempt at most once/day
    picks = eligible_tonight(odc, state, today, vix)
    if not picks:
        at.log("earnings: no eligible defined-risk condor tonight — standing down")
        at.save_state(state)
        return
    for p in picks:
        sym, legs = p["underlying"], p["legs"]
        net_credit, risk = p["net_credit"], p["risk"]
        if dry:
            at.log(f"[DRY] earnings would SELL condor {sym} net≈{net_credit} risk≈${risk:.0f} "
                   f"legs={[l['symbol'] for l in legs]}")
            continue
        try:
            o = at.place_multi_leg(tc, legs, net_credit, 1, dry)
            e["holdings"].append({"underlying": sym, "legs": legs,
                                  "entry_net": round(net_credit * 100, 2),  # $ credit received
                                  "qty": 1, "risk": risk, "entry_date": today_s,
                                  "order_id": str(getattr(o, "id", "")) or None,
                                  "opened": now.isoformat()})
            at.log(f"EARNINGS condor SELL {sym} net≈{net_credit} risk≈${risk:.0f} "
                   f"id={getattr(o,'id',None)}")
            at.tg_send(f"📅 Earnings IV-crush: sold a defined-risk condor on {sym} "
                       f"(credit ≈ ${net_credit*100:,.0f}, max risk ${risk:,.0f}).")
        except Exception as ex:
            at.log(f"earnings: condor submit {sym} failed: {ex}")
    at.save_state(state)


def _close_one(tc, odc, state, h, dry) -> bool:
    """Close a single earnings condor post-crush. Returns True if it was closed (so the
    caller can drop it from holdings)."""
    import autotrade as at
    legs = h.get("legs", [])
    if dry:
        at.log(f"[DRY] earnings would CLOSE {h.get('underlying')} condor (post-crush)")
        return False
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
        state.setdefault("earnings", {})["last_realized"] = round(realized, 2)
        at.log(f"EARNINGS close {h.get('underlying')} realized≈{realized:+.0f}")
        at.tg_send(f"📅 Earnings IV-crush on {h.get('underlying')} closed: "
                   f"P&L ≈ ${realized:+,.0f}.")
        return True
    except Exception as ex:
        at.log(f"earnings: close {h.get('underlying')} failed: {ex}")
        return False


def _exit(tc, odc, state, dry):
    """The session AFTER entry (post-announcement), close each held condor on the crush."""
    import autotrade as at
    e = state.setdefault("earnings", {})
    holds = _holdings(state)
    if not holds:
        return
    now = at.et_now()
    today_s = now.date().isoformat()
    # Post-earnings: close once the open has settled.
    if now.hour < 9 or (now.hour == 9 and now.minute < 35):
        return
    remaining = []
    closed_any = False
    for h in holds:
        if h.get("entry_date") == today_s:
            remaining.append(h)                      # earnings is tonight; hold
            continue
        if _close_one(tc, odc, state, h, dry):
            closed_any = True
        else:
            remaining.append(h)                      # dry-run or close failed — keep it
    # Re-home everything into the canonical 'holdings' list (drops the legacy 'holding').
    e["holdings"] = remaining
    e["holding"] = None
    if closed_any:
        at.save_state(state)


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
    holds = _holdings(state)
    return {
        "holdings": [{"underlying": h["underlying"], "risk": h.get("risk"),
                      "entry_net": h.get("entry_net"), "entry_date": h.get("entry_date")}
                     for h in holds],
        "open_risk": round(sum(float(h.get("risk") or 0.0) for h in holds), 2),
        "concurrent": len(holds),
        "last_realized": e.get("last_realized"),
        "last_entry_date": e.get("last_entry_date"),
    }
