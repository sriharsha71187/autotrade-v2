#!/usr/bin/env python3
"""GROWTH-TILTED deployable portfolio: 25% risk-parity+TV, 30% stock-momentum, 25% vol-target
QQQ, 10% leveraged-QQQ+regime, 10% thematic trend. Backtests the exact blend AND prints the
CURRENT target positions (aggregated ticker weights) — i.e., the orders to place. This is the
bridge to paper-trading. Run: python research/portfolio_growth.py
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe
COST = 0.0005
WEIGHTS = {"rp": 0.25, "stockmom": 0.30, "vtqqq": 0.25, "levqqq": 0.10, "thematic": 0.10}
THEMES = ["SMH","SOXX","IGV","XBI","XOP","GDX","KRE","ITB","XRT","XME","TAN","IYT","ARKK","JETS"]


def stats(x):
    x = x.dropna(); eq = (1+x).cumprod(); yrs = len(x)/252
    return eq.iloc[-1]**(1/yrs)-1, (x.mean()*252)/(x.std()*np.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()


def atr(h, l, c, n=20):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(1)
    return tr.rolling(n).mean()


def compute(verbose=True):
    import yfinance as yf
    stk = fetch(universe()); stk.index = pd.to_datetime(stk.index).tz_localize(None).normalize()
    stk = stk.loc[:, stk.notna().sum() > 252]
    etfs = ["SPY","TLT","GLD","QQQ","QLD","IEF"] + THEMES
    raw = yf.download(etfs, start=str(stk.index.min().date()), auto_adjust=True, progress=False)
    C, H, L = raw["Close"], raw["High"], raw["Low"]
    for x in (C, H, L): x.index = pd.to_datetime(C.index).tz_localize(None).normalize()
    idx = stk.index.intersection(C.index); stk = stk.loc[idx]; C, H, L = C.loc[idx], H.loc[idx], L.loc[idx]
    rstk = stk.pct_change(); rc = C.pct_change()
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    bull = (C["SPY"] > C["SPY"].rolling(200).mean()).shift(1).fillna(False)
    stocks = [c for c in stk.columns if c not in ("SPY","QQQ")]
    target = {}                                   # current target weights (ticker -> portfolio %)

    # --- Sleeve 1: risk parity + target vol ---
    a = ["SPY","TLT","GLD"]; iv = 1/rc[a].rolling(60).std(); w = iv.div(iv.sum(1), axis=0)
    mk = np.array([d in mstart for d in idx]); w[~mk] = np.nan; w = w.ffill().shift(1).fillna(0)
    rp = (w*rc[a]).sum(1); lev = (0.12/(rp.rolling(40).std()*np.sqrt(252))).clip(0,2.5).shift(1).fillna(0)
    s_rp = (lev*rp).fillna(0)
    for t in a: target[t] = target.get(t,0) + WEIGHTS["rp"]*float((w.iloc[-1][t])*lev.iloc[-1])

    # --- Sleeve 2: stock momentum top-10 + regime ---
    mom = stk[stocks].shift(21)/stk[stocks].shift(252)-1
    wsm = pd.DataFrame(0.0, index=idx, columns=stocks); cur=[]
    for dt in idx:
        if dt in mstart and dt in mom.index:
            m = mom.loc[dt].dropna(); cur = list(m.nlargest(10).index) if len(m)>=10 else []
        if cur and bool(bull.loc[dt]):
            for t in cur: wsm.loc[dt,t] = 1/len(cur)
    s_sm = (wsm.shift(1)*rstk[stocks]).sum(1).fillna(0)
    for t in stocks:
        wv = float(wsm.iloc[-1][t])
        if wv > 0: target[t] = target.get(t,0) + WEIGHTS["stockmom"]*wv

    # --- Sleeve 3: vol-target QQQ ---
    wq = (0.22/(rc["QQQ"].rolling(20).std()*np.sqrt(252))).clip(0,2).shift(1)
    s_vq = (wq*rc["QQQ"]).fillna(0); target["QQQ"] = target.get("QQQ",0) + WEIGHTS["vtqqq"]*float(wq.iloc[-1])

    # --- Sleeve 4: leveraged QQQ + regime ---
    s_lq = (bull*rc["QLD"]).fillna(0)
    if bool(bull.iloc[-1]): target["QLD"] = target.get("QLD",0) + WEIGHTS["levqqq"]*1.0

    # --- Sleeve 5: thematic trend-following ---
    av = [t for t in THEMES if t in C and C[t].notna().sum()>300]; pm = pd.DataFrame(0.0, index=idx, columns=av)
    for t in av:
        c = C[t].dropna(); m200 = c.rolling(200).mean(); hi = c.rolling(50).max().shift(1); at = atr(H[t].dropna(),L[t].dropna(),c)
        pp = np.zeros(len(c)); s=0; pk=0
        for i in range(len(c)):
            if s==1:
                pk=max(pk,c.iloc[i])
                if c.iloc[i] < pk-3*at.iloc[i] or c.iloc[i] < m200.iloc[i]: s=0
            if s==0 and c.iloc[i]>=hi.iloc[i] and c.iloc[i]>m200.iloc[i] and not np.isnan(hi.iloc[i]): s=1; pk=c.iloc[i]
            pp[i]=s
        pm[t] = pd.Series(pp, index=c.index).reindex(idx).fillna(0)
    no = pm.sum(1); wt = pm.div(no.replace(0,np.nan), axis=0).fillna(0)
    s_th = ((wt.shift(1)*rc[av]).sum(1) + (no.shift(1)==0)*rc["IEF"]).fillna(0)
    for t in av:
        wv = float(wt.iloc[-1][t])
        if wv > 0: target[t] = target.get(t,0) + WEIGHTS["thematic"]*wv
    if no.iloc[-1] == 0: target["IEF"] = target.get("IEF",0) + WEIGHTS["thematic"]  # thematic in cash

    blend = (WEIGHTS["rp"]*s_rp + WEIGHTS["stockmom"]*s_sm + WEIGHTS["vtqqq"]*s_vq
             + WEIGHTS["levqqq"]*s_lq + WEIGHTS["thematic"]*s_th).fillna(0)
    c, s, dd = stats(blend)
    target = {t: wv for t, wv in target.items() if wv > 0.005}
    if verbose:
        print(f"\n=== GROWTH-TILTED portfolio · {idx.min().date()}..{idx.max().date()} ===\n")
        print(f"  Backtest: CAGR {c*100:.1f}%   Sharpe {s:.2f}   maxDD {dd*100:.1f}%   (vs SPY ~13%/0.78/-34%)")
        print(f"\n  TARGET POSITIONS NOW (regime: {'RISK-ON' if bool(bull.iloc[-1]) else 'RISK-OFF'}):")
        for t, wv in sorted(target.items(), key=lambda x: -x[1]):
            print(f"    {t:6} {wv*100:5.1f}%")
        tot = sum(target.values()); print(f"    {'TOTAL':6} {tot*100:5.1f}%  (cash: {max(0,1-tot)*100:.1f}%)")
    return target, (c, s, dd)


if __name__ == "__main__":
    compute()
