#!/usr/bin/env python3
"""Two requested configs at 1.5x margin vs the current deploy:
  A) 50% momentum stocks + 50% other sleeves
  B) 100% momentum stocks only
All with 1.5x leverage (6.5% borrow netted). Run: python research/backtest_configs.py
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe
COST = 0.0005; BORROW = 0.065


def stats(x):
    x = x.dropna(); eq = (1+x).cumprod(); yrs = len(x)/252
    return eq.iloc[-1]**(1/yrs)-1, (x.mean()*252)/(x.std()*np.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()


def lev(ret, L=1.5):
    return (L*ret - (L-1)*(BORROW/252)).fillna(0)


def row(n, ret, L=1.5):
    c, s, d = stats(lev(ret, L)); print(f"  {n:42} {c*100:6.1f}% {s:6.2f} {d*100:7.1f}%")


def main():
    import yfinance as yf
    stk = fetch(universe()); stk.index = pd.to_datetime(stk.index).tz_localize(None).normalize()
    stk = stk.loc[:, stk.notna().sum() > 252]
    E = yf.download(["SPY","TLT","GLD","QQQ","QLD","SPMO"], start=str(stk.index.min().date()), auto_adjust=True, progress=False)["Close"]
    E.index = pd.to_datetime(E.index).tz_localize(None).normalize()
    idx = stk.index.intersection(E.index); stk = stk.loc[idx]; E = E.loc[idx]
    rs = stk.pct_change(); re = E.pct_change()
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    bull = (E["SPY"] > E["SPY"].rolling(200).mean()).shift(1).fillna(False)
    stocks = [c for c in stk.columns if c not in ("SPY","QQQ")]
    mom = stk[stocks].shift(21)/stk[stocks].shift(252)-1

    # sleeves
    vtq = ((0.22/(re['SPY'].rolling(20).std()*np.sqrt(252))).clip(0,2).shift(1).fillna(0)*re['SPY']).fillna(0)
    qld = (bull*re["QLD"]).fillna(0); spmo = (bull*re["SPMO"]).fillna(0)
    a = ["SPY","TLT","GLD"]; iv = 1/re[a].rolling(60).std(); w = iv.div(iv.sum(1), axis=0)
    mk = np.array([d in mstart for d in idx]); w[~mk] = np.nan; w = w.ffill().shift(1).fillna(0)
    rpb = (w*re[a]).sum(1); lv = (0.12/(rpb.rolling(40).std()*np.sqrt(252))).clip(0,2.5).shift(1).fillna(0)
    rp = (lv*rpb).fillna(0)
    wsm = pd.DataFrame(0.0, index=idx, columns=stocks); cur=[]
    for dt in idx:
        if dt in mstart and dt in mom.index:
            m = mom.loc[dt].dropna(); cur = list(m.nlargest(10).index) if len(m)>=10 else []
        if cur and bool(bull.loc[dt]):
            for t in cur: wsm.loc[dt, t] = 1/len(cur)
    smom = ((wsm.shift(1)*rs[stocks]).sum(1) - COST*wsm.diff().abs().sum(1)).fillna(0)

    other = (rp + vtq + qld*0.0 + spmo)  # placeholder, replaced below
    # "other sleeves" normalized basket (rp/vtq/qld/spmo in 25/25/10/10 proportion = the non-momentum part)
    other = (0.25*rp + 0.25*vtq + 0.10*qld + 0.10*spmo) / 0.70  # = unit "other-sleeves" return stream

    current = 0.25*rp + 0.30*smom + 0.25*vtq + 0.10*qld + 0.10*spmo
    cfgA = 0.50*smom + 0.50*other
    cfgB = smom

    print(f"\n=== Configs @ 1.5x margin ({BORROW*100:.1f}% borrow) · {idx.min().date()}..{idx.max().date()} ===\n")
    print(f"  {'portfolio':42} {'CAGR':>7} {'Sharpe':>6} {'maxDD':>8}")
    row("A) 50% momentum + 50% other  @1.5x", cfgA)
    row("B) 100% momentum only        @1.5x", cfgB)
    print()
    row("current deploy (30% mom)     @1.5x", current)
    row("current deploy (30% mom)     @1.0x", current, L=1.0)
    row("SPY buy-hold                 @1.0x", re["SPY"].fillna(0), L=1.0)
    print("\n  momentum-heavy = more return + much deeper drawdown; leverage amplifies both. 1.0x rows for reference.")


if __name__ == "__main__":
    main()
