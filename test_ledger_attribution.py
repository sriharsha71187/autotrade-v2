"""Cross-day ledger strategy attribution: a long opened on day X but CLOSED on a later
day Y must still attribute to 'stock_long', not be sign-inferred as 'stock_short' from the
closing SELL. Pins the _strategy_recent_label lookback fix."""
import json, tempfile, pathlib
import config as cfg
import autotrade as A

failures = []
def check(name, cond):
    print(f"{'PASS' if cond else 'FAIL'}: {name}")
    if not cond:
        failures.append(name)

# Redirect SNAPSHOT_DIR to a temp dir.
tmp = pathlib.Path(tempfile.mkdtemp())
cfg.SNAPSHOT_DIR = tmp

today = A.et_now().date()
day_open = (today - A.timedelta(days=3)).isoformat()   # MU long opened 3 days ago
day_close = today.isoformat()                          # ...closed today

def write_submit(day, decision):
    rec = {"result": {"status": "submitted"}, "decision": decision}
    (tmp / f"{day}.jsonl").write_text(json.dumps(rec) + "\n")

# MU was a LONG stock entry on day_open; nothing about MU in day_close's log.
write_submit(day_open, {"action": "buy_stock", "symbol": "MU", "direction": "long"})
write_submit(day_close, {"action": "buy_stock", "symbol": "AVGO", "direction": "long"})

# The closing SELL of MU lands on day_close (signed negative). Pre-fix this returned
# 'stock_short' (day_close map has no MU -> sign inference on the sell). Post-fix the
# lookback finds day_open's stock_long.
label = A._strategy_for_open("MU", day_close, signed=-10.0)
check(f"cross-day close of MU long -> stock_long (got {label})", label == "stock_long")

# Same-day case still correct (AVGO decided today).
check("same-day AVGO long -> stock_long",
      A._strategy_for_open("AVGO", day_close, signed=10.0) == "stock_long")

# A genuine short with no prior decision still infers stock_short.
check("unknown-symbol sell with no decision -> stock_short",
      A._strategy_for_open("ZZZZ", day_close, signed=-5.0) == "stock_short")

# A bare long option leg with no decision -> long_option.
check("bare option leg, no decision -> long_option",
      A._strategy_for_open("MU260618C00100000", day_close, signed=1.0) == "long_option")

# Lookback window is bounded: a decision older than the window is NOT found.
old = (today - A.timedelta(days=cfg.LEDGER_STRATEGY_LOOKBACK_DAYS + 3)).isoformat()
write_submit(old, {"action": "buy_stock", "symbol": "NVDA", "direction": "long"})
check("decision older than lookback window -> falls back to sign inference",
      A._strategy_for_open("NVDA", day_close, signed=-7.0) == "stock_short")

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    raise SystemExit(1)
print("ALL LEDGER-ATTRIBUTION TESTS PASSED")
