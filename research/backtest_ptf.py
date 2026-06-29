#!/usr/bin/env python3
"""Does PTF's less-concentrated / quarterly approach trade return for a smoother ride?
(1) Reconstructed grid on OUR universe (same 6mo-momentum signal, regime-gated, equal-weight, 1.0x):
    concentration {top-10, top-30} x frequency {monthly, quarterly}.
(2) Real funds (yfinance, survivorship-FREE truth): PTF, SPMO, MTUM, QQQ, SPY over a common window.
Run: python research/backtest_ptf.py
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
    r = px.pct_change(); rs = r[stocks]
    mom6 = px[stocks].shift(21)/px[stocks].shift(147) - 1
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    qtr_dates = [d for d in mstart if d.month in (1,4,7,10)]
    bull = (px["SPY"] > px["SPY"].rolling(200).mean()).shift(1).fillna(False)

    def run(topn, rebal):
        w = pd.DataFrame(0.0, index=idx, columns=stocks); cur = []
        for dt in idx:
            if dt in rebal and dt in mom6.index:
                m = mom6.loc[dt].dropna(); cur = list(m.nlargest(topn).index) if len(m) >= topn else []
            if cur and bool(bull.loc[dt]):
                for t in cur: w.loc[dt, t] = 1/len(cur)
        return ((w.shift(1)*rs).sum(1) - COST*w.diff().abs().sum(1)).fillna(0)

    print(f"\n=== (1) Reconstructed · same universe & 6mo signal · regime-gated · 1.0x · {idx.min().date()}..{idx.max().date()} ===\n")
    print(f"  {'config':32}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}")
    for lbl, n, rb in [("top-10 monthly  (your strategy)", 10, mstart), ("top-30 monthly", 30, mstart),
                       ("top-10 quarterly", 10, qtr_dates), ("top-30 quarterly (PTF-like)", 30, qtr_dates)]:
        c, sh, dd = stats(run(n, rb)); print(f"  {lbl:32}{c*100:7.1f}%{sh:7.2f}{dd*100:7.0f}%")

    # ---- (2) real funds ----
    import yfinance as yf
    f = yf.download(["PTF","SPMO","MTUM","QQQ","SPY"], start="2015-01-01", auto_adjust=True, progress=False)["Close"]
    f.index = pd.to_datetime(f.index).tz_localize(None); f = f.dropna()
    fr = f.pct_change()
    print(f"\n=== (2) REAL funds (survivorship-free) · {f.index[0].date()}..{f.index[-1].date()} ===\n")
    print(f"  {'fund':28}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}")
    for t in ["PTF","SPMO","MTUM","QQQ","SPY"]:
        c, sh, dd = stats(fr[t])
        note = {"PTF":" tech RS momentum","SPMO":" S&P momentum","MTUM":" vol-scaled mom"}.get(t,"")
        print(f"  {t+note:28}{c*100:7.1f}%{sh:7.2f}{dd*100:7.0f}%")
    print("\n  Reconstructed = survivorship-inflated (relative shape is the signal). Real funds = the honest level.")


if __name__ == "__main__":
    main()
