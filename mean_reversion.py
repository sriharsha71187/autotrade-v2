#!/usr/bin/env python3
"""
mean_reversion.py — single-name intraday MEAN-REVERSION (the chop-day book).

Fills the gap the momentum/trend books can't: on a range/chop day, liquid names
get STRETCHED below VWAP and oversold, then snap back toward VWAP. We buy that
snap-back with DEFINED RISK (a stock bracket: target = VWAP-reversion, stop below
the recent swing low). This is directional/long-the-dip — NOT short premium — so it
does NOT violate the index-only premium-selling rule.

Evidence / standard setup (intraday MR):
  - quality liquid name STRETCHED below its session VWAP (vwap_ext <= -MR_VWAP_EXT_PCT)
  - AND oversold (RSI(14) on 5-min closes <= MR_RSI_MAX, ~30)
  - but NOT a confirmed breakdown / falling knife: require the broad tape NOT risk_off,
    AND the name still above a longer reference (prior close, i.e. not gapped-and-bleeding).
  - enter LONG toward a VWAP-reversion target; stop below the session low (fixed R fallback).
  - symmetric SHORT for overbought stretched-ABOVE-VWAP into a risk_off tape (optional;
    gated by MR_ALLOW_SHORT, default off — backtest found long-only the robust side).

Mechanics mirror gap_fade.py: deterministic (no model call), ONE trade per name per day,
INTRADAY stock bracket (managed by the existing trailing-stop + EOD-flatten machinery, so
NOT shielded / NOT added to held_books). Once-per-day-per-name latch lives in
state["mean_reversion"]["traded"][date].

Gate: MEANREV_ENABLED=False — wired but dark until the integrator flips it on.
"""

from __future__ import annotations

import config as cfg

# --- module-level gate (the integrator flips this; dark by default) ----------
MEANREV_ENABLED = False

# --- tunables (defaults = backtest-recommended base params) -------------------
MR_VWAP_EXT_PCT = 1.5      # stretched: vwap_ext <= -1.5%  (long side)
MR_RSI_MAX = 30.0          # oversold long entry
MR_TARGET_VWAP = True      # target = session VWAP (True) else fixed 1R
MR_R_MULT_TARGET = 1.0     # used when MR_TARGET_VWAP is False
MR_STOP_BUFFER_PCT = 0.003 # stop placed this far BELOW the session low (knife buffer)
MR_NOTIONAL = 8000.0       # per-trade gross notional (sizing; risk is bracket-defined)
MR_MAX_TRADES_PER_DAY = 2  # bounded daily risk across the universe
MR_WINDOW_START_MIN = 10 * 60 + 0     # 10:00 ET — let the open settle, VWAP/RSI warm
MR_WINDOW_END_MIN = 15 * 60 + 0       # 15:00 ET — leave room to revert before EOD flat
MR_ALLOW_SHORT = False     # symmetric overbought short (backtest: long-only is the edge)

try:
    import yfinance as yf
    _YF_OK = True
except Exception:
    _YF_OK = False


def _universe() -> list[str]:
    u = list(getattr(cfg, "CORE_UNIVERSE", [])) + list(getattr(cfg, "MOMENTUM_UNIVERSE", []))
    seen, out = set(), []
    for s in u:
        if s not in seen and s not in getattr(cfg, "BLACKLIST", set()):
            seen.add(s)
            out.append(s)
    return out[:25]


def held_symbols(state: dict) -> set:
    """MR positions are managed as normal intraday brackets, NOT shielded — always empty.
    Present for a uniform book interface (matches gap_fade)."""
    return set()


def held_value(tc, state) -> float:
    return 0.0


def _in_window(now) -> bool:
    mins = now.hour * 60 + now.minute
    return MR_WINDOW_START_MIN <= mins < MR_WINDOW_END_MIN


