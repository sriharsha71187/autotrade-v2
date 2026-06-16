#!/usr/bin/env python3
"""
sector_pairs.py — sector PAIRS / market-neutral stat-arb book (AUDIT_ROADMAP #26).

WHY THIS BOOK EXISTS
--------------------
Every other book in the engine (growth sleeve, overnight drift, momentum, conviction-ITM,
gap-fade) is LONG-BETA: it makes money when the market goes up and loses when it falls.
That is one concentrated bet on direction. This book is the deliberately UNCORRELATED,
beta-balanced stream: a classic statistical-arbitrage pairs trade. Within a sector group
(config.SECTOR_MAP) we trade the SPREAD between two cointegrated peers — long the cheap
leg, short the rich leg, dollar/beta balanced — so the trade's P&L depends on the two
names CONVERGING, not on where the index goes. Backtested daily-P&L correlation to SPY
was ~0.0 with the strict cointegration screen (see /tmp/bt_pairs.py).

STRATEGY (standard stat-arb)
----------------------------
  - For each deployable pair (A, B): each run, regress logA on logB over a rolling
    LOOKBACK window -> hedge ratio beta + intercept alpha (NO lookahead: only past bars).
  - spread = logA - (alpha + beta*logB); z = (spread - mean) / std over the window.
  - ENTER when |z| >= ENTRY_Z:
        z > 0 (A rich vs B) -> SHORT A, LONG B (short the spread)
        z < 0 (A cheap)     -> LONG  A, SHORT B (long  the spread)
    Legs sized so the pair risks ~PAIRS_RISK_PER_TRADE at the stop; B leg scaled by
    beta*(priceA/priceB) so the dollar/beta exposure nets to ~0.
  - EXIT when z reverts through ~EXIT_Z (mean reversion = the profit), OR
  - STOP  when |z| >= STOP_Z (the spread kept diverging — thesis broken; DEFINED RISK), OR
  - MAX-HOLD after PAIRS_MAX_HOLD_DAYS (a non-reverting spread is dead money).

  Multi-day holds are expected. Both legs require shortability on the short side.

DEFINED RISK
------------
The |z| >= STOP_Z stop is the max-loss bound; legs are sized to that. Because it's a
two-leg market-neutral spread (one long, one short), gross directional exposure is small
even though notional is two-sided.

INTEGRATION (mirrors overnight_drift / gap_fade)
------------------------------------------------
Public interface for autotrade.py:
    held_symbols(state) -> set      # all legs currently open (SHIELDED from intraday)
    held_value(tc, state) -> float  # gross $ market value of open legs
    run(tc, state, dry)             # open/manage/close pairs (multi-day)
    summary(state) -> dict

This is a SHIELDED, multi-day book like overnight_drift: add held_symbols(state) to
autotrade's held_books so the intraday engine never EOD-flattens, trailing-stops, or
re-enters these names. Each closed pair records realized P&L into the shared ledger via
record_strategy_realized(state, "sector_pairs", pnl).

DISABLED BY DEFAULT. PAIRS_ENABLED = False until the owner integrates a winner.
"""
from __future__ import annotations

import math

import config as cfg

try:
    import numpy as np
    import yfinance as yf
    _DEPS_OK = True
except Exception:
    _DEPS_OK = False


# ===========================================================================
# Module-level config (kept HERE, not in config.py, per the no-edit constraint)
# ===========================================================================
PAIRS_ENABLED          = False   # master gate — OFF until owner integrates a winner

PAIRS_LOOKBACK         = 60      # rolling window for beta + z (days)
PAIRS_ENTRY_Z          = 2.0     # enter when |z| >= this
PAIRS_EXIT_Z           = 0.0     # exit when z reverts through this (mean reversion)
PAIRS_STOP_Z           = 3.5     # hard stop when |z| >= this (defined risk)
PAIRS_MAX_HOLD_DAYS    = 30      # force-close a non-reverting spread after N trading days
PAIRS_RISK_PER_TRADE   = 250.0   # $ risk per pair at the stop (sizes both legs)
PAIRS_MAX_CONCURRENT   = 4       # cap simultaneously open pairs (correlated-spread heat)
PAIRS_MAX_LEG_SHARES   = 5000    # absolute share cap per leg (sizing-blowup guard)
PAIRS_MIN_LEG_NOTIONAL = 50.0    # skip if a leg's notional is below this (junk size)

