#!/usr/bin/env python3
"""Re-run the signal comparison on a DE-BIASED universe: today's members PLUS major S&P names that
declined/failed/were-removed 2017-2026 (the crashers a survivor-universe wrongly excludes). Each
extra name is pickable only while it has price data, and the strategy eats its decline (delisted
names go NaN after their last trade). Tests whether crash-protection signals (vol-scaled/residual)
beat raw momentum once the crashers are present. Run: python research/backtest_signals_debiased.py
"""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe
COST = 0.0005; BORROW = 0.065

# S&P 500/400 names removed for DECLINE/FAILURE 2017-2026 (the survivorship-critical crashers)
EXTRA = ["SIVB","FRC","SBNY","BBBY","SEDG","ENPH","WBA","PARA","VFC","UAA","UA","GPS","KSS","M","JWN",
         "FL","XRX","MAT","NWL","DXC","COTY","AAP","LUMN","HBI","LNC","GNRC","ZION","CMA","ALGN","WHR",
         "DISH","RIVN","WBD","PYPL","MRNA","DLTR","ETSY","CZR","WYNN","BEN","IPG","MHK","CE","APA","DVA"]


def stats(r):
    r = r.dropna(); eq = (1+r).cumprod(); yrs = len(r)/252
    return eq.iloc[-1]**(1/yrs)-1, (r.mean()*252)/(r.std()*math.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()


def main():
    import yfinance as yf
    base = fetch(universe()); base.index = pd.to_datetime(base.index).tz_localize(None).normalize()
    base = base.loc[:, base.notna().sum() > 252]
    add_tk = [t for t in EXTRA if t not in base.columns]
    ex = yf.download(add_tk, start=str(base.index.min().date()), auto_adjust=True, progress=False)["Close"]
    ex.index = pd.to_datetime(ex.index).tz_localize(None).normalize()
    ex = ex.reindex(base.index)                                   # align; NO ffill -> delisted go NaN after last trade
    got = [t for t in add_tk if t in ex.columns and ex[t].notna().sum() > 252]
    px = pd.concat([base, ex[got]], axis=1)
    print(f"  universe: {base.shape[1]-2} survivors + {len(got)} added decliners/delisted = {px.shape[1]-2} names")

    idx = px.index; stocks = [c for c in px.columns if c not in ("SPY","QQQ")]
    r = px.pct_change(); rs = r[stocks]; rspy = r["SPY"]
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    bull = (px["SPY"] > px["SPY"].rolling(200).mean()).shift(1).fillna(False)
    mom6 = px[stocks].shift(21)/px[stocks].shift(147) - 1; vol6 = rs.rolling(126).std()
    beta = rs.rolling(120).cov(rspy).div(rspy.rolling(120).var(), axis=0)
    resid = rs.sub(beta.mul(rspy, axis=0))
    updays = (rs > 0).shift(21).rolling(126).mean()
    sig = {"raw 6mo momentum": mom6, "vol-scaled momentum": mom6/vol6,
           "residual momentum": resid.shift(21).rolling(126).sum(), "smooth momentum": mom6*updays}

    def run(s):
        w = pd.DataFrame(0.0, index=idx, columns=stocks); cur = []; picks=set()
        for dt in idx:
            if dt in mstart and dt in s.index:
                m = s.loc[dt].replace([np.inf,-np.inf], np.nan).dropna()
                cur = list(m.nlargest(10).index) if len(m) >= 10 else []
                picks.update(cur)
            if cur and bool(bull.loc[dt]):
                for t in cur: w.loc[dt, t] = 1/len(cur)
        ret = ((w.shift(1)*rs).sum(1) - COST*w.diff().abs().sum(1)).fillna(0)
        n_extra = len(picks & set(got))
        return (1.5*ret - 0.5*(BORROW/252)).fillna(0), n_extra

    print(f"\n  === Signal comparison · DE-BIASED universe · 1.5x · {idx.min().date()}..{idx.max().date()} ===\n")
    print(f"  {'signal':24}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}{'extra-picked':>13}")
    for name, s in sig.items():
        ret, ne = run(s); c, sh, dd = stats(ret)
        print(f"  {name:24}{c*100:7.1f}%{sh:7.2f}{dd*100:7.0f}%{ne:>11}")
    print("\n  extra-picked = how many of the added decliners the signal actually bought (did de-biasing bite?).")
    print("  If vol-scaled/residual now beat raw momentum, survivorship was masking their edge.")


if __name__ == "__main__":
    main()
