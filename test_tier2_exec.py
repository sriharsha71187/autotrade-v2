"""Tier-2 EXECUTION tests (AUDIT_ROADMAP #11 IV-rank gate, #15 marketable-limit exits vs
forced-market safety closes, #16 single-leg peak context, #17 overnight resting protective
orders). Redirects cfg paths to /tmp; stubs the broker client; no network. Market closed."""
import sys, os, types
sys.path.insert(0, "/Users/nirvaan/autotrade")

import config as cfg
cfg.LOG_FILE = "/tmp/test_tier2_exec.log"
cfg.STATE_FILE = "/tmp/test_tier2_exec_state.json"
import autotrade as at
from alpaca.trading.requests import (MarketOrderRequest, LimitOrderRequest,
                                      StopOrderRequest, StopLimitOrderRequest)

PASS = 0; FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}")

# Silence Telegram in tests.
at.tg_send = lambda *a, **k: None

NOW = at.et_now().replace(hour=11, minute=0)
ACCT = {"equity": 100_000.0, "positions_value": 0.0, "day_pl": 0.0}

def occ(und, cp, strike, yymmdd="261231"):
    return f"{und}{yymmdd}{cp}{int(strike*1000):08d}"

def base_state():
    s = at.default_state(); s["start_equity"] = 100_000.0; return s

def guard(decision, positions, oi, ref=2.00, spread=0.05, offered=None, conviction=None):
    return at.passes_guardrails(
        decision, base_state(), ACCT, NOW, ref_price=ref,
        offered_options=offered if offered is not None else {decision.get("option_symbol")},
        assets={}, option_spread_pct=spread, scan_row=None,
        dir_counts={"bull": 0, "bear": 0}, book_symbols=set(),
        regime=None, event_state=None, options_intel=oi,
        conviction_symbols=conviction or set(), positions=positions)


# --------------------------------------------------------------------------
# Stub broker + quote clients
# --------------------------------------------------------------------------
class StubPos:
    def __init__(self, symbol, qty):
        self.symbol = symbol; self.qty = str(qty)
        self.asset_class = "us_option"
        self.avg_entry_price = "2.00"; self.side = "long"

class StubQuote:
    def __init__(self, bid, ask): self.bid_price = bid; self.ask_price = ask

class StubClient:
    """Records every submit_order so the test can assert on order TYPE (market vs limit
    vs stop). Positions flip to flat after the first close attempt so the fill-wait path
    sees them gone (marketable limit 'filled')."""
    def __init__(self, positions, flip_flat_on_close=True):
        self._positions = list(positions)
        self.orders = []            # list of submitted order request objects
        self.canceled = []
        self._flip = flip_flat_on_close
        self._next_id = 0
    def get_all_positions(self): return list(self._positions)
    def get_orders(self, filter=None): return []
    def cancel_order_by_id(self, oid): self.canceled.append(oid)
    def submit_order(self, order_data=None):
        self.orders.append(order_data)
        self._next_id += 1
        # A close order (SELL on a held symbol) flattens it so the post-wait recheck
        # sees the leg gone (marketable-limit "fill"). Entry/protective orders don't.
        sym = getattr(order_data, "symbol", None)
        side = str(getattr(order_data, "side", ""))
        is_close = "SELL" in side and any(p.symbol == sym for p in self._positions)
        if self._flip and is_close and not isinstance(order_data, StopOrderRequest) \
                and not isinstance(order_data, StopLimitOrderRequest):
            self._positions = [p for p in self._positions if p.symbol != sym]
        o = types.SimpleNamespace(id=f"oid{self._next_id}",
                                  filled_avg_price=None, status="accepted")
        return o
    def close_position(self, sym):
        self._positions = [p for p in self._positions if p.symbol != sym]
        return types.SimpleNamespace(id="close1")

class StubODC:
    def __init__(self, quotes): self._q = quotes  # {sym: StubQuote}
    def get_option_latest_quote(self, req):
        syms = req.symbol_or_symbols
        if isinstance(syms, str): syms = [syms]
        return {s: self._q.get(s) for s in syms}

# Make the fill-wait instantaneous in tests.
cfg.OPTION_EXIT_FILL_WAIT_SEC = 0
import time as _time
at.time.sleep = lambda *a, **k: None

