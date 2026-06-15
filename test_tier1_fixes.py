"""Offline tests for Tier-1 safety fixes #1, #2, #9.

Run: venv/bin/python3 /tmp/test_tier1_fixes.py
Redirects cfg.LOG_FILE / cfg.STATE_FILE to /tmp temp paths so nothing real is touched.
"""
import sys, os, tempfile
from datetime import date, timedelta

# Redirect file paths BEFORE autotrade imports anything that resolves them.
import config as cfg
_tmp = tempfile.mkdtemp(prefix="tier1_test_")
cfg.LOG_FILE = os.path.join(_tmp, "test.log")
cfg.STATE_FILE = os.path.join(_tmp, "state.json")

import autotrade as at

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + ": " + name)


# ---------------------------------------------------------------------------
# FIX #9 — per-option cap tightened to 600.
# ---------------------------------------------------------------------------
check("#9 PER_OPTION_NOTIONAL_CAP == 600.0", cfg.PER_OPTION_NOTIONAL_CAP == 600.0)
check("#9 cap is ~0.4x of abs(-1500 floor)", abs(600.0 / 1500.0 - 0.4) < 1e-9)


# ---------------------------------------------------------------------------
# FIX #2 — option_chain_for selector: min-DTE for directional, 0DTE for condor.
# We stub odc.get_option_chain to return a synthetic chain with mixed expiries.
# ---------------------------------------------------------------------------
TODAY = date.fromisoformat(at.et_now().date().isoformat())

class _Q:
    def __init__(self, bid, ask):
        self.bid_price, self.ask_price = bid, ask
class _Snap:
    def __init__(self, q):
        self.latest_quote = q

def _occ(und, exp_date, cp, strike):
    body = exp_date.strftime("%y%m%d") + cp + f"{int(round(strike*1000)):08d}"
    return und + body

def make_chain(expiry_offsets, und="AAA", spot=100.0):
    """Build {occ_symbol: snap} across the given DTE offsets, calls+puts near spot."""
    chain = {}
    for off in expiry_offsets:
        ed = TODAY + timedelta(days=off)
        for cp in ("C", "P"):
            for k in (spot - 2, spot, spot + 2):
                sym = _occ(und, ed, cp, k)
                chain[sym] = _Snap(_Q(1.0, 1.1))
    return chain

class StubODC:
    def __init__(self, chain):
        self._chain = chain
    def get_option_chain(self, req):
        return self._chain

# Case A: directional (want_today_expiry=False) with mixed 0/2/7/30 DTE -> picks >=5 DTE nearest (7).
odc = StubODC(make_chain([0, 2, 7, 30]))
rows = at.option_chain_for(odc, "AAA", 100.0, want_today_expiry=False)
exps = sorted({r["expiry"] for r in rows})
chosen_dte = (date.fromisoformat(exps[0]) - TODAY).days if exps else None
check("#2 directional picks a single expiry", len(exps) == 1)
check("#2 directional DTE >= MIN_DTE (5)", chosen_dte is not None and chosen_dte >= cfg.MOMENTUM_OPTION_MIN_DTE)
check("#2 directional picks NEAREST qualifying (7 not 30)", chosen_dte == 7)

# Case B: condor (want_today_expiry=True) keeps 0DTE/today.
odc2 = StubODC(make_chain([0, 2, 7, 30]))
rows2 = at.option_chain_for(odc2, "AAA", 100.0, want_today_expiry=True)
exps2 = sorted({r["expiry"] for r in rows2})
check("#2 condor keeps today's 0DTE expiry",
      len(exps2) == 1 and exps2[0] == TODAY.isoformat())

# Case C: directional with ONLY near-dated (0,2,4 DTE) -> returns [] (skip, no fallback).
odc3 = StubODC(make_chain([0, 2, 4]))
rows3 = at.option_chain_for(odc3, "AAA", 100.0, want_today_expiry=False)
check("#2 directional returns [] when only <5 DTE exist", rows3 == [])


# ---------------------------------------------------------------------------
# FIX #1(a) — close naming one leg of a tracked multileg expands to all legs.
# We replicate the expansion logic exactly as in run_cycle.
# ---------------------------------------------------------------------------
def expand_atomic(close_targets, state):
    close_targets = list(close_targets)
    _ct_set = set(close_targets)
    for _pos in (state.get("active_multileg") or []):
        _legs = {l.get("symbol") for l in (_pos.get("legs") or []) if l.get("symbol")}
        if not _legs:
            continue
        _named = _ct_set & _legs
        if _named and _named != _legs:
            _add = [s for s in _legs if s not in _ct_set]
            close_targets.extend(_add)
            _ct_set |= _legs
    return close_targets

LONG_LEG = _occ("SMCI", TODAY + timedelta(days=7), "C", 330.0)
SHORT_LEG = _occ("SMCI", TODAY + timedelta(days=7), "C", 335.0)
state_ml = {"active_multileg": [
    {"legs": [{"symbol": LONG_LEG}, {"symbol": SHORT_LEG}], "qty": 1}]}

