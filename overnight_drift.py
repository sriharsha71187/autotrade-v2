#!/usr/bin/env python3
"""
overnight_drift.py — close-to-open index-drift capture book.

A small, SEPARATE sleeve that harvests the well-documented overnight equity drift:
historically almost all of the index's long-run return has accrued OVERNIGHT
(close -> next open), not intraday. This book simply:
  - BUY  a broad-index ETF (config OVERNIGHT_SYMBOL, default SPY) near the close
         (>=15:55 ET) for a fixed notional, held with no bracket.
  - SELL it at the next open (>=9:35 ET, after the open settles).

It is regime-gated — it only takes the overnight hold when the index is above its
200-DMA (risk-on) and VIX is calm — and optionally skips Fridays (the weekend hold
is historically weaker and carries more gap risk). It is directional and
UNCORRELATED to the intraday momentum book and to the short-premium options, so it
diversifies the return stream rather than stacking the same factor.

Like the growth sleeve, these holdings are SHIELDED from the intraday machinery:
autotrade.py excludes them from the EOD flatten, the intraday trailing stops, the
daily loss-halt flatten, the overnight reconcile, and the decision model. Realized
P&L is recorded into the shared strategy ledger (autotrade.record_strategy_realized)
under the "overnight_drift" label so attribution can report it.

Broker/log/clock helpers are reused from autotrade via a lazy import (autotrade
imports this module lazily inside run_cycle) to avoid a circular import at load.
"""

from __future__ import annotations

import config as cfg

try:
    import yfinance as yf
    _YF_OK = True
except Exception:
    _YF_OK = False


# ===========================================================================
# State helpers
# ===========================================================================
def held_symbols(state: dict) -> set:
    """Symbol currently held overnight (empty set if flat) — the set the intraday
    engine must exclude from EOD flatten / trailing stops / loss-halt / new entries."""
    h = (state.get("overnight") or {}).get("holding")
    return {h["symbol"]} if h else set()


def held_value(tc, state) -> float:
    """Current $ market value of the overnight holding (to exclude from the intraday
    deployed-capital cap)."""
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