def _indicators(idf, last):
    """VWAP extension, session low/high, RSI(14) on 5-min closes — from a 1-min OHLCV frame.
    Reuses the same math autotrade._intraday_indicators uses. None on failure."""
    out = {"vwap_ext": None, "rsi": None, "lo": None, "hi": None, "vwap": None}
    try:
        h = idf["High"].astype(float)
        l = idf["Low"].astype(float)
        c = idf["Close"].astype(float).dropna()
        v = idf["Volume"].astype(float).fillna(0)
        if len(c) < 15:
            return out
        typ = (h + l + c) / 3.0
        cumv = v.cumsum()
        vwap = (typ * v).cumsum() / cumv.replace(0, float("nan"))
        vwap_now = float(vwap.dropna().iloc[-1])
        out["vwap"] = vwap_now
        if vwap_now > 0:
            out["vwap_ext"] = round((last - vwap_now) / vwap_now * 100, 3)  # percent
        out["lo"] = float(l.min())
        out["hi"] = float(h.max())
        try:
            c5 = c.resample("5min").last().dropna()
        except Exception:
            c5 = c
        if len(c5) >= 15:
            delta = c5.diff().dropna()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, 1e-9)
            r = float((100 - 100 / (1 + rs)).iloc[-1])
            if r == r:
                out["rsi"] = round(r, 1)
    except Exception:
        pass
    return out


def _candidates(tape_bias):
    """Scan the universe for the strongest qualifying long (and short, if allowed) MR setup.
    Returns list of dicts sorted by stretch magnitude (most stretched first). [] on failure."""
    if not _YF_OK:
        return []
    syms = _universe()
    try:
        intraday = yf.download(syms, period="2d", interval="1m", progress=False,
                               group_by="ticker", threads=True)
        daily = yf.download(syms, period="5d", interval="1d", progress=False,
                            group_by="ticker", threads=True)
    except Exception:
        return []
    out = []
    for s in syms:
        try:
            idf = intraday[s] if len(syms) > 1 else intraday
            idf = idf.dropna(subset=["Close"])
            # keep only today's session (last calendar date present)
            if len(idf) == 0:
                continue
            last_day = idf.index[-1].date()
            idf = idf[idf.index.map(lambda t: t.date() == last_day)]
            if len(idf) < 15:
                continue
            last = float(idf["Close"].iloc[-1])
            ddf = daily[s] if len(syms) > 1 else daily
            dclose = ddf["Close"].dropna()
            prev_close = float(dclose.iloc[-2]) if len(dclose) >= 2 else last
            ind = _indicators(idf, last)
            ext, rsi_v = ind["vwap_ext"], ind["rsi"]
            if ext is None or rsi_v is None or ind["lo"] is None:
                continue
            # LONG setup: stretched below VWAP, oversold, tape not risk_off, not bleeding
            # below prior close (knife filter — a name UP on the day that dipped under VWAP
            # is a healthy pullback; a name gapped down and grinding is a falling knife).
            if (ext <= -MR_VWAP_EXT_PCT and rsi_v <= MR_RSI_MAX
                    and tape_bias != "risk_off" and last >= prev_close * 0.995):
                out.append({"symbol": s, "side": "long", "last": last,
                            "vwap": ind["vwap"], "lo": ind["lo"], "hi": ind["hi"],
                            "ext": ext, "rsi": rsi_v, "prev_close": prev_close})
            elif (MR_ALLOW_SHORT and ext >= MR_VWAP_EXT_PCT and rsi_v >= (100 - MR_RSI_MAX)
                    and tape_bias == "risk_off" and last <= prev_close * 1.005):
                out.append({"symbol": s, "side": "short", "last": last,
                            "vwap": ind["vwap"], "lo": ind["lo"], "hi": ind["hi"],
                            "ext": ext, "rsi": rsi_v, "prev_close": prev_close})
        except Exception:
            continue
    out.sort(key=lambda r: abs(r["ext"]), reverse=True)
    return out


