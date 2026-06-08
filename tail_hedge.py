#!/usr/bin/env python3
"""
tail_hedge.py — always-on long-volatility / crash-insurance book.

The structural fix for the "everything loses together" flaw: every other strategy
in the system is implicitly short-vol or long-beta, so a crash day (Friday 6/5:
SPY -2.6%, VIX +40%) hits them all at once. This book holds a small, standing
position in cheap OTM SPY puts that PAY on exactly those days — convexity the rest
of the book lacks. Per the evidence (Bhansali/Spitznagel), a tail hedge sized and
monetized properly raises whole-portfolio CAGR rather than just draining it.

Mechanics (deterministic, no model call):
  - maintain ~TAIL_HEDGE_BUDGET of premium in OTM SPY puts (~TAIL_HEDGE_OTM_PCT
    out of the money, TAIL_HEDGE_DTE_MIN..MAX days to expiry)
  - ROLL when the holding drops under TAIL_HEDGE_ROLL_DTE days to expiry
  - MONETIZE: sell when the hedge appreciates >= TAIL_HEDGE_TAKE_PROFIT_MULT x
    (a vol spike) — that's when the insurance is worth cashing
  - buy at most once per day; respect available cash

Like the growth/overnight sleeves this holding is SHIELDED from the intraday
machinery (autotrade adds it to held_books). Realized P&L is recorded under the
"tail_hedge" label in the shared strategy ledger.
"""

from __future__ import annotations

from datetime import timedelta

import config as cfg

try:
    import yfinance as yf
    _YF_OK = True
except Exception:
    _YF_OK = False


def held_symbols(state: dict) -> set:
    h = (state.get("tail_hedge") or {}).get("holding")
    return {h["symbol"]} if h else set()


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


def _select_put(odc, spot, today):
    """Pick the OTM SPY put closest to TAIL_HEDGE_OTM_PCT below spot within the
    target DTE window. Returns {symbol, strike, expiry, ask, mid} or None."""
    import autotrade as at
    if odc is None or not spot or spot <= 0:
        return None
    target_strike = spot * (1 - cfg.TAIL_HEDGE_OTM_PCT)
    try:
        from alpaca.data.requests import OptionChainRequest
        from alpaca.trading.enums import ContractType
        req = OptionChainRequest(
            underlying_symbol=cfg.TAIL_HEDGE_SYMBOL,
            strike_price_gte=round(target_strike * 0.94, 2),
            strike_price_lte=round(target_strike * 1.02, 2),
            expiration_date_gte=today + timedelta(days=cfg.TAIL_HEDGE_DTE_MIN),
            expiration_date_lte=today + timedelta(days=cfg.TAIL_HEDGE_DTE_MAX),
            type=ContractType.PUT,
        )
        chain = odc.get_option_chain(req)
    except Exception as e:
        at.log(f"tail_hedge: chain fetch failed: {e}")
        return None
    rows = []
    for sym, snap in (chain or {}).items():
        meta = at.parse_occ(sym)
        if not meta or meta["type"] != "put":
            continue
        q = getattr(snap, "latest_quote", None)
        bid = float(getattr(q, "bid_price", 0) or 0) if q else 0.0
        ask = float(getattr(q, "ask_price", 0) or 0) if q else 0.0
        if ask <= 0:
            continue
        mid = (bid + ask) / 2 if bid > 0 else ask
        rows.append({"symbol": sym, "strike": meta["strike"], "expiry": meta["expiry"],
                     "ask": round(ask, 2), "mid": round(mid, 2)})
    if not rows:
        return None
    # Closest strike to our target OTM level (then nearest-to-min-DTE as tiebreak).
    rows.sort(key=lambda r: (abs(r["strike"] - target_strike), r["expiry"]))
    return rows[0]


def _spot(tc):
    """Current SPY spot for strike selection (yfinance; tolerant of failure)."""
    if not _YF_OK:
        return None
    try:
        fi = yf.Ticker(cfg.TAIL_HEDGE_SYMBOL).fast_info
        px = float(fi.last_price)
        return px if px > 0 else None
    except Exception:
        return None


