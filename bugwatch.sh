#!/bin/bash
# bugwatch.sh — first-30-min trading-cycle health probe for AutoTrade.
# Read-only. Surfaces the 3 known bug signatures (#5 orphaned spread, #6 null-symbol
# attribution, #7 partial-flatten remnant) + any NEW tracebacks/cycle errors since a
# baseline, so the monitoring session can decide whether to auto-fix. Filters the
# benign yfinance "failed download / possibly delisted" upstream noise.
#
# Usage:
#   bugwatch.sh baseline   # snapshot current error/traceback counts -> /tmp/bugwatch_base
#   bugwatch.sh check      # print health probe vs baseline

HOME_DIR="$HOME"
MAIN_LOG="$HOME_DIR/autotrade.log"
ERR_LOG="$HOME_DIR/autotrade_error.log"
STATE="$HOME_DIR/autotrade_state.json"
SNAPDIR="$HOME_DIR/autotrade_snapshots"
BASE="/tmp/bugwatch_base"

NOISE='Failed download|possibly delisted|YFRateLimit|peer closed|Retrying|urllib3'

tb_count() { grep -hcE "Traceback \(most recent call last\)" "$MAIN_LOG" "$ERR_LOG" 2>/dev/null | paste -sd+ - | bc; }

if [ "$1" = "baseline" ]; then
  echo "$(tb_count)" > "$BASE"
  echo "baseline traceback count: $(cat "$BASE")"
  exit 0
fi

echo "===== BUGWATCH $(date -u '+%Y-%m-%d %H:%M:%S UTC') ====="
ET_H=$(( (10#$(date -u +%H) + 24 - 4) % 24 ))   # ET = UTC-4 (June DST); 10# forces base-10 (avoids 08/09 octal error)
echo "approx ET hour: ${ET_H}:$(date -u +%M)   (market 9:30-16:00 ET)"

echo
echo "--- NEW tracebacks since baseline ---"
BASE_TB=$(cat "$BASE" 2>/dev/null || echo 0)
NOW_TB=$(tb_count)
echo "baseline=$BASE_TB  now=$NOW_TB  -> NEW=$(( NOW_TB - BASE_TB ))"
if [ "$NOW_TB" -gt "$BASE_TB" ]; then
  echo ">>> NEW TRACEBACKS PRESENT — context (last 25 non-noise error-log lines):"
  grep -vE "$NOISE" "$ERR_LOG" 2>/dev/null | tail -25
fi

echo
echo "--- snapshot + bug-signature probe (precise, today only) ---"
TODAY=$(date -u +%Y-%m-%d)
./venv/bin/python3 - "$SNAPDIR/$TODAY.jsonl" "$STATE" <<'PY' 2>/dev/null
import json,sys,os
from datetime import datetime, timezone, timedelta
snap, statef = sys.argv[1], sys.argv[2]

# --- today's cycle snapshots ---
recs=[]
if os.path.exists(snap):
    for ln in open(snap):
        ln=ln.strip()
        if ln:
            try: recs.append(json.loads(ln))
            except Exception: pass
print(f"snapshot: {os.path.basename(snap)}  cycles={len(recs)}")
if not recs:
    print("  (no cycles yet — market likely not open)")

from collections import Counter
st=Counter()
SUBMIT={"submitted","close","filled","partial"}
null_fills=[]      # #6: a SUBMITTED/FILLED record with no underlying symbol
for r in recs:
    res=r.get("result") or {}; d=r.get("decision") or {}
    status=res.get("status"); st[status]+=1
    if status in SUBMIT:
        sym=d.get("symbol") or (d.get("legs") or [{}])[0].get("symbol") if isinstance(d.get("legs"),list) else d.get("symbol")
        sym=sym or d.get("symbol")
        if not sym:
            null_fills.append({"t":(r.get("t") or "")[11:19],"action":d.get("action"),"status":status})
print("  statuses:",dict(st))

# #6 null-symbol on a real fill (NOT on holds — those legitimately have no symbol)
print(f"\n#6 null-symbol FILLS (submitted/filled w/ empty symbol): {len(null_fills)}")
for x in null_fills[:6]: print("   >>>",x)

# crash/exception inside a cycle result
errs=[r for r in recs if "exception" in json.dumps(r.get("result") or {}).lower()
      or (r.get("result") or {}).get("status")=="error"]
print(f"cycle-result errors/exceptions: {len(errs)}")
for r in errs[:4]:
    print("   >>>",(r.get("t") or "")[11:19], json.dumps(r.get("result"))[:200])

# --- state: #5 spread orphan check + cycle staleness ---
print()
try:
    s=json.load(open(statef))
except Exception as e:
    print("state read error:",e); s={}
ml=s.get("active_multileg") or []; op=s.get("active_options") or []
print(f"#5 tracked: active_multileg={len(ml)} active_options={len(op)} halted={s.get('halted')}")
for m in ml:
    legs=m.get("legs") or []; syms=[l.get("symbol") for l in legs]
    flag=" <<EMPTY_SYM!" if any(not x for x in syms) else ""
    print(f"   spread status={m.get('status')} opened={m.get('opened_at')} legs={syms}{flag}")
# cycle staleness (during market hours a cycle should land every ~5 min)
lca=s.get("last_cycle_at")
print("last_cycle_at:",lca)
if lca:
    try:
        t=datetime.fromisoformat(lca); age=(datetime.now(timezone.utc)-t.astimezone(timezone.utc)).total_seconds()/60
        et_h=(datetime.now(timezone.utc).hour-4)%24
        mkt = (9<=et_h<16) or (et_h==9)
        flag=" <<STALE (>8m during market hours!)" if (mkt and age>8) else ""
        print(f"   cycle age: {age:.1f} min{flag}")
    except Exception as e: print("   (age calc skipped:",e,")")
PY

echo
echo "--- #5 orphan / #7 flatten alerts in main log (today) ---"
grep -hiE "orphan|could not (flatten|confirm)|remnant|force-clos|sweep_orphan" "$MAIN_LOG" 2>/dev/null | tail -8

echo
echo "===== END ====="
