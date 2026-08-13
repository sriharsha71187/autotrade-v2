#!/usr/bin/env python3
"""
calm_condor.py — the CALM-DAY AFTERNOON CONDOR book (deterministic, no model call).

The one daily premium structure that survived this repo's own out-of-sample gate
(research/backtest_gap_premium.py, backtest_pm_stops.py, backtest_structure_router.py):

  At ~1:00 PM ET, IF the day is going nowhere and vol is asleep —
    * no big overnight gap-down (open vs prior close > -0.5%),
    * flat so far   (|now/open - 1| < 0.25%  AND  session range < 0.6%),
    * VIX below CALM_CONDOR_VIX_MAX (16),
  THEN sell a SAME-DAY SPY iron condor: shorts ~1x the remaining-session expected
  move (the ATM straddle mid — market-implied, not modeled), wings ~2.5x, and
  manage it hard:
    * TOUCH-STOP: buy the condor back the moment SPY trades through either short
      strike (checked every cycle — cadence is ~3-5 min, so real stop fills are
      worse than the backtest's; that slippage is why the backtest charged +10%),
    * hard flatten at 15:40 ET (never ride a 0DTE into expiration),
    * one condor per day, risk per day = CALM_CONDOR_RISK_PER_DAY (default $2,000
      = 10% of the $20k book), gross open risk capped at CALM_CONDOR_BOOK_CAPITAL.

Evidence base (2007-2026 daily + 2025-26 real intraday, synthetic BS pricing):
IS +6.5%/trade t=4.4 -> OOS +6.4% t=2.8 at 10% friction (+3.9% t=1.7 at 20%);
touch-stop trades ~1.8%/trade of expectancy for worst-day -11% instead of -105%.
THIS LIVE RUN IS THE POINT: it replaces synthetic credits with real Alpaca fills.
Judge it on whether entry credits and stop exits track the model — not on a few
days of P&L (36-60 trades/yr means a week proves nothing).

Integration: runs BEFORE the TRADING_PAUSED early-return in run_cycle (the pause
verdict was about LLM intraday selection; this book is code-only) — flag-gated by
cfg.CALM_CONDOR_ENABLED, default OFF. Its legs live in state["calm_condor"] and
are SHIELDED via held_symbols() -> held_books; the book owns its whole lifecycle
(entry, touch-stop, 15:40 flatten, stale-position cleanup). Realized P&L is
booked under "calm_condor". PAPER ONLY like everything else here.
"""

from __future__ import annotations

import config as cfg

# mirrored from cfg.CALM_CONDOR_ENABLED by the engine call-site
CALM_CONDOR_ENABLED = False

# strike geometry in units of the remaining-session expected move (ATM straddle)
SHORT_EM = 1.0        # short strikes ~1x EM  (~0.75 sigma of the remaining session)
WING_EM = 2.5         # wings ~2.5x EM        (~2 sigma)

_ENTRY_START_MIN = 12 * 60 + 55    # 12:55 ET
_ENTRY_END_MIN = 13 * 60 + 35      # 13:35 ET — after this the day is too short
_FLAT_MIN = 15 * 60 + 40           # hard close 15:40 ET

try:
    import yfinance as yf
    _YF_OK = True
except Exception:
    _YF_OK = False


# --------------------------- pure decision logic ------------------------------

def day_is_calm(open_px, hi, lo, now_px, prev_close, vix) -> tuple[bool, str]:
    """The three entry gates, on plain numbers (unit-testable)."""
    if not all(x and x > 0 for x in (open_px, hi, lo, now_px, prev_close)):
        return False, "missing data"
    if vix is None or vix >= cfg.CALM_CONDOR_VIX_MAX:
        return False, f"VIX {vix} not < {cfg.CALM_CONDOR_VIX_MAX}"
    if open_px / prev_close - 1 <= -0.005:
        return False, "gap-down >= 0.5% (never sell premium into it)"
    if abs(now_px / open_px - 1) >= 0.0025:
        return False, "day not flat (|move| >= 0.25%)"
    if (hi - lo) / open_px >= 0.006:
        return False, "session range >= 0.6% (trending/whippy day)"
    return True, "calm"


