#!/usr/bin/env python3
"""Can we catch SNDK-type runs EARLIER than 6mo momentum without paying more in whipsaw than
we gain in early entry? Variants (top-10, monthly, regime-gated, 1.0x, same universe):
  A. 6mo momentum (baseline — enters ~month 2-3 of a run)
  B. 3mo momentum (enters ~month 1-2, noisier)
  C. fast blend: mean pct-rank of 3mo & 6mo
  D. fast-track: 6mo top-10, but any name in the 3mo TOP-5 not already held replaces the
     lowest-ranked 6mo pick (catches fresh explosions a month early)
  E. 80% 6mo core + 20% gap-continuation satellite (buys +8% gappers instantly, holds 21d)
Run: python research/backtest_earlycatch.py
"""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe
COST = 0.0005; GCOST = 0.0010


def stats(r):
    r = r.dropna(); eq = (1+r).cumprod(); yrs = len(r)/252
    return eq.iloc[-1]**(1/yrs)-1, (r.mean()*252)/(r.std()*math.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()


def main():
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    idx = px.index; stocks = [c for c in px.columns if c not in ("SPY","QQQ")]
    rs = px[stocks].pct_change()
    m6 = px[stocks].shift(21)/px[stocks].shift(147) - 1
    m3 = px[stocks].shift(21)/px[stocks].shift(84) - 1
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    bull = (px["SPY"] > px["SPY"].rolling(200).mean()).shift(1).fillna(False)

    def basket_run(picker):
        w = pd.DataFrame(0.0, index=idx, columns=stocks); cur = []
        for dt in idx:
            if dt in mstart and dt in m6.index:
                cur = picker(dt) or cur
            if cur and bool(bull.loc[dt]):
                for t in cur: w.loc[dt, t] = 1/len(cur)
        return ((w.shift(1)*rs).sum(1) - COST*w.diff().abs().sum(1)).fillna(0)

    def pick6(dt):
        m = m6.loc[dt].dropna(); return list(m.nlargest(10).index) if len(m) >= 10 else None
    def pick3(dt):
        m = m3.loc[dt].dropna(); return list(m.nlargest(10).index) if len(m) >= 10 else None
    def pickblend(dt):
        a = m6.loc[dt].dropna().rank(pct=True); b = m3.loc[dt].dropna().rank(pct=True)
        m = pd.concat([a, b], axis=1).mean(1).dropna()
        return list(m.nlargest(10).index) if len(m) >= 10 else None
    def pickfast(dt):
        m = m6.loc[dt].dropna(); f = m3.loc[dt].dropna()
        if len(m) < 10 or len(f) < 5: return None
        core = list(m.nlargest(10).index)
        fast = [t for t in f.nlargest(5).index if t not in core]
        return (core[:10-len(fast)] + fast) if fast else core

    # E: 80% core + 20% gap satellite
    ev = (rs > 0.08).shift(1).fillna(False)
    wg = pd.DataFrame(0.0, index=idx, columns=stocks)
    for k in range(21): wg += ev.shift(k).fillna(False).astype(float)
    wg = wg.clip(0, 1); n = wg.sum(1).replace(0, np.nan); wg = wg.div(n, axis=0).fillna(0)
    wg = wg.mul(bull.astype(float), axis=0)
    rgap = ((wg.shift(1)*rs).sum(1) - GCOST*wg.diff().abs().sum(1)).fillna(0)

    rA = basket_run(pick6)
    combos = [("A. 6mo baseline", rA), ("B. 3mo (earlier entry)", basket_run(pick3)),
              ("C. blend 3+6mo rank", basket_run(pickblend)),
              ("D. 6mo + 3mo-top5 fast-track", basket_run(pickfast)),
              ("E. 80% 6mo + 20% gap-satellite", 0.8*rA + 0.2*rgap)]
    print(f"\n=== Catching runs earlier · top-10 · regime-gated · 1.0x · {idx.min().date()}..{idx.max().date()} ===\n")
    print(f"  {'variant':36}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}")
    for lbl, r in combos:
        c, sh, dd = stats(r); print(f"  {lbl:36}{c*100:7.1f}%{sh:7.2f}{dd*100:7.0f}%")
    print("\n  Survivorship inflates all rows equally — read the RELATIVE ranking.")


if __name__ == "__main__":
    main()
