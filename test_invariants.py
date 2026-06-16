import sys, os, types, datetime

sys.path.insert(0, "/Users/nirvaan/autotrade")

import config as cfg
# Redirect file outputs so the test never touches real log/state.
cfg.LOG_FILE = "/tmp/_inv_test.log"
cfg.STATE_FILE = "/tmp/_inv_test_state.json"
cfg.INVARIANT_CHECKS_ENABLED = True
cfg.INVARIANT_DAY_PL_TOL = 1000.0

import autotrade as at

# ---- stubs / counters --------------------------------------------------------
TG = []
LOGS = []
at.tg_send = lambda msg: TG.append(msg)
at.log = lambda msg: LOGS.append(msg)

NOW = datetime.datetime(2026, 6, 16, 11, 0, 0)

def base_acct(equity=100000.0, last_equity=100000.0):
    return {"equity": equity, "last_equity": last_equity, "cash": 50000.0,
            "buying_power": 100000.0, "positions_value": 50000.0, "daytrade_count": 0}

def base_state():
    return {"active_options": [], "active_multileg": [], "open_lots": {},
            "processed_fills": [], "closed_trades": [], "halted": False,
            "start_equity": 100000.0}

def keys(viols):
    return {k for k, _ in viols}

def reset():
    TG.clear(); LOGS.clear()

OK = []
def check(name, cond):
    OK.append((name, cond))
    print(("PASS" if cond else "FAIL"), name)

# ===== 1. SINGLE_BOOK_OWNERSHIP ==============================================
reset()
bh = {"growth": {"LRCX", "NVDA"}, "pairs": {"LRCX", "KLAC"}, "tail": set(),
      "earnings": set(), "overnight": set()}
v = at.check_invariants(base_state(), base_acct(), [], bh, 0.0)
check("SINGLE_BOOK fires on LRCX collision", "SINGLE_BOOK_OWNERSHIP" in keys(v))
check("SINGLE_BOOK msg names LRCX + both books",
      any("LRCX" in m and "growth" in m and "pairs" in m
          for k, m in v if k == "SINGLE_BOOK_OWNERSHIP"))

bh_disjoint = {"growth": {"NVDA"}, "pairs": {"KLAC"}, "tail": {"SPY260101P00400000"},
               "earnings": set(), "overnight": set()}
v = at.check_invariants(base_state(), base_acct(), [], bh_disjoint, 0.0)
check("SINGLE_BOOK quiet when disjoint", "SINGLE_BOOK_OWNERSHIP" not in keys(v))

# ===== 2. DAY_PL_RECONCILE ===================================================
acct = base_acct(equity=99800.0, last_equity=100000.0)   # broker day pl = -200
v = at.check_invariants(base_state(), acct, [], {}, -3500.0)
check("DAY_PL fires: -3500 vs broker -200", "DAY_PL_RECONCILE" in keys(v))

v = at.check_invariants(base_state(), acct, [], {}, -700.0)   # within 1000 of -200
check("DAY_PL quiet within tol (-700 vs -200)", "DAY_PL_RECONCILE" not in keys(v))

# ===== 3. POSITION_TRACKED ===================================================
# 3a. fires: broker stock in no book / active_* and unbracketed
pos = [{"symbol": "TSLA"}]
v = at.check_invariants(base_state(), base_acct(), pos, {"growth": {"NVDA"}}, 0.0,
                        bracketed={"AAPL"})
check("POSITION_TRACKED fires on untracked unbracketed stock",
      "POSITION_TRACKED" in keys(v) and any("TSLA" in m for k, m in v if k == "POSITION_TRACKED"))

# 3b. quiet: position IS in a book
v = at.check_invariants(base_state(), base_acct(), [{"symbol": "TSLA"}],
                        {"growth": {"TSLA"}}, 0.0, bracketed={"AAPL"})
check("POSITION_TRACKED quiet when in a book", "POSITION_TRACKED" not in keys(v))

# 3c. quiet: option leg IS in active_options
occ = "TSLA260116C00250000"
st = base_state(); st["active_options"] = [{"symbol": occ}]
v = at.check_invariants(st, base_acct(), [{"symbol": occ}], {}, 0.0, bracketed=set())
check("POSITION_TRACKED quiet when in active_options", "POSITION_TRACKED" not in keys(v))

# 3c-ii. quiet: multileg leg
st = base_state(); st["active_multileg"] = [{"legs": [{"symbol": occ}]}]
v = at.check_invariants(st, base_acct(), [{"symbol": occ}], {}, 0.0, bracketed=set())
check("POSITION_TRACKED quiet when in active_multileg leg", "POSITION_TRACKED" not in keys(v))

# 3d. quiet: bracketed stock
v = at.check_invariants(base_state(), base_acct(), [{"symbol": "TSLA"}], {}, 0.0,
                        bracketed={"TSLA"})
check("POSITION_TRACKED quiet when stock is bracketed", "POSITION_TRACKED" not in keys(v))

# 3e. conservative: NO bracket info -> do NOT flag a stock (avoid false positive)
v = at.check_invariants(base_state(), base_acct(), [{"symbol": "TSLA"}], {}, 0.0,
                        bracketed=set())
