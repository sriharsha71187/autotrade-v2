#!/usr/bin/env python3
"""Momentum lookback test: 3mo vs 6mo vs 12mo (our current). Shorter = catches moves earlier
(less prior run-up = less chasing) but more whipsaw/short-term-reversal. Reports sleeve + blend
performance, turnover, AND the avg trailing-12mo gain of the picks (the chasing metric).
All skip the most recent month (21d). Run: python research/backtest_lookback.py
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe
COST = 0.0005


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
    trail12 = stk[stocks].shift(21)/stk[stocks].shift(252)-1   # consistent "how much has it run" metric

    # non-momentum sleeves (fixed across lookbacks)
    vtq = ((0.22/(re['SPY'].rolling(20).std()*np.sqrt(252))).clip(0,2).shift(1).fillna(0)*re['SPY']).fillna(0)
    qld = (bull*re["QLD"]).fillna(0); spmo = (bull*re["SPMO"]).fillna(0)
    a = ["SPY","TLT","GLD"]; iv = 1/re[a].rolling(60).std(); w = iv.div(iv.sum(1), axis=0)
    mk = np.array([d in mstart for d in idx]); w[~mk] = np.nan; w = w.ffill().shift(1).fillna(0)
    rpb = (w*re[a]).sum(1); lv = (0.12/(rpb.rolling(40).std()*np.sqrt(252))).clip(0,2.5).shift(1).fillna(0)
    rp = (lv*rpb).fillna(0)

    def sleeve(L):
        mom = stk[stocks].shift(21)/stk[stocks].shift(21+L)-1
        wsm = pd.DataFrame(0.0, index=idx, columns=stocks); cur=[]; priors=[]
        for dt in idx:
            if dt in mstart and dt in mom.index:
                m = mom.loc[dt].dropna(); cur = list(m.nlargest(10).index) if len(m)>=10 else []
                if cur and dt in trail12.index:
                    priors += [trail12.loc[dt, t] for t in cur if pd.notna(trail12.loc[dt, t])]
            if cur and bool(bull.loc[dt]):
                for t in cur: wsm.loc[dt, t] = 1/len(cur)
        smom = ((wsm.shift(1)*rs[stocks]).sum(1) - COST*wsm.diff().abs().sum(1)).fillna(0)
        turn = wsm.diff().abs().sum(1).sum()/(len(idx)/252)
        return smom, turn, (np.mean(priors)*100 if priors else np.nan)

    print(f"\n=== Momentum lookback: 3 vs 6 vs 12 month · {idx.min().date()}..{idx.max().date()} ===\n")
    print(f"  {'lookback':10} {'-- sleeve --':>22} {'-- in growth blend --':>24} {'turn/yr':>9} {'avg prior 12mo run':>20}")
    print(f"  {'':10} {'CAGR':>7}{'Shrp':>6}{'maxDD':>8} {'CAGR':>9}{'Shrp':>6}{'maxDD':>8}")
    for L, lbl in [(63, "3-month"), (126, "6-month"), (231, "12-month*")]:
        sm, turn, prior = sleeve(L)
        cs, ss, ds = stats(sm)
        blend = 0.18*rp + 0.50*sm + 0.18*vtq + 0.07*qld + 0.07*spmo
        cb, sb, db = stats(blend)
        print(f"  {lbl:10} {cs*100:6.0f}%{ss:6.2f}{ds*100:7.0f}% {cb*100:8.1f}%{sb:6.2f}{db*100:7.0f}% {turn*100:8.0f}% {prior:17.0f}%")
    print("\n  * = current. avg prior 12mo run = how much the picks had already gained (chasing metric).")


if __name__ == "__main__":
    main()
