#!/usr/bin/env python3
"""Unit tests for option_scalp.py — pure signal/contract/exit logic only (no
network, no broker, no pandas). Mirrors the house PASS/FAIL script style."""
import sys
from datetime import date

import option_scalp as osc

passed = 0; failed = 0
def chk(name, cond):
    global passed, failed
    print(("PASS " if cond else "FAIL ") + name); passed += cond; failed += (not cond)


def bars(n, c=100.0, h=100.5, l=99.5, v=1000.0):
    return [{"t_min": 600 + 5*i, "o": c, "h": h, "l": l, "c": c, "v": v}
            for i in range(n)]


# ---- _signal ----------------------------------------------------------------
LB = osc.SCALP_LOOKBACK

# 1) upside burst: last close above prior-N high, volume-confirmed, tape aligned
b = bars(LB) + [{"t_min": 660, "o": 100, "h": 101.2, "l": 100, "c": 101.0, "v": 2000}]
sig = osc._signal(b, tape_pct=0.005)
chk("upside burst -> long", sig is not None and sig["direction"] == "long")

# 2) same burst WITHOUT volume confirmation -> None
b2 = bars(LB) + [{"t_min": 660, "o": 100, "h": 101.2, "l": 100, "c": 101.0, "v": 1000}]
chk("no volume confirmation blocks", osc._signal(b2, 0.005) is None)

# 3) same burst against the tape -> None (long needs tape >= +TAPE_MIN)
chk("counter-tape long blocked", osc._signal(b, tape_pct=-0.005) is None)

# 4) downside burst with risk-off tape -> short (put)
b3 = bars(LB) + [{"t_min": 660, "o": 100, "h": 100, "l": 98.8, "c": 99.0, "v": 2000}]
sig3 = osc._signal(b3, tape_pct=-0.005)
chk("downside burst -> short", sig3 is not None and sig3["direction"] == "short")

# 5) inside-range close (no break) -> None
b4 = bars(LB) + [{"t_min": 660, "o": 100, "h": 100.4, "l": 99.9, "c": 100.2, "v": 3000}]
chk("no range break -> no signal", osc._signal(b4, 0.005) is None)

# 6) unknown tape or short history -> None
chk("tape None blocked", osc._signal(b, None) is None)
chk("short history blocked", osc._signal(bars(LB), 0.005) is None)

# ---- _pick_contract ---------------------------------------------------------
TODAY = date(2026, 8, 3)   # Monday
def row(typ, strike, expiry, bid, ask):
    mid = (bid + ask) / 2
    return {"symbol": f"SPY{expiry.replace('-','')[2:]}{'C' if typ=='call' else 'P'}{int(strike*1000):08d}",
            "type": typ, "strike": strike, "expiry": expiry, "bid": bid, "ask": ask, "mid": mid}

ROWS = [
    row("call", 630.0, "2026-08-03", 2.00, 2.02),   # 0DTE — must NEVER be picked
    row("call", 630.0, "2026-08-04", 2.50, 2.52),   # 1DTE ITM (spot 636 -> ~1% ITM)
    row("call", 636.0, "2026-08-04", 1.20, 1.22),   # 1DTE ATM (not ITM side)
    row("call", 640.0, "2026-08-04", 0.60, 0.62),   # OTM — wrong side
    row("put",  642.0, "2026-08-04", 2.40, 2.42),   # 1DTE ITM put
    row("put",  630.0, "2026-08-04", 0.55, 0.57),   # OTM put — wrong side
]
spot = 636.0

# 7) long burst -> ITM call at >=1 DTE, never today's expiry
pick = osc._pick_contract(ROWS, spot, "long", TODAY)
chk("long picks ITM call", pick is not None and pick["type"] == "call" and pick["strike"] < spot)
chk("never 0DTE", pick is not None and pick["expiry"] == "2026-08-04")

# 8) short burst -> ITM put (strike above spot)
pickp = osc._pick_contract(ROWS, spot, "short", TODAY)
chk("short picks ITM put", pickp is not None and pickp["type"] == "put" and pickp["strike"] > spot)

# 9) wide-spread contract rejected (only wide ITM call available -> None)
WIDE = [row("call", 630.0, "2026-08-04", 2.00, 2.40)]   # ~18% of mid
chk("wide spread rejected", osc._pick_contract(WIDE, spot, "long", TODAY) is None)

# 10) chain with only 0DTE -> None (no silent fallback)
ZERO = [row("call", 630.0, "2026-08-03", 2.00, 2.02)]
chk("0DTE-only chain -> no trade", osc._pick_contract(ZERO, spot, "long", TODAY) is None)

# ---- _exit_reason -----------------------------------------------------------
# 11) TP / SL / time / EOD / hold
chk("TP fires", osc._exit_reason(0.30, 10, 12*60) == "tp")
chk("SL fires", osc._exit_reason(-0.25, 10, 12*60) == "sl")
chk("time-stop fires", osc._exit_reason(0.05, osc.SCALP_MAX_HOLD_MIN, 12*60) == "time")
chk("15:30 hard flat", osc._exit_reason(0.05, 5, osc.SCALP_FLAT_MIN) == "eod")
chk("winner inside window holds", osc._exit_reason(0.10, 5, 12*60) is None)
# 12) unquotable (ret None) still respects time/EOD but not TP/SL
chk("no quote: time-stop still fires", osc._exit_reason(None, 60, 12*60) == "time")
chk("no quote: holds inside window", osc._exit_reason(None, 5, 12*60) is None)

# ---- book interface ---------------------------------------------------------
STATE = {"option_scalp": {"holdings": [
    {"symbol": "SPY260804C00630000", "underlying": "SPY", "qty": 1, "entry": 2.52,
     "direction": "long", "entry_date": "2026-08-03", "opened": "2026-08-03T10:05:00"}],
    "count": 1, "count_date": "2026-08-03"}}
# 13) open scalp symbols are shielded
chk("held_symbols shields open scalp", osc.held_symbols(STATE) == {"SPY260804C00630000"})
chk("held_symbols empty when flat", osc.held_symbols({}) == set())
# 14) summary shape
s = osc.summary(STATE)
chk("summary lists holding", len(s["holdings"]) == 1 and s["trades_today"] == 1)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