# Deployable pairs (the strict-cointegration survivors from the 2y backtest:
# corr>=0.7 of daily log-returns AND Engle-Granger residual ADF p < 0.05 — the screen
# that delivered ~0.0 SPY correlation). (symbolA, symbolB, sector). Beta is recomputed
# live each run — these are only the WHICH-pairs whitelist, not frozen hedge ratios.
PAIRS_DEPLOY = [
    ("LRCX", "KLAC", "semis_ai"),   # semi-cap equipment twins; tightest cointegration
    ("NVDA", "TSM",  "semis_ai"),
    ("TSM",  "LRCX", "semis_ai"),
    ("ASML", "LRCX", "semis_ai"),
    ("QQQ",  "DIA",  "index_etf"),  # index spread (Nasdaq vs Dow) — naturally neutral
]


# ===========================================================================
# Book interface — shielding
# ===========================================================================
def _open_pairs(state: dict) -> dict:
    return (state.get("sector_pairs") or {}).get("open", {}) or {}


def held_symbols(state: dict) -> set:
    """Every leg currently open across all live pairs — SHIELDED from the intraday
    engine (no EOD flatten, no trailing stop, no loss-halt, no re-entry)."""
    out = set()
    for p in _open_pairs(state).values():
        out.add(p["a"]); out.add(p["b"])
    return out


def held_value(tc, state) -> float:
    """Gross $ market value of all open legs (to exclude from the deployed-capital cap)."""
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
# Signal — rolling beta + z (NO lookahead: regression uses only the trailing window)
# ===========================================================================
def _daily_logs(symbols: list[str]):
    """Trailing ~1y of daily auto-adjusted closes -> {sym: np.array(log-close)}.
    Returns ({}, None) on any failure."""
    if not _DEPS_OK:
        return {}, None
    try:
        df = yf.download(symbols, period="1y", interval="1d", auto_adjust=True,
                         progress=False, group_by="ticker", threads=True)
    except Exception:
        return {}, None
    out = {}
    idx = None
    for s in symbols:
        try:
            c = (df[s]["Close"] if len(symbols) > 1 else df["Close"]).dropna()
            if hasattr(c, "columns"):
                c = c.iloc[:, 0]
            if len(c) >= PAIRS_LOOKBACK + 5:
                out[s] = c
                idx = c.index if idx is None else idx
        except Exception:
            continue
    return out, idx


def _signal(close_a, close_b):
    """Return (z, beta, alpha, price_a, price_b, spread_std) from the trailing window,
    or None if not computable. Uses ONLY the last PAIRS_LOOKBACK closes ending at the
    most recent completed bar — no future data."""
    a, b = close_a.align(close_b, join="inner")
    if len(a) < PAIRS_LOOKBACK + 1:
        return None
    la = np.log(a.values); lb = np.log(b.values)
    w_a = la[-PAIRS_LOOKBACK:]; w_b = lb[-PAIRS_LOOKBACK:]
    X = np.column_stack([np.ones(PAIRS_LOOKBACK), w_b])
    coef, *_ = np.linalg.lstsq(X, w_a, rcond=None)
    alpha, beta = float(coef[0]), float(coef[1])
    spread_w = w_a - (alpha + beta * w_b)
    mu = float(spread_w.mean()); sd = float(spread_w.std())
    if sd <= 1e-9 or beta <= 0:        # beta<=0 => not a hedge; skip degenerate fit
        return None
    cur_spread = la[-1] - (alpha + beta * lb[-1])
    z = (cur_spread - mu) / sd
    return dict(z=z, beta=beta, alpha=alpha,
                pa=float(a.values[-1]), pb=float(b.values[-1]), sd=sd)


def _size_legs(sig) -> tuple[int, int]:
    """Beta/dollar-balanced leg share counts for ~PAIRS_RISK_PER_TRADE at the stop.
    Returns (qa, qb) magnitudes (>=0); (0,0) if un-sizable."""
    pa, sd, z = sig["pa"], sig["sd"], abs(sig["z"])
    stop_dist_z = max(PAIRS_STOP_Z - z, 0.25)         # z-room to the stop (>=0.25 floor)
    dollar_per_z_legA = pa * sd                        # ~$ move on 1 share A per 1 z
    if dollar_per_z_legA <= 0:
        return 0, 0
    qa = PAIRS_RISK_PER_TRADE / (dollar_per_z_legA * stop_dist_z)
    qa = int(max(min(qa, PAIRS_MAX_LEG_SHARES), 0))
    qb = int(max(min(qa * sig["beta"] * (sig["pa"] / sig["pb"]), PAIRS_MAX_LEG_SHARES), 0))
    return qa, qb


