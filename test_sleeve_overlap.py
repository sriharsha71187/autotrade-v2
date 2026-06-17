"""Sleeve-overlap: the intraday engine may take a LONG stock entry in a name held ONLY by
the growth sleeve (a minor hold), instead of being blocked by the managed-book guard — and
the intraday slice is flattened at EOD without touching the sleeve's piece. Flag-OFF default."""
import tempfile, pathlib
import config as cfg
import autotrade as A

cfg.LOG_FILE = pathlib.Path(tempfile.mkdtemp()) / "test.log"
A.tg_send = lambda *a, **k: None

failures = []
def check(name, cond):
    print(f"{'PASS' if cond else 'FAIL'}: {name}")
    if not cond:
        failures.append(name)

now = A.et_now()
acct = {"equity": 100000.0, "cash": 50000.0, "last_equity": 100000.0}
cfg.REGIME_ENGINE_ENABLED = False   # skip the regime gate so we isolate the managed-book block

def blocked_as_managed(decision, book_syms, overlap):
    ok, reason = A.passes_guardrails(decision, {}, acct, now,
                                     book_symbols=set(book_syms), sleeve_overlap_ok=set(overlap))
    return (not ok) and ("managed-book holding" in (reason or ""))

LONG = {"action": "buy_stock", "symbol": "LRCX", "direction": "long",
        "qty": 10, "stop_price": 380.0, "target_price": 410.0}
SHORT = {**LONG, "direction": "short"}
OPT = {"action": "buy_option", "symbol": "LRCX", "option_symbol": "LRCX260618C00400000", "qty": 1}

# ---- guardrail relaxation ----
cfg.SLEEVE_OVERLAP_ENABLED = False
check("OFF: LONG on sleeve name still BLOCKED (inert)",
      blocked_as_managed(LONG, ["LRCX"], ["LRCX"]))

cfg.SLEEVE_OVERLAP_ENABLED = True
check("ON: LONG on sleeve-overlap name is NOT managed-book-blocked",
      not blocked_as_managed(LONG, ["LRCX"], ["LRCX"]))
check("ON: SHORT on sleeve-overlap name STILL blocked (long-only)",
      blocked_as_managed(SHORT, ["LRCX"], ["LRCX"]))
check("ON: buy_OPTION on sleeve-overlap name STILL blocked (stock-only)",
      blocked_as_managed(OPT, ["LRCX"], ["LRCX"]))
check("ON: book name NOT in overlap set (e.g. pairs/conviction) STILL blocked",
      blocked_as_managed(LONG, ["LRCX"], []))           # in book_symbols, not overlap-eligible
check("ON: non-book name unaffected by this block",
      not blocked_as_managed({**LONG, "symbol": "ZZZZ"}, ["LRCX"], ["LRCX"]))

# ---- cycle eligibility: sleeve-only + minor cost; excludes other-book + big holds ----
# (replicate the cycle's sleeve_overlap_ok computation inline against the real config)
def overlap_ok(sleeve, other_books, already=None):
    cfg.SLEEVE_OVERLAP_ENABLED = True
    ok = set()
    other = set(other_books); already = set(already or [])
    for h in sleeve:
        s = h.get("symbol"); cost = abs(h.get("qty",0)*h.get("entry",0))
        if s and s not in other and s not in already and cost < cfg.SLEEVE_OVERLAP_MAX_SLEEVE_COST:
            ok.add(s)
    return ok
sleeve = [{"symbol": "LRCX", "qty": 0.293, "entry": 334.0},   # ~$98 minor -> eligible
          {"symbol": "BIGX", "qty": 5.0, "entry": 400.0}]      # ~$2000 -> too big, excluded
check("eligible: minor sleeve-only LRCX is overlap-ok", "LRCX" in overlap_ok(sleeve, set()))
check("excluded: large sleeve hold BIGX not overlap-ok", "BIGX" not in overlap_ok(sleeve, set()))
check("excluded: sleeve name ALSO in another book (pairs) not overlap-ok",
      "LRCX" not in overlap_ok(sleeve, {"LRCX"}))
check("brake: name already overlapped today is NOT eligible again (no re-stack runaway)",
      "LRCX" not in overlap_ok(sleeve, set(), already={"LRCX"}))

# ---- EOD overlap flatten: sells the slice, preserves the sleeve piece ----
class FakePos:
    def __init__(self, symbol, qty): self.symbol=symbol; self.qty=str(qty)
class FakeTC:
    def __init__(self, held): self._h=held; self.sold=[]
    def get_all_positions(self): return [FakePos(s,q) for s,q in self._h.items()]
    def get_orders(self, filter=None): return []
    def cancel_order_by_id(self, i): pass
    def submit_order(self, order_data=None):
        self.sold.append((order_data.symbol, order_data.qty, str(order_data.side)))
        class O: id="x"
        return O()

# broker holds 10.293 LRCX = 10 intraday slice + 0.293 sleeve; flatten must sell 10, keep 0.293
st = {"intraday_overlap": {"LRCX": 10},
      "growth_sleeve": [{"symbol": "LRCX", "qty": 0.293, "entry": 334.0}]}
tc = FakeTC({"LRCX": 10.293})
A._flatten_overlap_slices(tc, st, dry=False)
check("EOD: sold exactly the 10-share slice", tc.sold and tc.sold[0][0]=="LRCX" and tc.sold[0][1]==10)
check("EOD: SELL side", tc.sold and "SELL" in tc.sold[0][2].upper())
check("EOD: tracking cleared after flatten", "LRCX" not in st["intraday_overlap"])

# if the slice already closed (broker back to sleeve only), sell NOTHING and clear
st2 = {"intraday_overlap": {"LRCX": 10},
       "growth_sleeve": [{"symbol": "LRCX", "qty": 0.293, "entry": 334.0}]}
tc2 = FakeTC({"LRCX": 0.293})       # stop/target already took the 10
A._flatten_overlap_slices(tc2, st2, dry=False)
check("EOD: nothing to sell when slice already gone", not tc2.sold)
check("EOD: tracking cleared even when nothing sold", "LRCX" not in st2["intraday_overlap"])

# never sells into the sleeve: broker 5.293 but slice recorded 10 -> sell only excess 5
st3 = {"intraday_overlap": {"LRCX": 10},
       "growth_sleeve": [{"symbol": "LRCX", "qty": 0.293, "entry": 334.0}]}
tc3 = FakeTC({"LRCX": 5.293})
A._flatten_overlap_slices(tc3, st3, dry=False)
check("EOD: sells only the excess over sleeve (5), never into the sleeve piece",
      tc3.sold and tc3.sold[0][1]==5)

cfg.SLEEVE_OVERLAP_ENABLED = False   # leave it OFF (deployed/staged state)
print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    raise SystemExit(1)
print("ALL SLEEVE-OVERLAP TESTS PASSED")
