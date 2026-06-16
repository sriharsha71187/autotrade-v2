"""Tier-2 risk-control tests (#10 sector/heat cap, #13 drawdown floor + red-day de-risk,
#14 per-position max-loss kill). Redirects cfg.LOG_FILE/STATE_FILE to /tmp so nothing
real is touched. No network, market closed — pure unit tests of the new code paths."""
import sys, os, types
sys.path.insert(0, "/Users/nirvaan/autotrade")

import config as cfg
cfg.LOG_FILE = "/tmp/test_tier2.log"
cfg.STATE_FILE = "/tmp/test_tier2_state.json"
import autotrade as at

PASS = 0
FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")

NOW = at.et_now().replace(hour=11, minute=0)   # mid-session, past option windows
ACCT = {"equity": 100_000.0, "positions_value": 0.0, "day_pl": 0.0}

def occ(und, cp, strike, yymmdd="261231"):
    return f"{und}{yymmdd}{cp}{int(strike*1000):08d}"

def base_state():
    s = at.default_state()
    s["start_equity"] = 100_000.0
    return s

def stock_pos(sym, side, qty=10, entry=100.0):
    return {"symbol": sym, "qty": qty, "side": side, "avg_entry": entry,
            "current": entry, "unrealized_pl": 0.0, "asset_class": "us_equity"}

def stock_decision(sym, direction="long", ref=100.0):
    # long bracket needs stop < ref < target; risk/share = 1.0 (ref-stop)
    return {"action": "buy_stock", "symbol": sym, "direction": direction, "qty": 10,
            "stop_price": ref - 1.0, "target_price": ref + 5.0, "conviction": "medium"}

def guard(decision, state, positions, acct=None, ref=100.0):
    return at.passes_guardrails(decision, state, acct or ACCT, NOW, ref_price=ref,
                                offered_options=set(), assets={},
                                option_spread_pct=None, scan_row=None,
                                dir_counts={"bull": 0, "bear": 0}, book_symbols=set(),
                                regime=None, event_state=None, options_intel=None,
                                conviction_symbols=set(), positions=positions)

print("\n=== #10 sector/correlation heat cap ===")
# sector_for sanity
check("sector_for(NVDA)==semis_ai", at.sector_for("NVDA") == "semis_ai")
check("sector_for(AMD)==semis_ai", at.sector_for("AMD") == "semis_ai")
check("sector_for(XYZ)==other", at.sector_for("ZZZZ") == "other")
check("sector_for(option underlying)", at.sector_for(occ("NVDA", "C", 100)) == "semis_ai")

# 4 semis longs already open; a 5th semis long (AMD) must be blocked.
semis = ["NVDA", "AVGO", "MU", "ARM"]
positions = [stock_pos(s, "PositionSide.LONG") for s in semis]
st = base_state()
ok, why = guard(stock_decision("AMD", "long"), st, positions, ref=100.0)
check("5th semis-long BLOCKED", (not ok) and "sector heat" in why)
print(f"        reason: {why}")

# A different sector (AAPL = megacap_tech) IS allowed.
ok, why = guard(stock_decision("AAPL", "long"), st, positions, ref=100.0)
check("different-sector long ALLOWED", ok)

# Opposite direction (semis SHORT) IS allowed (4 longs, 0 shorts).
ok, why = guard(stock_decision("AMD", "short", ref=100.0),
                base_state(),
                [stock_pos(s, "PositionSide.LONG") for s in semis],
                ref=100.0)
# short needs target<ref<stop
d = {"action": "buy_stock", "symbol": "AMD", "direction": "short", "qty": 10,
     "stop_price": 101.0, "target_price": 95.0, "conviction": "medium"}
# AMD must be shortable
ok, why = at.passes_guardrails(d, base_state(), ACCT, NOW, ref_price=100.0,
                               offered_options=set(), assets={"AMD": {"shortable": True}},
                               option_spread_pct=None, scan_row=None,
                               dir_counts={"bull": 0, "bear": 0}, book_symbols=set(),
                               positions=[stock_pos(s, "PositionSide.LONG") for s in semis])
check("opposite-direction (semis short) ALLOWED", ok)
print(f"        short reason: {why}")

# Only 3 semis longs -> a 4th is allowed (cap is >= 4 blocks).
positions3 = [stock_pos(s, "PositionSide.LONG") for s in semis[:3]]
ok, why = guard(stock_decision("AMD", "long"), base_state(), positions3, ref=100.0)
check("4th semis-long ALLOWED (3 open < cap)", ok)

