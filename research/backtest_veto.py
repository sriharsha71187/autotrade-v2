#!/usr/bin/env python3
"""WITH vs WITHOUT the LLM-veto, on the 6-month momentum sleeve, last 12 months. Uses the agent's
POINT-IN-TIME veto list. A vetoed name is replaced two ways: (a) backfill with the next-ranked
momentum name (keep 10), (b) redistribute its weight to survivors (hold fewer). Shows the realized
return delta + each veto's individual impact. Run: python research/backtest_veto.py
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe

# point-in-time vetoes from the research agent (month -> names removed)
VETO = {"2025-07": ["HIMS"], "2025-08": ["CVNA"], "2025-09": ["SMCI", "HIMS"],
        "2025-10": ["ECHO"], "2025-11": ["ECHO"], "2025-12": ["ECHO"], "2026-01": ["ECHO"],
        "2026-02": ["ECHO"], "2026-03": ["ECHO", "SLV"], "2026-04": ["ECHO"], "2026-05": ["ECHO"]}


def main():
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    idx = px.index; stocks = [c for c in px.columns if c not in ("SPY", "QQQ")]
    mom = px[stocks].shift(21)/px[stocks].shift(147)-1            # 6-month
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    rebs = [d for d in mstart if d in mom.index][-13:]

    def month_ret(held, d0, d1):                                  # equal-weight realized return d0->d1
        rr = []
        for t in held:
            s = px[t].loc[d0:d1].dropna()
            if len(s) > 1: rr.append(s.iloc[-1]/s.iloc[0]-1)
        return np.mean(rr) if rr else 0.0

    eq_no, eq_bf, eq_rd = 1.0, 1.0, 1.0; rows = []
    for i in range(len(rebs)-1):
        d0, d1 = rebs[i], rebs[i+1]; ym = d0.strftime("%Y-%m")
        ranked = list(mom.loc[d0].dropna().sort_values(ascending=False).index)
        top10 = ranked[:10]
        vetoed = [t for t in VETO.get(ym, []) if t in top10]
        survivors = [t for t in top10 if t not in vetoed]
        backfill = survivors + [t for t in ranked[10:] if t not in survivors][:len(vetoed)]   # keep 10
        r_no = month_ret(top10, d0, d1)
        r_bf = month_ret(backfill, d0, d1)
        r_rd = month_ret(survivors, d0, d1)                       # hold fewer (redistribute)
        eq_no *= (1+r_no); eq_bf *= (1+r_bf); eq_rd *= (1+r_rd)
        # impact of the vetoed names themselves over the month
        vimp = month_ret(vetoed, d0, d1) if vetoed else np.nan
        rows.append((ym, ",".join(vetoed) or "—", r_no, r_bf, r_rd, vimp))

    print(f"\n=== 6-month sleeve: WITH vs WITHOUT veto · {rebs[0].strftime('%Y-%m')}..{rebs[-1].strftime('%Y-%m')} ===\n")
    print(f"  {'month':9}{'vetoed':14}{'no-veto%':>10}{'veto+backfill%':>16}{'veto+redist%':>14}{'vetoed-name ret%':>18}")
    for ym, v, rn, rb, rr, vi in rows:
        vis = f"{vi*100:+.0f}%" if vi == vi else "—"
        print(f"  {ym:9}{v:14}{rn*100:>9.1f}%{rb*100:>15.1f}%{rr*100:>13.1f}%{vis:>18}")
    print(f"\n  12-MONTH TOTAL:   no-veto {(eq_no-1)*100:+.0f}%   veto+backfill {(eq_bf-1)*100:+.0f}%   veto+redist {(eq_rd-1)*100:+.0f}%")
    print("  (vetoed-name ret% = what the removed name did that month — positive means the veto GAVE UP gains)")


if __name__ == "__main__":
    main()
