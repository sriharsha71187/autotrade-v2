#!/usr/bin/env python3
"""Test SPMO (quality momentum ETF) as a counterweight to the concentrated top-10 stock-momentum.
Shifts weight from stock-momentum -> SPMO and checks return/Sharpe/drawdown over the FULL cycle
(2017-26, incl 2018/2020/2022). 1.0x and 1.5x. Run: python research/backtest_spmo.py
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

    # configs: (label, stockmom_wt, spmo_wt) — rp 25 / vtq 25 / qld 10 fixed; mom+spmo split the remaining 40
    cfgs = [
        ("current  (30 stk-mom / 10 SPMO)", 0.30, 0.10),
        ("shift10  (20 stk-mom / 20 SPMO)", 0.20, 0.20),
        ("even     (20 stk-mom / 20 SPMO)*", 0.20, 0.20),
        ("shift20  (10 stk-mom / 30 SPMO)", 0.10, 0.30),
        ("all-SPMO ( 0 stk-mom / 40 SPMO)", 0.00, 0.40),
    ]
    def lever(x, L): return (L*x - (L-1)*(BORROW/252)).fillna(0)
    print(f"\n=== SPMO counterweight test · {idx.min().date()}..{idx.max().date()} (full cycle) ===\n")
    print(f"  {'config':36} {'--- 1.0x ---':>22}   {'--- 1.5x ---':>22}")
    print(f"  {'':36} {'CAGR':>6} {'Shrp':>5} {'maxDD':>8}  {'CAGR':>7} {'Shrp':>5} {'maxDD':>8}")
    for lbl, mw, sw in cfgs:
        if "even" in lbl: continue
        blend = 0.25*rp + mw*smom + 0.25*vtq + 0.10*qld + sw*spmo
        c1, s1, d1 = stats(blend); c2, s2, d2 = stats(lever(blend, 1.5))
        print(f"  {lbl:36} {c1*100:5.1f}% {s1:5.2f} {d1*100:7.1f}%  {c2*100:6.1f}% {s2:5.2f} {d2*100:7.1f}%")
    print("\n  rp 25 / vtq 25 / qld 10 fixed; stock-momentum and SPMO split the remaining 40%.")


if __name__ == "__main__":
    main()
