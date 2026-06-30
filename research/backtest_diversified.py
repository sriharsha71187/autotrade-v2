#!/usr/bin/env python3
"""Diversified momentum: same 6mo-momentum signal, but build the 10-name basket greedily by
momentum WHILE skipping any name too correlated to those already picked (caps one-theme pile-ups
like 'all semis'). Compare concentrated top-10 vs decorrelated baskets. Regime-gated, 1.0x.
Run: python research/backtest_diversified.py
"""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe
COST = 0.0005


def stats(r):
    r = r.dropna(); eq = (1+r).cumprod(); yrs = len(r)/252
    return eq.iloc[-1]**(1/yrs)-1, (r.mean()*252)/(r.std()*math.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()


def main():
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    idx = px.index; stocks = [c for c in px.columns if c not in ("SPY","QQQ")]
    rs = px[stocks].pct_change()
    mom = px[stocks].shift(21)/px[stocks].shift(147) - 1
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    bull = (px["SPY"] > px["SPY"].rolling(200).mean()).shift(1).fillna(False)

    def pick(d0, n=10, cmax=None):
        cands = list(mom.loc[d0].dropna().sort_values(ascending=False).index[:50])
        if cmax is None:
            return cands[:n]
        i = idx.get_loc(d0); win = rs[cands].iloc[max(0,i-120):i]
        corr = win.corr().abs()
        basket = []
        for t in cands:
            if len(basket) == n: break
            if not basket or corr.loc[t, basket].max() < cmax: basket.append(t)
        for t in cands:                                    # backfill if too strict
            if len(basket) == n: break
            if t not in basket: basket.append(t)
        return basket

    def run(cmax):
        w = pd.DataFrame(0.0, index=idx, columns=stocks); cur = []
        for dt in idx:
            if dt in mstart and dt in mom.index:
                cur = pick(dt, 10, cmax)
            if cur and bool(bull.loc[dt]):
                for t in cur: w.loc[dt, t] = 1/len(cur)
        return ((w.shift(1)*rs).sum(1) - COST*w.diff().abs().sum(1)).fillna(0)

    print(f"\n=== Diversified momentum · top-10 · regime-gated · 1.0x · {idx.min().date()}..{idx.max().date()} ===\n")
    print(f"  {'basket':34}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}")
    for lbl, cm in [("concentrated (pure top-10)", None), ("decorrelated  (corr<0.7)", 0.7),
                    ("decorrelated  (corr<0.5)", 0.5)]:
        c, sh, dd = stats(run(cm)); print(f"  {lbl:34}{c*100:7.1f}%{sh:7.2f}{dd*100:7.0f}%")

    # current-month baskets + how concentrated each is (avg pairwise correlation)
    d0 = mstart[-1]; i = idx.get_loc(d0)
    def avgcorr(b):
        w = rs[b].iloc[max(0,i-120):i].corr().abs().values
        return (w.sum()-len(b))/(len(b)*(len(b)-1))
    conc = pick(d0, 10, None); div = pick(d0, 10, 0.5)
    print(f"\n  THIS MONTH:")
    print(f"    concentrated: {', '.join(conc)}\n      avg pairwise corr {avgcorr(conc):.2f}  (high = one theme)")
    print(f"    decorrelated: {', '.join(div)}\n      avg pairwise corr {avgcorr(div):.2f}  (lower = spread across themes)")
    print("\n  Survivorship inflates levels; read the dCAGR / dDD between baskets, not the absolute number.")


if __name__ == "__main__":
    main()
