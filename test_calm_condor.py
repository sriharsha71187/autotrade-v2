#!/usr/bin/env python3
"""Unit tests for calm_condor.py — entry gates, strike picker, exit logic.
Pure functions only (no network/broker). House PASS/FAIL style."""
import sys

import config as cfg
import calm_condor as cc

passed = 0; failed = 0
def chk(name, cond):
    global passed, failed
    cond = bool(cond)
    print(("PASS " if cond else "FAIL ") + name); passed += cond; failed += (not cond)


# ---- day_is_calm ------------------------------------------------------------
# baseline calm day: open 600, tight range, flat now, no gap, VIX 13
ok, why = cc.day_is_calm(600.0, 601.0, 599.5, 600.5, 600.5, 13.0)
chk("calm day passes", ok)
ok, why = cc.day_is_calm(600.0, 601.0, 599.5, 600.5, 600.5, 17.0)
chk("VIX >= 16 blocks", not ok and "VIX" in why)
ok, why = cc.day_is_calm(596.0, 597.0, 595.5, 596.2, 600.0, 13.0)   # -0.67% gap-down
chk("big gap-down blocks", not ok and "gap-down" in why)
ok, why = cc.day_is_calm(604.5, 605.0, 604.0, 604.6, 600.0, 13.0)   # +0.75% gap-up
chk("big gap-up blocks (symmetric)", not ok and "gap-up" in why)
ok, why = cc.day_is_calm(600.0, 602.5, 599.8, 602.2, 600.2, 13.0)   # +0.37% move now
chk("trending day blocks (|move|)", not ok and "flat" in why)
ok, why = cc.day_is_calm(600.0, 602.8, 598.9, 600.2, 600.2, 13.0)   # 0.65% range
chk("wide session range blocks", not ok and "range" in why)
ok, why = cc.day_is_calm(None, 601.0, 599.5, 600.5, 600.5, 13.0)
chk("missing data blocks", not ok)

# ---- pick_condor ------------------------------------------------------------
def row(typ, strike, bid, ask):
    return {"symbol": f"SPY260813{'C' if typ=='call' else 'P'}{int(strike*1000):08d}",
            "type": typ, "strike": strike, "expiry": "2026-08-13",
            "bid": bid, "ask": ask, "mid": round((bid + ask) / 2, 3)}

SPOT = 600.0
# ATM straddle mid = 1.50 + 1.50 = 3.0 -> shorts ~603/597, wings ~607.5/592.5
ROWS = ([row("call", k, 1.50 - (k - 600) * 0.28, 1.52 - (k - 600) * 0.28)
         for k in (600, 601, 602, 603, 604)] +
        [row("call", k, 0.14, 0.16) for k in (605, 606, 607, 608)] +
        [row("put", k, 1.50 - (600 - k) * 0.28, 1.52 - (600 - k) * 0.28)
         for k in (600, 599, 598, 597, 596)] +
        [row("put", k, 0.14, 0.16) for k in (595, 594, 593, 592)])
pick = cc.pick_condor(ROWS, SPOT)
chk("condor built on calm chain", pick is not None)
chk("short call ~1xEM above spot", pick and abs(pick["short_call"] - 603) <= 1)
chk("short put ~1xEM below spot", pick and abs(pick["short_put"] - 597) <= 1)
chk("net credit positive", pick and pick["net_credit"] > 0)
chk("4 legs, sell shorts buy wings", pick and len(pick["legs"]) == 4
    and [l["side"] for l in pick["legs"]] == ["sell", "buy", "sell", "buy"])

# wide-quoted chain -> stand down
WIDE = [dict(r, bid=round(r["mid"] * 0.80, 2), ask=round(r["mid"] * 1.30, 2)) for r in ROWS]
chk("wide legs -> no condor", cc.pick_condor(WIDE, SPOT) is None)
chk("empty chain -> None", cc.pick_condor([], SPOT) is None)

# ---- exit_reason ------------------------------------------------------------
chk("inside shorts holds", cc.exit_reason(600.0, 603.0, 597.0, 14 * 60) is None)
chk("touch call stops", cc.exit_reason(603.1, 603.0, 597.0, 14 * 60) == "touch-call")
chk("touch put stops", cc.exit_reason(596.9, 603.0, 597.0, 14 * 60) == "touch-put")
chk("15:40 hard flatten", cc.exit_reason(600.0, 603.0, 597.0, 15 * 60 + 40) == "eod")
chk("no quote still flattens at 15:40", cc.exit_reason(None, 603.0, 597.0, 15 * 60 + 41) == "eod")
chk("no quote holds otherwise", cc.exit_reason(None, 603.0, 597.0, 14 * 60) is None)

# ---- book interface ---------------------------------------------------------
ST = {"calm_condor": {"holding": {"legs": [{"symbol": "A", "side": "sell"},
                                           {"symbol": "B", "side": "buy"}],
                                  "qty": 1, "short_call": 603.0, "short_put": 597.0,
                                  "entry_date": "2026-08-13"}}}
chk("held_symbols shields legs", cc.held_symbols(ST) == {"A", "B"})
chk("held_symbols empty when flat", cc.held_symbols({}) == set())
s = cc.summary(ST)
chk("summary exposes shorts", s["holding"] and s["holding"]["shorts"] == [597.0, 603.0])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
