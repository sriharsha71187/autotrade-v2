#!/usr/bin/env python3
"""Is there a better monthly-basket signal than raw price momentum? Tests documented variants
head-to-head: raw 6mo momentum, vol-scaled, residual (idiosyncratic), 52wk-high proximity, smooth
(frog-in-the-pan), and a blend. Top-10 monthly, regime-gated, 1.5x leverage, same US universe (so
the comparison is fair despite survivorship). Run: python research/backtest_signals.py
"""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe
COST = 0.0005; BORROW = 0.065


def stats(r):
    r = r.dropna(); eq = (1+r).cumprod(); yrs = len(r)/252
    return eq.iloc[-1]**(1/yrs)-1, (r.mean()*252)/(r.std()*math.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()


def main():
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    idx = px.index; stocks = [c for c in px.columns if c not in ("SPY","QQQ")]
    r = px.pct_change(); rs = r[stocks]; rspy = r["SPY"]
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    bull = (px["SPY"] > px["SPY"].rolling(200).mean()).shift(1).fillna(False)

    mom6 = px[stocks].shift(21)/px[stocks].shift(147) - 1
    vol6 = rs.rolling(126).std()
    sig = {}
    sig["raw 6mo momentum"] = mom6
    sig["vol-scaled momentum"] = mom6 / vol6
    # residual momentum: strip market beta, cumulate idiosyncratic return (t-147..t-21)
    beta = rs.rolling(120).cov(rspy).div(rspy.rolling(120).var(), axis=0)
    resid = rs.sub(beta.mul(rspy, axis=0))
    sig["residual momentum"] = resid.shift(21).rolling(126).sum()
    # 52-week-high proximity (skip last month)
    sig["52wk-high proximity"] = (px[stocks].shift(21)/px[stocks].rolling(252).max().shift(21))
    # smooth momentum: momentum * fraction of up-days (frog-in-the-pan)
    updays = (rs > 0).shift(21).rolling(126).mean()
    sig["smooth momentum"] = mom6 * updays
    # blend: average z-score of the above
    def zx(p): return p.sub(p.mean(1), 0).div(p.std(1), 0)
    sig["BLEND (z-avg)"] = (zx(sig["raw 6mo momentum"]) + zx(sig["vol-scaled momentum"])
                            + zx(sig["residual momentum"]) + zx(sig["smooth momentum"]))/4

    def run(s):
        w = pd.DataFrame(0.0, index=idx, columns=stocks); cur = []
        for dt in idx:
            if dt in mstart and dt in s.index:
                m = s.loc[dt].replace([np.inf, -np.inf], np.nan).dropna()
                cur = list(m.nlargest(10).index) if len(m) >= 10 else []
            if cur and bool(bull.loc[dt]):
                for t in cur: w.loc[dt, t] = 1/len(cur)
        ret = ((w.shift(1)*rs).sum(1) - COST*w.diff().abs().sum(1)).fillna(0)
        return (1.5*ret - 0.5*(BORROW/252)).fillna(0)

    print(f"\n=== Signal comparison · top-10 monthly · regime-gated · 1.5x · {idx.min().date()}..{idx.max().date()} ===\n")
    print(f"  {'signal':24}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}")
    for name, s in sig.items():
        c, sh, dd = stats(run(s))
        star = "  <- baseline" if name == "raw 6mo momentum" else ""
        print(f"  {name:24}{c*100:7.1f}%{sh:7.2f}{dd*100:7.0f}%{star}")
    print("\n  Survivorship inflates ALL equally -> the RELATIVE ranking (which beats raw momentum) is the signal.")


if __name__ == "__main__":
    main()