# ===========================================================================
# run — open / manage / close
# ===========================================================================
def run(tc, state, dry: bool):
    if not PAIRS_ENABLED:
        return
    if not _DEPS_OK:
        return
    import autotrade as at
    try:
        sp = state.setdefault("sector_pairs", {})
        sp.setdefault("open", {})
        now = at.et_now()

        # Pull data once for every symbol we might touch.
        symset = sorted({s for p in PAIRS_DEPLOY for s in (p[0], p[1])}
                        | {p["a"] for p in sp["open"].values()}
                        | {p["b"] for p in sp["open"].values()})
        closes, _ = _daily_logs(symset)
        if not closes:
            at.log("sector_pairs: no price data — standing down")
            return

        # ---- 1) MANAGE open pairs (exit on revert / stop / max-hold) ----
        for key in list(sp["open"].keys()):
            pos = sp["open"][key]
            ca = closes.get(pos["a"]); cb = closes.get(pos["b"])
            if ca is None or cb is None:
                continue
            sig = _signal(ca, cb)
            if sig is None:
                continue
            z = sig["z"]
            opened = pos.get("opened_date")
            held_days = _bars_since(ca, opened)
            reason = None
            # revert: z crossed back through EXIT_Z relative to the side we're on
            if pos["dir"] == 1 and z >= PAIRS_EXIT_Z:
                reason = "revert"
            elif pos["dir"] == -1 and z <= PAIRS_EXIT_Z:
                reason = "revert"
            if abs(z) >= PAIRS_STOP_Z:
                reason = "stop"
            if held_days is not None and held_days >= PAIRS_MAX_HOLD_DAYS:
                reason = "maxhold"
            if reason:
                _close_pair(at, tc, state, key, pos, reason, dry)

        # ---- 2) ENTER new pairs ----
        if len(sp["open"]) >= PAIRS_MAX_CONCURRENT:
            return
        held = held_symbols(state)
        for a, b, sector in PAIRS_DEPLOY:
            if len(sp["open"]) >= PAIRS_MAX_CONCURRENT:
                break
            key = f"{a}/{b}"
            if key in sp["open"]:
                continue
            if a in held or b in held:                 # don't double-book a leg
                continue
            if a in cfg.BLACKLIST or b in cfg.BLACKLIST:
                continue
            ca = closes.get(a); cb = closes.get(b)
            if ca is None or cb is None:
                continue
            sig = _signal(ca, cb)
            if sig is None or abs(sig["z"]) < PAIRS_ENTRY_Z:
                continue
            qa, qb = _size_legs(sig)
            if qa < 1 or qb < 1:
                continue
            if qa * sig["pa"] < PAIRS_MIN_LEG_NOTIONAL or qb * sig["pb"] < PAIRS_MIN_LEG_NOTIONAL:
                continue
            direction = -1 if sig["z"] > 0 else 1      # z>0: A rich -> short spread
            _open_pair(at, tc, state, key, a, b, sector, direction, qa, qb, sig, now, dry)
            held |= {a, b}

        at.save_state(state)
    except Exception as e:
        at.log(f"sector_pairs: run failed: {e}")


def _bars_since(close_series, opened_date) -> int | None:
    """Trading bars elapsed since opened_date (a 'YYYY-MM-DD' string)."""
    if not opened_date:
        return None
    try:
        dts = [d.strftime("%Y-%m-%d") for d in close_series.index]
        if opened_date in dts:
            return len(dts) - 1 - dts.index(opened_date)
        # opened before window start -> count whole window
        return len(dts)
    except Exception:
        return None


def _open_pair(at, tc, state, key, a, b, sector, direction, qa, qb, sig, now, dry):
    """direction +1 => LONG A / SHORT B; -1 => SHORT A / LONG B. Both legs shortable-gated."""
    side_a = "buy" if direction == 1 else "sell"
    side_b = "sell" if direction == 1 else "buy"
    short_leg = b if direction == 1 else a
    # shortability gate on whichever leg is the short side
    try:
        if not bool(getattr(tc.get_asset(short_leg), "shortable", False)):
            at.log(f"sector_pairs: {key} short leg {short_leg} not shortable — skip")
            return
    except Exception:
        return
    if dry:
        at.log(f"[DRY] sector_pairs would open {key} z={sig['z']:+.2f} beta={sig['beta']:.2f} "
               f"{side_a} {qa} {a} / {side_b} {qb} {b}")
        _record_open(state, key, a, b, sector, direction, qa, qb, sig, now)
        return
    try:
        # Two independent market legs (no native bracket — the z-stop is the risk bound,
        # enforced each run by manage()). Submit the SHORT leg first so a partial long
        # never sits un-hedged net-long.
        if direction == 1:
            at.submit_market(tc, b, qb, "sell") if hasattr(at, "submit_market") else _mkt(at, tc, b, qb, "sell")
            _mkt(at, tc, a, qa, "buy")
        else:
            _mkt(at, tc, a, qa, "sell")
            _mkt(at, tc, b, qb, "buy")
        _record_open(state, key, a, b, sector, direction, qa, qb, sig, now)
        at.log(f"PAIRS OPEN {key} z={sig['z']:+.2f} beta={sig['beta']:.2f} "
               f"{side_a} {qa} {a} / {side_b} {qb} {b}")
        try:
            at.tg_send(f"⚖️ Pairs: {side_a} {qa} {a} / {side_b} {qb} {b} "
                       f"(z={sig['z']:+.2f}, market-neutral).")
        except Exception:
            pass
    except Exception as e:
        at.log(f"sector_pairs: open {key} failed: {e}")


