#!/usr/bin/env python3
"""
gap_fade.py — opening-gap fade (9:30-10:00 ET).

Fills the otherwise-dead first half hour. The evidence: liquid stocks revert their
opening gap back toward the prior close ~60-70% of the time (and gap-fade beats
gap-and-go for single names). So near the open we find the LARGEST qualifying gap
in a liquid universe and fade it back toward yesterday's close with a defined-risk
bracket: target = prior close, stop = a fixed % beyond the open (past the extreme).

Rules (all deterministic — no model call):
  - window: 9:30-10:00 ET only; at most ONE gap-fade per day (bounded risk)
  - gap size between GAP_FADE_MIN_PCT and GAP_FADE_MAX_PCT (skip monster news gaps,
    which tend to gap-and-go rather than fill)
  - gap UP  -> SHORT toward prior close (needs a shortable name)
  - gap DOWN -> LONG toward prior close
  - size GAP_FADE_NOTIONAL; bracket stop GAP_FADE_STOP_PCT beyond entry

Unlike the overnight/growth books, a gap-fade is INTRADAY: it places a normal stock
bracket, so the existing trailing-stop + EOD-flatten machinery manages and closes
it. It is therefore NOT added to held_books (it is not shielded — it's just another
deterministically-triggered intraday entry). Once-per-day latch lives in
state["gap_fade"].
"""

from __future__ import annotations

import config as cfg

try:
    import yfinance as yf
    _YF_OK = True
except Exception:
    _YF_OK = False


def held_symbols(state: dict) -> set:
    """Gap-fade positions are managed as normal intraday brackets, NOT shielded —
    so this is always empty. Present for a uniform book interface."""
    return set()


def held_value(tc, state) -> float:
    return 0.0


def _in_window(now) -> bool:
    """9:30 ET (open) through 10:00 ET entry window."""
    mins = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= mins < 10 * 60 + cfg.GAP_FADE_WINDOW_END_MIN


def _gaps() -> list[dict]:
    """For each universe name, today's gap vs the prior completed close. Returns
    [{symbol, prev_close, last, gap_pct}] sorted by |gap| desc. [] on any failure."""
    if not _YF_OK:
        return []
    syms = list(cfg.GAP_FADE_UNIVERSE)
    out = []
    try:
        daily = yf.download(syms, period="5d", interval="1d", progress=False,
                            group_by="ticker", threads=True)
        intraday = yf.download(syms, period="1d", interval="1m", progress=False,
                               group_by="ticker", threads=True)
    except Exception:
        return []
    for s in syms:
        try:
            df = daily[s] if len(syms) > 1 else daily
            dclose = df["Close"].dropna()
            if len(dclose) < 2:
                continue
            prev_close = float(dclose.iloc[-2])
            last = None
            try:
                idf = intraday[s] if len(syms) > 1 else intraday
                ic = idf["Close"].dropna()
                if len(ic):
                    last = float(ic.iloc[-1])
            except Exception:
                last = None
            if last is None:
                last = float(dclose.iloc[-1])
            if prev_close <= 0:
                continue
            gap = (last - prev_close) / prev_close * 100.0
            out.append({"symbol": s, "prev_close": round(prev_close, 2),
                        "last": round(last, 2), "gap_pct": round(gap, 2)})
        except Exception:
            continue
    out.sort(key=lambda r: abs(r["gap_pct"]), reverse=True)
    return out


def run(tc, state, dry: bool):
    """Once per day in the 9:30-10:00 window, fade the largest qualifying gap."""
    if not cfg.GAP_FADE_ENABLED:
        return
    import autotrade as at
    try:
        now = at.et_now()
        today = now.strftime("%Y-%m-%d")
        gf = state.setdefault("gap_fade", {})
        if gf.get("last_trade_date") == today:
            return                                   # already faded once today
        if not _in_window(now):
            return
        cands = [g for g in _gaps()
                 if cfg.GAP_FADE_MIN_PCT <= abs(g["gap_pct"]) <= cfg.GAP_FADE_MAX_PCT
                 and g["symbol"] not in cfg.BLACKLIST]
        if not cands:
            return
        g = cands[0]
        sym, last, prev = g["symbol"], g["last"], g["prev_close"]
        gapped_up = g["gap_pct"] > 0
        direction = "short" if gapped_up else "long"   # fade the gap
        side = "sell" if gapped_up else "buy"
        # Shortability gate for the gap-up fade.
        if direction == "short":
            try:
                if not bool(getattr(tc.get_asset(sym), "shortable", False)):
                    at.log(f"gap_fade: {sym} gapped up but not shortable — skip")
                    gf["last_trade_date"] = today      # don't thrash the same gap
                    at.save_state(state)
                    return
            except Exception:
                return
        qty = int(cfg.GAP_FADE_NOTIONAL // last)
        if qty < 1:
            return
        # A bracket is an ENTRY order — Alpaca rejects it ("bracket orders must be
        # entry orders", code 42210000) if a position OR a working order already
        # exists on the name, because the TP/SL legs would then reduce/close rather
        # than open (this is exactly why the 6/9 AMD gap-fade failed). Only fade a
        # name we're genuinely flat on; otherwise latch and move on.
        try:
            held = {p.symbol for p in tc.get_all_positions()}
            working = {o.symbol for o in tc.get_orders(
                filter=at.GetOrdersRequest(status=at.QueryOrderStatus.OPEN, limit=200))}
        except Exception as e:
            at.log(f"gap_fade: could not verify flat for {sym} ({e}) — skip")
            return
        if sym in held or sym in working:
            at.log(f"gap_fade: {sym} already has a position/order — skip (bracket needs a flat entry)")
            gf["last_trade_date"] = today
            at.save_state(state)
            return
        # Bracket: target = prior close (the fade fill); stop a fixed % beyond entry.
        if direction == "short":
            target = round(prev, 2)
            stop = round(last * (1 + cfg.GAP_FADE_STOP_PCT), 2)
        else:
            target = round(prev, 2)
            stop = round(last * (1 - cfg.GAP_FADE_STOP_PCT), 2)
        gf["last_trade_date"] = today                  # latch BEFORE submit (at-most-once)
        if dry:
            at.log(f"[DRY] gap_fade would {side} {qty} {sym} (gap {g['gap_pct']:+.1f}%) "
                   f"target {target} stop {stop}")
            at.save_state(state)
            return
        try:
            o = at.place_stock_bracket(tc, sym, qty, side, stop, target, dry)
            state.setdefault("last_entry_time", {})[sym] = now.isoformat()
            gf["last"] = {"symbol": sym, "qty": qty, "direction": direction,
                          "entry_ref": last, "target": target, "stop": stop,
                          "gap_pct": g["gap_pct"], "opened": now.isoformat()}
            at.log(f"GAP FADE {side} {qty} {sym} gap={g['gap_pct']:+.1f}% "
                   f"target={target} stop={stop} id={getattr(o,'id',None)}")
            at.tg_send(f"↩️ Gap fade: {side} {qty} {sym} (gapped {g['gap_pct']:+.1f}%), "
                       f"target {target}.")
            at.save_state(state)
        except Exception as e:
            at.log(f"gap_fade: order {sym} failed: {e}")
    except Exception as e:
        at.log(f"gap_fade: run failed: {e}")


def summary(state: dict) -> dict:
    gf = state.get("gap_fade", {}) or {}
    return {"last_trade_date": gf.get("last_trade_date"), "last": gf.get("last")}
