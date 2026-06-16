import sys, os, tempfile
sys.path.insert(0, "/Users/nirvaan/autotrade")
import config as cfg
cfg.DAILY_HALT_CONFIRM_CYCLES = 2

# Replicate the run_cycle halt-debounce branch against a state dict.
def step(state, daily_pl, halted_already=False, override=False):
    latched = False
    if daily_pl > cfg.DAILY_LOSS_HALT:
        state["halt_breach_count"] = 0
    if daily_pl <= cfg.DAILY_LOSS_HALT and not halted_already and not override:
        state["halt_breach_count"] = int(state.get("halt_breach_count", 0)) + 1
        if state["halt_breach_count"] >= cfg.DAILY_HALT_CONFIRM_CYCLES:
            latched = True
    return latched

HALT = cfg.DAILY_LOSS_HALT  # -300 base
passed = 0; failed = 0
def chk(name, cond):
    global passed, failed
    print(("PASS " if cond else "FAIL ")+name); passed += cond; failed += (not cond)

# 1) Single transient breach (the 6/16 phantom) -> NOT latched
s = {}
chk("single transient breach does NOT latch", step(s, HALT-3000) is False)
# 2) ...and if next cycle recovers (real pl positive), counter resets, still no latch
chk("recovery after transient does NOT latch", step(s, +50) is False and s["halt_breach_count"]==0)
# 3) Two CONSECUTIVE real breaches -> latches on the 2nd
s2 = {}
first = step(s2, HALT-100); second = step(s2, HALT-100)
chk("first real breach pends (no latch)", first is False)
chk("second consecutive breach LATCHES", second is True)
# 4) breach, recover, breach -> does NOT latch (not consecutive)
s3 = {}
step(s3, HALT-100); step(s3, +10); third = step(s3, HALT-100)
chk("non-consecutive breaches do NOT latch", third is False)
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
