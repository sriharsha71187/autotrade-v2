import sys, os, types
sys.path.insert(0, "/Users/nirvaan/autotrade")

import config as cfg
cfg.LOG_FILE = "/tmp/batch1_test.log"
cfg.STATE_FILE = "/tmp/batch1_state.json"

import autotrade as at

# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------
from datetime import datetime
import pytz
ET = pytz.timezone("US/Eastern")
NOW = ET.localize(datetime(2026, 6, 15, 11, 0))   # 11:00 ET, inside option window

ACCT = {"equity": 100_000.0, "positions_value": 0.0, "day_pl": 0.0, "start_equity": 100_000.0}

def base_state():
    return {"active_options": [], "active_multileg": [], "book_symbols": [],
            "open_lots": {}, "processed_fills": [], "closed_trades": []}

def guard(decision, **kw):
    st = kw.pop("state", None) or base_state()
    acct = kw.pop("acct", None) or dict(ACCT)
    return at.passes_guardrails(decision, st, acct, NOW, **kw)

# ===========================================================================
# FIX #6 — STOCK risk sizing
# ===========================================================================
def stock_decision(entry, stop, conviction="medium"):
    return {"action": "buy_stock", "symbol": "TESTX", "direction": "long",
            "conviction": conviction, "qty": 999,   # model qty should be overridden
            "stop_price": stop, "target_price": entry * 1.10}

def test_stock_risk_equal_across_stops():
    # Wide stop and tight stop, same conviction -> different qty, ~equal $ risk (~$250).
    # Low entry so PER_TRADE_NOTIONAL_CAP never binds (250sh * $10 = $2500 < $5000).
    entry = 10.0
    d1 = stock_decision(entry, 5.0); at.passes_guardrails(d1, base_state(), dict(ACCT), NOW,
                                                          ref_price=entry, assets={"TESTX": {"shortable": True}})  # risk/sh 5
    d2 = stock_decision(entry, 9.0); at.passes_guardrails(d2, base_state(), dict(ACCT), NOW,
                                                          ref_price=entry, assets={"TESTX": {"shortable": True}})  # risk/sh 1
    risk1 = d1["qty"] * 5.0
    risk2 = d2["qty"] * 1.0
    assert d1["qty"] != d2["qty"], (d1["qty"], d2["qty"])
    assert abs(risk1 - 250) <= 5, risk1
    assert abs(risk2 - 250) <= 5, risk2
    print(f"  stock risk-equal: wide qty={d1['qty']} risk={risk1}, tight qty={d2['qty']} risk={risk2}  OK")

def test_stock_conviction_scales():
    # Low entry price so PER_TRADE_NOTIONAL_CAP ($5000) never binds (80*$10=$800).
    entry, stop = 10.0, 5.0   # risk/sh 5
    dl = stock_decision(entry, stop, "low"); at.passes_guardrails(dl, base_state(), dict(ACCT), NOW,
                                                                  ref_price=entry, assets={"TESTX": {"shortable": True}})
    dm = stock_decision(entry, stop, "medium"); at.passes_guardrails(dm, base_state(), dict(ACCT), NOW,
                                                                     ref_price=entry, assets={"TESTX": {"shortable": True}})
    dh = stock_decision(entry, stop, "high"); at.passes_guardrails(dh, base_state(), dict(ACCT), NOW,
                                                                   ref_price=entry, assets={"TESTX": {"shortable": True}})
    # budgets: 150/250/400 over risk/sh 5 -> 30/50/80
    assert dl["qty"] < dm["qty"] < dh["qty"], (dl["qty"], dm["qty"], dh["qty"])
    assert dl["qty"] == 30 and dm["qty"] == 50 and dh["qty"] == 80, (dl["qty"], dm["qty"], dh["qty"])
    print(f"  stock conviction scale: low={dl['qty']} med={dm['qty']} high={dh['qty']}  OK")

