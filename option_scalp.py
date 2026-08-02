#!/usr/bin/env python3
"""
option_scalp.py — intraday momentum-burst LONG-OPTION scalps on index ETFs.
SHIPS OFF (OPTION_SCALP_ENABLED=False) — wired but dark, like orb/mean_reversion.

The setup ("scalping" done the only way that isn't instantly dead on costs):
a liquid index ETF (SPY/QQQ — penny-increment option chains, the ONLY chains where
a scalp's cost hurdle is even plausible) makes a momentum BURST — the last 5m bar
closes beyond the high/low of the prior SCALP_LOOKBACK bars on confirming volume,
aligned with the broad tape. Buy a slightly-ITM (~0.6-delta) SHORT-DTE option in
the break direction (defined risk = premium), and exit FAST: take-profit /
stop-loss as a % of premium, a hard time-stop, and a hard 15:30 ET flatten.
NEVER 0DTE (SCALP_MIN_DTE >= 1) — the 0DTE-directional kill in AUDIT_ROADMAP #2
stands; a scalp held minutes doesn't need same-day expiry to have gamma.

  *** PRIOR EVIDENCE IS ADVERSE — DO NOT ENABLE WITHOUT A PASSING BACKTEST. ***
  This book exists so the strategy is wired and MEASURABLE, not because it is
  believed profitable. Everything this repo has already tested points the other
  way: intraday time-series momentum, ORB and VWAP fades die at realistic costs
  (README "Where this landed"), 0DTE structures die on tail days
  (research/backtest_0dte.py), and the LLM showed no intraday selection edge.
  An option scalp stacks EXTRA friction on top of that dead signal class:
    - spread: even a penny-wide SPY contract at ~$2.00 premium donates ~0.5-1%
      of premium PER SIDE; single-name chains donate 2-5% and are hopeless.
    - theta: a 1-DTE ATM/near-ITM option bleeds ~1-3% of premium per 30min
      midday — the scalp pays rent while it waits.
    - cycle cadence: the engine manages exits every ~3-5 min, not tick-by-tick,
      so real TP/SL slippage is WORSE than any bar-close backtest shows.
  Run research/backtest_option_scalp.py (realistic friction grid + cycle-lag
  variant) and demand it survives the 2%/side column before ever flipping the
  flag. The burst threshold/volume gate here match the backtest's base params.

Mechanics: deterministic (no model call), at most SCALP_MAX_CONCURRENT open at
once and SCALP_MAX_TRADES_PER_DAY entries per day, one attempt per underlying per
day (latch in state["option_scalp"]["done"]). Holdings are tracked in the book's
OWN state and its symbols are SHIELDED via held_symbols() -> held_books (the
generic option manager / orphan sweep must not double-manage them) — so run()
owns the WHOLE lifecycle: it closes on TP/SL/time/EOD each cycle and force-closes
any stale (pre-today) holding at the next open as belt-and-suspenders. Realized
P&L is booked under "option_scalp" in the shared ledger.
"""

from __future__ import annotations

import config as cfg

# --- module-level gate (mirrored from cfg.OPTION_SCALP_ENABLED by the engine) --
OPTION_SCALP_ENABLED = False

# --- tunables (self-contained; mirror into config.py only if ever deployed) ----
SCALP_UNIVERSE       = ["SPY", "QQQ"]  # penny-wide index chains ONLY (cost hurdle)
SCALP_LOOKBACK       = 12       # burst = last 5m close beyond prior-N-bar extreme (~1h)
SCALP_VOL_MULT       = 1.5      # last bar volume >= this x median(prior N) to confirm
SCALP_TAPE_MIN       = 0.0010   # tape alignment: SPY day-so-far >= +0.10% for calls (<= - for puts)
SCALP_ITM_PCT        = 0.010    # strike ~1% ITM (~0.6 delta proxy) — mostly intrinsic, less theta
SCALP_MIN_DTE        = 1        # NEVER 0DTE directional (AUDIT_ROADMAP #2); nearest >= this
SCALP_MAX_SPREAD_PCT = 0.02     # reject a contract quoting wider than 2% of mid
SCALP_NOTIONAL       = 1500.0   # max premium outlay per scalp (qty from this; defined risk)
SCALP_TP_PCT         = 0.25     # take-profit at +25% of premium
SCALP_SL_PCT         = 0.20     # stop at -20% of premium
SCALP_MAX_HOLD_MIN   = 45       # time-stop: a scalp that hasn't paid in 45min is a chop trade
SCALP_MAX_TRADES_PER_DAY = 2    # bounded daily attempts across the universe
SCALP_MAX_CONCURRENT = 1        # one scalp at a time
SCALP_ENTRY_START_MIN = 10 * 60        # 10:00 ET — let the open settle
SCALP_ENTRY_END_MIN   = 14 * 60 + 30   # 14:30 ET — no new scalp into the close
SCALP_FLAT_MIN        = 15 * 60 + 30   # 15:30 ET — hard flatten, before the engine's 15:45 sweeps

