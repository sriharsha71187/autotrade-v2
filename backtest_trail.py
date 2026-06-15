#!/usr/bin/env python3
"""backtest_trail.py — would a lower OPTION_TRAIL_ACTIVATE have banked more?

Reconstructs the profit-fraction path (unrealized_pl / debit) of every LONG-option
position from the per-cycle `positions` snapshots, then simulates the engine's trailing
exit under several activation thresholds (giveback + hard stop held at current config).
Compares realized $ and exit-reason mix vs the current 0.40 setting.

Read-only. Caveats it prints: per-cycle sampling granularity (~2-3 min), no reinvestment
of freed capital, only positions actually OPENED are in the data (no missed-entry names),
short legs of spreads excluded (long-premium positions only). Tail-hedge SPY puts excluded.

Run: ~/autotrade/venv/bin/python3 ~/autotrade/backtest_trail.py
"""
import json, glob, os

GIVEBACK = 0.25      # OPTION_TRAIL_GIVEBACK (held at current)
STOP     = -0.50     # OPTION_STOP_PCT (held at current)
ACTIVATES = [0.40, 0.30, 0.25, 0.20]   # 0.40 = current
SNAP = "/Users/nirvaan/autotrade_snapshots"

def is_option(sym): return ("C00" in sym or "P00" in sym) and len(sym) > 12
def is_tail_hedge(sym): return sym.startswith("SPY2607")  # long-dated SPY put = tail hedge book

# --- collect each option position's profit-fraction path, per day ---
positions = []   # each: {day, sym, debit, pf_path:[...]}
for fp in sorted(glob.glob(f"{SNAP}/2026-*.jsonl")):
    day = os.path.basename(fp)[:-6]
    bysym = {}   # sym -> list of (pf, upl, debit)
    for ln in open(fp):
        ln = ln.strip()
        if not ln: continue
        try: rec = json.loads(ln)
        except Exception: continue
        for p in rec.get("positions") or []:
            sym = str(p.get("symbol", ""))
            if not is_option(sym) or is_tail_hedge(sym): continue
            qty = p.get("qty") or 0
            if qty <= 0: continue   # long premium only (skip short legs)
            ae = p.get("avg_entry"); upl = p.get("unrealized_pl")
            if ae is None or upl is None: continue
            debit = float(ae) * float(qty) * 100.0
            if debit <= 0: continue
            bysym.setdefault(sym, []).append((float(upl) / debit, float(upl), debit))
    for sym, seq in bysym.items():
        positions.append({"day": day, "sym": sym, "debit": seq[-1][2],
                          "pf_path": [x[0] for x in seq]})

def simulate(pf_path, activate):
    """Return (exit_pf, reason). Walk the path applying hard stop + trailing logic."""
    hw = pf_path[0]
    armed = hw >= activate
    for pf in pf_path:
        hw = max(hw, pf)
        if not armed and hw >= activate:
            armed = True
        if pf <= STOP:
            return STOP, "stop"
        if armed and pf <= hw * (1 - GIVEBACK):
            return pf, "trail"
    return pf_path[-1], "eod/close (no trigger)"

print(f"=== OPTION_TRAIL_ACTIVATE backtest ===  (giveback={GIVEBACK}, hard stop={STOP})")
print(f"long-option positions reconstructed: {len(positions)} across "
      f"{len(set(p['day'] for p in positions))} days\n")

base = None
for act in ACTIVATES:
    total = 0.0; reasons = {"trail":0,"stop":0,"eod/close (no trigger)":0}; armed_ct = 0
    per = []
    for p in positions:
        exit_pf, reason = simulate(p["pf_path"], act)
        pnl = exit_pf * p["debit"]
        total += pnl; reasons[reason]+=1
        if max(p["pf_path"]) >= act: armed_ct += 1
        per.append((p["sym"], p["day"], round(max(p["pf_path"])*100), round(exit_pf*100), reason, round(pnl)))
    tag = "  <-- CURRENT" if act == 0.40 else ""
    if base is None: base = total
    delta = total - base
    print(f"ACTIVATE={act:.2f}{tag}")
    print(f"  total realized P&L: {total:+8.0f}   vs current: {delta:+8.0f}")
    print(f"  exits: {reasons['trail']} trail / {reasons['stop']} stop / "
          f"{reasons['eod/close (no trigger)']} eod-notrigger   | armed: {armed_ct}/{len(positions)}")
    if act in (0.40, 0.25):
        # show the positions that changed the most between current and this setting
        print(f"  per-position (sym | day | peak% | exit% | reason | $):")
        for row in sorted(per, key=lambda r: r[5]):
            print(f"     {row[0]:22s} {row[1]} peak{row[2]:+4d}% exit{row[3]:+4d}% {row[4]:22s} {row[5]:+5d}")
    print()