def _manage(tc, odc, state, dry):
    """Take profit on a vol spike, or roll when too close to expiry."""
    import autotrade as at
    th = state.setdefault("tail_hedge", {})
    h = th.get("holding")
    if not h:
        return
    now = at.et_now()
    today = now.date()
    sym = h["symbol"]
    cur = at.option_price(odc, sym)
    entry = float(h.get("entry") or 0)
    qty = int(h.get("qty") or 0)
    # Take profit: hedge has multiplied -> monetize the spike.
    if cur and entry and cur >= entry * cfg.TAIL_HEDGE_TAKE_PROFIT_MULT:
        if dry:
            at.log(f"[DRY] tail_hedge would TAKE PROFIT {sym} cur {cur} vs entry {entry}")
            return
        try:
            tc.close_position(sym)
            realized = (cur - entry) * qty * 100
            at.record_strategy_realized(state, "tail_hedge", realized)
            th["holding"] = None
            th["last_realized"] = round(realized, 2)
            at.log(f"TAIL HEDGE take-profit {sym} cur≈{cur} entry={entry} realized≈{realized:+.0f}")
            at.tg_send(f"🛡️ Tail hedge monetized {sym} on a vol spike: P&L ≈ ${realized:+,.0f}.")
            at.save_state(state)
        except Exception as e:
            at.log(f"tail_hedge: take-profit {sym} failed: {e}")
        return
    # Roll when too close to expiry (let the buy-leg re-establish next pass).
    try:
        exp = h.get("expiry")
        dte = (at.datetime.fromisoformat(exp).date() - today).days if exp else 99
    except Exception:
        dte = 99
    if dte <= cfg.TAIL_HEDGE_ROLL_DTE:
        if dry:
            at.log(f"[DRY] tail_hedge would ROLL {sym} (DTE {dte})")
            return
        try:
            cur = cur or 0.0
            tc.close_position(sym)
            realized = (cur - entry) * qty * 100 if entry else 0.0
            at.record_strategy_realized(state, "tail_hedge", realized)
            th["holding"] = None
            th["last_realized"] = round(realized, 2)
            at.log(f"TAIL HEDGE roll {sym} DTE={dte} realized≈{realized:+.0f}")
            at.save_state(state)
        except Exception as e:
            at.log(f"tail_hedge: roll {sym} failed: {e}")


def _establish(tc, odc, state, dry):
    """If flat and not yet bought today, buy a fresh OTM-put hedge."""
    import autotrade as at
    th = state.setdefault("tail_hedge", {})
    now = at.et_now()
    today = now.date()
    today_s = today.isoformat()
    if th.get("holding"):
        return
    if th.get("last_buy_date") == today_s:
        return
    # Only establish during market hours (need live option quotes).
    spot = _spot(tc)
    pick = _select_put(odc, spot, today)
    # NOTE: do NOT latch last_buy_date here. We only mark "tried today" on a real
    # establish (or a deliberate dry run) — otherwise a transient skip (no chain, or
    # an affordability miss that a later SET TAIL_HEDGE_BUDGET would fix) would block
    # the hedge for the rest of the day.
    if not pick:
        at.log("tail_hedge: no suitable OTM put found — retry next cycle")
        return
    ask = pick["ask"]
    contract_cost = ask * 100
    qty = int(cfg.TAIL_HEDGE_BUDGET // contract_cost)
    if qty < 1:
        # Allow a single contract if it's within 1.5x the budget (insurance is lumpy).
        if contract_cost <= cfg.TAIL_HEDGE_BUDGET * 1.5:
            qty = 1
        else:
            at.log(f"tail_hedge: 1 contract {pick['symbol']} (${contract_cost:.0f}) "
                   f"exceeds budget ${cfg.TAIL_HEDGE_BUDGET:.0f} — skip")
            at.save_state(state)
            return
    try:
        cash = at.account_snapshot(tc)["cash"]
        if contract_cost * qty > max(0.0, cash - 50):
            qty = int(max(0.0, cash - 50) // contract_cost)
        if qty < 1:
            at.log("tail_hedge: insufficient cash to establish hedge")
            at.save_state(state)
            return
    except Exception:
        pass
    if dry:
        at.log(f"[DRY] tail_hedge would BUY {qty}x {pick['symbol']} @≈{ask} "
               f"(strike {pick['strike']}, exp {pick['expiry']})")
        at.save_state(state)
        return
    try:
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        lim = round(ask * 1.03, 2)
        req = LimitOrderRequest(symbol=pick["symbol"], qty=qty, side=OrderSide.BUY,
                                time_in_force=TimeInForce.DAY, limit_price=lim)
        o = tc.submit_order(order_data=req)
        entry = float(o.filled_avg_price) if getattr(o, "filled_avg_price", None) else ask
        th["last_buy_date"] = today_s          # latch only on a real establish
        th["holding"] = {"symbol": pick["symbol"], "qty": qty, "entry": entry,
                         "entry_date": today_s, "strike": pick["strike"],
                         "expiry": pick["expiry"]}
        at.log(f"TAIL HEDGE BUY {qty}x {pick['symbol']} @lim {lim} (ask {ask}) "
               f"strike={pick['strike']} exp={pick['expiry']} id={o.id}")
        at.tg_send(f"🛡️ Tail hedge: bought {qty}x {pick['symbol']} "
                   f"(SPY {cfg.TAIL_HEDGE_OTM_PCT*100:.0f}% OTM put insurance).")
        at.save_state(state)
    except Exception as e:
        at.log(f"tail_hedge: buy failed: {e}")


def run(tc, state, dry: bool):
    if not cfg.TAIL_HEDGE_ENABLED:
        return
    import autotrade as at
    try:
        odc = at.option_data_client() if at._ALPACA_OK else None
        _manage(tc, odc, state, dry)
        _establish(tc, odc, state, dry)
    except Exception as e:
        at.log(f"tail_hedge: run failed: {e}")


def summary(state: dict) -> dict:
    th = state.get("tail_hedge", {}) or {}
    h = th.get("holding")
    return {
        "holding": ({"symbol": h["symbol"], "qty": h.get("qty"), "strike": h.get("strike"),
                     "expiry": h.get("expiry"), "entry": h.get("entry")} if h else None),
        "last_realized": th.get("last_realized"),
        "last_buy_date": th.get("last_buy_date"),
    }
