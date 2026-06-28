#!/usr/bin/env python3
"""Does an options OVERLAY (via ETF proxies) improve the leveraged growth portfolio? Tests adding
a tail hedge (VIXM/TAIL) or swapping growth for covered-call income (JEPI/DIVO) to the growth
blend at 1.5x. The portfolio's weakness is its -38% drawdown -> does a hedge improve Sharpe?
Run: python research/backtest_optoverlay.py
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


def lev(x, L): return (L*x - (L-1)*(BORROW/252)).fillna(0)


def main():
    import yfinance as yf
    stk = fetch(universe()); stk.index = pd.to_datetime(stk.index).tz_localize(None).normalize()
    stk = stk.loc[:, stk.notna().sum() > 252]
    extra = ["SPY","TLT","GLD","QQQ","QLD","SPMO","VIXM","TAIL","JEPI","DIVO"]
    E = yf.download(extra, start=str(stk.index.min().date()), auto_adjust=True, progress=False)["Close"]
    E.index = pd.to_datetime(E.index).tz_localize(None).normalize()
    idx = stk.index.intersection(E.index); stk = stk.loc[idx]; E = E.loc[idx]
    rs = stk.pct_change(); re = E.pct_change()
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    bull = (E["SPY"] > E["SPY"].rolling(200).mean()).shift(1).fillna(False)
    stocks = [c for c in stk.columns if c not in ("SPY","QQQ")]
    mom = stk[stocks].shift(21)/stk[stocks].shift(252)-1

    vtq = ((0.22/(re['SPY'].rolling(20).std()*np.sqrt(252))).clip(0,2).shift(1).fillna(0)*re['SPY']).fillna(0)
    qld = (bull*re["QLD"]).fillna(0)
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
    # current deploy: 50% momentum / 50% other (18 rp / 18 vtq / 7 qld / 7 thematic≈spmo)
    growth = 0.18*rp + 0.50*smom + 0.18*vtq + 0.07*qld + 0.07*(bull*re["SPMO"]).fillna(0)

    print(f"\n=== Options overlay on growth portfolio (1.5x) · {idx.min().date()}..{idx.max().date()} ===\n")
    print(f"  {'config':40} {'CAGR':>7} {'Sharpe':>6} {'maxDD':>8}")
    G = lambda x: x  # already a return stream
    base = lev(growth, 1.5)
    def show(n, ret): c, s, d = stats(ret); print(f"  {n:40} {c*100:6.1f}% {s:6.2f} {d*100:7.1f}%")
    show("growth @1.5x (no overlay)", base)
    for hedge, hw in [("VIXM", 0.05), ("VIXM", 0.10), ("TAIL", 0.05), ("TAIL", 0.10)]:
        if hedge in re:
            blended = lev((1-hw)*growth, 1.5) + hw*re[hedge].fillna(0)   # hedge unlevered, growth levered on the rest
            show(f"+{int(hw*100)}% {hedge} tail hedge", blended)
    # income swap: replace 20% of momentum with JEPI/DIVO covered-call income
    for inc in ["JEPI", "DIVO"]:
        if inc in re and re[inc].notna().sum() > 252:
            g2 = 0.18*rp + 0.30*smom + 0.20*re[inc].fillna(0) + 0.18*vtq + 0.07*qld + 0.07*(bull*re["SPMO"]).fillna(0)
            show(f"swap 20% momentum -> {inc} (income)", lev(g2, 1.5))
    print("\n  Tail hedge cost/bleed vs drawdown cut; income caps upside. Note: VIXM/TAIL/JEPI have limited history.")


if __name__ == "__main__":
    main()
