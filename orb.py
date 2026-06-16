#!/usr/bin/env python3
"""
orb.py — Opening-Range-Breakout book (AUDIT_ROADMAP #24).  SHIPS OFF (ORB_ENABLED=False).

Standard ORB. The opening range (OR) is the high/low of the 9:30-10:00 ET bars. After
10:00, the FIRST bar that closes ABOVE the OR-high triggers a LONG (or BELOW the OR-low a
SHORT) in the break direction, with a defined-risk bracket:
  - stop  = the opposite side of the OR (full) — see ORB_STOP_MODE for OR-mid (half)
  - target = ORB_TARGET_R x the stop distance (a fixed R multiple)
  - one trade per name per day (state latch), tape-gated (longs only when the broad tape
    is up, shorts only when down — don't fight the tape)
  - skip if the OR is too WIDE (>ORB_OR_MAX_PCT of price — a gap/news day) or too TIGHT
    (<ORB_OR_MIN_PCT — no range to break)
  - sizing is a fixed $ RISK budget: qty = ORB_RISK / stop_distance

Integration shape mirrors gap_fade.py: a module-level run(tc, state, dry) the engine
calls each cycle, plus held_symbols/held_value/summary for a uniform book interface. Like
gap_fade, an ORB entry is an INTRADAY stock bracket — it is NOT shielded; the existing
trailing-stop + EOD-flatten machinery manages and closes it. The once-per-name-per-day
latch lives in state["orb"].

  *** BACKTEST VERDICT: DO NOT DEPLOY. ***
  60d/5m real-data backtest (25 liquid names, 574 trades, $250 fixed risk, 3bps/side +
  $0.005/sh costs): the only positive-expectancy grid cell (target=2R, stop=full, tape on)
  earned just +$5.70/trade (PF 1.07, Sharpe 0.53) and its ENTIRE edge was a single-name
  artifact — dropping the 3 top contributors (PANW/NVDA/TSLA) flipped expectancy to
  −$1.03/trade. Every other grid cell lost money after costs. Edge does not survive costs
  or name-removal. This module exists so the strategy is wired and re-testable, NOT because
  it is profitable. Re-run /tmp/bt_orb.py on a larger sample before ever flipping the flag.
"""

from __future__ import annotations

import config as cfg

try:
    import yfinance as yf
    _YF_OK = True
except Exception:
    _YF_OK = False

# ---- module-level config (self-contained; mirror into config.py only if deployed) ----
ORB_ENABLED      = False        # master flag — engine is byte-for-byte unchanged while False
ORB_RISK         = 250.0        # fixed $ risk per ORB trade (qty = ORB_RISK / stop_distance)
ORB_TARGET_R     = 2.0          # take-profit = entry +/- this x stop_distance (backtest: 2R > 1R)
ORB_STOP_MODE    = "full"       # "full" = stop at far side of OR; "half" = stop at OR-mid
ORB_OR_MIN_PCT   = 0.0015       # skip if OR width < 0.15% of price (too tight / no range)
ORB_OR_MAX_PCT   = 0.030        # skip if OR width > 3.0% of price (gap/news day -> gap-and-go)
ORB_TAPE_PCT     = 0.0          # tape gate: SPY day-so-far must be > this for longs (< -this shorts)
ORB_NOTIONAL_CAP = 5_000.0      # clamp qty * price to this (defined-risk + notional sanity)
ORB_UNIVERSE     = ["SPY", "QQQ", "IWM", "NVDA", "AAPL", "MSFT", "AMZN", "META",
                    "GOOGL", "TSLA", "AMD", "AVGO", "MU", "PLTR", "COIN"]
_OR_END_MIN  = 10 * 60          # opening range ends 10:00 ET
_ENTRY_CUTOFF_MIN = 15 * 60     # no NEW ORB entry after 15:00 ET (needs room before EOD flatten)


def held_symbols(state: dict) -> set:
    """ORB positions are managed as normal intraday brackets, NOT shielded — always empty.
    Present for a uniform book interface (mirrors gap_fade)."""
    return set()


def held_value(tc, state) -> float:
    return 0.0


def _intraday() -> dict | None:
    """Today's 5m bars for the universe, keyed by symbol (ET tz). None on any failure."""
    if not _YF_OK:
        return None
    syms = list(ORB_UNIVERSE)
    try:
        data = yf.download(syms, period="1d", interval="5m", progress=False,
                           group_by="ticker", auto_adjust=True, prepost=False, threads=True)
    except Exception:
        return None
    out = {}
    import pandas as pd
    multi = hasattr(data.columns, "levels")
    for s in syms:
        try:
            df = data[s] if multi else data
            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            if df.empty:
                continue
            idx = df.index
            if idx.tz is None:
                idx = idx.tz_localize("UTC")
            df = df.copy()
            df.index = idx.tz_convert("America/New_York")
            out[s] = df
        except Exception:
            continue
    return out or None


def _tape_pct(frames: dict, prev_spy_close: float | None) -> float | None:
    """SPY day-so-far % move (prior close -> latest) as the broad-tape proxy. None if unknown."""
    spy = frames.get("SPY")
    if spy is None or spy.empty:
        return None
    last = float(spy["Close"].iloc[-1])
    base = prev_spy_close if prev_spy_close else float(spy["Open"].iloc[0])
    return (last - base) / base if base else None