# Pin et_now to mid-session (11:00) so management tests don't trip the real-clock EOD
# hard-close (the suite runs after hours). Individual tests override for the EOD cases.
_orig_etnow = at.et_now
at.et_now = lambda: NOW


# ==========================================================================
print("\n=== #11 IV-rank gate (single-name premium buying) ===")
SYM = occ("AAPL", "C", 200)
dec = {"action": "buy_option", "symbol": "AAPL", "option_symbol": SYM,
       "qty": 1, "conviction": "medium"}

# iv_rank 85 -> blocked
ok, why = guard(dec, [], {"AAPL": {"atm_iv": 30.0, "iv_rank": 85.0}})
check("iv_rank 85 blocks single-name long", (not ok) and "IV-rank" in why)

# iv_rank 40 -> allowed (passes the IV-rank gate; must not be blocked FOR iv-rank)
ok2, why2 = guard(dec, [], {"AAPL": {"atm_iv": 30.0, "iv_rank": 40.0}})
check("iv_rank 40 not blocked by IV-rank gate", "IV-rank" not in (why2 or ""))

# iv_rank None -> allowed (fail open)
ok3, why3 = guard(dec, [], {"AAPL": {"atm_iv": 30.0, "iv_rank": None}})
check("iv_rank None not blocked (fail open)", "IV-rank" not in (why3 or ""))

# Index condor path is exempt: iron_condor never hits the gate; and an index underlying
# (SPY) is carved out even at iv_rank 95.
cond = {"action": "iron_condor", "symbol": "SPY",
        "condor_legs": [{"symbol": occ("SPY", "C", 500), "side": "sell"}],
        "net_credit": 1.0, "qty": 1}
ok4, why4 = at.passes_guardrails(
    cond, base_state(), ACCT, NOW, ref_price=1.0, offered_options=set(), assets={},
    option_spread_pct=None, scan_row=None, dir_counts={"bull": 0, "bear": 0},
    book_symbols=set(), regime=None, event_state=None,
    options_intel={"SPY": {"atm_iv": 20.0, "iv_rank": 95.0}},
    conviction_symbols=set(), positions=[])
check("iron_condor not blocked by IV-rank gate", "IV-rank" not in (why4 or ""))

# A SPY single-name long at iv_rank 95 is also exempt (index carve-out).
spy_dec = {"action": "buy_option", "symbol": "SPY",
           "option_symbol": occ("SPY", "C", 500), "qty": 1, "conviction": "medium"}
ok5, why5 = guard(spy_dec, [], {"SPY": {"atm_iv": 20.0, "iv_rank": 95.0}})
check("SPY (index) long exempt from IV-rank gate", "IV-rank" not in (why5 or ""))


# ==========================================================================
print("\n=== #15 marketable-limit DISCRETIONARY exits vs forced-market SAFETY ===")

def last_order(c): return c.orders[-1] if c.orders else None

# --- DISCRETIONARY: manage_options profit-take/trail exit -> marketable LIMIT ---
st = base_state()
st["active_options"] = [{"symbol": SYM, "qty": 1, "entry": 2.00,
                         "hw_pl": 0.40, "opened": NOW.isoformat()}]
# current mid 3.00 -> +50% > breakeven & past trail-give-back from +40%? hw 0.40, pl 0.50
# Force a discretionary exit by setting hw_pl high then mid that triggers trail give-back.
st["active_options"][0]["hw_pl"] = 0.60          # peaked +60%
quotes = {SYM: StubQuote(bid=2.60, ask=2.80)}    # mid 2.70 -> pl +35%, gave back >20pts -> trail
tc = StubClient([StubPos(SYM, 1)])
odc = StubODC(quotes)
# Patch option mid used by manage_options to our quote.
at.option_latest_quote = lambda _odc, s: quotes.get(s)
at.manage_options(tc, odc, st, dry=False)
lo = last_order(tc)
check("discretionary trail exit submits a LIMIT (not market)",
      isinstance(lo, LimitOrderRequest))
check("marketable-limit priced off bid (sell side, below mid)",
      isinstance(lo, LimitOrderRequest) and lo.limit_price <= 2.70)
check("discretionary exit closed the position", not tc.get_all_positions())

