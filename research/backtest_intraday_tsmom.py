#!/usr/bin/env python3
"""TRUE-intraday test of the one published intraday anomaly we never checked at real granularity:
intraday time-series momentum (Gao-Han-Li-Zhou 2018) — the FIRST half-hour's direction predicts
the LAST half-hour. Uses actual SPY 30-minute bars from Alpaca (IEX/SIP history).
Strategies (long/short $1 in the last half-hour only, 1bp/side):
  1. sign(first 30min) -> trade 15:30-16:00 same direction   (the published effect)
  2. sign(day so far 9:30-15:30) -> trade last 30min          (close momentum variant)
  3. combo: trade only when (1) and (2) agree
  4. high-vol filter: (1) only on days with above-median first-HH |move|
Run: python research/backtest_intraday_tsmom.py
"""
import math
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
import sys as _s, pathlib as _p; _s.path.insert(0, str(_p.Path(__file__).resolve().parent.parent))
import config as cfg
COST = 0.0002          # 1bp per side round trip


def stats(r):
    r = pd.Series(r).dropna(); r = r[r != 0]
    if len(r) < 50: return 0, 0, 0, 0, 0
    ann = r.mean()*252; sh = r.mean()/(r.std()+1e-12)*math.sqrt(252)
    eq = (1+r).cumprod(); dd = (eq/eq.cummax()-1).min()
    return ann, sh, dd, (r > 0).mean(), len(r)


def main():
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    import datetime as dt
    cli = StockHistoricalDataClient(cfg.ALPACA_API_KEY, cfg.ALPACA_SECRET_KEY)
    req = StockBarsRequest(symbol_or_symbols="SPY",
                           timeframe=TimeFrame(30, TimeFrameUnit.Minute),
                           start=dt.datetime(2016, 1, 1), end=dt.datetime(2026, 7, 1))
    bars = cli.get_stock_bars(req).df.reset_index()
    bars["ts"] = pd.to_datetime(bars["timestamp"]).dt.tz_convert("America/New_York")
    bars["date"] = bars["ts"].dt.date; bars["hm"] = bars["ts"].dt.strftime("%H:%M")
    piv = bars.pivot_table(index="date", columns="hm", values="close", aggfunc="last")
    po = bars.pivot_table(index="date", columns="hm", values="open", aggfunc="first")
    need = ["09:30", "10:00", "15:30", "16:00"]
    have = [c for c in need if c in piv.columns]
    df = pd.DataFrame({
        "o930": po.get("09:30"), "c1000": piv.get("09:30"),      # 09:30 bar = 09:30-10:00
        "c1530": piv.get("15:00"),                                # 15:00 bar closes at 15:30
        "o1530": po.get("15:30"), "c1600": piv.get("15:30"),      # 15:30 bar = last half hour
    }).dropna()
    print(f"  days with full intraday data: {len(df)}  ({df.index.min()} .. {df.index.max()})")
    fh = df["c1000"]/df["o930"] - 1                               # first half-hour
    day = df["c1530"]/df["o930"] - 1                              # 9:30 -> 15:30
    lh = df["c1600"]/df["o1530"] - 1                              # last half-hour (the trade)

    r1 = np.sign(fh)*lh - COST
    r2 = np.sign(day)*lh - COST
    agree = np.sign(fh) == np.sign(day)
    r3 = np.where(agree, np.sign(fh)*lh - COST, 0.0)
    hi = fh.abs() > fh.abs().rolling(252).median()
    r4 = np.where(hi, np.sign(fh)*lh - COST, 0.0)

    print(f"\n  {'strategy (trade = last 30min only)':44}{'ann.ret':>8}{'Sharpe':>8}{'maxDD':>7}{'win%':>6}{'days':>6}")
    for lbl, r in [("1. follow FIRST half-hour (published)", r1),
                   ("2. follow day-so-far", r2),
                   ("3. only when 1 & 2 agree", r3),
                   ("4. first-HH signal, high-vol days only", r4)]:
        ann, sh, dd, win, n = stats(pd.Series(np.asarray(r), index=df.index))
        print(f"  {lbl:44}{ann*100:7.1f}%{sh:8.2f}{dd*100:6.0f}%{win*100:5.0f}%{n:6}")
    # sub-period check: does it still work recently?
    half = len(df)//2
    for lbl, sl in [("   (1) first half of sample", slice(0, half)), ("   (1) second half of sample", slice(half, None))]:
        ann, sh, dd, win, n = stats(pd.Series(np.asarray(r1)[sl], index=df.index[sl]))
        print(f"  {lbl:44}{ann*100:7.1f}%{sh:8.2f}{dd*100:6.0f}%{win*100:5.0f}%{n:6}")
    print("\n  ann.ret is on the CAPITAL CYCLED THROUGH THE LAST 30 MINUTES (in cash 97% of the day).")


if __name__ == "__main__":
    main()
