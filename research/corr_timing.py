#!/usr/bin/env python3
"""Correlation-structure timing study + IBS robustness grid.
 A. Average pairwise stock correlation (rolling 60d, 200 largest names): does high/low
    correlation predict forward SPY returns or momentum-strategy returns?
 B. Cross-sectional dispersion (std of 21d returns): same questions (the live dispersion
    gate uses this - validate on clean data).
 C. IBS parameter grid on SPY/QQQ (entry x exit x regime filter) - is IBS robust or one
    lucky cell?  All IS 2016-2021 / OOS 2022-2026.
Run: python research/corr_timing.py
"""
import math, pickle, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")

CACHE = Path(__file__).resolve().parent / ".yf_ohlcv.pkl"
OOS = pd.Timestamp("2022-01-01")
COST = 0.0001
ETFS = set("""SPY QQQ IWM DIA MDY RSP VTI XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC
TLT IEF SHY HYG LQD GLD SLV USO GDX GDXJ UUP FXE VXX UVXY SVXY SMH SOXX XBI IBB ARKK KRE
KBE ITB XHB XRT XME XOP OIH TAN ICLN URA LIT JETS IYT EEM EFA FXI EWZ EWJ INDA VNQ IYR
QLD TQQQ SSO UPRO SQQQ SDS PSQ SH EFV EFG VTV VUG IWD IWF IWO IWN BND AGG TIP EMB""".split())


def nw_t(x, lag=4):
    x = pd.Series(x).dropna().values
    n = len(x)
    if n < 30:
        return np.nan
    mu = x.mean(); e = x - mu; s = e @ e / n
    for l in range(1, lag + 1):
        s += 2 * (1 - l / (lag + 1)) * (e[l:] @ e[:-l]) / n
    return mu / math.sqrt(s / n) if s > 0 else np.nan


def stats(r):
    r = r.dropna()
    if len(r) < 50: return "n<50"
    sh = r.mean() / (r.std() + 1e-12) * math.sqrt(252)
    eq = (1 + r).cumprod(); dd = (eq / eq.cummax() - 1).min()
    return f"ann {r.mean()*252*100:+6.1f}% Sh {sh:+5.2f} dd {dd*100:5.1f}%"


def main():
    d = pickle.load(open(CACHE, "rb"))
    C, H, L = d["Close"], d["High"], d["Low"]
    stocks = [c for c in C.columns if c not in ETFS]
    r = C[stocks].pct_change()
    spy = C["SPY"].pct_change()

    # liquidity top-200 by dollar volume (stable membership: full-period median)
    dv = (C[stocks] * d["Volume"][stocks]).median()
    top = dv.nlargest(200).index.tolist()

    # A. average pairwise correlation ~ mean corr to the EW basket (fast proxy)
    ew = r[top].mean(axis=1)
    avgcorr = r[top].rolling(60).corr(ew).mean(axis=1)      # mean corr of each stock to EW
    # B. dispersion
    disp = (C[stocks] / C[stocks].shift(21) - 1).std(axis=1)

    mom = (C[stocks].shift(21) / C[stocks].shift(252) - 1)   # 12-1 momentum
    fridays = C.index[C.index.weekday == 4]

    # momentum-strategy weekly return: top decile 12-1, next 5d, excess vs universe
    mrets = {}
    fwd5 = (C[stocks].shift(-5) / C[stocks] - 1)
    fwd5x = fwd5.sub(fwd5.mean(axis=1), axis=0)
    for dt_ in fridays:
        if dt_ not in mom.index: continue
        x = mom.loc[dt_].dropna()
        if len(x) < 200: continue
        sel = x.nlargest(len(x) // 10).index
        mrets[dt_] = fwd5x.loc[dt_, sel].mean()
    mrets = pd.Series(mrets)

    print("=== A/B: does correlation structure PREDICT? (weekly, quintile-conditioned) ===")
    for nm, sig in [("avg pairwise corr", avgcorr), ("XS dispersion 21d", disp)]:
        s = sig.reindex(fridays).dropna()
        spy5 = (C["SPY"].shift(-5) / C["SPY"] - 1).reindex(s.index)
        m = mrets.reindex(s.index)
        q = pd.qcut(s.rank(method="first"), 5, labels=False)
        print(f"\n{nm}: quintile -> fwd 5d SPY | momentum-decile excess")
        for k in range(5):
            sel = q == k
            print(f"  Q{k}: SPY {spy5[sel].mean()*100:+.2f}% (t{nw_t(spy5[sel],2):+.1f}) | mom-xs {m[sel].mean()*100:+.2f}% (t{nw_t(m[sel],2):+.1f})  n={sel.sum()}")
        # IS/OOS rank-IC of signal vs momentum-strategy forward return
        for lbl, mask in [("IS", s.index < OOS), ("OOS", s.index >= OOS)]:
            ic = s[mask].rank().corr(m[mask].rank())
            print(f"  {lbl} rank-corr(signal, fwd mom-excess): {ic:+.3f}")

    # C. IBS grid
    print("\n=== C: IBS grid (entry/exit/regime) ===")
    for sym in ["SPY", "QQQ"]:
        c, h, l = C[sym], H[sym], L[sym]
        rr = c.pct_change(); idx = c.index
        ibs = ((c - l) / (h - l)).replace([np.inf, -np.inf], np.nan)
        m200 = c > c.rolling(200).mean()
        print(f"\n{sym}:  {'cfg':22s} {'IS':>34s}   {'OOS':>34s}")
        for ent, ex in [(0.1, 0.9), (0.15, 0.8), (0.2, 0.8), (0.25, 0.7), (0.3, 0.7)]:
            for reg in [False, True]:
                p = np.zeros(len(idx)); st = 0
                for i in range(len(idx)):
                    v = ibs.iloc[i]
                    okreg = (not reg) or bool(m200.iloc[i])
                    if st == 0 and v < ent and okreg: st = 1
                    elif st == 1 and v > ex: st = 0
                    p[i] = st
                pos = pd.Series(p, index=idx)
                strat = pos.shift(1) * rr - COST * pos.diff().abs()
                tag = f"e{ent}/x{ex}" + ("/>200dma" if reg else "")
                print(f"  {tag:22s} {stats(strat[strat.index < OOS]):>34s}   {stats(strat[strat.index >= OOS]):>34s}")


if __name__ == "__main__":
    main()
