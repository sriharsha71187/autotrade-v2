#!/usr/bin/env python3
"""LEAPS leveraged momentum over the FULL history (2017-2026) — through 2018/2020/2022 drawdowns.
Deep-ITM LEAPS (~0.80 delta, 12mo, rolled <9mo) on the 10 momentum names; LET IT RUN; REGIME GATE
(sell all to cash when SPY<200DMA). $100k start, ~6% premium/name. Defined risk (loss bounded at
premium, no margin call). Black-Scholes modeled. Run: python research/backtest_leaps_full.py
"""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe

R = 0.045; ITM = 0.80; PREM_PCT = 0.06; OPT_COST = 0.02; LEAPS_T = 252; CAP = 0.15  # max % of book per position


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
    bull = (px["SPY"] > px["SPY"].rolling(200).mean()) if "SPY" in px else pd.Series(True, index=idx)
    mstart = [d for d in idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])] if d in mom.index]
    mstart = [d for d in mstart if not np.isnan(mom.loc[d].dropna().head(1).sum())][6:]  # warmup

    def val(p, t, i):
        S = px[t].iloc[i]; T = max((p["exp"]-i)/252, 0)
        return p["sh"]*bs_call(S, p["strike"], T, p["iv"])

    cash = 100000.0; pos = {}; curve = []
    for d0 in mstart:
        i0 = idx.get_loc(d0)
        for t, p in list(pos.items()):                 # roll <9mo
            if (p["exp"]-i0)/252 < 0.75:
                v = val(p, t, i0); cash += v*(1-OPT_COST); S = px[t].iloc[i0]; iv = rv[t].iloc[i0]
                iv = iv if iv == iv and iv > 0 else 0.5; prem = bs_call(S, 0.8*S, 1.0, iv)
                pos[t] = {"sh": v/prem if prem > 0 else 0, "strike": 0.8*S, "exp": i0+LEAPS_T, "iv": iv}
        equity = cash + sum(val(p, t, i0) for t, p in pos.items())
        for t, p in list(pos.items()):                  # CAP: trim winners over CAP% of book to cash
            v = val(p, t, i0)
            if v > CAP*equity and v > 0:
                keep = CAP*equity; cash += (v-keep)*(1-OPT_COST); p["sh"] *= keep/v
        on = bool(bull.loc[d0]) if d0 in bull.index else True
        top10 = list(mom.loc[d0].dropna().sort_values(ascending=False).index[:10]) if on else []
        for t in list(pos):                              # sell removed / all if regime off
            if t not in top10: cash += val(pos[t], t, i0)*(1-OPT_COST); del pos[t]
        for t in top10:                                  # buy added
            if t not in pos:
                S = px[t].iloc[i0]; iv = rv[t].iloc[i0]
                if not (S > 0 and iv == iv and iv > 0): continue
                prem = bs_call(S, ITM*S, LEAPS_T/252, iv)
                if prem <= 0: continue
                spend = min(PREM_PCT*equity, cash*0.95)
                if spend <= 0: continue
                pos[t] = {"sh": spend/prem, "strike": ITM*S, "exp": i0+LEAPS_T, "iv": iv}
                cash -= spend*(1+OPT_COST)
        curve.append((d0, cash + sum(val(p, t, i0) for t, p in pos.items())))

    eq = pd.Series([e for _, e in curve], index=[d for d, _ in curve])
    r = eq.pct_change().dropna(); yrs = (eq.index[-1]-eq.index[0]).days/365.25
    cagr = (eq.iloc[-1]/eq.iloc[0])**(1/yrs)-1
    sharpe = r.mean()/r.std()*math.sqrt(12) if r.std() else 0
    dd = (eq/eq.cummax()-1).min()
    print(f"\n=== LEAPS leveraged momentum · FULL history · {eq.index[0].date()}..{eq.index[-1].date()} ===\n")
    print(f"  ${eq.iloc[0]:,.0f} -> ${eq.iloc[-1]:,.0f}   CAGR {cagr*100:.1f}%   Sharpe {sharpe:.2f}   maxDD {dd*100:.0f}%")
    print(f"\n  calendar-year returns:")
    for y, g in eq.groupby(eq.index.year):
        yr = g.iloc[-1]/g.iloc[0]-1
        print(f"    {y}: {yr*100:+6.0f}%")
    print("\n  vs unlevered stock-momentum sleeve ~ Sharpe 1.1, -32%DD (the equity version).")
    print("  CAVEAT: monthly marks (intra-month crash drawdowns understated); IV=realized vol;")
    print("  but defined-risk: loss bounded at premium (~6%/name), NO margin call. Regime-gated.")


if __name__ == "__main__":
    main()
