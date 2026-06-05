#!/usr/bin/env python3
"""
growth_sleeve.py — long-horizon compounding book funded by the intraday engine's
prior-day gains.

Separate from the day-trading logic in autotrade.py:
  - DEPLOY: once per trading day (>=09:40 ET) it puts yesterday's profit — plus a
    small floor on flat/red days (config GROWTH_DAILY_FLOOR) — into the strongest
    screened long-term growth name(s). Funds it as plain LONG holdings (notional/
    whole-share market buys), never bracketed, so they ride for weeks.
  - SCREEN: a trend+momentum screen over GROWTH_UNIVERSE (+ GROWTH_LEVERAGED).
    Eligibility = price above its 50- and 200-day MAs; leveraged ETFs additionally
    require a confirmed uptrend (price>200DMA AND positive 6-month return) and get a
    score de-rate so they only win when clearly stronger. Screen is cached once/day.
  - MANAGE (every cycle): a wide chandelier trailing stop (GROWTH_TRAIL_PCT below the
    high-water mark) plus a trend-break exit (close below the 50-DMA). Freed cash is
    redeployed on a later day.
  - ROTATE (weekly): swap the weakest holding for the top screen leader if the
    leader's score beats it by GROWTH_ROTATE_MARGIN — at most one swap per week.

These holdings are SHIELDED from the intraday machinery: autotrade.py skips them in
the EOD flatten, the tight intraday trailing stops, and the daily loss-halt flatten,
and the decision model is told they are off-limits. `held_symbols(state)` is the set
the rest of the bot uses to recognise and exclude them.

All broker/log/clock helpers are reused from autotrade via a lazy import to avoid a
circular import at module load (autotrade imports this module lazily inside run_cycle).
"""

from __future__ import annotations

import json

import config as cfg

try:
    import yfinance as yf
    import pandas as pd
    _YF_OK = True
except Exception:
    _YF_OK = False


# ===========================================================================
# State helpers
# ===========================================================================
def held_symbols(state: dict) -> set:
    """Symbols currently held by the growth sleeve — the set the intraday engine
    must exclude from EOD flatten / trailing stops / loss-halt flatten / new entries."""
    return {h["symbol"] for h in (state.get("growth_sleeve") or [])}


def _sleeve_market_value(tc, state) -> float:
    """Current $ market value of the growth holdings (for the sleeve cap and to
    exclude from the intraday deployed-capital cap)."""
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
# Screen
# ===========================================================================
def _is_leveraged(sym: str) -> bool:
    return sym in cfg.GROWTH_LEVERAGED


def _compute_screen() -> dict:
    """Trend+momentum screen over the growth universe. Returns
    {symbol: {last, ma50, ma200, ret_1m, ret_3m, ret_6m, score, eligible, leveraged}}.
    One batched yfinance pull; safe to call once/day (cached by screen())."""
    import autotrade as at
    if not _YF_OK:
        at.log("growth: yfinance/pandas unavailable — screen skipped")
        return {}
    tickers = list(dict.fromkeys(cfg.GROWTH_UNIVERSE + cfg.GROWTH_LEVERAGED))
    try:
        data = yf.download(tickers, period="1y", interval="1d",
                           auto_adjust=True, progress=False, group_by="ticker",
                           threads=True)
    except Exception as e:
        at.log(f"growth: screen download failed: {e}")
        return {}

    out = {}
    for sym in tickers:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if sym not in data.columns.get_level_values(0):
                    continue
                closes = data[sym]["Close"].dropna()
            else:                       # single-ticker frame
                closes = data["Close"].dropna()
        except Exception:
            continue
        if len(closes) < 130:           # need ~6mo of history to score
            continue
        c = [float(x) for x in closes.tolist()]
        last = c[-1]
        ma50 = sum(c[-50:]) / 50
        ma200 = sum(c[-200:]) / 200 if len(c) >= 200 else sum(c) / len(c)
        ret_1m = last / c[-21] - 1 if len(c) >= 21 else 0.0
        ret_3m = last / c[-63] - 1 if len(c) >= 63 else 0.0
        ret_6m = last / c[-126] - 1
        lev = _is_leveraged(sym)
        # Trend filter: must be above both MAs. Leverage additionally gated on a
        # confirmed uptrend (price>200DMA already required, plus positive 6-month).
        eligible = last > ma50 and last > ma200
        if lev:
            eligible = eligible and ret_6m > 0
        score = 0.5 * ret_6m + 0.3 * ret_3m + 0.2 * ret_1m
        if lev:
            score *= cfg.GROWTH_LEV_PENALTY
        out[sym] = {"last": round(last, 2), "ma50": round(ma50, 2),
                    "ma200": round(ma200, 2), "ret_1m": round(ret_1m, 4),
                    "ret_3m": round(ret_3m, 4), "ret_6m": round(ret_6m, 4),
                    "score": round(score, 4), "eligible": bool(eligible),
                    "leveraged": lev}
    return out


