#!/usr/bin/env python3
"""Backtest the ROBUST options strategies (volatility-risk-premium family) the agent flagged:
PutWrite (PUT), covered-call (BXM/BXMD), and ETF proxies (PUTW/QYLD/JEPI/DIVO). Tests them as a
DEFENSIVE/income sleeve and whether adding one improves a SPY-core blend. The honest question:
options overlays give better RISK-ADJUSTED return (Sharpe), not higher raw return.
Run: python research/backtest_options.py
"""
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")


def stats(x):
    x = x.dropna()
    if len(x) < 60: return (np.nan,)*4
    eq = (1+x).cumprod(); yrs = len(x)/252
    cagr = eq.iloc[-1]**(1/yrs)-1; vol = x.std()*np.sqrt(252)
    return cagr, (x.mean()*252)/(vol+1e-9), (eq/eq.cummax()-1).min(), x.skew()


def row(n, x, extra=""):
    c, s, d, sk = stats(x)
    print(f"  {n:30} {c*100:6.1f}% {s:6.2f} {d*100:7.1f}% {sk:6.2f}   {extra}")


def main():
    import yfinance as yf
    # CBOE indices + ETF proxies (whichever resolve)
    cand = ["^PUT","^BXM","^BXMD","PUTW","QYLD","XYLD","JEPI","DIVO","SPY","IEF"]
    px = yf.download(cand, start="2007-01-01", auto_adjust=True, progress=False)["Close"]
    px.index = pd.to_datetime(px.index).tz_localize(None)
    have = [c for c in cand if c in px and px[c].notna().sum() > 250]
    r = px[have].pct_change()
    print(f"\n=== Options (vol-risk-premium) strategies · resolved: {', '.join(have)} ===\n")
    print(f"  {'strategy':30} {'CAGR':>7} {'Sharpe':>6} {'maxDD':>8} {'skew':>6}")
    label = {"^PUT":"PutWrite ATM (PUT index)","^BXM":"Covered-call ATM (BXM)","^BXMD":"Covered-call 30d (BXMD)",
             "PUTW":"PutWrite ETF","QYLD":"Nasdaq covered-call (QYLD)","XYLD":"S&P covered-call (XYLD)",
             "JEPI":"JEPI (OTM cc+ELN)","DIVO":"DIVO (tactical cc)","SPY":"SPY buy-hold"}
    for t in have:
        if t == "IEF": continue
        row(label.get(t, t), r[t].fillna(0))

    # Does a put-write/covered-call sleeve improve a SPY core? (the real question)
    print()
    base = "^PUT" if "^PUT" in have else ("PUTW" if "PUTW" in have else None)
    if base and "SPY" in have:
        common = px[[base,"SPY"]].dropna().index
        rs = r.loc[common]
        print(f"  --- blend test on common window {common.min().date()}..{common.max().date()} ---")
        row("100% SPY", rs["SPY"])
        row(f"100% {base}", rs[base])
        row(f"70% SPY / 30% {base}", 0.7*rs["SPY"]+0.3*rs[base], f"corr {rs['SPY'].corr(rs[base]):+.2f}")
        row(f"50% SPY / 50% {base}", 0.5*rs["SPY"]+0.5*rs[base])
    print("\n  Honest read: vol-risk-premium = better SHARPE + smaller DD, NOT higher raw return,")
    print("  with MORE negative skew (fat left tail). Fits as a defensive/income sleeve, not a growth engine.")


if __name__ == "__main__":
    main()
