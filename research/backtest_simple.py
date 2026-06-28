#!/usr/bin/env python3
"""Simplicity-vs-drawdown ladder: from 1 ticker (QLD+regime) up to the full 16-position growth
portfolio. Shows what each level of complexity actually buys in drawdown/Sharpe. Run: python research/backtest_simple.py
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe


def stats(x):
    x = x.dropna(); eq = (1+x).cumprod(); yrs = len(x)/252
    return eq.iloc[-1]**(1/yrs)-1, (x.mean()*252)/(x.std()*np.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()


def main():
    import yfinance as yf
    stk = fetch(universe()); stk.index = pd.to_datetime(stk.index).tz_localize(None).normalize()
    stk = stk.loc[:, stk.notna().sum() > 252]
    etfs = yf.download(["SPY","TLT","GLD","QLD","SPMO"], start=str(stk.index.min().date()), auto_adjust=True, progress=False)["Close"]
    etfs.index = pd.to_datetime(etfs.index).tz_localize(None).normalize()
    idx = stk.index.intersection(etfs.index); stk = stk.loc[idx]; E = etfs.loc[idx]
    rs = stk.pct_change(); re = E.pct_change()
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    bull = (E["SPY"] > E["SPY"].rolling(200).mean()).shift(1).fillna(False)

    # sleeves
    qld = (bull*re["QLD"]).fillna(0)
    a = ["SPY","TLT","GLD"]; iv = 1/re[a].rolling(60).std(); w = iv.div(iv.sum(1), axis=0)
    mk = np.array([d in mstart for d in idx]); w[~mk] = np.nan; w = w.ffill().shift(1).fillna(0)
    rpb = (w*re[a]).sum(1); lev = (0.12/(rpb.rolling(40).std()*np.sqrt(252))).clip(0,2.5).shift(1).fillna(0)
    rp = (lev*rpb).fillna(0)
    spmo = (bull*re["SPMO"]).fillna(0)
    stocks = [c for c in stk.columns if c not in ("SPY","QQQ")]
    mom = stk[stocks].shift(21)/stk[stocks].shift(252)-1; wsm = pd.DataFrame(0.0, index=idx, columns=stocks); cur=[]
    for dt in idx:
        if dt in mstart and dt in mom.index:
            m = mom.loc[dt].dropna(); cur = list(m.nlargest(10).index) if len(m)>=10 else []
        if cur and bool(bull.loc[dt]):
            for t in cur: wsm.loc[dt,t] = 1/len(cur)
    smom = (wsm.shift(1)*rs[stocks]).sum(1).fillna(0)

    blends = {
        "1 ticker:  QLD + regime": (qld, 1),
        "2 sleeves: 60% QLD+reg / 40% RP-TV": (0.6*qld + 0.4*rp, 4),
        "3 sleeves: 50/30/20 QLD/RP/SPMO": (0.5*qld + 0.3*rp + 0.2*spmo, 5),
        "3 sleeves: 50/30/20 QLD/RP/stk-mom": (0.5*qld + 0.3*rp + 0.2*smom, 14),
        "full 16-position growth port": (0.10*qld + 0.25*rp + 0.30*smom + 0.25*(0.22/(re['SPY'].rolling(20).std()*np.sqrt(252))).clip(0,2).shift(1).fillna(0)*re['SPY'] + 0.10*spmo, 16),
    }
    for win, start in [("2017-2026", "2017-01-01"), ("2010-2026", "2010-01-01")]:
        print(f"\n=== Simplicity ladder · {win} ===")
        print(f"  {'portfolio':40} {'#tk':>4} {'CAGR':>7} {'Sharpe':>6} {'maxDD':>8}")
        m = idx >= start
        for n, (s, ntk) in blends.items():
            c, sh, dd = stats(s[m])
            print(f"  {n:40} {ntk:>4} {c*100:6.1f}% {sh:6.2f} {dd*100:7.1f}%")
        cs, ss, ds = stats(re["SPY"][m]); print(f"  {'SPY buy-hold':40} {1:>4} {cs*100:6.1f}% {ss:6.2f} {ds*100:7.1f}%")
    print("\n  #tk = approx distinct tickers to hold. Read: how much drawdown does each step of complexity buy?")


if __name__ == "__main__":
    main()