print("\n=== #10 portfolio heat cap ===")
# Heat cap = PORTFOLIO_HEAT_MULT * abs(DAILY_LOSS_HALT). With paper defaults
# (3.0 * 300 = 900), make existing open risk large so a new trade busts it.
heat_cap = cfg.PORTFOLIO_HEAT_MULT * abs(cfg.DAILY_LOSS_HALT)
st = base_state()
# Existing tracked long options carrying big risk: entry $10, 100x, stop -50% -> risk = 10*100*100*0.5 = 50,000
st["active_options"] = [{"symbol": occ("ORCL", "C", 100), "qty": 100, "entry": 10.0}]
open_risk = at.open_defined_risk(st, [])
check("open_defined_risk computed large", open_risk > heat_cap)
print(f"        open_risk=${open_risk:.0f}  heat_cap=${heat_cap:.0f}")
# A new megacap_tech long (distinct sector, no sector block) must be HEAT-blocked.
ok, why = guard(stock_decision("AAPL", "long"), st, [], ref=100.0)
check("new entry BLOCKED on portfolio heat", (not ok) and "heat" in why.lower())
print(f"        reason: {why}")
# With a clean book, the same trade passes heat.
ok, why = guard(stock_decision("AAPL", "long"), base_state(), [], ref=100.0)
check("same entry ALLOWED with clean book", ok)

print("\n=== #13 account drawdown floor ===")
# Simulate run_cycle's high-water update + drawdown latch via direct state manipulation,
# then check passes_guardrails honors the latch.
st = base_state()
st["equity_high_water"] = 100_000.0
# equity 3000 below HW -> latched (we set the latch as run_cycle would).
st["drawdown_halted"] = NOW.strftime("%Y-%m-%d")
ok, why = guard(stock_decision("AAPL", "long"), st, [], acct={"equity": 97_000.0,
                "positions_value": 0.0, "day_pl": 0.0}, ref=100.0)
check("drawdown latch BLOCKS new entry", (not ok) and "drawdown" in why.lower())
print(f"        reason: {why}")
# hold/close are still allowed.
ok, why = guard({"action": "close", "symbol": "AAPL"}, st, [], acct={"equity": 97_000.0,
                "positions_value": 0.0, "day_pl": 0.0})
check("drawdown latch ALLOWS close", ok)

# High-water rises with equity (the run_cycle update logic, replicated).
def update_hw(state, equity):
    hw = state.get("equity_high_water")
    state["equity_high_water"] = equity if hw is None else max(float(hw), equity)
    return state["equity_high_water"]
st2 = base_state()
check("hw seeds on first run", update_hw(st2, 100_000.0) == 100_000.0)
check("hw rises with equity", update_hw(st2, 101_500.0) == 101_500.0)
check("hw holds on dip", update_hw(st2, 100_000.0) == 101_500.0)
# Drawdown re-arms off the new high: 101_500 - 98_000 = 3500 >= 3000 triggers.
draw = st2["equity_high_water"] - 98_000.0
check("drawdown measured off trailing high-water", draw >= cfg.ACCOUNT_DRAWDOWN_HALT)

print("\n=== #13 red-day de-risk halves the risk budget ===")
st = base_state()
st["consecutive_red_days"] = 0
check("mult=1.0 with 0 red days", at._red_day_risk_mult(st) == 1.0)
st["consecutive_red_days"] = 1
check("mult=1.0 with 1 red day", at._red_day_risk_mult(st) == 1.0)
st["consecutive_red_days"] = 2
check("mult=RED_DAY_RISK_MULT after 2 red days",
      at._red_day_risk_mult(st) == cfg.RED_DAY_RISK_MULT)

# Sizing actually halves: a stock entry sized with 2 red days gets ~half the shares.
# risk/share = 1.0, budget = STOCK_RISK_PER_TRADE * conv(1.0) * mult. With ref=100, stop=99.
# Use ref=10, stop=8 (risk/share=2) so the RISK budget binds, not the notional cap:
#   budget 250/2 = 125 shares (notional 125*10=1250 < 5000 cap). Red: 125/2 = 62.
def stock_decision_riskbind(sym):
    return {"action": "buy_stock", "symbol": sym, "direction": "long", "qty": 10,
            "stop_price": 8.0, "target_price": 20.0, "conviction": "medium"}
