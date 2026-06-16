"""Offline tests for the conviction-ITM book (CONVICTION_ITM_SPEC §9 items 1-5).
Run: /Users/nirvaan/autotrade/venv/bin/python3 /tmp/test_conviction_itm.py
Redirects cfg.LOG_FILE / cfg.STATE_FILE to /tmp BEFORE importing engine code so the
real log/state are never touched (project convention)."""
import sys, os, tempfile
from pathlib import Path
from datetime import date, datetime, timedelta

sys.path.insert(0, "/Users/nirvaan/autotrade")

import config as cfg
_tmp = Path(tempfile.mkdtemp(prefix="conviction_test_"))
cfg.LOG_FILE = _tmp / "test.log"
cfg.STATE_FILE = _tmp / "test_state.json"

import autotrade as at

# Turn the book ON for the test process only (in-memory; no override file written).
cfg.CONVICTION_ITM_ENABLED = True

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (("  -- " + extra) if extra else ""))


# ---- synthetic option-chain plumbing ---------------------------------------
class FakeQuote:
    def __init__(self, bid, ask):
        self.bid_price, self.ask_price = bid, ask

class FakeSnap:
    def __init__(self, bid, ask):
        self.latest_quote = FakeQuote(bid, ask)

def occ(und, expiry: date, cp: str, strike: float) -> str:
    return f"{und}{expiry.strftime('%y%m%d')}{cp}{int(round(strike*1000)):08d}"

class FakeODC:
    """Returns a dict {occ_symbol: FakeSnap} for any get_option_chain call, filtered to
    the requested strike window so the selector's scan window matters."""
    def __init__(self, chain):
        self.chain = chain
    def get_option_chain(self, req):
        lo = getattr(req, "strike_price_gte", 0) or 0
        hi = getattr(req, "strike_price_lte", 1e9) or 1e9
        return {s: snap for s, snap in self.chain.items()
                if lo <= at.parse_occ(s)["strike"] <= hi}

def build_chain(und, spot, today, *, tight=True):
    """Calls+puts across a strike grid at three expiries (5, 24, 45 DTE)."""
    chain = {}
    for dte in (5, 24, 45):
        exp = today + timedelta(days=dte)
        for k in range(int(spot*0.85), int(spot*1.15)+1, 5):
            for cp in ("C", "P"):
                # deep-ITM => higher premium; price intrinsic + a little time value.
                intrinsic = max(0.0, (spot-k) if cp == "C" else (k-spot))
                mid = intrinsic + 1.5
                half = mid * (0.03 if tight else 0.30)   # tight or WIDE spread
                chain[occ(und, exp, cp, k)] = FakeSnap(round(mid-half, 2), round(mid+half, 2))
    return chain


# ---- §9.1 selector returns ITM strike at the target DTE --------------------
def test_selector():
    spot, und = 230.0, "COIN"
    today = date(2026, 6, 15)
    odc = FakeODC(build_chain(und, spot, today))
    # monkeypatch et_now so the selector's "today" matches our synthetic chain
    orig = at.et_now
    at.et_now = lambda: datetime(2026, 6, 15, 11, 0)
    try:
        rows = at.conviction_chain_for(odc, und, spot, bullish=True)
    finally:
        at.et_now = orig
    check("§1 selector returns exactly one contract", len(rows) == 1, str(rows))
    if not rows:
        return
    r = rows[0]
    check("§1 selector returns a CALL for bullish", r["type"] == "call")
    # ~7% ITM => strike below spot, near spot*0.93 = 213.9
    target = spot * (1 - cfg.CONVICTION_ITM_DEPTH)
    check("§1 strike is ITM (below spot) near target",
          r["strike"] < spot and abs(r["strike"] - target) <= 6, f"strike={r['strike']} target~{target:.0f}")
    mid_dte = (cfg.CONVICTION_ITM_DTE_MIN + cfg.CONVICTION_ITM_DTE_MAX) / 2  # 24.5
    check("§1 expiry DTE is closest to band middle (~24)", abs(r["dte"] - mid_dte) <= 2, f"dte={r['dte']}")
    # bearish -> a PUT above spot
    at.et_now = lambda: datetime(2026, 6, 15, 11, 0)
    try:
        prows = at.conviction_chain_for(odc, und, spot, bullish=False)
    finally:
        at.et_now = orig
    check("§1 bearish returns an ITM PUT above spot",
          len(prows) == 1 and prows[0]["type"] == "put" and prows[0]["strike"] > spot,
          str(prows))