def screen(force: bool = False) -> dict:
    """Daily-cached growth screen. Recomputes once per ET day (or when forced)."""
    import autotrade as at
    today = at.et_now().strftime("%Y-%m-%d")
    if not force and cfg.GROWTH_STATE_FILE.exists():
        try:
            cached = json.loads(cfg.GROWTH_STATE_FILE.read_text())
            if cached.get("day") == today and cached.get("rows"):
                return cached["rows"]
        except Exception:
            pass
    rows = _compute_screen()
    if rows:
        try:
            cfg.GROWTH_STATE_FILE.write_text(
                json.dumps({"day": today, "rows": rows}, indent=2))
        except Exception as e:
            at.log(f"growth: screen cache write failed: {e}")
        at.log(f"growth: screened {len(rows)} names, "
               f"{sum(1 for r in rows.values() if r['eligible'])} eligible")
    return rows


def _ranked_eligible(rows: dict) -> list[tuple[str, dict]]:
    return sorted(((s, r) for s, r in rows.items() if r.get("eligible")),
                  key=lambda kv: kv[1]["score"], reverse=True)


# ===========================================================================
# Orders
# ===========================================================================
def _buy(tc, sym: str, dollars: float, dry: bool):
    """Buy ~`dollars` of `sym` as a plain long market order (notional if the asset is
    fractionable, else floor to whole shares). Returns (filled_qty, avg_price) or
    (0, 0) if nothing was bought."""
    import autotrade as at
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    rows = screen()
    last = rows.get(sym, {}).get("last") or 0.0
    if dry:
        at.log(f"[DRY] growth would BUY ~${dollars:.0f} {sym} (last {last})")
        return (round(dollars / last, 4) if last else 0.0), last
    fractionable = True
    try:
        fractionable = bool(getattr(tc.get_asset(sym), "fractionable", True))
    except Exception:
        pass
    try:
        if fractionable:
            req = MarketOrderRequest(symbol=sym, notional=round(dollars, 2),
                                     side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
        else:
            qty = int(dollars // last) if last else 0
            if qty < 1:
                at.log(f"growth: ${dollars:.0f} < 1 share of {sym} ({last}) — carried forward")
                return 0.0, last
            req = MarketOrderRequest(symbol=sym, qty=qty,
                                     side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
        o = tc.submit_order(order_data=req)
        filled_qty = float(o.filled_qty or 0) or (round(dollars / last, 4) if last else 0.0)
        avg = float(o.filled_avg_price) if o.filled_avg_price else last
        at.log(f"GROWTH BUY ~${dollars:.0f} {sym} qty≈{filled_qty} @≈{avg} id={o.id}")
        at.tg_send(f"🌱 Growth sleeve: bought ~${dollars:.0f} of {sym}.")
        return filled_qty, avg
    except Exception as e:
        at.log(f"growth: buy {sym} failed: {e}")
        return 0.0, last


def _sell_all(tc, sym: str, dry: bool, reason: str):
    import autotrade as at
    if dry:
        at.log(f"[DRY] growth would SELL all {sym} ({reason})")
        return
    try:
        tc.close_position(sym)
        at.log(f"GROWTH SELL {sym} — {reason}")
        at.tg_send(f"🍂 Growth sleeve: exited {sym} ({reason}).")
    except Exception as e:
        at.log(f"growth: sell {sym} failed: {e}")


# ===========================================================================
# Deploy
# ===========================================================================
def deploy(tc, state, dry: bool):
    """Once per day (>=09:40 ET): roll yesterday's gain (or the floor) into the most
    underweight strong screen leader, respecting the per-name and total-sleeve caps."""
    import autotrade as at
    if not cfg.GROWTH_SLEEVE_ENABLED:
        return
    now = at.et_now()
    g = state.setdefault("growth", {})
    today = now.strftime("%Y-%m-%d")

    # Roll the open-equity bookkeeping once per day (equity at the day's open ≈ prior
    # close; buying growth is equity-neutral, so day-over-day open equity = P&L).
    equity = at.account_snapshot(tc)["equity"]
    if g.get("open_date") != today:
        g["prev_open_equity"] = g.get("open_equity")
        g["open_equity"] = equity
        g["open_date"] = today

    if g.get("deployed_date") == today:
        return
    if now.hour < cfg.GROWTH_DEPLOY_HOUR or (
            now.hour == cfg.GROWTH_DEPLOY_HOUR and now.minute < cfg.GROWTH_DEPLOY_MIN):
        return  # wait until the open settles

    prev = g.get("prev_open_equity")
    prior_gain = (g["open_equity"] - prev) if prev is not None else None
    base = cfg.GROWTH_DAILY_FLOOR if prior_gain is None else max(prior_gain, cfg.GROWTH_DAILY_FLOOR)
    pending = round(g.get("pending_cash", 0.0) + base, 2)
    g["deployed_date"] = today          # we attempt at most once/day regardless of fill
    g["last_prior_gain"] = None if prior_gain is None else round(prior_gain, 2)

    # Respect the total-sleeve cap and available cash.
    acct = at.account_snapshot(tc)
    sleeve_val = _sleeve_market_value(tc, state)
    room = cfg.GROWTH_MAX_SLEEVE_CAPITAL - sleeve_val
    to_deploy = min(pending, room, max(0.0, acct["cash"] - 50))  # keep a small cash buffer
    if to_deploy < 1.0:
        at.log(f"growth: nothing to deploy (pending ${pending:.0f}, room ${room:.0f}, "
               f"cash ${acct['cash']:.0f})")
        g["pending_cash"] = pending
        at.save_state(state)
        return

    rows = screen()
    ranked = _ranked_eligible(rows)
    if not ranked:
        at.log("growth: no eligible candidates today — carrying cash forward")
        g["pending_cash"] = pending
        at.save_state(state)
        return

    # Current $ per held name, to deploy into the most-underweight top-N leader.
    held = {h["symbol"]: h for h in state.get("growth_sleeve", [])}
    pos_val = {}
    try:
        for p in tc.get_all_positions():
            if p.symbol in held:
                pos_val[p.symbol] = abs(float(p.market_value or 0))
    except Exception:
        pass
    leaders = ranked[: cfg.GROWTH_TOP_N]
    # Prefer a leader we hold least of and that's under the per-name cap.
    def _underweight_key(kv):
        sym = kv[0]
        return (pos_val.get(sym, 0.0))
    leaders_open = [kv for kv in leaders
                    if pos_val.get(kv[0], 0.0) < cfg.GROWTH_MAX_PER_NAME]
    target = (sorted(leaders_open, key=_underweight_key)[0]
              if leaders_open else leaders[0])
    sym = target[0]
    # Don't exceed the per-name cap with this deployment.
    headroom = cfg.GROWTH_MAX_PER_NAME - pos_val.get(sym, 0.0)
    spend = max(0.0, min(to_deploy, headroom))
    if spend < 1.0:
        at.log("growth: top leaders at per-name cap — carrying cash forward")
        g["pending_cash"] = pending
        at.save_state(state)
        return

    qty, avg = _buy(tc, sym, spend, dry)
    if qty and avg:
        sleeve = state.setdefault("growth_sleeve", [])
        existing = next((h for h in sleeve if h["symbol"] == sym), None)
        if existing:
            # blend the cost basis; keep the higher high-water mark
            tot_qty = existing["qty"] + qty
            existing["entry"] = round(
                (existing["entry"] * existing["qty"] + avg * qty) / tot_qty, 4)
            existing["qty"] = round(tot_qty, 4)
            existing["high_water"] = max(existing.get("high_water", avg), avg)
        else:
            sleeve.append({"symbol": sym, "qty": round(qty, 4), "entry": avg,
                           "high_water": avg, "entry_date": today,
                           "leveraged": _is_leveraged(sym)})
        g["pending_cash"] = round(pending - spend, 2)
        at.log(f"growth: deployed ${spend:.0f} into {sym} "
               f"(prior_gain {g['last_prior_gain']}, pending now ${g['pending_cash']:.0f})")
    else:
        g["pending_cash"] = pending     # carry the whole amount forward
    at.save_state(state)


# ===========================================================================
# Manage — chandelier trailing stop + trend-break exit
# ===========================================================================
def manage(tc, state, dry: bool):
    import autotrade as at
    if not cfg.GROWTH_SLEEVE_ENABLED:
        return
    holds = state.get("growth_sleeve") or []
    if not holds:
        return
    try:
        positions = {p.symbol: p for p in tc.get_all_positions()}
    except Exception as e:
        at.log(f"growth: positions fetch failed: {e}")
        return
    rows = screen()
    today = at.et_now().strftime("%Y-%m-%d")
    survivors = []
    for h in holds:
        sym = h["symbol"]
        p = positions.get(sym)
        if not p:
            # A buy placed earlier this same day may not be visible in positions yet
            # (pending fill) — keep it one day before concluding it's gone.
            if h.get("entry_date") == today:
                survivors.append(h)
                continue
            at.log(f"growth: {sym} no longer held — dropping from sleeve")
            continue
        cur = float(p.current_price or 0)
        if cur <= 0:
            survivors.append(h)
            continue
        h["high_water"] = round(max(h.get("high_water", h["entry"]), cur), 4)
        stop = h["high_water"] * (1 - cfg.GROWTH_TRAIL_PCT)
        ma50 = rows.get(sym, {}).get("ma50")
        reason = None
        if cur <= stop:
            reason = f"chandelier stop {stop:.2f} (HWM {h['high_water']:.2f})"
        elif ma50 and cur < ma50:
            reason = f"trend break: {cur:.2f} below {cfg.GROWTH_TREND_MA}DMA {ma50:.2f}"
        if reason:
            _sell_all(tc, sym, dry, reason)
            if dry:
                survivors.append(h)     # dry-run: keep tracking
        else:
            survivors.append(h)
    state["growth_sleeve"] = survivors


# ===========================================================================
# Rotate — weekly swap of the weakest holding for the screen leader
# ===========================================================================
def rotate(tc, state, dry: bool):
    import autotrade as at
    if not (cfg.GROWTH_SLEEVE_ENABLED and cfg.GROWTH_ROTATE_WEEKLY):
        return
    holds = state.get("growth_sleeve") or []
    if not holds:
        return
    now = at.et_now()
    iso_week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    g = state.setdefault("growth", {})
    if g.get("rotate_week") == iso_week:
        return
    # Only rotate during regular hours, after the deploy window.
    if now.hour < 10:
        return
    rows = screen()
    if not rows:
        return
    g["rotate_week"] = iso_week         # mark attempted this week regardless

    held = {h["symbol"] for h in holds}
    # Weakest current holding by today's score (unknown -> very weak).
    def _score(sym):
        return rows.get(sym, {}).get("score", -99)
    weakest = min(held, key=_score)
    weak_score = _score(weakest)
    # Best eligible leader we don't already hold.
    leaders = [(s, r) for s, r in _ranked_eligible(rows) if s not in held]
    if not leaders:
        at.save_state(state)
        return
    lead_sym, lead = leaders[0]
    if lead["score"] - weak_score < cfg.GROWTH_ROTATE_MARGIN:
        at.log(f"growth: weekly rotation — no swap (lead {lead_sym} {lead['score']} vs "
               f"weakest {weakest} {weak_score:.4f}, margin not met)")
        at.save_state(state)
        return
    # Swap: sell the laggard, redeploy its proceeds into the leader next deploy cycle.
    at.log(f"growth: weekly rotation — swap {weakest} ({weak_score:.4f}) -> "
           f"{lead_sym} ({lead['score']:.4f})")
    _sell_all(tc, weakest, dry, f"weekly rotation into {lead_sym}")
    if not dry:
        proceeds = 0.0
        try:
            for p in tc.get_all_positions():
                if p.symbol == weakest:
                    proceeds = abs(float(p.market_value or 0))
        except Exception:
            pass
        state["growth_sleeve"] = [h for h in holds if h["symbol"] != weakest]
        # Make the freed capital available to the next daily deploy.
        g["pending_cash"] = round(g.get("pending_cash", 0.0) + proceeds, 2)
    at.save_state(state)


# ===========================================================================
# Single entry point called by autotrade.run_cycle
# ===========================================================================
def run(tc, state, dry: bool):
    """Manage existing holdings, run the weekly rotation, then deploy the day's
    fresh capital. Order matters: free up stopped-out/rotated names first so their
    cash can be redeployed, then put new money to work."""
    if not cfg.GROWTH_SLEEVE_ENABLED:
        return
    import autotrade as at
    try:
        manage(tc, state, dry)
        rotate(tc, state, dry)
        deploy(tc, state, dry)
    except Exception as e:
        at.log(f"growth: run failed: {e}")


def summary(state: dict) -> dict:
    """Compact sleeve summary for status / the model context."""
    g = state.get("growth", {})
    return {
        "holdings": [{"symbol": h["symbol"], "qty": h["qty"], "entry": h["entry"],
                      "high_water": h.get("high_water"), "leveraged": h.get("leveraged", False)}
                     for h in (state.get("growth_sleeve") or [])],
        "pending_cash": round(g.get("pending_cash", 0.0), 2),
        "last_prior_gain": g.get("last_prior_gain"),
    }
