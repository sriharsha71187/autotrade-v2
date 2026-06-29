#!/usr/bin/env python3
"""Print this month's momentum picks (6-month momentum, top-10, ETFs/ETNs purged) in a paste-ready
format for the claude.ai monthly rebalance prompt. Run: python research/monthly_picks.py
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe

NONSTOCK = {"VXX","SVXY","UVXY","SLV","GLD","GDX","USO","TLT","SPY","QQQ","IEF","DIA",  # ETFs/ETNs
            "LIQUIDBEES","BIL","SHV"}


def main():
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    stocks = [c for c in px.columns if c not in NONSTOCK]
    mom = px[stocks].shift(21)/px[stocks].shift(147) - 1          # 6-month momentum (skip last month)
    spy = px["SPY"]
    risk_on = spy.iloc[-1] > spy.rolling(200).mean().iloc[-1]
    d = px.index[-1]
    top = list(mom.iloc[-1].dropna().sort_values(ascending=False).index[:10])
    print(f"\n=== MOMENTUM PICKS · as of {d.date()} ===")
    print(f"  regime: {'RISK-ON (deploy)' if risk_on else 'RISK-OFF -> go to CASH this month, skip the rebalance'}\n")
    for i, t in enumerate(top, 1):
        m = float(mom.iloc[-1][t]); p = float(px[t].iloc[-1])
        print(f"  {i:2}. {t:6} ${p:>8.2f}   6mo +{m*100:.0f}%")
    print(f"\n  PASTE THIS into the claude.ai prompt:")
    print(f"  MOMENTUM PICKS: {', '.join(top)}")
    if not risk_on:
        print(f"  (regime RISK-OFF — tell claude.ai to move the book to cash instead of buying)")


if __name__ == "__main__":
    main()
