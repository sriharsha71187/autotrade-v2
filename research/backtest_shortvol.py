#!/usr/bin/env python3
"""The "90% chance of profit" trade, measured. Systematically sell 1-month ~1.3-sigma OTM SPY puts
(~10-delta, ~90% PoP). Premium modeled at IV = realized-vol x VRP (implied richer than realized).
Compare: cash-secured (naked-ish) vs defined-risk spread, and ungated vs regime-gated (SPY>200DMA).
Report win-rate, CAGR-on-capital, Sharpe, maxDD, and the WORST month (the tail). 2007-2026 so it
includes 2008, Feb-2018 volmageddon, Mar-2020, 2022. Run: python research/backtest_shortvol.py
"""
import math
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
R = 0.03; VRP = 1.25; DELTA_K = 1.3      # IV = RV*1.25; strike ~1.3 monthly-sigma OTM (~10-delta)


def N(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs_put(S, K, T, sig):
    if T <= 0 or sig <= 0: return max(K-S, 0.0)
    d1 = (math.log(S/K)+(R+sig*sig/2)*T)/(sig*math.sqrt(T)); d2 = d1-sig*math.sqrt(T)
    return K*math.exp(-R*T)*N(-d2) - S*N(-d1)


def stats(rets):
    r = pd.Series(rets); eq = (1+r).cumprod(); yrs = len(r)/12
    cagr = eq.iloc[-1]**(1/yrs)-1; sh = r.mean()/r.std()*math.sqrt(12) if r.std() else 0
    dd = (eq/eq.cummax()-1).min(); win = (r > 0).mean()
    return cagr, sh, dd, win, r.min()


def main():
    import yfinance as yf
    s = yf.download("SPY", start="2007-01-01", auto_adjust=True, progress=False)["Close"]
    s = (s.iloc[:, 0] if hasattr(s, "columns") else s).dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    rv = s.pct_change().rolling(21).std()*math.sqrt(252)
    ma200 = s.rolling(200).mean()
    mstart = list(s.index[np.append([True], s.index.to_period("M")[1:] != s.index.to_period("M")[:-1])])
    T = 1/12
    books = {"cash-secured put (naked-ish)": [], "defined-risk spread (5% wide)": [],
             "cash-secured + regime gate": [], "defined-risk + regime gate": []}
    for d0 in mstart[:-1]:
        i = s.index.get_loc(d0); S0 = float(s.iloc[i]); sig = float(rv.iloc[i])
        if not (S0 > 0 and sig == sig and sig > 0): continue
        iv = sig*VRP; K = S0*math.exp(-DELTA_K*sig*math.sqrt(T)); K2 = K*0.95
        j = min(i+21, len(s)-1); ST = float(s.iloc[j])
        gated_on = S0 > float(ma200.iloc[i]) if ma200.iloc[i] == ma200.iloc[i] else True
        prem = bs_put(S0, K, T, iv); loss = max(K-ST, 0)
        r_cs = (prem - loss)/K                                      # cash-secured: capital ~ K
        prem_sp = prem - bs_put(S0, K2, T, iv); maxloss = (K-K2) - prem_sp
        r_sp = (prem_sp - max(min(K-ST, K-K2), 0))/max(maxloss, 1e-9)  # defined-risk: capital ~ maxloss
        books["cash-secured put (naked-ish)"].append(r_cs)
        books["defined-risk spread (5% wide)"].append(r_sp)
        books["cash-secured + regime gate"].append(r_cs if gated_on else 0.0)
        books["defined-risk + regime gate"].append(r_sp if gated_on else 0.0)

    print(f"\n=== Short-premium '90% PoP' SPY puts · 2007-2026 · IV=RV x{VRP} ===\n")
    print(f"  {'book':32}{'CAGR':>7}{'Sharpe':>8}{'maxDD':>8}{'win%':>7}{'worst mo':>10}")
    for name, rets in books.items():
        c, sh, dd, win, wm = stats(rets)
        print(f"  {name:32}{c*100:6.1f}%{sh:7.2f}{dd*100:7.0f}%{win*100:6.0f}%{wm*100:8.0f}%")
    print("\n  win% is high (~90%) by design — but watch 'worst mo' and maxDD: the tail is the whole story.")
    print("  Note: regime gate (SPY>200DMA) avoids grinding bears but NOT sudden crashes from highs")
    print("  (Feb-2018, Mar-2020 both started ABOVE the 200DMA) — exactly when short-vol blows up.")


if __name__ == "__main__":
    main()
