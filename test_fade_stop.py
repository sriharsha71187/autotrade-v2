"""Sector-confluence fade / thesis-break stop (fade_break_reason + manage path wiring).

The fade stop cuts a directional position ONLY when the NAME is adverse off entry AND the
NAME'S SECTOR (last cycle's regime.sector_trends, % vs prior close) is rolling against it.
A price-ONLY version backtested as negative-EV (whipsawed 26-46% of recover days); the sector
confluence filter (recalibrated to the engine metric) cuts green false-cut to ~13% — see
/tmp/bt_fade_sector_recal.py. These tests pin the decision logic, the fail-safes, and that the
whole thing is INERT when FADE_STOP_ENABLED is off (the deployed/staged state)."""
import config as cfg
import autotrade as A

# Isolation: never write to the production log or fire real Telegram from a test.
import tempfile, pathlib
cfg.LOG_FILE = pathlib.Path(tempfile.mkdtemp()) / "test.log"
A.tg_send = lambda *a, **k: None

failures = []
def check(name, cond):
    print(f"{'PASS' if cond else 'FAIL'}: {name}")
    if not cond:
        failures.append(name)

# ---- 1. inert when the flag is OFF (the staged/default state) ---------------------------
cfg.FADE_STOP_ENABLED = False
check("OFF: long deeply adverse + sector crashing -> None (inert)",
      A.fade_break_reason(1, 100.0, 90.0, -5.0) is None)

# ---- 2. confluence decision logic (flag ON) ---------------------------------------------
cfg.FADE_STOP_ENABLED = True
cfg.FADE_STOP_NAME_ADVERSE_PCT = -0.006     # name must be <= -0.6% off entry
cfg.FADE_STOP_SECTOR_RISKOFF_PCT = -1.5     # AND sector day_pct <= -1.5%

check("CUT: long -1% off entry AND sector -2% (both confirm)",
      bool(A.fade_break_reason(1, 100.0, 99.0, -2.0)))
check("HOLD: long -1% off entry but sector only -0.5% (sector not risk-off)",
      A.fade_break_reason(1, 100.0, 99.0, -0.5) is None)
check("HOLD: sector -3% but name still GREEN +0.5% (never fade a strong name)",
      A.fade_break_reason(1, 100.0, 100.5, -3.0) is None)
check("HOLD: name only -0.3% off entry (not past -0.6%) though sector -3%",
      A.fade_break_reason(1, 100.0, 99.7, -3.0) is None)
check("CUT: name -0.7% off entry AND sector at threshold -1.5%",
      bool(A.fade_break_reason(1, 100.0, 99.3, -1.5)))
check("FAIL-SAFE: no sector reading (None) -> None",
      A.fade_break_reason(1, 100.0, 80.0, None) is None)

# short / put thesis: 'adverse' = underlying UP; 'sector against' = sector RISK-ON
check("CUT (short): underlying +1% AND sector +2% (risk-on against the short)",
      bool(A.fade_break_reason(-1, 100.0, 101.0, 2.0)))
check("HOLD (short): underlying +1% but sector -2% (risk-off still helps the short)",
      A.fade_break_reason(-1, 100.0, 101.0, -2.0) is None)

# ---- 3. guards -------------------------------------------------------------------------
check("guard: u_entry<=0 -> None", A.fade_break_reason(1, 0, 100, -3.0) is None)
check("guard: u_now None -> None", A.fade_break_reason(1, 100, None, -3.0) is None)
check("guard: direction 0 -> None", A.fade_break_reason(0, 100, 90, -3.0) is None)

# ---- 4. sector lookup from state (freshness gate) --------------------------------------
today = A.et_now().strftime("%Y-%m-%d")
fresh = {"sector_trends": {"day": today, "trends": {"semis_ai": -2.3}}}
check("sector lookup: fresh map, AMD->semis_ai -> -2.3",
      A._fade_sector_pct(fresh, "AMD") == -2.3)
stale = {"sector_trends": {"day": "2000-01-01", "trends": {"semis_ai": -2.3}}}
check("sector lookup: STALE day -> None (no cut on yesterday's map)",
      A._fade_sector_pct(stale, "AMD") is None)
check("sector lookup: no state -> None", A._fade_sector_pct({}, "AMD") is None)
check("sector lookup: unmapped name ('other') -> None",
      A._fade_sector_pct(fresh, "ZZZZ") is None)

# ---- 5. manage_stops integration: confluence cut closes; non-confluence holds ----------
class FakePos:
    def __init__(self, symbol, entry, cur, side="long"):
        self.symbol = symbol; self.avg_entry_price = entry; self.current_price = cur
        self.side = side; self.asset_class = "us_equity"
class FakeTC:
    def __init__(self, positions): self._pos = positions; self.closed = []
    def get_all_positions(self): return self._pos
    def get_orders(self, filter=None): return []
    def cancel_order_by_id(self, i): pass
    def close_position(self, s):
        self.closed.append(s)
        self._pos = [p for p in self._pos if p.symbol != s]   # so verify-retry sees it flat

cfg.FADE_STOP_ENABLED = True
cfg.FADE_STOP_APPLY_STOCKS = True

# AMD long -1.5% off entry, semis_ai sector -2.0% -> should FADE-CUT
st = {"sector_trends": {"day": today, "trends": {"semis_ai": -2.0}}}
tc = FakeTC([FakePos("AMD", 100.0, 98.5)])
A.manage_stops(tc, dry=False, skip=set(), state=st)
check("manage_stops: confluence -> AMD closed", "AMD" in tc.closed)

# AMD long -1.5% off entry but sector only -0.4% -> HOLD (no cut)
st2 = {"sector_trends": {"day": today, "trends": {"semis_ai": -0.4}}}
tc2 = FakeTC([FakePos("AMD", 100.0, 98.5)])
A.manage_stops(tc2, dry=False, skip=set(), state=st2)
check("manage_stops: sector not risk-off -> AMD held", "AMD" not in tc2.closed)

# flag OFF -> never cuts even with confluence present
cfg.FADE_STOP_ENABLED = False
tc3 = FakeTC([FakePos("AMD", 100.0, 98.5)])
A.manage_stops(tc3, dry=False, skip=set(), state=st)
check("manage_stops: flag OFF -> AMD held (inert)", "AMD" not in tc3.closed)
cfg.FADE_STOP_ENABLED = True

# skip set (e.g. growth-sleeve hold) -> excluded even on confluence
tc4 = FakeTC([FakePos("AMD", 100.0, 98.5)])
A.manage_stops(tc4, dry=False, skip={"AMD"}, state=st)
check("manage_stops: shielded (skip) name -> not cut", "AMD" not in tc4.closed)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    raise SystemExit(1)
print("ALL FADE-STOP TESTS PASSED")