def _mkt(at, tc, sym, qty, side):
    """Submit a plain market order via autotrade's helper if present, else the SDK."""
    if hasattr(at, "place_stock_market"):
        return at.place_stock_market(tc, sym, qty, side)
    # Fallback to the Alpaca SDK request types autotrade already imports.
    req = at.MarketOrderRequest(symbol=sym, qty=qty,
                                side=(at.OrderSide.BUY if side == "buy" else at.OrderSide.SELL),
                                time_in_force=at.TimeInForce.DAY)
    return tc.submit_order(req)


def _record_open(state, key, a, b, sector, direction, qa, qb, sig, now):
    state["sector_pairs"]["open"][key] = {
        "a": a, "b": b, "sector": sector, "dir": direction,
        "qa": qa, "qb": qb, "beta": round(sig["beta"], 4),
        "entry_z": round(sig["z"], 3),
        "entry_pa": round(sig["pa"], 2), "entry_pb": round(sig["pb"], 2),
        "opened": now.isoformat(), "opened_date": now.strftime("%Y-%m-%d"),
    }


def _close_pair(at, tc, state, key, pos, reason, dry):
    """Flatten both legs and record realized P&L into the shared strategy ledger."""
    a, b, direction = pos["a"], pos["b"], pos["dir"]
    qa, qb = pos["qa"], pos["qb"]
    # closing sides are the opposite of the open sides
    close_a = "sell" if direction == 1 else "buy"
    close_b = "buy" if direction == 1 else "sell"
    if dry:
        at.log(f"[DRY] sector_pairs would close {key} ({reason})")
        _finalize_close(at, state, key, pos, 0.0)
        return
    realized = 0.0
    try:
        # realize P&L off broker cost basis where available
        realized = _leg_realized(tc, a) + _leg_realized(tc, b)
    except Exception:
        realized = 0.0
    try:
        _mkt(at, tc, a, qa, close_a)
        _mkt(at, tc, b, qb, close_b)
        at.log(f"PAIRS CLOSE {key} ({reason}) realized~${realized:.0f}")
        try:
            at.tg_send(f"⚖️ Pairs closed {key} ({reason}), P&L ~${realized:.0f}.")
        except Exception:
            pass
    except Exception as e:
        at.log(f"sector_pairs: close {key} failed: {e}")
        return
    _finalize_close(at, state, key, pos, realized)


def _leg_realized(tc, sym) -> float:
    """Unrealized P&L of the current position on sym (best-effort; becomes realized on
    the flatten). 0 if not held / unavailable."""
    try:
        for p in tc.get_all_positions():
            if p.symbol == sym:
                return float(p.unrealized_pl or 0)
    except Exception:
        pass
    return 0.0


def _finalize_close(at, state, key, pos, realized):
    sp = state["sector_pairs"]
    sp["open"].pop(key, None)
    hist = sp.setdefault("closed", [])
    rec = {"pair": key, "sector": pos["sector"], "dir": pos["dir"],
           "entry_z": pos.get("entry_z"), "realized": round(realized, 2),
           "opened": pos.get("opened")}
    hist.append(rec)
    sp["closed"] = hist[-100:]                          # bounded history
    try:
        at.record_strategy_realized(state, "sector_pairs", realized)
    except Exception:
        pass


# ===========================================================================
# summary
# ===========================================================================
def summary(state: dict) -> dict:
    sp = state.get("sector_pairs", {}) or {}
    openp = sp.get("open", {}) or {}
    closed = sp.get("closed", []) or []
    total = round(sum(c.get("realized", 0) for c in closed), 2)
    return {
        "enabled": PAIRS_ENABLED,
        "open_pairs": list(openp.keys()),
        "open_count": len(openp),
        "closed_count": len(closed),
        "realized_total": total,
    }
