#!/usr/bin/env python3
"""The honest benchmark check: do simple, KNOWN beta tilts beat SPY? Leverage (QLD/TQQQ),
Nasdaq (QQQ), momentum factor (MTUM/SPMO) — buy-and-hold AND with a 200DMA regime filter
(de-risk to cash in downtrends so leverage doesn't blow up). This is the 'sound logic, ride
the bus' approach, with risk management. Run: python research/backtest_beta.py
"""
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")

TKS = ["SPY", "QQQ", "QLD", "TQQQ", "MTUM", "SPMO"]


def metrics(ret):
    ret = ret.dropna()
    eq = (1 + ret).cumprod(); yrs = len(ret)/252
    cagr = eq.iloc[-1]**(1/yrs) - 1
    vol = ret.std()*np.sqrt(252); sharpe = (ret.mean()*252)/vol if vol > 0 else 0
    dd = (eq/eq.cummax() - 1).min()
    return cagr, vol, sharpe, dd


def line(name, ret):
    c, v, s, d = metrics(ret)
    return f"  {name:26} CAGR {c*100:6.1f}%  vol {v*100:5.1f}%  Sharpe {s:5.2f}  maxDD {d*100:6.1f}%"


def main():
    import yfinance as yf
    px = yf.download(TKS, start="2015-01-01", auto_adjust=True, progress=False)["Close"]
    px.index = pd.to_datetime(px.index).tz_localize(None)
    r = px.pct_change()
    spy_bull = (px["SPY"] > px["SPY"].rolling(200).mean()).shift(1).fillna(False)  # regime, lagged

    print(f"\n=== Known beta tilts vs SPY · {px.index.min().date()}..{px.index.max().date()} ===\n")
    print("  -- buy & hold --")
    for t in TKS:
        if t in px: print(line(t, r[t]))
    print("\n  -- with 200DMA regime filter (hold ETF when SPY>200DMA, else cash) --")
    for t in ["QQQ", "QLD", "TQQQ", "MTUM", "SPMO"]:
        if t in px:
            rm = r[t].where(spy_bull, 0.0)
            print(line(f"{t} + regime", rm))
    print("\n  Honest caveats: 2015-26 was a historic bull; leverage cuts BOTH ways (TQQQ ~-80% in 2022,")
    print("  why the regime filter matters). The regime filter trades whipsaw for survivable drawdown.")


if __name__ == "__main__":
    main()