def pick_condor(rows: list, spot: float) -> dict | None:
    """From today's-expiry chain rows pick shorts ~SHORT_EM x EM and wings
    ~WING_EM x EM, EM = ATM straddle mid (remaining-session implied move).
    Returns {legs, net_credit, em, strikes} or None. Pure on row dicts."""
    if not rows or not spot or spot <= 0:
        return None
    calls = [r for r in rows if r["type"] == "call" and (r.get("mid") or 0) > 0]
    puts = [r for r in rows if r["type"] == "put" and (r.get("mid") or 0) > 0]
    if not calls or not puts:
        return None
    atm_c = min(calls, key=lambda r: abs(r["strike"] - spot))
    atm_p = min(puts, key=lambda r: abs(r["strike"] - spot))
    em = atm_c["mid"] + atm_p["mid"]
    if em <= 0:
        return None
    sc = min(calls, key=lambda r: abs(r["strike"] - (spot + SHORT_EM * em)))
    sp = min(puts, key=lambda r: abs(r["strike"] - (spot - SHORT_EM * em)))
    lc_c = [r for r in calls if r["strike"] > sc["strike"]]
    lp_c = [r for r in puts if r["strike"] < sp["strike"]]
    if sc["strike"] <= spot or sp["strike"] >= spot or not lc_c or not lp_c:
        return None
    lc = min(lc_c, key=lambda r: abs(r["strike"] - (spot + WING_EM * em)))
    lp = min(lp_c, key=lambda r: abs(r["strike"] - (spot - WING_EM * em)))
    # tight two-sided quotes on all four legs, or stand down. Shorts are judged as
    # % of mid; wings ALSO pass on an absolute 5c width — a $0.15 wing quoting
    # 0.14x0.16 is 13% of mid yet perfectly liquid, so a pure pct filter would
    # reject nearly every real SPY 0DTE wing.
    for r, is_wing in ((sc, False), (sp, False), (lc, True), (lp, True)):
        bid, ask, mid = r.get("bid") or 0, r.get("ask") or 0, r.get("mid") or 0
        if bid <= 0 or ask <= 0 or mid <= 0:
            return None
        wid = ask - bid
        if wid / mid > cfg.CALM_CONDOR_MAX_LEG_SPREAD_PCT \
                and not (is_wing and wid <= 0.05):
            return None
    net_credit = sc["mid"] + sp["mid"] - lc["mid"] - lp["mid"]
    if net_credit <= 0:
        return None
    return {"legs": [{"symbol": sc["symbol"], "side": "sell"},
                     {"symbol": lc["symbol"], "side": "buy"},
                     {"symbol": sp["symbol"], "side": "sell"},
                     {"symbol": lp["symbol"], "side": "buy"}],
            "net_credit": round(net_credit, 2), "em": round(em, 2),
            "short_call": sc["strike"], "short_put": sp["strike"]}


def exit_reason(spot, short_call, short_put, now_min: int) -> str | None:
    """Touch-stop / hard-flatten decision (unit-testable)."""
    if now_min >= _FLAT_MIN:
        return "eod"
    if spot is not None:
        if spot >= short_call:
            return "touch-call"
        if spot <= short_put:
            return "touch-put"
    return None


# ------------------------------ book interface --------------------------------

def _holding(state: dict) -> dict | None:
    return (state.get("calm_condor") or {}).get("holding")


def held_symbols(state: dict) -> set:
    h = _holding(state)
    return {l["symbol"] for l in h.get("legs", [])} if h else set()


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


def _spy_session():
    """(open, hi, lo, last, prev_close) for SPY today via 5m bars. None on failure."""
    if not _YF_OK:
        return None
    try:
        t = yf.Ticker("SPY")
        df = t.history(period="1d", interval="5m", prepost=False)
        if df is None or df.empty:
            return None
        prev = float(t.fast_info.previous_close)
        return (float(df["Open"].iloc[0]), float(df["High"].max()),
                float(df["Low"].min()), float(df["Close"].iloc[-1]), prev)
    except Exception:
        return None


def _close(tc, odc, state, h, reason, dry) -> bool:
    import autotrade as at
    if dry:
        at.log(f"[DRY] calm_condor would CLOSE ({reason})")
        return False
    cost = 0.0
    try:
        for l in h.get("legs", []):
            _, _, mid = at.option_quote(odc, l["symbol"])
            m = mid or 0.0
            cost += m if l["side"] == "sell" else -m
    except Exception:
        cost = 0.0
    try:
        for l in h.get("legs", []):
            try:
                tc.close_position(l["symbol"])
            except Exception:
                pass                       # leg already expired/closed — fine
        qty = int(h.get("qty") or 1)
        realized = float(h.get("entry_net", 0.0)) - cost * 100 * qty
        at.record_strategy_realized(state, "calm_condor", realized)
        state.setdefault("calm_condor", {})["last_realized"] = round(realized, 2)
        at.log(f"CALM_CONDOR close ({reason}) realized≈{realized:+.0f}")
        at.tg_send(f"🦅 Calm-day condor closed ({reason}): P&L ≈ ${realized:+,.0f}.")
        return True
    except Exception as ex:
        at.log(f"calm_condor: close failed: {ex}")
        return False