def _signal(df, tape_pct: float | None) -> dict | None:
    """Compute today's ORB signal for one symbol from its 5m frame. None if no/blocked setup."""
    mins = df.index.hour * 60 + df.index.minute
    or_bars = df[(mins >= 9 * 60 + 30) & (mins < _OR_END_MIN)]
    if len(or_bars) < 4:
        return None
    or_hi = float(or_bars["High"].max())
    or_lo = float(or_bars["Low"].min())
    or_mid = (or_hi + or_lo) / 2.0
    ref = float(or_bars["Close"].iloc[-1])
    width = or_hi - or_lo
    if ref <= 0 or width <= 0:
        return None
    wpct = width / ref
    if wpct < ORB_OR_MIN_PCT or wpct > ORB_OR_MAX_PCT:
        return None

    post = df[mins >= _OR_END_MIN]
    if post.empty:
        return None
    last = float(post["Close"].iloc[-1])
    if last > or_hi:
        direction, side = "long", "buy"
    elif last < or_lo:
        direction, side = "short", "sell"
    else:
        return None

    # tape gate — don't fight the tape
    if tape_pct is None:
        return None
    if direction == "long" and tape_pct <= ORB_TAPE_PCT:
        return None
    if direction == "short" and tape_pct >= -ORB_TAPE_PCT:
        return None

    stop = or_lo if (ORB_STOP_MODE == "full" and direction == "long") else \
           or_hi if (ORB_STOP_MODE == "full") else or_mid
    stop_dist = abs(last - stop)
    if stop_dist <= 0:
        return None
    target = last + ORB_TARGET_R * stop_dist if direction == "long" \
        else last - ORB_TARGET_R * stop_dist
    return {"direction": direction, "side": side, "entry_ref": round(last, 2),
            "stop": round(stop, 2), "target": round(target, 2),
            "stop_dist": stop_dist, "or_hi": round(or_hi, 2), "or_lo": round(or_lo, 2)}


def run(tc, state, dry: bool):
    """Once per name per day, after 10:00 ET, take the first ORB breakout with the tape."""
    if not ORB_ENABLED:
        return
    import autotrade as at
    try:
        now = at.et_now()
        today = now.strftime("%Y-%m-%d")
        mins = now.hour * 60 + now.minute
        if mins < _OR_END_MIN or mins >= _ENTRY_CUTOFF_MIN:
            return                                        # only 10:00-15:00 ET
        orb = state.setdefault("orb", {})
        done = orb.setdefault("done", {})                 # symbol -> date already traded
        frames = _intraday()
        if not frames:
            return
        tape = _tape_pct(frames, orb.get("prev_spy_close"))

        # held / working names — a bracket is an ENTRY order and Alpaca rejects it if a
        # position OR working order already exists on the name (same constraint as gap_fade).
        try:
            held = {p.symbol for p in tc.get_all_positions()}
            working = {o.symbol for o in tc.get_orders(
                filter=at.GetOrdersRequest(status=at.QueryOrderStatus.OPEN, limit=200))}
        except Exception as e:
            at.log(f"orb: could not verify flat ({e}) — skip")
            return

        for sym in ORB_UNIVERSE:
            if done.get(sym) == today:
                continue
            if sym in cfg.BLACKLIST or sym in held or sym in working:
                continue
            df = frames.get(sym)
            if df is None or df.empty:
                continue
            sig = _signal(df, tape)
            if sig is None:
                continue
            if sig["direction"] == "short":
                try:
                    if not bool(getattr(tc.get_asset(sym), "shortable", False)):
                        done[sym] = today
                        continue
                except Exception:
                    continue
            qty = int(ORB_RISK // sig["stop_dist"])
            if qty < 1:
                done[sym] = today
                continue
            if qty * sig["entry_ref"] > ORB_NOTIONAL_CAP:               # clamp notional
                qty = int(ORB_NOTIONAL_CAP // sig["entry_ref"])
            if qty < 1:
                done[sym] = today
                continue
            done[sym] = today                                          # latch BEFORE submit
            if dry:
                at.log(f"[DRY] orb would {sig['side']} {qty} {sym} ({sig['direction']}) "
                       f"entry~{sig['entry_ref']} stop {sig['stop']} target {sig['target']}")
                at.save_state(state)
                return                                                # one ORB entry per cycle
            try:
                o = at.place_stock_bracket(tc, sym, qty, sig["side"], sig["stop"],
                                           sig["target"], dry, ref=sig["entry_ref"])
                state.setdefault("last_entry_time", {})[sym] = now.isoformat()
                orb["last"] = {"symbol": sym, "qty": qty, "direction": sig["direction"],
                               "entry_ref": sig["entry_ref"], "stop": sig["stop"],
                               "target": sig["target"], "opened": now.isoformat()}
                at.log(f"ORB {sig['side']} {qty} {sym} {sig['direction']} "
                       f"OR[{sig['or_lo']},{sig['or_hi']}] stop={sig['stop']} target={sig['target']} "
                       f"id={getattr(o,'id',None)}")
                at.tg_send(f"🚀 ORB {sig['side']} {qty} {sym} ({sig['direction']} breakout), "
                           f"target {sig['target']} stop {sig['stop']}.")
                at.save_state(state)
                return                                                # one ORB entry per cycle
            except Exception as e:
                at.log(f"orb: order {sym} failed: {e}")
                at.save_state(state)
                return
    except Exception as e:
        at.log(f"orb: run failed: {e}")


def summary(state: dict) -> dict:
    orb = state.get("orb", {}) or {}
    done = orb.get("done", {}) or {}
    return {"traded_today": [s for s, d in done.items()], "last": orb.get("last")}