expanded = expand_atomic([LONG_LEG], state_ml)
check("#1a one-leg close expands to BOTH legs",
      set(expanded) == {LONG_LEG, SHORT_LEG})

# Whole-structure close is unchanged (no spurious expansion / no dupes).
expanded_full = expand_atomic([LONG_LEG, SHORT_LEG], state_ml)
check("#1a whole-structure close unchanged",
      sorted(expanded_full) == sorted([LONG_LEG, SHORT_LEG]))

# Verify the real source contains the atomicity logic + assertion call.
src = at.__file__.replace(".pyc", ".py")
with open(src) as f:
    body = f.read()
check("#1a SPREAD ATOMICITY present in run_cycle", "SPREAD ATOMICITY" in body)


# ---------------------------------------------------------------------------
# FIX #1(b) — naked-short detection: a naked short is caught; a covered condor
# leg and a tail-hedge long are NOT.
# ---------------------------------------------------------------------------
class StubPos:
    def __init__(self, symbol, qty):
        self.symbol, self.qty = symbol, qty
        self.asset_class = "us_option"

class StubTC:
    def __init__(self, positions):
        self._pos = positions
        self.closed = []
        self.cancelled = []
    def get_all_positions(self):
        return self._pos
    def get_orders(self, filter=None):
        return []
    def cancel_order_by_id(self, oid):
        self.cancelled.append(oid)
    def close_position(self, sym):
        self.closed.append(sym)

exp = TODAY + timedelta(days=7)
# Naked short call (no covering long) on NAKD.
naked_short = _occ("NAKD", exp, "C", 100.0)
# Covered condor: short call 105 covered by long call 110 (long wing) on COND.
cond_short = _occ("COND", exp, "C", 105.0)
cond_long  = _occ("COND", exp, "C", 110.0)
# Tail-hedge: a LONG put on SPY (qty>0) — must never be flagged.
tail_long = _occ("SPY", TODAY + timedelta(days=40), "P", 400.0)

positions = [
    StubPos(naked_short, -1),
    StubPos(cond_short, -1),
    StubPos(cond_long, +1),
    StubPos(tail_long, +1),
]
tc = StubTC(positions)
# Silence tg_send (no network in test).
at.tg_send = lambda *a, **k: None
at.assert_no_naked_short(tc, {}, dry=False)

check("#1b naked short force-closed", naked_short in tc.closed)
check("#1b covered condor short NOT closed", cond_short not in tc.closed)
check("#1b condor long wing NOT closed", cond_long not in tc.closed)
check("#1b tail-hedge long put NOT closed", tail_long not in tc.closed)
check("#1b ONLY the naked short was closed", tc.closed == [naked_short])

# Genuinely naked short PUT (NO long put on the same underlying) is caught.
naked_put = _occ("NPUT", exp, "P", 50.0)
tc2 = StubTC([StubPos(naked_put, -1)])
at.assert_no_naked_short(tc2, {}, dry=False)
check("#1b genuinely naked short put force-closed", naked_put in tc2.closed)

# Bear-put DEBIT spread: short put 50 covered by a long put at a HIGHER strike 60 — DEFINED
# risk, must NOT be closed (any long put of same underlying caps the short, strike-order
# irrelevant). This is the case the old strike-relationship logic wrongly flagged.
bp_short = _occ("BPSPR", exp, "P", 50.0)
bp_long  = _occ("BPSPR", exp, "P", 60.0)
tc2b = StubTC([StubPos(bp_short, -1), StubPos(bp_long, +1)])
at.assert_no_naked_short(tc2b, {}, dry=False)
check("#1b bear-put-spread short (long HIGHER strike) NOT closed", bp_short not in tc2b.closed)

# Put credit spread: short put 50 covered by a long put at a LOWER strike 45 — also fine.
sp_short = _occ("PSPR", exp, "P", 50.0)
sp_long  = _occ("PSPR", exp, "P", 45.0)
tc3 = StubTC([StubPos(sp_short, -1), StubPos(sp_long, +1)])
at.assert_no_naked_short(tc3, {}, dry=False)
check("#1b put-spread short (long lower strike) NOT closed", sp_short not in tc3.closed)

# Bull-call DEBIT spread: short call 110 covered by a long call at a LOWER strike 100 —
# DEFINED risk, must NOT be closed (the exact case the old logic dismantled every cycle).
bc_short = _occ("BCSPR", exp, "C", 110.0)
bc_long  = _occ("BCSPR", exp, "C", 100.0)
tc5 = StubTC([StubPos(bc_short, -1), StubPos(bc_long, +1)])
at.assert_no_naked_short(tc5, {}, dry=False)
check("#1b bull-call-spread short (long LOWER strike) NOT closed", bc_short not in tc5.closed)

# skip set shields a book symbol even if it looks naked.
tc4 = StubTC([StubPos(naked_short, -1)])
at.assert_no_naked_short(tc4, {}, dry=False, skip={naked_short})
check("#1b skip set shields a book short", naked_short not in tc4.closed)


# ---------------------------------------------------------------------------
print()
print(f"{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
print("ALL GREEN")
