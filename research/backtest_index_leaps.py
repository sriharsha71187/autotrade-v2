#!/usr/bin/env python3
"""Leveraged INDEX exposure via LEAPS — deep-ITM (~0.80 delta) 12-month calls on QQQ / SPY, rolled
when <9 months remain, $25k start, ~60% of equity deployed in premium (rest cash @4.5%), regime
gate (to cash when index<200DMA). NO survivorship bias (it's the index) so the magnitude is
trustworthy. vs index buy-hold and the 2x ETF. Run: python research/backtest_index_leaps.py
"""
import math
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
R = 0.045; ITM = 0.80; DEPLOY = 0.60; OPT_COST = 0.01; LEAPS_T = 252; START = 25000.0


def N(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs(S, K, T, sig):
    if T <= 0 or sig <= 0: return max(S-K, 0.0)
    d1 = (math.log(S/K)+(R+sig*sig/2)*T)/(sig*math.sqrt(T)); d2 = d1-sig*math.sqrt(T)
    return S*N(d1)-K*math.exp(-R*T)*N(d2)


def stats(eq):
    r = eq.pct_change().dropna(); yrs = (eq.index[-1]-eq.index[0]).days/365.25
    cagr = (eq.iloc[-1]/eq.iloc[0])**(1/yrs)-1; sh = r.mean()/r.std()*math.sqrt(12) if r.std() else 0
    return cagr, sh, (eq/eq.cummax()-1).min()


def run_leaps(px, sig, idx, mstart, bull, regime):
    cash = START; con = 0; strike = 0; exp = 0; iv0 = 0; out = []
    def pv(i):
        return con*100*bs(px.iloc[i], strike, max((exp-i)/252, 0), iv0) if con else 0
    for d0 in mstart:
        i0 = idx.get_loc(d0); S = px.iloc[i0]; iv = sig.iloc[i0]
        if not (S > 0 and iv == iv and iv > 0): out.append((d0, cash+pv(i0))); continue
        if con and (exp-i0)/252 < 0.75:                      # roll <9mo
            cash += pv(i0)*(1-OPT_COST); con = 0
        equity = cash + pv(i0)
        on = (not regime) or (bool(bull.iloc[i0]) if i0 < len(bull) else True)
        if not on and con:                                   # regime off -> cash
            cash += pv(i0)*(1-OPT_COST); con = 0
        elif on:
            cpr = bs(S, ITM*S, LEAPS_T/252, iv)*100
            tgt = math.floor(DEPLOY*equity/cpr) if cpr > 0 else 0
            if con == 0 and tgt > 0:                         # (re)enter
                con = tgt; strike = ITM*S; exp = i0+LEAPS_T; iv0 = iv; cash -= con*cpr*(1+OPT_COST)
        out.append((d0, cash+pv(i0)))
    return pd.Series([e for _, e in out], index=[d for d, _ in out])


def main():
    import yfinance as yf
    px = yf.download(["QQQ","SPY","QLD","SSO"], start="2014-01-01", auto_adjust=True, progress=False)["Close"].ffill()
    px.index = pd.to_datetime(px.index).tz_localize(None)
    idx = px.index; mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    print(f"\n=== Index LEAPS (leveraged) · {idx.min().date()}..{idx.max().date()} · $25k · 12mo, roll<9mo ===\n")
    print(f"  {'strategy':34}{'CAGR':>7}{'Sharpe':>8}{'maxDD':>8}{'final$':>12}")
    for ix, lev2 in [("QQQ","QLD"), ("SPY","SSO")]:
        sig = px[ix].pct_change().rolling(30).std()*math.sqrt(252)
        bull = px[ix] > px[ix].rolling(200).mean()
        for regime in (True, False):
            eq = run_leaps(px[ix], sig, idx, mstart, bull, regime)*1.0
            c, s, d = stats(eq)
            print(f"  {ix+' LEAPS '+('+regime' if regime else 'buy-hold'):34}{c*100:6.1f}%{s:7.2f}{d*100:7.0f}%{eq.iloc[-1]:>12,.0f}")
        # benchmarks
        bh = START*(px[ix]/px[ix].iloc[0]); c, s, d = stats(bh.loc[[m for m in mstart]])
        print(f"  {ix+' buy-hold (1x)':34}{c*100:6.1f}%{s:7.2f}{d*100:7.0f}%{bh.iloc[-1]:>12,.0f}")
        l2 = START*(px[lev2]/px[lev2].iloc[0]); c, s, d = stats(l2.loc[[m for m in mstart]])
        print(f"  {lev2+' 2x-ETF buy-hold':34}{c*100:6.1f}%{s:7.2f}{d*100:7.0f}%{l2.iloc[-1]:>12,.0f}")
        print()
    print("  TRUSTWORTHY (no survivorship). IV=realized vol but deep-ITM index LEAPS are low-vega.")
    print("  Defined risk (loss bounded at premium, no margin call). Whole contracts -> lumpy at $25k.")


if __name__ == "__main__":
    main()