def run(tc, state, dry: bool):
    """Each cycle in-window: enter the most-stretched qualifying MR setup, defined-risk."""
    if not MEANREV_ENABLED:
        return
    import autotrade as at
    try:
        now = at.et_now()
        today = now.strftime("%Y-%m-%d")
        mr = state.setdefault("mean_reversion", {})
        traded = mr.setdefault("traded", {})
        today_traded = traded.setdefault(today, [])
        if len(today_traded) >= MR_MAX_TRADES_PER_DAY:
            return
        if not _in_window(now):
            return
        # tape from the live regime if present on state, else neutral
        regime = state.get("regime") or {}
        tape_bias = regime.get("tape_bias")
        cands = [c for c in _candidates(tape_bias)
                 if c["symbol"] not in today_traded
                 and c["symbol"] not in getattr(cfg, "BLACKLIST", set())]
        if not cands:
            return
        c = cands[0]
        sym, last, side = c["symbol"], c["last"], c["side"]
        # bracket: target = VWAP reversion; stop beyond session extreme (knife buffer).
        if side == "long":
            sl = round(c["lo"] * (1 - MR_STOP_BUFFER_PCT), 2)
            risk = last - sl
            if risk <= 0:
                return
            tgt = round(c["vwap"], 2) if MR_TARGET_VWAP else round(last + MR_R_MULT_TARGET * risk, 2)
            order_side = "buy"
        else:
            sl = round(c["hi"] * (1 + MR_STOP_BUFFER_PCT), 2)
            risk = sl - last
            if risk <= 0:
                return
            tgt = round(c["vwap"], 2) if MR_TARGET_VWAP else round(last - MR_R_MULT_TARGET * risk, 2)
            order_side = "sell"
            try:
                if not bool(getattr(tc.get_asset(sym), "shortable", False)):
                    at.log(f"mean_reversion: {sym} not shortable — skip")
                    return
            except Exception:
                return
        qty = int(MR_NOTIONAL // last)
        if qty < 1:
            return
        # bracket requires a flat name (same rule that bit gap_fade): no position/working order.
        try:
            held = {p.symbol for p in tc.get_all_positions()}
            working = {o.symbol for o in tc.get_orders(
                filter=at.GetOrdersRequest(status=at.QueryOrderStatus.OPEN, limit=200))}
        except Exception as e:
            at.log(f"mean_reversion: could not verify flat for {sym} ({e}) — skip")
            return
        if sym in held or sym in working:
            at.log(f"mean_reversion: {sym} already has a position/order — skip")
            today_traded.append(sym)
            at.save_state(state)
            return
        today_traded.append(sym)                       # latch BEFORE submit (at-most-once)
        if dry:
            at.log(f"[DRY] mean_reversion would {order_side} {qty} {sym} "
                   f"(ext {c['ext']:+.2f}% rsi {c['rsi']}) target {tgt} stop {sl}")
            at.save_state(state)
            return
        try:
            o = at.place_stock_bracket(tc, sym, qty, order_side, sl, tgt, dry)
            state.setdefault("last_entry_time", {})[sym] = now.isoformat()
            mr["last"] = {"symbol": sym, "qty": qty, "side": side, "entry_ref": last,
                          "target": tgt, "stop": sl, "ext": c["ext"], "rsi": c["rsi"],
                          "opened": now.isoformat()}
            at.log(f"MEAN REVERSION {order_side} {qty} {sym} ext={c['ext']:+.2f}% "
                   f"rsi={c['rsi']} target={tgt} stop={sl} id={getattr(o,'id',None)}")
            at.tg_send(f"🔁 Mean-reversion: {order_side} {qty} {sym} "
                       f"(stretched {c['ext']:+.1f}% off VWAP, RSI {c['rsi']}), target {tgt}.")
            at.save_state(state)
        except Exception as e:
            at.log(f"mean_reversion: order {sym} failed: {e}")
    except Exception as e:
        at.log(f"mean_reversion: run failed: {e}")


def summary(state: dict) -> dict:
    mr = state.get("mean_reversion", {}) or {}
    traded = mr.get("traded", {}) or {}
    last_day = max(traded) if traded else None
    return {"enabled": MEANREV_ENABLED,
            "today_count": len(traded.get(last_day, [])) if last_day else 0,
            "last": mr.get("last")}