# ===========================================================================
# Regime filter
# ===========================================================================
def _regime_ok(vix) -> tuple[bool, str]:
    """Risk-on gate: index above its trend MA and VIX calm. Returns (ok, reason)."""
    # Fail CLOSED on missing VIX: no overnight $5k buy without a confirmed-calm vol
    # read. Treating VIX=None as "calm" silently disabled the gate exactly on the
    # data-flaky nights where gap risk is highest.
    if vix is None:
        return False, "VIX unavailable — standing down (no overnight buy without a vol read)"
    if vix >= cfg.OVERNIGHT_VIX_CEILING:
        return False, f"VIX {vix:.1f} >= {cfg.OVERNIGHT_VIX_CEILING}"
    if not _YF_OK:
        # Without price history we can't confirm the uptrend — be conservative.
        return False, "yfinance unavailable (cannot confirm uptrend)"
    try:
        df = yf.download(cfg.OVERNIGHT_SYMBOL, period="1y", interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return False, "no price history"
        closes = df["Close"].dropna()
        # yfinance may return a 1-col DataFrame; squeeze to a Series.
        if hasattr(closes, "columns"):
            closes = closes.iloc[:, 0]
        last = float(closes.iloc[-1])
        ma = float(closes.tail(cfg.OVERNIGHT_TREND_MA).mean())
        if last <= ma:
            return False, f"{cfg.OVERNIGHT_SYMBOL} {last:.2f} <= {cfg.OVERNIGHT_TREND_MA}DMA {ma:.2f}"
        return True, f"{cfg.OVERNIGHT_SYMBOL} {last:.2f} > {cfg.OVERNIGHT_TREND_MA}DMA {ma:.2f}, VIX ok"
    except Exception as e:
        return False, f"regime check failed: {e}"


# ===========================================================================
# Legs
# ===========================================================================
def _sell_at_open(tc, state, dry: bool):
    """At/after the open, flatten yesterday's overnight holding and book its P&L."""
    import autotrade as at
    o = state.setdefault("overnight", {})
    h = o.get("holding")
    if not h:
        return
    now = at.et_now()
    today = now.strftime("%Y-%m-%d")
    # Only sell once the open has settled, and only a position actually held overnight
    # (entered on a prior calendar day). A same-day just-bought hold is left alone.
    if h.get("entry_date") == today:
        return
    if now.hour < cfg.OVERNIGHT_SELL_HOUR or (
            now.hour == cfg.OVERNIGHT_SELL_HOUR and now.minute < cfg.OVERNIGHT_SELL_MIN):
        return
    sym = h["symbol"]
    if dry:
        at.log(f"[DRY] overnight would SELL {sym} (qty {h.get('qty')}) at the open")
        return
    try:
        # Capture the exit price before closing, to record realized P&L.
        exit_px = None
        try:
            for p in tc.get_all_positions():
                if p.symbol == sym:
                    exit_px = float(p.current_price or 0) or None
                    break
        except Exception:
            pass
        tc.close_position(sym)
        entry = float(h.get("entry") or 0)
        qty = float(h.get("qty") or 0)
        realized = (exit_px - entry) * qty if (exit_px and entry and qty) else 0.0
        at.record_strategy_realized(state, "overnight_drift", realized)
        o["holding"] = None
        o["last_sell_date"] = today
        o["last_realized"] = round(realized, 2)
        at.log(f"OVERNIGHT SELL {sym} qty={qty} @≈{exit_px} entry={entry} realized≈{realized:+.2f}")
        at.tg_send(f"🌅 Overnight drift: sold {sym} at the open, P&L ≈ ${realized:+,.0f}.")
    except Exception as e:
        at.log(f"overnight: sell {sym} failed: {e}")


def _buy_near_close(tc, state, dry: bool, vix):
    """Near the close, if flat and the regime is risk-on, buy the overnight hold."""
    import autotrade as at
    o = state.setdefault("overnight", {})
    now = at.et_now()
    today = now.strftime("%Y-%m-%d")
    if o.get("holding"):
        return                                   # already holding
    if o.get("last_buy_date") == today:
        return                                   # already attempted today
    if now.hour < cfg.OVERNIGHT_BUY_HOUR or (
            now.hour == cfg.OVERNIGHT_BUY_HOUR and now.minute < cfg.OVERNIGHT_BUY_MIN):
        return                                   # not near the close yet
    if cfg.OVERNIGHT_SKIP_WEEKEND and now.weekday() == 4:  # Friday
        o["last_buy_date"] = today
        at.log("overnight: skipping Friday (weekend hold disabled)")
        at.save_state(state)
        return
    # Fetch VIX lazily here (only near the close), not every cycle.
    if vix is None:
        try:
            vix = at.get_vix()
        except Exception:
            vix = None
    ok, why = _regime_ok(vix)
    o["last_buy_date"] = today                    # attempt at most once/day regardless
    if not ok:
        at.log(f"overnight: regime not risk-on, standing down ({why})")
        at.save_state(state)
        return
    sym = cfg.OVERNIGHT_SYMBOL
    dollars = cfg.OVERNIGHT_NOTIONAL
    # Respect available cash (keep a small buffer); this book is additive capital.
    try:
        cash = at.account_snapshot(tc)["cash"]
        dollars = min(dollars, max(0.0, cash - 50))
    except Exception:
        pass
    if dollars < 1.0:
        at.log("overnight: insufficient cash to deploy")
        at.save_state(state)
        return
    if dry:
        at.log(f"[DRY] overnight would BUY ~${dollars:.0f} {sym} ({why})")
        at.save_state(state)
        return
    try:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        fractionable = True
        try:
            fractionable = bool(getattr(tc.get_asset(sym), "fractionable", True))
        except Exception:
            pass
        if fractionable:
            req = MarketOrderRequest(symbol=sym, notional=round(dollars, 2),
                                     side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
        else:
            px = at.option_or_stock_last(tc, sym) if hasattr(at, "option_or_stock_last") else None
            qty = int(dollars // px) if px else 0
            if qty < 1:
                at.log(f"overnight: ${dollars:.0f} < 1 share of {sym} — standing down")
                at.save_state(state)
                return
            req = MarketOrderRequest(symbol=sym, qty=qty,
                                     side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
        order = tc.submit_order(order_data=req)
        filled_qty = float(order.filled_qty or 0)
        avg = float(order.filled_avg_price) if order.filled_avg_price else None
        # Market order may not report fill instantly; estimate if needed.
        if filled_qty <= 0 and avg:
            filled_qty = round(dollars / avg, 6)
        o["holding"] = {"symbol": sym, "qty": filled_qty or round(dollars, 2),
                        "entry": avg, "entry_date": today, "notional": round(dollars, 2)}
        at.log(f"OVERNIGHT BUY ~${dollars:.0f} {sym} qty≈{filled_qty} @≈{avg} id={order.id} ({why})")
        at.tg_send(f"🌙 Overnight drift: bought ~${dollars:,.0f} of {sym} into the close.")
        at.save_state(state)
    except Exception as e:
        at.log(f"overnight: buy {sym} failed: {e}")


# ===========================================================================
# Entry point
# ===========================================================================
def run(tc, state, dry: bool, vix=None):
    """Sell yesterday's hold at the open, then (near the close) buy tonight's.
    Order matters: free the position first so cash is available to redeploy."""
    if not cfg.OVERNIGHT_DRIFT_ENABLED:
        return
    import autotrade as at
    try:
        _sell_at_open(tc, state, dry)
        _buy_near_close(tc, state, dry, vix)
    except Exception as e:
        at.log(f"overnight: run failed: {e}")


def summary(state: dict) -> dict:
    """Compact book summary for status / the model context."""
    o = state.get("overnight", {}) or {}
    h = o.get("holding")
    return {
        "holding": ({"symbol": h["symbol"], "qty": h.get("qty"), "entry": h.get("entry"),
                     "entry_date": h.get("entry_date")} if h else None),
        "last_realized": o.get("last_realized"),
        "last_buy_date": o.get("last_buy_date"),
    }