check("POSITION_TRACKED conservative: no bracket info -> stock NOT flagged",
      "POSITION_TRACKED" not in keys(v))

# 3f. orphan OPTION always flagged (no bracket concept)
v = at.check_invariants(base_state(), base_acct(), [{"symbol": occ}], {}, 0.0,
                        bracketed=set())
check("POSITION_TRACKED flags orphan option", "POSITION_TRACKED" in keys(v))

# ===== 4. LEDGER_BOUNDS ======================================================
st = base_state(); st["open_lots"] = {str(i): 1 for i in range(201)}
v = at.check_invariants(st, base_acct(), [], {}, 0.0)
check("LEDGER_BOUNDS fires on open_lots>200", "LEDGER_BOUNDS" in keys(v))

st = base_state(); st["processed_fills"] = list(range(1501))
v = at.check_invariants(st, base_acct(), [], {}, 0.0)
check("LEDGER_BOUNDS fires on processed_fills>1500", "LEDGER_BOUNDS" in keys(v))

st = base_state(); st["closed_trades"] = list(range(801))
v = at.check_invariants(st, base_acct(), [], {}, 0.0)
check("LEDGER_BOUNDS fires on closed_trades>800", "LEDGER_BOUNDS" in keys(v))

v = at.check_invariants(base_state(), base_acct(equity=0.0), [], {}, 0.0)
check("LEDGER_BOUNDS fires on equity<=0", "LEDGER_BOUNDS" in keys(v))

v = at.check_invariants(base_state(), base_acct(), [], {}, 0.0)
check("LEDGER_BOUNDS quiet on normal state", "LEDGER_BOUNDS" not in keys(v))

# ===== 5. HALT_SANITY ========================================================
st = base_state(); st["halted"] = True
# broker day pl above limit (limit is negative, e.g. -300); equity flat -> 0 > -300
v = at.check_invariants(st, base_acct(), [], {}, 0.0)
check("HALT_SANITY fires when halted but broker day pl above limit",
      "HALT_SANITY" in keys(v))

# not halted -> quiet
v = at.check_invariants(base_state(), base_acct(), [], {}, 0.0)
check("HALT_SANITY quiet when not halted", "HALT_SANITY" not in keys(v))

# halted AND legitimately below limit -> quiet (legit post-loss halt stays)
st = base_state(); st["halted"] = True
# day pl genuinely below the loss limit -> a legit halt, must stay quiet
_below = cfg.DAILY_LOSS_HALT - 500.0
acct = base_acct(equity=100000.0 + _below, last_equity=100000.0)
v = at.check_invariants(st, acct, [], {}, _below)
check("HALT_SANITY quiet when halted AND legitimately below limit",
      "HALT_SANITY" not in keys(v))

# ===== 6. RATE-LIMIT: 3 cycles -> tg once, log 3x ============================
reset()
st = base_state()
bh = {"growth": {"LRCX"}, "pairs": {"LRCX"}, "tail": set(), "earnings": set(),
      "overnight": set()}
for _ in range(3):
    at.run_invariant_checks(st, base_acct(), [], bh, 0.0, NOW)
sb_logs = [l for l in LOGS if "SINGLE_BOOK_OWNERSHIP" in l]
sb_tg = [t for t in TG if "SINGLE_BOOK_OWNERSHIP" in t]
check("RATE-LIMIT: log called 3x", len(sb_logs) == 3)
check("RATE-LIMIT: tg_send called once", len(sb_tg) == 1)

# 6b. daily reset: a new day re-alerts
reset()
st = {"active_options": [], "active_multileg": [], "open_lots": {}, "processed_fills": [],
      "closed_trades": [], "halted": False, "start_equity": 100000.0,
      "invariant_alerted": {"2026-06-15": ["SINGLE_BOOK_OWNERSHIP"]}}
at.run_invariant_checks(st, base_acct(), [], bh, 0.0, NOW)
check("RATE-LIMIT: new day re-alerts (prior-day latch dropped)",
      len([t for t in TG if "SINGLE_BOOK_OWNERSHIP" in t]) == 1)
check("RATE-LIMIT: latch reset to today only",
      list(st["invariant_alerted"].keys()) == ["2026-06-16"])

# ===== 7. ALERT-ONLY: no state mutation by check_invariants ===================
st = base_state()
snapshot = dict(st)
at.check_invariants(st, base_acct(), [{"symbol": "TSLA"}], bh, -9999.0, bracketed=set())
check("check_invariants does NOT mutate state", st == snapshot)

# ===== 8. try/except: an invariant bug can't crash run_invariant_checks =======
reset()
# acct missing 'equity' would raise inside check_invariants -> must be swallowed
try:
    at.run_invariant_checks(base_state(), {}, [], {}, 0.0, NOW)
    crashed = False
except Exception:
    crashed = True
check("run_invariant_checks swallows internal errors", not crashed)

# ===== summary ===============================================================
print()
failed = [n for n, c in OK if not c]
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print(f"ALL {len(OK)} CHECKS GREEN")