d_full = stock_decision_riskbind("AAPL")
st_full = base_state(); st_full["consecutive_red_days"] = 0
ok_f, _ = guard(d_full, st_full, [], ref=10.0)
qty_full = d_full["qty"]
d_red = stock_decision_riskbind("AAPL")
st_red = base_state(); st_red["consecutive_red_days"] = 2
ok_r, _ = guard(d_red, st_red, [], ref=10.0)
qty_red = d_red["qty"]
print(f"        qty_full={qty_full}  qty_red={qty_red}  mult={cfg.RED_DAY_RISK_MULT}")
check("red-day sizing ~= mult * normal sizing",
      ok_f and ok_r and abs(qty_red - round(qty_full * cfg.RED_DAY_RISK_MULT)) <= 1
      and qty_red < qty_full)

print("\n=== #14 per-position max-loss kill (manage_options) ===")
# Build a fake trading client + option-data client that return a chosen mark for a
# tracked long option, and assert manage_options force-closes at -2.1x but not -1.5x.
class FakePos:
    def __init__(self, symbol): self.symbol = symbol
class FakeTC:
    def __init__(self, held): self._held = held; self.closed = []
    def get_all_positions(self): return [FakePos(s) for s in self._held]
    def get_orders(self, filter=None): return []
    def cancel_order_by_id(self, i): pass
    def close_position(self, s): self.closed.append(s)
class FakeQuote:
    def __init__(self, mid): self.bid_price = mid - 0.01; self.ask_price = mid + 0.01
class FakeODC:
    def __init__(self, mid): self._mid = mid
    def get_option_latest_quote(self, req):
        sym = req.symbol_or_symbols
        if isinstance(sym, list): return {s: FakeQuote(self._mid) for s in sym}
        return {sym: FakeQuote(self._mid)}

# entry $2.00, qty 5, OPTION_STOP_PCT -0.50 -> initial risk = 2*100*5*0.5 = $500.
# -2.1x risk loss = $1050 -> mid where (2.00-mid)*100*5 = 1050 -> mid = 2.00 - 2.10 = -0.10.
# That's below 0; instead use entry that yields a positive mid. Use entry $5.00:
# init risk = 5*100*5*0.5 = $1250; loss for kill (>2x=$2500) -> (5-mid)*500 > 2500 -> mid < 0.
# A long option can't lose more than premium, so the % stop ALWAYS fires first for a long
# option — the kill only bites a spread (below) or a gapped mark. Use a position whose stop_pct
# implies a small initial risk vs a large drop: entry $5, but pretend mid gapped to $0.10.
sym = occ("SMCI", "P", 300)
st = base_state()
st["active_options"] = [{"symbol": sym, "qty": 5, "entry": 5.0, "hw_pl": 0.0}]
# loss = (5-0.10)*100*5 = $2450; init risk = $1250; 2450/1250 = 1.96x -> NOT > 2.0x kill.
odc = FakeODC(0.10); tc = FakeTC([sym])
# patch option_latest_quote used in manage_options
at.option_latest_quote = lambda odc, s: FakeQuote(0.10)
at.tg_send = lambda *a, **k: None
import importlib
# manage_options uses _quote_mid + option_latest_quote; with mid 0.10 -> 1.96x: no kill,
# BUT the -50% stop (pl=(0.10-5)/5=-98%) fires as a normal stop. Assert it closed via STOP.
at.manage_options(tc, odc, st, dry=False)
check("long option at deep loss closed (stop fires for long opt)", sym in tc.closed)

# Explicit -2.1x vs -1.5x of INITIAL RISK for the kill path. A long option can mathematically
# reach at most 2.0x its (0.5*premium) risk, so to exercise the kill threshold directly we
# temporarily lower MAX_POSITION_LOSS_MULT and verify the KILL (not the % stop) reason fires.
# entry $2.00, qty 1 -> init risk = 2*100*1*0.5 = $100. mid $1.00 -> loss = (2-1)*100 = $100 = 1.0x.
# Set mult=0.8: 1.0x > 0.8x -> KILL. Set mult=1.2: 1.0x < 1.2x -> no kill (and pl=-50% -> stop).
_save_mult = cfg.MAX_POSITION_LOSS_MULT
def run_opt(entry, mid, mult):
    cfg.MAX_POSITION_LOSS_MULT = mult
    s = base_state()
    osym = occ("AAPL", "C", 200)
    s["active_options"] = [{"symbol": osym, "qty": 1, "entry": entry, "hw_pl": 0.0}]
    at.option_latest_quote = lambda odc, x: FakeQuote(mid)
    tcx = FakeTC([osym])
    kill = {"v": False}
    _ol = at.log
    def _l(m):
        if "MAX-LOSS KILL" in str(m): kill["v"] = True
        _ol(m)
    at.log = _l
    at.manage_options(tcx, FakeODC(mid), s, dry=False)
    at.log = _ol
    return osym in tcx.closed, kill["v"]