try:
    import yfinance as yf
    _YF_OK = True
except Exception:
    _YF_OK = False


# --------------------------- pure signal / exit logic -------------------------
# These take plain bar dicts ({"t_min","o","h","l","c","v"}) and quote-row dicts
# so they are unit-testable with no pandas / network / broker.

def _signal(bars: list, tape_pct) -> dict | None:
    """Momentum burst on one symbol's completed 5m bars. The LAST bar is the
    trigger candidate; the prior SCALP_LOOKBACK bars are the compression range.
    Returns {"direction": "long"|"short", "ref": last_close} or None."""
    if tape_pct is None or len(bars) < SCALP_LOOKBACK + 1:
        return None
    last, prior = bars[-1], bars[-SCALP_LOOKBACK - 1:-1]
    ref = float(last["c"])
    if ref <= 0:
        return None
    hi = max(b["h"] for b in prior)
    lo = min(b["l"] for b in prior)
    vols = sorted(float(b["v"]) for b in prior)
    med_v = vols[len(vols) // 2]
    if med_v <= 0 or float(last["v"]) < SCALP_VOL_MULT * med_v:
        return None                                   # no volume confirmation
    if ref > hi and tape_pct >= SCALP_TAPE_MIN:
        return {"direction": "long", "ref": ref}
    if ref < lo and tape_pct <= -SCALP_TAPE_MIN:
        return {"direction": "short", "ref": ref}
    return None


def _pick_contract(rows: list, spot: float, direction: str, today) -> dict | None:
    """From chain rows ({symbol,type,strike,expiry,bid,ask,mid}) pick the scalp
    contract: nearest expiry with DTE >= SCALP_MIN_DTE (never 0DTE), strike
    ~SCALP_ITM_PCT in-the-money on the break side, tight two-sided quote."""
    from datetime import date, timedelta
    if not rows or not spot or spot <= 0:
        return None
    want = "call" if direction == "long" else "put"
    min_exp = (today + timedelta(days=SCALP_MIN_DTE)).isoformat()
    expiries = sorted({r["expiry"] for r in rows if r["expiry"] >= min_exp})
    if not expiries:
        return None
    exp = expiries[0]
    target = spot * (1 - SCALP_ITM_PCT) if want == "call" else spot * (1 + SCALP_ITM_PCT)
    cands = [r for r in rows if r["type"] == want and r["expiry"] == exp
             and (r["strike"] < spot if want == "call" else r["strike"] > spot)]
    cands = [r for r in cands if (r.get("bid") or 0) > 0 and (r.get("ask") or 0) > 0
             and (r.get("mid") or 0) > 0
             and (r["ask"] - r["bid"]) / r["mid"] <= SCALP_MAX_SPREAD_PCT]
    if not cands:
        return None
    return min(cands, key=lambda r: abs(r["strike"] - target))


def _exit_reason(ret, held_min: float, now_min: int) -> str | None:
    """TP / SL / time-stop / hard-flat decision for one open scalp. `ret` is the
    option's return on premium (mid/entry - 1), None if unquotable right now."""
    if now_min >= SCALP_FLAT_MIN:
        return "eod"
    if ret is not None:
        if ret >= SCALP_TP_PCT:
            return "tp"
        if ret <= -SCALP_SL_PCT:
            return "sl"
    if held_min >= SCALP_MAX_HOLD_MIN:
        return "time"
    return None


# ------------------------------ book interface --------------------------------

def _holdings(state: dict) -> list:
    return list((state.get("option_scalp") or {}).get("holdings") or [])


def held_symbols(state: dict) -> set:
    """Open scalp contracts — SHIELDED: this book manages its own exits, so the
    generic option manager / orphan sweep must leave these symbols alone."""
    return {h["symbol"] for h in _holdings(state)}


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


# ------------------------------ data plumbing ----------------------------------

def _bars_from_df(df) -> list:
    """A yfinance 5m frame (ET-tz index) -> plain bar dicts for _signal."""
    out = []
    for ts, row in df.iterrows():
        out.append({"t_min": ts.hour * 60 + ts.minute,
                    "o": float(row["Open"]), "h": float(row["High"]),
                    "l": float(row["Low"]), "c": float(row["Close"]),
                    "v": float(row["Volume"])})
    return out


def _intraday() -> dict | None:
    """Today's 5m bars for the universe as {sym: [bar dicts]}. None on failure."""
    if not _YF_OK:
        return None
    syms = list(dict.fromkeys(SCALP_UNIVERSE + ["SPY"]))
    try:
        data = yf.download(syms, period="1d", interval="5m", progress=False,
                           group_by="ticker", auto_adjust=True, prepost=False, threads=True)
    except Exception:
        return None
    out = {}
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
            out[s] = _bars_from_df(df)
        except Exception:
            continue
    return out or None


def _tape_pct(frames: dict) -> float | None:
    """SPY day-so-far % (session open -> latest close) as the broad-tape proxy."""
    spy = frames.get("SPY")
    if not spy:
        return None
    base = spy[0]["o"]
    return (spy[-1]["c"] - base) / base if base else None


def _chain_rows(odc, sym, spot, today):
    """Near-money chain rows around spot (same row shape as option_chain_for)."""
    import autotrade as at
    if odc is None or not spot or spot <= 0:
        return []
    try:
        from alpaca.data.requests import OptionChainRequest
        chain = odc.get_option_chain(OptionChainRequest(
            underlying_symbol=sym,
            strike_price_gte=round(spot * 0.95, 2),
            strike_price_lte=round(spot * 1.05, 2),
        ))
    except Exception as e:
        at.log(f"option_scalp: chain fetch {sym} failed: {e}")
        return []
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
    return rows


# ------------------------------ manage / enter ---------------------------------

def _close_one(tc, odc, state, h, reason: str, dry: bool) -> bool:
    """Close one scalp holding. Returns True if closed (caller drops it)."""
    import autotrade as at
    sym, qty, entry = h["symbol"], int(h.get("qty") or 1), float(h.get("entry") or 0.0)
    if dry:
        at.log(f"[DRY] option_scalp would CLOSE {sym} ({reason})")
        return False
    _, _, mid = at.option_quote(odc, sym)
    try:
        tc.close_position(sym)
    except Exception as ex:
        at.log(f"option_scalp: close {sym} failed: {ex}")
        return False
    realized = ((mid or 0.0) - entry) * 100 * qty if entry else 0.0
    at.record_strategy_realized(state, "option_scalp", realized)
    state.setdefault("option_scalp", {})["last_realized"] = round(realized, 2)
    at.log(f"OPTION_SCALP close {sym} ({reason}) realized≈{realized:+.0f}")
    at.tg_send(f"⚡ Option scalp {sym} closed ({reason}): P&L ≈ ${realized:+,.0f}.")
    return True


def _manage(tc, odc, state, now, dry):
    """Exit pass: TP/SL/time-stop/15:30 flat on every open scalp, and force-close
    anything stale from a prior day (a missed flatten must not ride to expiry)."""
    import autotrade as at
    sc = state.setdefault("option_scalp", {})
    holds = _holdings(state)
    if not holds:
        return
    today_s = now.date().isoformat()
    now_min = now.hour * 60 + now.minute
    remaining, closed_any = [], False
    for h in holds:
        if h.get("entry_date") != today_s:
            reason = "stale"                          # belt-and-suspenders next-day close
        else:
            _, _, mid = at.option_quote(odc, h["symbol"])
            entry = float(h.get("entry") or 0.0)
            ret = (mid / entry - 1) if (mid and entry) else None
            try:
                from datetime import datetime
                opened = datetime.fromisoformat(h["opened"])
                held_min = (now - opened).total_seconds() / 60.0
            except Exception:
                held_min = 0.0
            reason = _exit_reason(ret, held_min, now_min)
        if reason and _close_one(tc, odc, state, h, reason, dry):
            closed_any = True
        else:
            remaining.append(h)
    sc["holdings"] = remaining
    if closed_any:
        at.save_state(state)


def _enter(tc, odc, state, now, dry):
    import autotrade as at
    sc = state.setdefault("option_scalp", {})
    today_s = now.date().isoformat()
    now_min = now.hour * 60 + now.minute
    if not (SCALP_ENTRY_START_MIN <= now_min < SCALP_ENTRY_END_MIN):
        return
    if len(_holdings(state)) >= SCALP_MAX_CONCURRENT:
        return
    if sc.get("count_date") != today_s:
        sc["count_date"], sc["count"] = today_s, 0
    if int(sc.get("count") or 0) >= SCALP_MAX_TRADES_PER_DAY:
        return
    done = sc.setdefault("done", {})                  # underlying -> date attempted
    frames = _intraday()
    if not frames:
        return
    tape = _tape_pct(frames)
    for sym in SCALP_UNIVERSE:
        if done.get(sym) == today_s or sym in cfg.BLACKLIST:
            continue
        bars = frames.get(sym)
        if not bars:
            continue
        sig = _signal(bars, tape)
        if sig is None:
            continue
        done[sym] = today_s                           # latch BEFORE submit
        spot = sig["ref"]
        row = _pick_contract(_chain_rows(odc, sym, spot, now.date()),
                             spot, sig["direction"], now.date())
        if row is None:
            at.log(f"option_scalp: {sym} burst but no tight >=1-DTE contract — skip")
            at.save_state(state)
            continue
        ask = float(row["ask"])
        qty = int(SCALP_NOTIONAL // (ask * 100))
        if qty < 1:
            at.log(f"option_scalp: {sym} {row['symbol']} ask ${ask:.2f} exceeds "
                   f"${SCALP_NOTIONAL:.0f} notional — skip")
            at.save_state(state)
            continue
        sc["count"] = int(sc.get("count") or 0) + 1
        if dry:
            at.log(f"[DRY] option_scalp would BUY {qty}x {row['symbol']} "
                   f"({sig['direction']} burst on {sym}) @lim {round(ask*1.02, 2)}")
            at.save_state(state)
            return                                    # one entry per cycle
        try:
            lim = round(ask * 1.02, 2)                # marketable limit, house style
            req = at.LimitOrderRequest(symbol=row["symbol"], qty=qty,
                                       side=at.OrderSide.BUY,
                                       time_in_force=at.TimeInForce.DAY,
                                       limit_price=lim)
            o = tc.submit_order(order_data=req)
            entry = float(o.filled_avg_price) if getattr(o, "filled_avg_price", None) else ask
            sc.setdefault("holdings", []).append({
                "symbol": row["symbol"], "underlying": sym, "qty": qty,
                "entry": entry, "direction": sig["direction"],
                "entry_date": today_s, "opened": now.isoformat(),
                "order_id": str(getattr(o, "id", "")) or None})
            at.log(f"OPTION_SCALP buy {qty}x {row['symbol']} ({sig['direction']} burst "
                   f"{sym}) @lim {lim} entry≈{entry} id={getattr(o, 'id', None)}")
            at.tg_send(f"⚡ Option scalp: bought {qty}x {row['symbol']} "
                       f"({sig['direction']} burst on {sym}).")
        except Exception as ex:
            at.log(f"option_scalp: order {row['symbol']} failed: {ex}")
        at.save_state(state)
        return                                        # one entry per cycle
    at.save_state(state)


def run(tc, state, dry: bool):
    if not OPTION_SCALP_ENABLED:
        return
    import autotrade as at
    try:
        odc = at.option_data_client() if at._ALPACA_OK else None
        now = at.et_now()
        _manage(tc, odc, state, now, dry)
        _enter(tc, odc, state, now, dry)
    except Exception as e:
        at.log(f"option_scalp: run failed: {e}")


def summary(state: dict) -> dict:
    sc = state.get("option_scalp", {}) or {}
    holds = _holdings(state)
    return {
        "holdings": [{"symbol": h["symbol"], "underlying": h.get("underlying"),
                      "qty": h.get("qty"), "entry": h.get("entry"),
                      "direction": h.get("direction"), "opened": h.get("opened")}
                     for h in holds],
        "trades_today": int(sc.get("count") or 0) if sc.get("count_date") else 0,
        "last_realized": sc.get("last_realized"),
    }