def _manage(tc, odc, state, now, dry):
    import autotrade as at
    h = _holding(state)
    if not h:
        return
    today_s = now.date().isoformat()
    if h.get("entry_date") != today_s:
        reason = "stale"                   # belt-and-suspenders (0DTE should never survive)
    else:
        spot = at.stock_latest_price("SPY")
        reason = exit_reason(spot, h["short_call"], h["short_put"],
                             now.hour * 60 + now.minute)
    if reason and _close(tc, odc, state, h, reason, dry):
        state["calm_condor"]["holding"] = None
        at.save_state(state)


def _enter(tc, odc, state, now, dry):
    import autotrade as at
    cc = state.setdefault("calm_condor", {})
    today_s = now.date().isoformat()
    now_min = now.hour * 60 + now.minute
    if _holding(state) or cc.get("last_entry_date") == today_s:
        return
    if not (_ENTRY_START_MIN <= now_min < _ENTRY_END_MIN):
        return
    sess = _spy_session()
    if not sess:
        return
    try:
        vix = at.get_vix()
    except Exception:
        vix = None
    ok, why = day_is_calm(sess[0], sess[1], sess[2], sess[3], sess[4], vix)
    cc["last_check"] = {"when": now.isoformat(), "why": why}
    if not ok:
        return                             # re-check next cycle inside the window
    cc["last_entry_date"] = today_s        # latch: ONE attempt once calm confirms
    spot = sess[3]
    rows = at.option_chain_for(odc, "SPY", spot, want_today_expiry=True,
                               strike_pct=0.04, max_per_side=25)
    today_rows = [r for r in rows if r["expiry"] == today_s]
    pick = pick_condor(today_rows, spot)
    if not pick:
        at.log("calm_condor: calm day but no tight same-day condor — standing down")
        at.save_state(state)
        return
    risk = at.multileg_risk(pick["legs"], pick["net_credit"], 1)
    if not risk or risk <= 0:
        at.save_state(state)
        return
    qty = int(min(cfg.CALM_CONDOR_RISK_PER_DAY,
                  cfg.CALM_CONDOR_BOOK_CAPITAL) // risk)
    if qty < 1:
        at.log(f"calm_condor: per-condor risk ${risk:.0f} exceeds daily budget — skip")
        at.save_state(state)
        return
    try:
        o = at.place_multi_leg(tc, pick["legs"], pick["net_credit"], qty, dry)
        if not dry:
            cc["holding"] = {"legs": pick["legs"], "qty": qty,
                             "entry_net": round(pick["net_credit"] * 100 * qty, 2),
                             "short_call": pick["short_call"], "short_put": pick["short_put"],
                             "risk": risk * qty, "entry_date": today_s,
                             "opened": now.isoformat(),
                             "order_id": str(getattr(o, "id", "")) or None}
        at.log(f"CALM_CONDOR sell {qty}x SPY {today_s} shorts "
               f"[{pick['short_put']}/{pick['short_call']}] EM≈{pick['em']} "
               f"credit≈{pick['net_credit']} risk≈${risk * qty:.0f}")
        at.tg_send(f"🦅 Calm-day condor: sold {qty}x SPY same-day condor, shorts "
                   f"{pick['short_put']}/{pick['short_call']}, credit ≈ "
                   f"${pick['net_credit'] * 100 * qty:,.0f}, max risk ${risk * qty:,.0f}. "
                   f"Touch-stop armed; hard close 15:40 ET.")
    except Exception as ex:
        at.log(f"calm_condor: submit failed: {ex}")
    at.save_state(state)


def run(tc, state, dry: bool):
    if not CALM_CONDOR_ENABLED:
        return
    import autotrade as at
    try:
        odc = at.option_data_client() if at._ALPACA_OK else None
        now = at.et_now()
        _manage(tc, odc, state, now, dry)
        _enter(tc, odc, state, now, dry)
    except Exception as e:
        at.log(f"calm_condor: run failed: {e}")


def summary(state: dict) -> dict:
    cc = state.get("calm_condor", {}) or {}
    h = _holding(state)
    return {
        "holding": ({"legs": [l["symbol"] for l in h.get("legs", [])],
                     "qty": h.get("qty"), "entry_net": h.get("entry_net"),
                     "shorts": [h.get("short_put"), h.get("short_call")],
                     "risk": h.get("risk"), "opened": h.get("opened")} if h else None),
        "last_entry_date": cc.get("last_entry_date"),
        "last_realized": cc.get("last_realized"),
        "last_check": cc.get("last_check"),
    }