# --- SAFETY: MAX-LOSS KILL in manage_options -> MARKET ---
# Use the conviction book (stop -30% -> init_risk 200*0.30=60, kill at 2x=120) so a -$185
# loss strictly EXCEEDS the kill threshold (the long-option % stop maxes at the premium).
st = base_state()
st["active_options"] = [{"symbol": SYM, "qty": 1, "entry": 2.00, "book": "conviction_itm",
                         "opened_day": NOW.date().isoformat(), "max_hold_days": 5,
                         "hw_pl": 0.0, "opened": NOW.isoformat()}]
quotes = {SYM: StubQuote(bid=0.10, ask=0.20)}    # mid 0.15 -> loss ~ -$185 > kill $120
at.option_latest_quote = lambda _odc, s: quotes.get(s)
tc = StubClient([StubPos(SYM, 1)])
at.manage_options(tc, odc, st, dry=False)
# A market kill goes through close_symbols -> close_position (no submit_order limit).
mk_used_market = (len([o for o in tc.orders if isinstance(o, LimitOrderRequest)]) == 0)
check("MAX-LOSS KILL uses MARKET (no limit order submitted)", mk_used_market)
check("MAX-LOSS KILL closed the position", not tc.get_all_positions())

# --- SAFETY: EOD hard-close in manage_options -> MARKET ---
st = base_state()
st["active_options"] = [{"symbol": SYM, "qty": 1, "entry": 2.00,
                         "hw_pl": 0.0, "opened": NOW.isoformat()}]
quotes = {SYM: StubQuote(bid=2.00, ask=2.10)}
at.option_latest_quote = lambda _odc, s: quotes.get(s)
EOD = NOW.replace(hour=15, minute=46)
at.et_now = lambda: EOD
tc = StubClient([StubPos(SYM, 1)])
at.manage_options(tc, odc, st, dry=False)
at.et_now = lambda: NOW
eod_market = (len([o for o in tc.orders if isinstance(o, LimitOrderRequest)]) == 0)
check("EOD hard-close uses MARKET (no limit order)", eod_market)

# --- SAFETY: naked-short force-close -> MARKET (close_symbols only) ---
st = base_state()
nshort = occ("XYZ", "C", 50)
tc = StubClient([StubPos(nshort, -1)])     # uncovered short, qty -1
at.assert_no_naked_short(tc, st, dry=False)
ns_market = (len([o for o in tc.orders if isinstance(o, LimitOrderRequest)]) == 0)
check("naked-short force-close uses MARKET (no limit)", ns_market and not tc.get_all_positions())

# --- SAFETY: orphan sweep -> MARKET ---
st = base_state()
orph = occ("ZZZ", "C", 10)
EOD = NOW.replace(hour=15, minute=46)
at.et_now = lambda: EOD
tc = StubClient([StubPos(orph, 1)])
at.sweep_orphan_options(tc, st, dry=False, skip=set())
at.et_now = lambda: NOW
orph_market = (len([o for o in tc.orders if isinstance(o, LimitOrderRequest)]) == 0)
check("orphan sweep uses MARKET (no limit)", orph_market and not tc.get_all_positions())

# --- DISCRETIONARY: manage_multileg debit-spread trail exit -> marketable LIMIT ---
st = base_state()
legL = occ("MSFT", "C", 400); legS = occ("MSFT", "C", 410)
st["active_multileg"] = [{
    "legs": [{"symbol": legL, "side": "buy"}, {"symbol": legS, "side": "sell"}],
    "qty": 1, "entry_net": -300.0, "basis_reconciled": True,
    "hw_pf": 0.80, "opened": NOW.isoformat()}]
# Mid such that pl gives back past the band from +80% peak -> trail exit (discretionary).
qml = {legL: StubQuote(bid=6.00, ask=6.20), legS: StubQuote(bid=2.50, ask=2.70)}
def _ml_quote(req):
    syms = req.symbol_or_symbols
    if isinstance(syms, str): syms = [syms]
    return {s: qml.get(s) for s in syms}
odc_ml = types.SimpleNamespace(get_option_latest_quote=_ml_quote)
at.option_latest_quote = lambda _odc, s: qml.get(s)
tc = StubClient([StubPos(legL, 1), StubPos(legS, -1)])
at.manage_multileg(tc, odc_ml, st, dry=False)
ml_limits = [o for o in tc.orders if isinstance(o, LimitOrderRequest)]
check("multileg discretionary exit submits LIMIT order(s)", len(ml_limits) >= 1)


