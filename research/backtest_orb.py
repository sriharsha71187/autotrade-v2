#!/usr/bin/env python3
"""Opening Range Breakout (ORB) on QQQ — long-only, the one intraday strategy with real evidence
(Zarattini-Aziz 2023). Rule: first 5-min bar (09:30-09:35 ET); if it closes UP, enter long at the
09:35 open; stop = first-bar low; exit at EOD close. Long-only (short leg is cost-killed). Uses
Alpaca minute bars. Honest: feed-fragile + slippage-critical per the research. Run: python research/backtest_orb.py
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as cfg


def main():
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    import datetime as dt
    cl = StockHistoricalDataClient(cfg.ALPACA_API_KEY, cfg.ALPACA_SECRET_KEY)
    end = dt.datetime(2026, 6, 25); start = end - dt.timedelta(days=730)
    req = StockBarsRequest(symbol_or_symbols="QQQ", timeframe=TimeFrame(5, TimeFrame.Minute.unit),
                           start=start, end=end)
    df = cl.get_stock_bars(req).df
    if df.empty:
        print("no minute data available"); return
    df = df.reset_index()
    df["t"] = pd.to_datetime(df["timestamp"]).dt.tz_convert("America/New_York")
    df["day"] = df["t"].dt.date; df["hm"] = df["t"].dt.strftime("%H:%M")
    df = df[(df["hm"] >= "09:30") & (df["hm"] <= "16:00")]

    trades = []
    for day, g in df.groupby("day"):
        g = g.sort_values("t")
        b1 = g[g["hm"] == "09:30"]
        if b1.empty: continue
        b1 = b1.iloc[0]
        nxt = g[g["hm"] == "09:35"]
        if nxt.empty: continue
        if b1["close"] <= b1["open"]: continue              # long-only: first bar must close UP
        entry = nxt.iloc[0]["open"]; stop = b1["low"]
        rest = g[g["hm"] >= "09:35"]
        low_after = rest["low"].min(); eod = rest.iloc[-1]["close"]
        exitpx = stop if low_after <= stop else eod          # stopped or EOD
        trades.append((entry, exitpx, exitpx/entry - 1))

    if not trades:
        print("no ORB trades generated"); return
    rets = np.array([t[2] for t in trades]) - 0.0003          # ~3bps round-trip cost
    eq = (1+rets).cumprod(); yrs = len(rets)/252
    cagr = eq[-1]**(1/yrs)-1 if yrs > 0 else np.nan
    sharpe = rets.mean()/rets.std()*np.sqrt(252) if rets.std() else 0
    dd = (pd.Series(eq)/pd.Series(eq).cummax()-1).min()
    print(f"\n=== ORB long-only QQQ · {df['day'].min()}..{df['day'].max()} · {len(trades)} trading days ===\n")
    print(f"  trades {len(trades)} · win {(rets>0).mean()*100:.0f}% · avg/day {rets.mean()*100:+.3f}% · "
          f"avg WIN +{rets[rets>0].mean()*100:.2f}% · avg LOSS {rets[rets<=0].mean()*100:.2f}%")
    print(f"  approx ann return {cagr*100:.1f}% · Sharpe {sharpe:.2f} · maxDD {dd*100:.1f}%  (vs QQQ buy-hold)")
    print("\n  CAVEAT (per research): feed-fragile (>3x dispersion across data vendors), slippage-critical")
    print("  on the breakout fill, Sharpe drops 2.8->~1.0 out-of-sample. 2-year sample only here.")


if __name__ == "__main__":
    main()