def test_stock_notional_clamp():
    # Tiny stop distance -> huge risk-qty, must clamp to PER_TRADE_NOTIONAL_CAP.
    entry = 100.0
    d = stock_decision(entry, stop=99.99, conviction="high")  # risk/sh 0.01 -> budget 400 -> 40000 sh
    ok, r = guard(dict(d), ref_price=entry, assets={"TESTX": {"shortable": True}})
    dd = stock_decision(entry, 99.99, "high")
    at.passes_guardrails(dd, base_state(), dict(ACCT), NOW, ref_price=entry, assets={"TESTX": {"shortable": True}})
    assert dd["qty"] * entry <= cfg.PER_TRADE_NOTIONAL_CAP + 1e-6, (dd["qty"], dd["qty"]*entry)
    assert dd["qty"] == int(cfg.PER_TRADE_NOTIONAL_CAP // entry), dd["qty"]
    print(f"  stock notional clamp: qty={dd['qty']} notional={dd['qty']*entry} cap={cfg.PER_TRADE_NOTIONAL_CAP}  OK")

def test_stock_no_stop_fallback():
    # No usable stop distance (stop == entry) -> keep model qty, enforce notional clamp, no crash.
    # stop must satisfy bracket orientation, so use a tiny modeling: stop slightly below entry but
    # we force |entry-stop| path off by setting ref_price == stop is invalid; instead test the
    # branch where abs(ref-stop)==0 cannot occur with valid bracket. Simulate via direct: stop=entry
    # would fail bracket. So the "no usable stop" guard is really when abs==0 -> can't happen post-bracket.
    # Instead verify a normal-but-huge model qty still gets notional-clamped (the fallback clamp path).
    entry = 50.0
    d = {"action": "buy_stock", "symbol": "TESTX", "direction": "long", "conviction": "medium",
         "qty": 100000, "stop_price": 49.5, "target_price": 55.0}
    ok, r = at.passes_guardrails(d, base_state(), dict(ACCT), NOW, ref_price=entry,
                                 assets={"TESTX": {"shortable": True}})
    assert ok, r
    assert d["qty"] * entry <= cfg.PER_TRADE_NOTIONAL_CAP + 1e-6, (d["qty"], d["qty"]*entry)
    print(f"  stock huge-model-qty clamped to qty={d['qty']} (no crash)  OK")

# ===========================================================================
# FIX #6 — OPTION risk sizing (non-conviction)
# ===========================================================================
def test_option_risk_sizing():
    # $5 premium option. risk/ct = 500*0.5 = 250. target 900 -> floor(900/250)=3 contracts.
    # Then $600 notional cap: 3*500=1500 > 600 -> reduce to 1 (1*500=500 <= 600).
    osym = "AAPL260116C00150000"
    d = {"action": "buy_option", "symbol": "AAPL", "option_symbol": osym, "qty": 99,
         "conviction": "medium"}
    ok, r = at.passes_guardrails(
        d, base_state(), dict(ACCT), NOW,
        ref_price=5.0, offered_options={osym}, option_spread_pct=0.0,
        scan_row={"signal": "STRONG_BULL", "day_pct": 1.0})
    assert ok, r
    # risk-driven, then capped by $600 notional -> qty 1
    assert d["qty"] == 1, d["qty"]
    notional = d["qty"] * 5.0 * 100
    assert notional <= cfg.PER_OPTION_NOTIONAL_CAP, notional
    print(f"  option risk-size: qty={d['qty']} notional={notional} cap={cfg.PER_OPTION_NOTIONAL_CAP}  OK")

def test_option_risk_sizing_cheap():
    # Cheap $1 premium: risk/ct = 100*0.5 = 50, target 900 -> 18 contracts.
    # notional 18*100=1800 > 600 -> reduce to 6 (6*100=600).
    osym = "AAPL260116C00150000"
    d = {"action": "buy_option", "symbol": "AAPL", "option_symbol": osym, "qty": 99,
         "conviction": "medium"}
    ok, r = at.passes_guardrails(
        d, base_state(), dict(ACCT), NOW,
        ref_price=1.0, offered_options={osym}, option_spread_pct=0.0,
        scan_row={"signal": "STRONG_BULL", "day_pct": 1.0})
    assert ok, r
    assert d["qty"] * 1.0 * 100 <= cfg.PER_OPTION_NOTIONAL_CAP, d["qty"]
    assert d["qty"] == 6, d["qty"]
    print(f"  option risk-size cheap: qty={d['qty']} notional={d['qty']*100}  OK")

# ===========================================================================
# FIX #3 / #4 — exit ladder
# ===========================================================================
def test_exit_ladder():
    f = at._option_exit_reason
    # Peaked +30% then fell to +5% -> trail (fixed band 0.20: 0.30-0.20=0.10; 0.05<=0.10) EXIT.
    r = f(0.05, 0.30); assert r and "trail" in r, r
    # Peaked +30%, at +12% -> 0.12 > 0.10 -> hold
    assert f(0.12, 0.30) is None, "should hold above band"
    # Peaked +16% (>=breakeven, <activate) then to 0% -> breakeven EXIT (pl<=0).
    r = f(0.0, 0.16); assert r and "breakeven" in r, r
    # Peaked +16% still +5% -> hold (above 0, below activate so no trail)
    assert f(0.05, 0.16) is None, "should hold +5% under breakeven trigger"
    # Peaked +10% (below breakeven 0.15) then to -20% -> rides to hard stop only (None).
    assert f(-0.20, 0.10) is None, "below breakeven should ride to hard stop"
    # ...continues to -50% hard stop -> EXIT
    r = f(-0.50, 0.10); assert r and r.startswith("stop"), r
    # Conviction path uses same ladder with its own (wider) stop.
    r = f(-0.55, 0.10, stop_pct=cfg.CONVICTION_ITM_STOP_PCT)
    assert r and r.startswith("stop"), r
    # conviction not stopped at -0.50 if its stop is wider (e.g. -0.60)
    if cfg.CONVICTION_ITM_STOP_PCT < -0.50:
        assert f(-0.50, 0.05, stop_pct=cfg.CONVICTION_ITM_STOP_PCT) is None
    print(f"  exit ladder (single+conviction) OK  (conv stop={cfg.CONVICTION_ITM_STOP_PCT})")

# ===========================================================================
# FIX A — broker reconcile
# ===========================================================================
class FakePos:
    def __init__(self, symbol, qty, avg, asset_class="us_equity"):
        self.symbol = symbol; self.qty = qty
        self.avg_entry_price = avg; self.asset_class = asset_class

class FakeTC:
    def __init__(self, positions):
        self._p = positions
    def get_all_positions(self):
        return self._p

def test_reconcile_seeds_missing():
    st = base_state()
    st["book_symbols"] = ["BOOKX"]
    positions = [
        FakePos("MISSINGX", "10", "42.50"),         # stock, no lot -> seed
        FakePos("BOOKX", "5", "10.0"),              # book symbol -> NOT seeded
        FakePos("AAPL260116C00150000", "-3", "4.20", asset_class="us_option"),  # short option, no lot
    ]
    n = at._reconcile_lots_to_broker(FakeTC(positions), st)
    lots = st["open_lots"]
    assert "MISSINGX" in lots, lots
    assert lots["MISSINGX"]["avg_cost"] == 42.5, lots["MISSINGX"]
    assert lots["MISSINGX"]["qty"] == 10.0
    assert lots["MISSINGX"]["mult"] == 1
    assert "BOOKX" not in lots, "book symbol must not be seeded"
    assert "AAPL260116C00150000" in lots
    assert lots["AAPL260116C00150000"]["qty"] == -3.0
    assert lots["AAPL260116C00150000"]["mult"] == 100
    assert lots["AAPL260116C00150000"]["avg_cost"] == 4.20
    print(f"  reconcile seeds missing, skips book: n={n}  OK")

def test_reconcile_sign_mismatch_replaces():
    st = base_state()
    # Pre-existing lot says LONG 5, broker says SHORT 8 -> replace, keep strategy.
    st["open_lots"]["FLIPX"] = {"qty": 5.0, "avg_cost": 10.0, "mult": 1,
                                "strategy": "stock_long", "opened_day": "2026-06-01"}
    positions = [FakePos("FLIPX", "-8", "12.0")]
    n = at._reconcile_lots_to_broker(FakeTC(positions), st)
    lot = st["open_lots"]["FLIPX"]
    assert lot["qty"] == -8.0, lot
    assert lot["avg_cost"] == 12.0, lot
    assert lot["strategy"] == "stock_long", "should preserve existing strategy"
    assert lot["opened_day"] == "2026-06-01", "should preserve opened_day"
    print(f"  reconcile sign-mismatch replaces, preserves strategy/day  OK")

def test_reconcile_no_touch_matching_lot():
    st = base_state()
    st["open_lots"]["KEEPX"] = {"qty": 5.0, "avg_cost": 10.0, "mult": 1,
                                "strategy": "stock_long", "opened_day": "2026-06-01"}
    positions = [FakePos("KEEPX", "5", "99.0")]   # same sign -> NOT replaced
    n = at._reconcile_lots_to_broker(FakeTC(positions), st)
    lot = st["open_lots"]["KEEPX"]
    assert lot["avg_cost"] == 10.0, "matching-sign lot must not be overwritten"
    assert n == 0, n
    print(f"  reconcile leaves matching lot untouched  OK")

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} tests...")
    for t in tests:
        t()
    print("ALL GREEN")
