"""Behavioral tripwire: alert-only flags for (A) setups-but-no-trades and (B) a stuck
repeated block reason. Verifies tallying, the two fire conditions, once-per-day latching,
and that it's inert when the flag is off and never raises."""
import config as cfg
import autotrade as A

# Isolation: log to a temp file, never the production log.
import tempfile, pathlib
cfg.LOG_FILE = pathlib.Path(tempfile.mkdtemp()) / "test.log"

failures = []
def check(name, cond):
    print(f"{'PASS' if cond else 'FAIL'}: {name}")
    if not cond:
        failures.append(name)

# Capture tg_send calls instead of hitting Telegram.
sent = []
A.tg_send = lambda m: sent.append(m)
now = A.et_now()

cfg.BEHAVIORAL_TRIPWIRE_ENABLED = True
cfg.TRIPWIRE_NOTRADE_CYCLES = 5
cfg.TRIPWIRE_REPEAT_BLOCKS = 3

# ---- flag OFF -> fully inert ----
cfg.BEHAVIORAL_TRIPWIRE_ENABLED = False
st = {}
A.behavioral_tripwire(st, {"status": "blocked", "reason": "anti-chase: too extended"}, now)
check("OFF: no tracking, no alert", "behavior_track" not in st and not sent)
cfg.BEHAVIORAL_TRIPWIRE_ENABLED = True

# ---- A) setups but no trades ----
st = {}
for i in range(4):
    A.behavioral_tripwire(st, {"status": "hold", "action": "hold"}, now)
check("A: 4 qualifying holds, no alert yet", not any("ZERO entries" in m for m in sent))
A.behavioral_tripwire(st, {"status": "hold", "action": "hold"}, now)   # 5th -> fires
check("A: 5th qualifying cycle, 0 entries -> alert", any("ZERO entries" in m for m in sent))
n_a = sum("ZERO entries" in m for m in sent)
A.behavioral_tripwire(st, {"status": "hold", "action": "hold"}, now)   # 6th -> no re-alert
check("A: alert latched once/day", sum("ZERO entries" in m for m in sent) == n_a)
check("A: counters tracked", st["behavior_track"]["qualified"] == 6
      and st["behavior_track"]["entries"] == 0)

# ---- an entry suppresses tripwire A entirely ----
sent.clear()
st2 = {}
A.behavioral_tripwire(st2, {"status": "submitted", "action": "buy_stock"}, now)
for i in range(8):
    A.behavioral_tripwire(st2, {"status": "hold", "action": "hold"}, now)
check("A: any entry today -> no-trade tripwire never fires",
      not any("ZERO entries" in m for m in sent) and st2["behavior_track"]["entries"] == 1)

# ---- B) stuck repeated block reason (collapsed to the rule) ----
sent.clear()
st3 = {}
for i in range(3):
    A.behavioral_tripwire(
        st3, {"status": "blocked", "reason": f"anti-chase: {i} pts over HOD"}, now)
check("B: same rule 'anti-chase' 3x -> stuck-block alert",
      any("anti-chase" in m and "blocked entries" in m for m in sent))
check("B: variable tail collapsed to one rule key",
      st3["behavior_track"]["blocks"].get("anti-chase") == 3)
nb = sum("blocked entries" in m for m in sent)
A.behavioral_tripwire(st3, {"status": "blocked", "reason": "anti-chase: more"}, now)
check("B: stuck-block latched once/day per reason",
      sum("blocked entries" in m for m in sent) == nb)

# distinct reasons tally separately, don't cross-trip
st4 = {}
for r in ["IV-rank too high", "IV-rank too high", "daily halt"]:
    A.behavioral_tripwire(st4, {"status": "blocked", "reason": r}, now)
check("B: distinct reasons counted separately",
      st4["behavior_track"]["blocks"].get("IV-rank too high") == 2
      and st4["behavior_track"]["blocks"].get("daily halt") == 1)
# leading TICKER token is stripped so the SAME rule across symbols tallies as one
st5b = {}
for sym in ("LRCX", "ASML", "AMD"):
    A.behavioral_tripwire(
        st5b, {"status": "blocked", "reason": f"{sym} is a managed-book holding (off-limits to intraday)"}, now)
check("B: ticker stripped — LRCX/ASML/AMD managed-book collapse to one key (3)",
      st5b["behavior_track"]["blocks"].get("is a managed-book holding") == 3)
check("B: lowercase/hyphenated rule NOT over-collapsed",
      "IV-rank too high" in st4["behavior_track"]["blocks"])

# ---- never raises on a malformed result ----
st5 = {}
A.behavioral_tripwire(st5, None, now)
A.behavioral_tripwire(st5, {"status": "blocked"}, now)   # no reason key
check("robust: malformed result doesn't raise", True)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    raise SystemExit(1)
print("ALL BEHAVIORAL-TRIPWIRE TESTS PASSED")