def test_selector_illiquid_reject():
    spot, und = 230.0, "COIN"
    today = date(2026, 6, 15)
    odc = FakeODC(build_chain(und, spot, today, tight=False))  # 30% half-spread => wide
    orig = at.et_now
    at.et_now = lambda: datetime(2026, 6, 15, 11, 0)
    try:
        rows = at.conviction_chain_for(odc, und, spot, bullish=True)
    finally:
        at.et_now = orig
    check("§1 wide-spread contract is rejected (illiquid)", rows == [], str(rows))


# ---- §9.2 risk-based sizing (~$900 risk, not blocked by old $1,200 cap) -----
def _acct():
    return {"equity": 100000.0, "cash": 100000.0}

def _guard_option(osym, ref_price, spread, conviction, scan_row=None):
    decision = {"action": "buy_option", "option_symbol": osym, "qty": 1,
                "symbol": at.parse_occ(osym)["underlying"]}
    state = {"active_multileg": [], "active_options": []}
    # Default scan_row: a name AT its highs (off_hod tiny) — the conviction book's target,
    # which the generic anti-chase would block (so this also proves conviction bypasses it).
    sr = scan_row or {"symbol": decision["symbol"], "day_pct": 5.0,
                      "vwap_ext": 0.01, "rsi": 60, "off_hod": 0.001,
                      "signal": "STRONG_BULL", "last": 230.0}
    return decision, at.passes_guardrails(
        decision, state, _acct(), datetime(2026, 6, 15, 11, 0),
        ref_price=ref_price, offered_options={osym},
        option_spread_pct=spread, scan_row=sr,
        dir_counts={"bull": 0, "bear": 0},
        conviction_symbols={osym})

def test_sizing_ok():
    # A $25 deep-ITM call: risk/contract = 25*100*0.30 = $750 => floor(900/750)=1 contract.
    osym = occ("COIN", date(2026, 7, 9), "C", 213.0)
    decision, (ok, reason) = _guard_option(osym, 25.0, 0.02, conviction=True)
    check("§2 $25 deep-ITM call is NOT rejected by the old $1,200 cap", ok, reason)
    check("§2 sized to >=1 contract", decision.get("qty", 0) >= 1, f"qty={decision.get('qty')}")
    check("§2 decision tagged book=conviction_itm", decision.get("book") == "conviction_itm")
    # risk = premium*100*qty*|stop|; ask_est = 25*(1+0.01)=25.25 -> risk/contract ~757.5
    ask_est = 25.0 * (1 + 0.02/2)
    rpc = ask_est * 100 * abs(cfg.CONVICTION_ITM_STOP_PCT)
    check("§2 risk per contract ~ $757 (≈$900 target band)", 700 <= rpc <= 900, f"rpc={rpc:.0f}")

def test_sizing_too_expensive():
    # A $120 premium: risk/contract = 120*100*0.30 = $3600 > $900 target => <1 contract => skip.
    osym = occ("COIN", date(2026, 7, 9), "C", 110.0)
    decision, (ok, reason) = _guard_option(osym, 120.0, 0.02, conviction=True)
    check("§2 too-expensive (<1 contract) is rejected", not ok, reason)
    check("§2 rejection reason mentions too expensive", "expensive" in (reason or "").lower(), reason)

def test_flag_off_uses_old_cap():
    # With the flag OFF the same routed symbol falls through to the old $1,200 cap path.
    cfg.CONVICTION_ITM_ENABLED = False
    try:
        osym = occ("COIN", date(2026, 7, 9), "C", 213.0)
        # Use a non-chasing scan_row so the request reaches the cap check (not blocked
        # by anti-chase first) — proving the OLD $1,200 cap path is what governs.
        sr = {"symbol": "COIN", "day_pct": 5.0, "vwap_ext": 0.01, "rsi": 60,
              "off_hod": 0.03, "signal": "STRONG_BULL", "last": 230.0}
        decision, (ok, reason) = _guard_option(osym, 25.0, 0.02, conviction=True, scan_row=sr)
        # $25*1.01*100 = $2525 notional > $1200 cap => rejected by the OLD cap.
        check("flag-OFF: routed symbol still hits old $1,200 cap (unchanged behavior)",
              (not ok) and "cap" in (reason or "").lower(), reason)
        check("flag-OFF: decision NOT tagged conviction_itm", decision.get("book") is None)
    finally:
        cfg.CONVICTION_ITM_ENABLED = True


# ---- §9.3 EOD-flatten exemption --------------------------------------------
class FakePos:
    def __init__(self, symbol):
        self.symbol = symbol
        self.asset_class = "us_option"

