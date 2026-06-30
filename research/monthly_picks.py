#!/usr/bin/env python3
"""Print this month's momentum picks (6-month momentum, top-10, ETFs/ETNs purged) in a paste-ready
format for the claude.ai rebalance skill. Cross-checks every candidate against an independent
yfinance read and DROPS data artifacts (unadjusted splits/mergers that fake huge momentum, e.g.
the AMCR +367%-vs-real-(-6%) glitch). Run: python research/monthly_picks.py
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe

NONSTOCK = {"VXX","SVXY","UVXY","SLV","GLD","GDX","USO","TLT","SPY","QQQ","IEF","DIA",  # ETFs/ETNs
            "LIQUIDBEES","BIL","SHV"}
ARTIFACT_PP = 80     # |panel 6mo - yfinance 6mo| > this (percentage pts) => corporate-action artifact, drop


def main():
    import yfinance as yf
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    stocks = [c for c in px.columns if c not in NONSTOCK]
    mom = px[stocks].shift(21)/px[stocks].shift(147) - 1          # 6-month momentum (skip last month)
    spy = px["SPY"]
    risk_on = bool(spy.iloc[-1] > spy.rolling(200).mean().iloc[-1])
    d = px.index[-1]

    # data-integrity guard: cross-check the top-25 candidates vs independent yfinance momentum
    cand = list(mom.iloc[-1].dropna().sort_values(ascending=False).index[:25])
    yfp = yf.download(cand, period="9mo", auto_adjust=True, progress=False)["Close"].ffill()
    picks, dropped = [], []
    for t in cand:
        pm = float(mom.iloc[-1][t]) * 100
        if t in yfp and yfp[t].notna().sum() > 147:
            ym = float(yfp[t].iloc[-21]/yfp[t].iloc[-147] - 1) * 100
            if abs(pm - ym) > ARTIFACT_PP:
                dropped.append((t, pm, ym)); continue
        picks.append(t)
        if len(picks) == 10: break

    new = picks if risk_on else []                                # risk-off -> hold nothing (cash)
    print(f"\n=== MOMENTUM PICKS · as of {d.date()} ===")
    print(f"  regime: {'RISK-ON (deploy)' if risk_on else 'RISK-OFF -> CASH out the account, no buys'}\n")
    for i, t in enumerate(picks, 1):
        print(f"  {i:2}. {t:6} ${float(px[t].iloc[-1]):>8.2f}   6mo +{float(mom.iloc[-1][t])*100:.0f}%")
    if dropped:
        print(f"\n  data-artifact(s) DROPPED (panel vs real momentum mismatch):")
        for t, pm, ym in dropped:
            print(f"    {t:6} panel {pm:+.0f}% vs real {ym:+.0f}%  -> excluded (likely unadjusted split/merger)")
    import json
    STATE = Path.home() / ".momentum_holdings.json"
    print(f"\n  --- PASTE INTO the claude.ai skill ---")
    print(f"  MOMENTUM PICKS: {', '.join(new) if new else '(none — RISK-OFF, sell to cash)'}")
    STATE.write_text(json.dumps({"held": new, "asof": str(d.date())}, indent=2))


if __name__ == "__main__":
    main()
