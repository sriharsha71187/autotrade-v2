#!/usr/bin/env python3
"""Hypothetical: each month buy a 1-month ~5% ITM CALL on each of the 10 momentum stocks; stop out
if the option loses 75% of value, else sell on expiry day. Last 6 months. Options are BLACK-SCHOLES
MODELED (IV = trailing realized vol) since we lack daily option-price history — so treat as
illustrative, NOT executable. Run: python research/backtest_opt_momentum.py
"""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe

R = 0.045            # risk-free
ITM = 0.95           # strike = 95% of spot (5% in-the-money)
STOP = 0.25          # exit if option value <= 25% of entry (lost 75%)
HOLD = 21            # ~1 month
OPT_COST = 0.03      # round-trip option spread haircut (~3%)


def N(x): return 0.5*(1+math.erf(x/math.sqrt(2)))


def bs_call(S, K, T, sig):
    if T <= 0 or sig <= 0: return max(S-K, 0.0)
    d1 = (math.log(S/K) + (R + sig*sig/2)*T)/(sig*math.sqrt(T)); d2 = d1 - sig*math.sqrt(T)
    return S*N(d1) - K*math.exp(-R*T)*N(d2)


def main():
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    idx = px.index; stocks = [c for c in px.columns if c not in ("SPY","QQQ")]
    mom = px[stocks].shift(21)/px[stocks].shift(147)-1                 # 6-month momentum
    rv = px[stocks].pct_change().rolling(30).std()*math.sqrt(252)      # IV proxy = realized vol
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    rebs = [d for d in mstart if d in mom.index][-6:]                  # last 6 months

    def sim(t, d0):
        i0 = idx.get_loc(d0); S0 = px[t].iloc[i0]; K = ITM*S0
        sig = rv[t].iloc[i0]
        if not (S0 > 0 and sig == sig and sig > 0): return None
        ev = bs_call(S0, K, HOLD/252, sig)
        if ev <= 0: return None
        for d in range(1, HOLD+1):
            if i0+d >= len(idx): break
            S = px[t].iloc[i0+d]; T = (HOLD-d)/252
            v = bs_call(S, K, T, sig)
            if v <= STOP*ev: return v/ev - 1 - OPT_COST                # stopped at -75%
            if d == HOLD: return max(S-K, 0.0)/ev - 1 - OPT_COST       # expiry
        return max(px[t].iloc[min(i0+HOLD, len(idx)-1)]-K, 0.0)/ev - 1 - OPT_COST

    eq_opt, eq_stk = 1.0, 1.0; allrets = []
    print(f"\n=== ITM-call on momentum (modeled) · last 6 months · 5% ITM, -75% stop, {OPT_COST*100:.0f}% cost ===\n")
    print(f"  {'month':9}{'opt port%':>10}{'stop-outs':>11}{'stock port%':>13}")
    for d0 in rebs:
        top10 = list(mom.loc[d0].dropna().sort_values(ascending=False).index[:10])
        i0 = idx.get_loc(d0); i1 = min(i0+HOLD, len(idx)-1)
        orets = [r for r in (sim(t, d0) for t in top10) if r is not None]
        srets = [px[t].iloc[i1]/px[t].iloc[i0]-1 for t in top10 if px[t].iloc[i0] > 0]
        opt_m = np.mean(orets) if orets else 0; stk_m = np.mean(srets) if srets else 0
        stops = sum(1 for r in orets if r < -0.7)
        eq_opt *= (1+opt_m); eq_stk *= (1+stk_m); allrets += orets
        print(f"  {d0.strftime('%Y-%m'):9}{opt_m*100:>9.0f}%{stops:>8}/10{stk_m*100:>12.1f}%")
    a = np.array(allrets)
    print(f"\n  6-MONTH TOTAL:  options {(eq_opt-1)*100:+.0f}%   vs  stocks {(eq_stk-1)*100:+.0f}%")
    print(f"  per-option trade: avg {a.mean()*100:+.0f}% · win {(a>0).mean()*100:.0f}% · best {a.max()*100:+.0f}% · worst {a.min()*100:.0f}%")
    print("\n  CAVEATS: IV=realized-vol (you usually OVERPAY vol buying calls -> real returns LOWER);")
    print("  no bid-ask gaps/assignment; -75% stop assumes you can exit intraday; window is a momentum BULL.")


if __name__ == "__main__":
    main()