class FakeTC:
    def __init__(self, held):
        self._held = held
        self.closed = []
    def get_all_positions(self):
        return [FakePos(s) for s in self._held]
    def close_position(self, s):
        self.closed.append(s)
    def get_order_by_id(self, oid):
        class O: status = "filled"
        return O()

def _mid_patch(value):
    # patch the quote-mid path so manage_options sees a price (no broker).
    at._orig_olq = getattr(at, "_orig_olq", at.option_latest_quote)
    at.option_latest_quote = lambda odc, sym: ("Q", value)  # sentinel
    at._orig_qmid = getattr(at, "_orig_qmid", at._quote_mid)
    at._quote_mid = lambda q, allow_one_sided=False: value if q else None

def test_eod_exemption():
    osym = occ("COIN", date(2026, 7, 9), "C", 213.0)
    state = {"active_options": [
        {"symbol": osym, "qty": 1, "entry": 25.0, "opened": "2026-06-15T11:00:00",
         "opened_day": "2026-06-15", "max_hold_days": 5, "book": "conviction_itm"}]}
    tc = FakeTC([osym])
    closed = []
    at_close_orig = at.close_symbols
    at.close_symbols = lambda tc, syms, dry: (closed.extend(syms) or set(syms))
    orig_now = at.et_now
    at.et_now = lambda: datetime(2026, 6, 15, 15, 50)   # AFTER 15:45 EOD window
    _mid_patch(26.0)   # small profit, no stop/trail trigger
    try:
        at.manage_options(tc, odc=None, state=state, dry=True)
    finally:
        at.close_symbols = at_close_orig
        at.et_now = orig_now
        at.option_latest_quote = at._orig_olq
        at._quote_mid = at._orig_qmid
    still = [o["symbol"] for o in state["active_options"]]
    check("§3 conviction-ITM NOT force-closed at 15:45 (still tracked)",
          osym in still and osym not in closed, f"closed={closed} still={still}")

def test_eod_closes_normal_option():
    osym = occ("AAPL", date(2026, 6, 15), "C", 200.0)   # 0DTE, NOT conviction
    state = {"active_options": [
        {"symbol": osym, "qty": 1, "entry": 1.0, "opened": "2026-06-15T11:00:00"}]}
    tc = FakeTC([osym])
    closed = []
    at_close_orig = at.close_symbols
    at.close_symbols = lambda tc, syms, dry: (closed.extend(syms) or set(syms))
    orig_now = at.et_now
    at.et_now = lambda: datetime(2026, 6, 15, 15, 50)
    _mid_patch(1.0)
    try:
        at.manage_options(tc, odc=None, state=state, dry=True)
    finally:
        at.close_symbols = at_close_orig
        at.et_now = orig_now
        at.option_latest_quote = at._orig_olq
        at._quote_mid = at._orig_qmid
    check("§3 a normal (non-conviction) option IS EOD-closed at 15:45", osym in closed, str(closed))


# ---- §9.4 max-hold + DTE-exit + trailing stop ------------------------------
def _run_manage(state_opt, now_dt, mid):
    osym = state_opt["symbol"]
    state = {"active_options": [dict(state_opt)]}
    tc = FakeTC([osym])
    closed = []
    at_close_orig = at.close_symbols
    at_closem_orig = at.close_symbols_marketable
    at.close_symbols = lambda tc, syms, dry: (closed.extend(syms) or set(syms))
    # #15: conviction discretionary exits (max-hold/DTE/trail/stop) now route through the
    # marketable-limit close; capture it too so the exit-trigger assertions still hold.
    at.close_symbols_marketable = lambda tc, odc, syms, dry: (closed.extend(syms) or set(syms))
    orig_now = at.et_now
    at.et_now = lambda: now_dt
    _mid_patch(mid)
    try:
        at.manage_options(tc, odc=None, state=state, dry=True)
    finally:
        at.close_symbols = at_close_orig
        at.close_symbols_marketable = at_closem_orig
        at.et_now = orig_now
        at.option_latest_quote = at._orig_olq
        at._quote_mid = at._orig_qmid
    return closed, state["active_options"]

def test_max_hold():
    osym = occ("COIN", date(2026, 7, 9), "C", 213.0)   # ~24 DTE on 6/15 -> still far
    base = {"symbol": osym, "qty": 1, "entry": 25.0, "opened": "2026-06-15T11:00:00",
            "opened_day": "2026-06-15", "max_hold_days": 5, "book": "conviction_itm"}
    # 6 calendar days later, mid hour (not EOD), healthy price -> max-hold should fire.
    closed, _ = _run_manage(base, datetime(2026, 6, 21, 11, 0), 27.0)
    check("§4 max-hold exit fires after max_hold_days", osym in closed, str(closed))

