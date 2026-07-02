#!/usr/bin/env python3
"""The flagship intraday options strategy, measured: sell a 0DTE iron condor on SPY/SPX at the
open (short strangle ~0.75 intraday-sigma OTM, wings ~2 sigma), hold to the close. Premium priced
by Black-Scholes at IV=VIX (0DTE IV ~ VIX intraday); P&L = credit minus intraday |open->close|
move beyond the shorts, on defined-risk margin (wing width - credit). Costs = 10% of gross credit
round-trip (realistic SPX 0DTE spread+fees). Variants: ungated, regime-gated, calm-VIX-only, and
the LONG side (buy the straddle). 2007-2026. Run: python research/backtest_0dte.py
"""
import math
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
R = 0.0; FRICTION = 0.10                      # 10% of gross credit lost to spread+fees round trip


def N(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs(S, K, T, sig, put=False):
    if T <= 0 or sig <= 0: return max((K-S) if put else (S-K), 0.0)
    d1 = (math.log(S/K)+(sig*sig/2)*T)/(sig*math.sqrt(T)); d2 = d1-sig*math.sqrt(T)
    return (K*N(-d2)-S*N(-d1)) if put else (S*N(d1)-K*N(d2))


def stats(rets):
    r = pd.Series(rets).dropna(); eq = (1+r).cumprod(); yrs = len(r)/252
    if eq.iloc[-1] <= 0: return -1.0, 0.0, -1.0, (r > 0).mean(), r.min()
    return eq.iloc[-1]**(1/yrs)-1, r.mean()/r.std()*math.sqrt(252) if r.std() else 0, \
           (eq/eq.cummax()-1).min(), (r > 0).mean(), r.min()


def main():
    import yfinance as yf
    d = yf.download(["SPY","^VIX"], start="2007-01-01", auto_adjust=True, progress=False)
    op = d["Open"]["SPY"]; cl = d["Close"]["SPY"]; vix = d["Close"]["^VIX"].ffill()
    df = pd.DataFrame({"o": op, "c": cl, "v": vix}).dropna()
    ma200 = df["c"].rolling(200).mean()
    T = 1/252
    rows = {"condor (every day)": [], "condor + regime gate": [], "condor calm days only (VIX<20)": [],
            "LONG straddle (every day)": []}
    for i in range(200, len(df)):
        S = float(df["o"].iloc[i]); C = float(df["c"].iloc[i]); iv = float(df["v"].iloc[i-1])/100
        sig_d = iv*math.sqrt(T)
        Kc = S*math.exp(0.75*sig_d); Kp = S*math.exp(-0.75*sig_d)          # short strikes
        Wc = S*math.exp(2.0*sig_d);  Wp = S*math.exp(-2.0*sig_d)           # wings
        credit = (bs(S,Kc,T,iv)+bs(S,Kp,T,iv,put=True)
                  - bs(S,Wc,T,iv) - bs(S,Wp,T,iv,put=True))
        credit_net = credit*(1-FRICTION)
        width = min(Wc-Kc, Kp-Wp); margin = width - credit
        loss = min(max(C-Kc,0)+max(Kp-C,0), width)                        # capped at the wing
        r_condor = (credit_net - loss)/margin
        # long straddle at the money, pay friction on the debit
        stx = bs(S,S,T,iv)+bs(S,S,T,iv,put=True); stx_cost = stx*(1+FRICTION)
        r_straddle = (abs(C-S) - stx_cost)/stx_cost
        gate = S > float(ma200.iloc[i])
        rows["condor (every day)"].append(r_condor)
        rows["condor + regime gate"].append(r_condor if gate else 0.0)
        rows["condor calm days only (VIX<20)"].append(r_condor if iv < 0.20 else 0.0)
        rows["LONG straddle (every day)"].append(r_straddle)

    print(f"\n=== 0DTE on SPY · open->close · defined-risk margin · friction {FRICTION:.0%} of credit · 2007-2026 ===\n")
    print(f"  {'strategy':34}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}{'win%':>7}{'worst day':>11}")
    for lbl, rr in rows.items():
        c, sh, dd, win, wd = stats(rr)
        print(f"  {lbl:34}{c*100:7.1f}%{sh:7.2f}{dd*100:7.0f}%{win*100:6.0f}%{wd*100:9.0f}%")
    print("\n  Returns are on the condor's own margin (fully-invested daily). No overnight gap risk —")
    print("  that's 0DTE's one real advantage — but the intraday tail days are in the sample.")


if __name__ == "__main__":
    main()
