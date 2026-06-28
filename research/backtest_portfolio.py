#!/usr/bin/env python3
"""THE CAPSTONE: combine the validated sleeves into one portfolio and test whether a tactical
sleeve actually IMPROVES the blend (the user's real question — tactical IN ADDITION to core).
Sleeves: risk-parity+target-vol (core), vol-target QQQ (growth), IBS mean-reversion (tactical
timing), thematic trend-following (tactical rotation). Compares core-only vs core+tactical vs SPY.
Run: python research/backtest_portfolio.py
"""
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
COST = 0.0005


def stats(x):
    x = x.dropna(); eq = (1+x).cumprod(); yrs = len(x)/252
    return eq.iloc[-1]**(1/yrs)-1, (x.mean()*252)/(x.std()*np.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()


def atr(h, l, c, n=20):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(1)
    return tr.rolling(n).mean()


def main():
    import yfinance as yf
    themes = ["SMH","SOXX","IGV","XBI","XOP","GDX","KRE","ITB","XRT","XME","TAN","IYT","ARKK","JETS"]
    tk = ["SPY","QQQ","TLT","GLD","IEF"] + themes
    raw = yf.download(tk, start="2010-01-01", auto_adjust=True, progress=False)
    C, H, L = raw["Close"], raw["High"], raw["Low"]
    for x in (C, H, L): x.index = pd.to_datetime(C.index).tz_localize(None)
    r = C.pct_change(); idx = C.index
    mstart = set(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])

    # SLEEVE 1: risk parity + 12% target-vol (SPY/TLT/GLD)
    a = ["SPY","TLT","GLD"]; iv = 1/r[a].rolling(60).std(); w = iv.div(iv.sum(1), axis=0)
    mask = np.array([d in mstart for d in idx]); w[~mask] = np.nan; w = w.ffill().shift(1).fillna(0)
    rp = (w*r[a]).sum(1); lev = (0.12/(rp.rolling(40).std()*np.sqrt(252))).clip(0,2.5).shift(1).fillna(0)
    s_rp = (lev*rp - COST*(w.mul(lev,axis=0)).diff().abs().sum(1)).fillna(0)

    # SLEEVE 2: vol-target QQQ
    wq = (0.22/(r["QQQ"].rolling(20).std()*np.sqrt(252))).clip(0,2).shift(1)
    s_vtq = (wq*r["QQQ"] - COST*wq.diff().abs()).fillna(0)

    # SLEEVE 3: IBS mean-reversion (SPY)
    ibs = (C["SPY"]-L["SPY"])/(H["SPY"]-L["SPY"]).replace(0,np.nan); p=np.zeros(len(idx)); st=0
    for i in range(len(idx)):
        if st==0 and ibs.iloc[i]<0.2: st=1
        elif st==1 and ibs.iloc[i]>0.8: st=0
        p[i]=st
    ibp=pd.Series(p,index=idx); s_ibs=(ibp.shift(1)*r["SPY"]-COST*ibp.diff().abs()).fillna(0)

    # SLEEVE 4: thematic trend-following (chandelier)
    av=[t for t in themes if t in C and C[t].notna().sum()>300]; pm=pd.DataFrame(0.0,index=idx,columns=av)
    for t in av:
        c=C[t].dropna(); m200=c.rolling(200).mean(); hi=c.rolling(50).max().shift(1); at=atr(H[t].dropna(),L[t].dropna(),c)
        pp=np.zeros(len(c)); s=0; pk=0
        for i in range(len(c)):
            if s==1:
                pk=max(pk,c.iloc[i])
                if c.iloc[i]<pk-3*at.iloc[i] or c.iloc[i]<m200.iloc[i]: s=0
            if s==0 and c.iloc[i]>=hi.iloc[i] and c.iloc[i]>m200.iloc[i] and not np.isnan(hi.iloc[i]): s=1; pk=c.iloc[i]
            pp[i]=s
        pm[t]=pd.Series(pp,index=c.index).reindex(idx).fillna(0)
    no=pm.sum(1); wt=pm.div(no.replace(0,np.nan),axis=0).fillna(0)
    s_tf=((wt.shift(1)*r[av]).sum(1)+(no.shift(1)==0)*r["IEF"]-COST*wt.diff().abs().sum(1)).fillna(0)

    sleeves={"risk-parity+TV (core)":s_rp,"vol-target QQQ (growth)":s_vtq,"IBS mean-rev (tactical)":s_ibs,"thematic trend (tactical)":s_tf}
    print(f"\n=== Portfolio blend · {idx.min().date()}..{idx.max().date()} ===\n")
    print(f"  {'sleeve / blend':34} {'CAGR':>7} {'Sharpe':>6} {'maxDD':>8}  corr-to-SPY")
    for n,s in sleeves.items():
        c,sh,dd=stats(s); print(f"  {n:34} {c*100:6.1f}% {sh:6.2f} {dd*100:7.1f}%   {s.corr(r['SPY']):+.2f}")
    print()
    core = 0.55*s_rp + 0.45*s_vtq
    core_tac = 0.45*s_rp + 0.40*s_vtq + 0.075*s_ibs + 0.075*s_tf
    for n,s in {"CORE (55% RP-TV / 45% VTQQQ)":core,"CORE + 15% TACTICAL":core_tac,"SPY buy-hold":r["SPY"].fillna(0)}.items():
        c,sh,dd=stats(s); print(f"  {n:34} {c*100:6.1f}% {sh:6.2f} {dd*100:7.1f}%   {s.corr(r['SPY']):+.2f}")
    print("\n  Does the 15% tactical sleeve improve the blend's Sharpe/DD vs core-only? That's the verdict.")


if __name__ == "__main__":
    main()
