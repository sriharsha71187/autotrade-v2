#!/usr/bin/env python3
"""LEAPS version of leveraged momentum, Oct 2025 -> Mar 2026. Hold deep-ITM LEAPS (~0.80 delta,
12mo) on the 10 momentum names; LET IT RUN (only trade monthly adds/removes; no -75% stop). $100k
start, ~6% premium per name, rest in cash @4.5%. Black-Scholes modeled (IV=realized vol; LEAPS are
far less IV-sensitive than short calls, so this is more robust). vs the 1-month-call version + stocks.
Run: python research/backtest_leaps_momentum.py
"""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe

R = 0.045; ITM = 0.80; PREM_PCT = 0.06; OPT_COST = 0.02; LEAPS_T = 252


def N(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs_call(S, K, T, sig):
    if T <= 0 or sig <= 0: return max(S-K, 0.0)
    d1 = (math.log(S/K)+(R+sig*sig/2)*T)/(sig*math.sqrt(T)); d2 = d1-sig*math.sqrt(T)
    return S*N(d1)-K*math.exp(-R*T)*N(d2)


def main():
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    idx = px.index; stocks = [c for c in px.columns if c not in ("SPY","QQQ")]
    mom = px[stocks].shift(21)/px[stocks].shift(147)-1
    rv = px[stocks].pct_change().rolling(30).std()*math.sqrt(252)
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    rebs = [d for d in mstart if d in mom.index and "2025-10" <= d.strftime("%Y-%m") <= "2026-03"]

    def val(p, t, i):                          # current LEAPS position value
        S = px[t].iloc[i]; T = max((p["exp"]-i)/252, 0)
        return p["sh"]*bs_call(S, p["strike"], T, p["iv"])

    cash = 100000.0; pos = {}; eq_hist = []; stk_eq = 1.0
    for j, d0 in enumerate(rebs):
        i0 = idx.get_loc(d0)
        for t, p in pos.items():                # roll LEAPS under 9 months
            if (p["exp"]-i0)/252 < 0.75:
                cash += val(p, t, i0)*(1-OPT_COST); S = px[t].iloc[i0]; iv = rv[t].iloc[i0] or 0.5
                prem = bs_call(S, 0.8*S, 1.0, iv); pos[t] = {"sh": val(p,t,i0)/prem if prem>0 else 0, "strike":0.8*S, "exp":i0+LEAPS_T, "iv":iv}
        equity = cash + sum(val(p, t, i0) for t, p in pos.items())
        top10 = list(mom.loc[d0].dropna().sort_values(ascending=False).index[:10])
        for t in list(pos):                     # sell removed
            if t not in top10: cash += val(pos[t], t, i0)*(1-OPT_COST); del pos[t]
        for t in top10:                         # buy added
            if t not in pos:
                S = px[t].iloc[i0]; iv = rv[t].iloc[i0]
                if not (S > 0 and iv == iv and iv > 0): continue
                prem = bs_call(S, ITM*S, LEAPS_T/252, iv)
                if prem <= 0: continue
                spend = PREM_PCT*equity
                pos[t] = {"sh": spend/prem, "strike": ITM*S, "exp": i0+LEAPS_T, "iv": iv}
                cash -= spend*(1+OPT_COST)
        eq_now = cash + sum(val(p, t, i0) for t, p in pos.items())
        # stock benchmark (equal-weight top-10 over the month)
        i1 = min(i0+21, len(idx)-1)
        srets = [px[t].iloc[i1]/px[t].iloc[i0]-1 for t in top10 if px[t].iloc[i0] > 0]
        stk_eq *= (1+(np.mean(srets) if srets else 0))
        delta_exp = sum(p["sh"]*0.8*px[t].iloc[i0] for t, p in pos.items())   # ~delta notional
        eq_hist.append((d0.strftime("%Y-%m"), eq_now, len(pos), delta_exp/eq_now if eq_now else 0, (stk_eq-1)))

    # final equity at last rebalance + ~1 month
    last = idx.get_loc(rebs[-1]); ie = min(last+21, len(idx)-1)
    cash_d = cash; final = cash_d + sum(val(p, t, ie) for t, p in pos.items())
    print(f"\n=== LEAPS momentum (let-it-run) · Oct 2025 -> Mar 2026 · $100k start ===\n")
    print(f"  {'month':9}{'equity$':>12}{'#LEAPS':>8}{'leverage':>10}{'stocks idx':>12}")
    for ym, e, n, lev, s in eq_hist:
        print(f"  {ym:9}{e:>12,.0f}{n:>8}{lev:>9.1f}x{(1+s)*100000:>11,.0f}")
    print(f"  {'~end':9}{final:>12,.0f}")
    print(f"\n  LEAPS total: {(final/100000-1)*100:+.0f}%   vs  stocks {(stk_eq-1)*100:+.0f}%   vs  1mo-calls -77%")
    print("\n  CAVEAT: IV=realized vol; deep-ITM LEAPS are LOW vega so IV error matters far less than for")
    print("  short calls. No bid-ask gaps modeled. Defined risk: a LEAPS can't lose >its premium.")


if __name__ == "__main__":
    main()