def test_dte_exit():
    osym = occ("COIN", date(2026, 6, 18), "C", 213.0)   # only 3 DTE on 6/15 (< MIN_DTE_EXIT=5)
    base = {"symbol": osym, "qty": 1, "entry": 25.0, "opened": "2026-06-15T11:00:00",
            "opened_day": "2026-06-15", "max_hold_days": 5, "book": "conviction_itm"}
    closed, _ = _run_manage(base, datetime(2026, 6, 15, 11, 0), 27.0)
    check("§4 DTE-exit fires when DTE < CONVICTION_ITM_MIN_DTE_EXIT", osym in closed, str(closed))

def test_trailing_stop():
    osym = occ("COIN", date(2026, 7, 9), "C", 213.0)
    base = {"symbol": osym, "qty": 1, "entry": 25.0, "opened": "2026-06-15T11:00:00",
            "opened_day": "2026-06-15", "max_hold_days": 5, "book": "conviction_itm",
            "hw_pl": 0.50}   # peaked +50% already
    # now mid 30 -> pl = +20%; trail level = 0.50*(1-0.25)=0.375 -> 0.20 < 0.375 -> EXIT
    closed, _ = _run_manage(base, datetime(2026, 6, 16, 11, 0), 30.0)
    check("§4 trailing-stop fires (gave back below the peak)", osym in closed, str(closed))

def test_option_stop():
    osym = occ("COIN", date(2026, 7, 9), "C", 213.0)
    base = {"symbol": osym, "qty": 1, "entry": 25.0, "opened": "2026-06-15T11:00:00",
            "opened_day": "2026-06-15", "max_hold_days": 5, "book": "conviction_itm"}
    # mid 17 -> pl = (17-25)/25 = -32% <= -30% stop -> EXIT
    closed, _ = _run_manage(base, datetime(2026, 6, 16, 11, 0), 17.0)
    check("§4 option stop fires at CONVICTION_ITM_STOP_PCT", osym in closed, str(closed))

def test_hold_when_healthy():
    osym = occ("COIN", date(2026, 7, 9), "C", 213.0)
    base = {"symbol": osym, "qty": 1, "entry": 25.0, "opened": "2026-06-15T11:00:00",
            "opened_day": "2026-06-15", "max_hold_days": 5, "book": "conviction_itm"}
    # day 1, healthy mid 26, mid-day -> NO exit, still held.
    closed, still = _run_manage(base, datetime(2026, 6, 15, 13, 0), 26.0)
    check("§4 healthy conviction hold is NOT exited", not closed and len(still) == 1, str(closed))


# ---- §9.5 eligibility router -----------------------------------------------
def test_qualifies():
    bull = {"symbol": "COIN", "signal": "STRONG_BULL", "vwap_ext": 0.012,
            "rsi": 62.0, "off_hod": 0.001, "day_pct": 5.0}
    check("§2 clean STRONG_BULL trend qualifies", at.conviction_itm_qualifies(bull))
    blown = dict(bull); blown["rsi"] = 88.0
    check("§2 RSI blow-off disqualifies", not at.conviction_itm_qualifies(blown))
    fading = dict(bull); fading["vwap_ext"] = -0.02
    check("§2 negative vwap_ext (fading) disqualifies", not at.conviction_itm_qualifies(fading))
    notnew = dict(bull); notnew["off_hod"] = 0.03
    check("§2 not making new HOD disqualifies", not at.conviction_itm_qualifies(notnew))
    weak = {"symbol": "X", "signal": "BULL", "vwap_ext": 0.01, "rsi": 60, "off_hod": 0.001, "day_pct": 1.0}
    check("§2 non-STRONG trend without catalyst does NOT qualify", not at.conviction_itm_qualifies(weak))
    cat = dict(weak); cat["event_catalyst"] = True
    check("§2 hard catalyst qualifies even without a STRONG trend", at.conviction_itm_qualifies(cat))


if __name__ == "__main__":
    test_selector()
    test_selector_illiquid_reject()
    test_sizing_ok()
    test_sizing_too_expensive()
    test_flag_off_uses_old_cap()
    test_eod_exemption()
    test_eod_closes_normal_option()
    test_max_hold()
    test_dte_exit()
    test_trailing_stop()
    test_option_stop()
    test_hold_when_healthy()
    test_qualifies()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", FAIL)
        sys.exit(1)
