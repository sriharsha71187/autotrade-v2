#!/usr/bin/env python3
"""Structure ROUTER: instead of condor-only, let each day's conditions choose the
structure — condor, credit spread (either side), or debit spread (either side).

Method (no peeking): on IS (2007-2018), for every (gap bucket x trend x VIX band)
cell pick the structure with the best avg after-friction return, admitted only if
N>=50, t>=2 and avg>0. That mined playbook is then FROZEN and evaluated on OOS
(2019-2026), against the condor-only calm-day rule as the benchmark — plus the
"$20k since Jan-2023" experiment on both. Also prints the IS cell->structure map
so you can see WHAT the data chose (spoiler risk: debit spreads never qualify).

Reuses pricing/feature code from backtest_gap_premium.py (same conventions).
Run: python3 research/backtest_structure_router.py       (pure stdlib)
"""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_gap_premium as g

STRUCTS = ["IC", "PCS", "CCS", "CDS", "PDS"]
MIN_N, MIN_T = 50, 2.0


def main():
    rows = g.load("research/data/spy_vix_daily.csv")
    days = g.build_days(rows)
    is_days = [d for d in days if d["d"] < g.IS_END]
    oos_days = [d for d in days if d["d"] >= g.IS_END]

    for fric in (0.10, 0.20):
        # 1) mine the playbook on IS
        cells = {}
        for d in is_days:
            key = (d["gap"], d["tr"], d["vb"])
            for s in STRUCTS:
                cells.setdefault(key, {}).setdefault(s, []).append(g.struct_ret(d, s, fric))
        playbook = {}
        for key, per in cells.items():
            best = None
            for s, rs in per.items():
                if len(rs) >= MIN_N and g.tstat(rs) >= MIN_T and sum(rs) > 0:
                    avg = sum(rs) / len(rs)
                    if best is None or avg > best[1]:
                        best = (s, avg)
            if best:
                playbook[key] = best[0]
        print(f"\n=== router @ friction {fric:.0%} — IS-mined playbook (cell -> structure) ===")
        for key in sorted(playbook):
            rs = cells[key][playbook[key]]
            print(f"  gap={key[0]:6} trend={key[1]:4} vix={key[2]:7} -> {playbook[key]:3}  "
                  f"(IS {g.fmt(rs)})")
        if not playbook:
            print("  (no cell qualified)"); continue

        # 2) frozen playbook on OOS vs condor-only benchmark
        def run(sel_days, label):
            r_router, r_ic = [], []
            for d in sel_days:
                key = (d["gap"], d["tr"], d["vb"])
                if key in playbook:
                    r_router.append(g.struct_ret(d, playbook[key], fric))
                if d["gap"] == "flat" and d["vix"] < 16:
                    r_ic.append(g.struct_ret(d, "IC", fric))
            print(f"  {label} router      {g.fmt(r_router)}")
            print(f"  {label} condor-only {g.fmt(r_ic)}")
        print("-- OOS 2019-2026 --"); run(oos_days, "")

        # 3) $20k since Jan-2023, 10% of account per trade day
        for lbl, pick in (("router", lambda d: playbook.get((d["gap"], d["tr"], d["vb"]))),
                          ("condor-only", lambda d: "IC" if (d["gap"] == "flat"
                                                             and d["vix"] < 16) else None)):
            eq, peak, dd, n = 20000.0, 20000.0, 0.0, 0
            for d in days:
                if d["d"] < "2023-01-01": continue
                s = pick(d)
                if not s: continue
                r = g.struct_ret(d, s, fric) * 0.10
                n += 1; eq *= 1 + r; peak = max(peak, eq); dd = min(dd, eq / peak - 1)
            print(f"  $20k Jan-2023->now, {lbl:11}: ${eq:,.0f}  ({n} trades, maxDD {dd*100:.1f}%)")

    print("\n  Reading: if the playbook table above picked IC nearly everywhere and no")
    print("  debit spread ever qualified, then 'choose the structure by the day' IS the")
    print("  condor-on-calm-days rule — the router just rediscovers it. Any router edge")
    print("  over condor-only comes from the few credit-spread cells; judge it OOS.")


if __name__ == "__main__":
    main()
