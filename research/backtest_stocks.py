#!/usr/bin/env python3
"""Long-term holds BEYOND indexes — systematic single-STOCK baskets + leverage. Tests stock
momentum baskets (hold the strongest names, not the index), with regime + vol-target risk
management, and a leveraged combo. vs SPY/QQQ. Uses our 740-name daily panel.
Run: python research/backtest_stocks.py
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe
COST = 0.0005


def stats(x):
    x = x.dropna(); eq = (1+x).cumprod(); yrs = len(x)/252
    return eq.iloc[-1]**(1/yrs)-1, (x.mean()*252)/(x.std()*np.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()


def row(n, x, extra=""):
    c, s, d = stats(x); print(f"  {n:38} {c*100:6.1f}% {s:6.2f} {d*100:7.1f}%   {extra}")


def main():
    import yfinance as yf
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    need = [t for t in ["SPY","QQQ","TQQQ"] if t not in px.columns]
    if need:
        bm = yf.download(need, start=str(px.index.min().date()), auto_adjust=True, progress=False)["Close"]
        if isinstance(bm, pd.Series): bm = bm.to_frame(need[0])
        bm.index = pd.to_datetime(bm.index).tz_localize(None).normalize()
        px = px.join(bm, how="left").ffill()
    r = px.pct_change()
    idx = px.index; mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    bull = (px["SPY"] > px["SPY"].rolling(200).mean()).shift(1).fillna(False)

    stocks = [c for c in px.columns if c not in ("SPY","QQQ","TQQQ")]
    mom = px[stocks].shift(21)/px[stocks].shift(252) - 1            # 12-1 momentum

    def basket(topn, regime, lev_etf=None):
        w = pd.DataFrame(0.0, index=idx, columns=stocks); cur = []
        for dt in idx:
            if dt in mstart and dt in mom.index:
                m = mom.loc[dt].dropna()
                cur = list(m.nlargest(topn).index) if len(m) >= topn else []
            if cur:
                on = (not regime) or bool(bull.loc[dt])
                if on:
                    for t in cur: w.loc[dt, t] = 1/len(cur)
        ret = (w.shift(1)*r[stocks]).sum(1) - COST*w.diff().abs().sum(1)
        if lev_etf:                                                # invest idle (regime-off) cash in a leveraged ETF when bull
            idle = 1 - w.sum(1)
            ret = ret + (idle.shift(1).clip(lower=0) * np.where(bull, r[lev_etf], 0))
        return ret.fillna(0)

    print(f"\n=== Long-term holds beyond indexes · {idx.min().date()}..{idx.max().date()} ===\n")
    print(f"  {'strategy':38} {'CAGR':>7} {'Sharpe':>6} {'maxDD':>8}")
    row("Stock momentum top-20 (monthly)", basket(20, False), "hold 20 strongest 12-1mo names")
    row("Stock momentum top-20 + regime", basket(20, True), "+ to cash when SPY<200DMA")
    row("Stock momentum top-10 + regime", basket(10, True), "more concentrated")
    # vol-targeted version of the regime basket
    b = basket(20, True); vt = (0.18/(b.rolling(20).std()*np.sqrt(252))).clip(0,2).shift(1).fillna(0)
    row("Stock momentum + regime + vol-tgt", (vt*b).fillna(0), "18% vol target")
    row("Momentum stocks + TQQQ idle-tilt", basket(20, True, "TQQQ"), "regime-off cash -> TQQQ when bull")
    print()
    for b_ in ["QQQ","SPY"]:
        row(f"BUY-HOLD {b_}", r[b_].fillna(0))
    eq = r[stocks].mean(1); row("equal-weight all stocks", eq.fillna(0))
    print("\n  CAVEAT: universe = today's index members (survivorship) -> absolute returns optimistic;")
    print("  read the SPREAD vs SPY/equal-weight + the Sharpe/DD shape. Momentum has crash risk.")


if __name__ == "__main__":
    main()