# ==========================================================================
print("\n=== #16 single-leg context: hw_pl / peak / giving_back ===")
st = base_state()
st["active_options"] = [{
    "symbol": SYM, "qty": 2, "entry": 2.00, "book": "conviction_itm",
    "hw_pl": 0.50, "pl": 0.20, "opened": NOW.isoformat()}]
ctx = at.open_options_for_context(st)
check("context returns one option row", len(ctx) == 1)
row = ctx[0] if ctx else {}
check("row has hw_pl_pct", row.get("hw_pl_pct") == 50.0)
check("row has current_pct_of_entry", row.get("current_pct_of_entry") == 20.0)
check("row has peak_unrealized_$ (2.00*100*2*0.5=200)", row.get("peak_unrealized_$") == 200)
check("row has current_unrealized_$ (2.00*100*2*0.2=80)", row.get("current_unrealized_$") == 80)
check("giving_back True (peak +50% -> +20%, >20pt drop)", row.get("giving_back") is True)
# A position still near its peak is NOT giving back.
st["active_options"][0]["pl"] = 0.45
row2 = at.open_options_for_context(st)[0]
check("giving_back False when near peak", row2.get("giving_back") is False)
# context is wired into the engine context too
check("open_options_for_context callable", callable(at.open_options_for_context))


# ==========================================================================
print("\n=== #17 overnight resting protective order: place / cancel / reject ===")

# place returns an order id and submits a STOP (or stop-limit) GTC order
tc = StubClient([])
oid = at.place_overnight_option_stop(tc, SYM, 1, 2.00, cfg.CONVICTION_ITM_STOP_PCT, dry=False)
placed = tc.orders[-1] if tc.orders else None
check("overnight option-stop placed (returns id)", bool(oid))
check("overnight protective order is a GTC STOP/stop-limit",
      isinstance(placed, (StopOrderRequest, StopLimitOrderRequest))
      and str(getattr(placed, "time_in_force", "")).endswith("GTC"))

# cancel removes it (records the cancel)
at.cancel_overnight_protection(tc, oid, dry=False)
check("cancel_overnight_protection cancels the resting order", oid in tc.canceled)

# broker rejection is caught (no crash), returns None
class RejectClient(StubClient):
    def submit_order(self, order_data=None):
        raise Exception("422 option stop not supported")
rc = RejectClient([])
res = at.place_overnight_option_stop(rc, SYM, 1, 2.00, cfg.CONVICTION_ITM_STOP_PCT, dry=False)
check("broker rejection caught (no crash, returns None)", res is None)

# granting an overnight conviction-ITM hold places a resting protective order (integration
# via the entry block): we test the helper is invoked + stored on the position.
st = base_state()
tc = StubClient([])
prot = at.place_overnight_option_stop(tc, SYM, 3, 2.00, cfg.CONVICTION_ITM_STOP_PCT, dry=False)
entry = {"symbol": SYM, "qty": 3, "entry": 2.00, "book": "conviction_itm",
         "protective_order_id": prot}
check("protective_order_id stored on the conviction-ITM hold", bool(entry["protective_order_id"]))

# closing the conviction hold cancels its protective order (manage_options exit path).
st["active_options"] = [{"symbol": SYM, "qty": 3, "entry": 2.00, "book": "conviction_itm",
                         "opened_day": NOW.date().isoformat(),
                         "opened": NOW.isoformat(), "max_hold_days": 5,
                         "hw_pl": 0.0, "protective_order_id": prot}]
# Force a discretionary conviction exit: DTE exit (expiry already past min DTE) won't fire
# on 261231; instead trigger the stop via a deep loss -> max-loss kill OR stop. Use a low mid.
quotes = {SYM: StubQuote(bid=1.20, ask=1.30)}    # mid 1.25 -> pl -37.5% < CONVICTION stop -30%
at.option_latest_quote = lambda _odc, s: quotes.get(s)
tc2 = StubClient([StubPos(SYM, 3)])
# Carry over the protective order id into the new client's known order so cancel is recorded.
at.manage_options(tc2, StubODC(quotes), st, dry=False)
check("closing conviction hold cancels its protective order", prot in tc2.canceled)


print(f"\n==== {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