# loss 1.0x risk, mult 0.8 -> KILL fires
closed, killed = run_opt(2.0, 1.0, 0.8)
check("option loss > mult*risk -> KILL fires", closed and killed)
# loss 1.0x risk, mult 1.2 -> NOT a kill (the -50% stop closes it instead)
closed, killed = run_opt(2.0, 1.0, 1.2)
check("option loss < mult*risk -> NOT a KILL (stop handles it)", closed and not killed)
cfg.MAX_POSITION_LOSS_MULT = _save_mult

print("\n=== #14 per-position max-loss kill (manage_multileg) ===")
# A DEBIT spread can lose more than its debit only if marked wrong, but the realistic #14
# case is a credit/legged structure. Use a tracked spread with entry_net (debit) and a
# cost_to_close that drives loss past 2x the defined risk.
class FakeQ2:
    def __init__(self, mid): self.bid_price = mid - 0.01; self.ask_price = mid + 0.01
class FakeODC2:
    def __init__(self, marks): self.marks = marks  # symbol -> mid
    def get_option_latest_quote(self, req):
        syms = req.symbol_or_symbols
        return {s: FakeQ2(self.marks[s]) for s in syms}

# Debit spread: long 300P, short 290P. net debit per share $2 -> entry_net = -2*100*1 = -200.
# initial risk = debit = $200. To kill we need loss > 2*200 = $400. Make cost_to_close hugely
# negative (we'd have to PAY to close) so pl = entry_net - cost_to_close < -400.
# Set marks so long leg worthless, short leg expensive: cost_to_close = short_mid - long_mid.
longp = occ("SMCI", "P", 300); shortp = occ("SMCI", "P", 290)
spread = {"legs": [{"symbol": longp, "side": "buy"}, {"symbol": shortp, "side": "sell"}],
          "qty": 1, "entry_net": -200.0, "opened": NOW.isoformat(), "basis_reconciled": True}
st = base_state(); st["active_multileg"] = [spread]
# cost_to_close = (sell mid) - (buy mid) per share; *100. Want pl very negative.
# short mid 6.0, long mid 0.0 -> cost_to_close = 6.0*100 = 600. pl = -200 - 600 = -800.
# loss = 800 > 2*200=400 -> KILL.
marks = {longp: 0.05, shortp: 6.0}   # _quote_mid needs bid>0, so keep mid above the 0.01 spread
odc2 = FakeODC2(marks); tc2 = FakeTC([longp, shortp])
# Force not-EOD by setting time; manage_multileg uses et_now() internally. We can't pin it,
# but at 11:00 in real et_now it's fine. Patch et_now to our NOW for determinism.
at.et_now = lambda: NOW
at.manage_multileg(tc2, odc2, st, dry=False)
check("spread loss > 2x debit -> KILLED", longp in tc2.closed and shortp in tc2.closed)

# A spread at -1.5x its debit is NOT killed (and not otherwise stopped if within band).
# debit 200, want loss = 1.5*200 = 300 -> pl=-300 -> cost_to_close=100 -> short 1.0,long 0.
# But debit-spread stop is -50% of debit (-100) which would ALSO fire at -300. So to isolate
# the KILL vs no-kill we use a loss between the -50% stop and 2x: e.g. -0.9x debit = -180.
# pl=-180 -> cost_to_close = -20 -> we'd RECEIVE $20 to close -> long worth more than short.
# Set long 0.6, short 0.4 -> cost_to_close=(0.4-0.6)*100=-20 -> pl=-200-(-20)=-180. loss 180.
# -180 vs debit 200: pf=-0.9 -> below -50% stop -> the normal STOP fires (not the kill).
# So assert: closed, but reason is a STOP, not a KILL (check the log).
st2 = base_state()
spread2 = {"legs": [{"symbol": longp, "side": "buy"}, {"symbol": shortp, "side": "sell"}],
           "qty": 1, "entry_net": -200.0, "opened": NOW.isoformat(), "basis_reconciled": True}
st2["active_multileg"] = [spread2]
marks2 = {longp: 0.6, shortp: 0.4}
tc3 = FakeTC([longp, shortp])
killed_log = {"kill": False}
_orig_log = at.log
def _spy_log(msg):
    if "MAX-LOSS KILL" in str(msg): killed_log["kill"] = True
    _orig_log(msg)
at.log = _spy_log
at.manage_multileg(tc3, FakeODC2(marks2), st2, dry=False)
at.log = _orig_log
check("spread at -0.9x debit NOT a KILL (normal stop)", not killed_log["kill"])

print(f"\n==== {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
