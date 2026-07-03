#!/usr/bin/env python3
"""Print this month's picks for the claude.ai rebalance skill: 7 CORE names (6-month momentum)
+ 3 FAST-TRACK names (3-month momentum, not already core) = 10 tickers. Backtested 2026-07-03:
7+3 hybrid 38.1%/1.23 Sharpe vs 35.1%/1.15 pure 6mo; fast slots capped at 3 to bound whipsaw.
Both signals are ETF-purged and cross-checked against an independent yfinance read; candidates
whose panel momentum diverges >80pp from the real number are DROPPED as corporate-action data
artifacts (the AMCR/DELL class of glitch). Run: python research/monthly_picks.py
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe

NONSTOCK = {"VXX","SVXY","UVXY","SLV","GLD","GDX","USO","TLT","SPY","QQQ","IEF","DIA",  # ETFs/ETNs
            "LIQUIDBEES","BIL","SHV","XLE","XLF","XLK","SMH","SOXX","XOP","XLU"}
ARTIFACT_PP = 80     # |panel mom - yfinance mom| > this (pp) on the ranking window => drop
N_CORE, N_FAST = 7, 3


def main():
    import yfinance as yf
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    stocks = [c for c in px.columns if c not in NONSTOCK]
    m6 = (px[stocks].shift(21)/px[stocks].shift(147) - 1).iloc[-1]    # 6mo momentum (skip last month)
    m3 = (px[stocks].shift(21)/px[stocks].shift(84) - 1).iloc[-1]     # 3mo momentum (skip last month)
    spy = px["SPY"]
    risk_on = bool(spy.iloc[-1] > spy.rolling(200).mean().iloc[-1])
    d = px.index[-1]

    cand6 = list(m6.dropna().sort_values(ascending=False).index[:25])
    cand3 = list(m3.dropna().sort_values(ascending=False).index[:15])
    yfp = yf.download(sorted(set(cand6+cand3)), period="9mo", auto_adjust=True, progress=False)["Close"].ffill()

    def real_mom(t, back):
        if t in yfp and yfp[t].notna().sum() > back:
            return float(yfp[t].iloc[-21]/yfp[t].iloc[-back] - 1) * 100
        return None

    dropped = []
    def clean(cands, mom, back):
        out = []
        for t in cands:
            pm = float(mom[t]) * 100; ym = real_mom(t, back)
            if ym is not None and abs(pm - ym) > ARTIFACT_PP:
                dropped.append((t, pm, ym)); continue
            out.append(t)
        return out

    core = clean(cand6, m6, 147)[:N_CORE]
    fast = [t for t in clean(cand3, m3, 84) if t not in core][:N_FAST]
    picks = core + fast
    new = picks if risk_on else []

    print(f"\n=== MOMENTUM PICKS · 7 core + 3 fast-track · as of {d.date()} ===")
    print(f"  regime: {'RISK-ON (deploy)' if risk_on else 'RISK-OFF -> CASH out the account, no buys'}\n")
    for i, t in enumerate(picks, 1):
        tag = "core" if t in core else "FAST"
        m = float((m6 if t in core else m3)[t]) * 100; win = "6mo" if t in core else "3mo"
        print(f"  {i:2}. {t:6} ${float(px[t].iloc[-1]):>8.2f}   {win} +{m:.0f}%   [{tag}]")
    if dropped:
        print(f"\n  data-artifact(s) DROPPED (panel vs real momentum mismatch):")
        for t, pm, ym in dropped:
            print(f"    {t:6} panel {pm:+.0f}% vs real {ym:+.0f}%  -> excluded (likely unadjusted split/merger)")
    import json
    STATE = Path.home() / ".momentum_holdings.json"
    print(f"\n  --- PASTE INTO the claude.ai skill ---")
    print(f"  MOMENTUM PICKS: {', '.join(new) if new else '(none — RISK-OFF, sell to cash)'}")
    if new and fast:
        print(f"  (fast-track names — early-stage 3mo movers, give extra veto scrutiny: {', '.join(fast)})")
    STATE.write_text(json.dumps({"held": new, "core": core if risk_on else [], "fast": fast if risk_on else [],
                                 "asof": str(d.date())}, indent=2))


if __name__ == "__main__":
    main()
