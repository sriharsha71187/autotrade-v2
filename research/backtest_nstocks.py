#!/usr/bin/env python3
"""Does the US momentum sleeve do better with 10, 15, or 20 stocks? More names = more
diversification (smoother) but dilutes the momentum signal with weaker names. Tests the sleeve
standalone AND inside the full growth blend. Run: python research/backtest_nstocks.py
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
    mom = stk[stocks].shift(21)/stk[stocks].shift(252)-1

    # blend sleeves that don't depend on N
    vtq = ((0.22/(re['SPY'].rolling(20).std()*np.sqrt(252))).clip(0,2).shift(1).fillna(0)*re['SPY']).fillna(0)
    qld = (bull*re["QLD"]).fillna(0); spmo = (bull*re["SPMO"]).fillna(0)
    a = ["SPY","TLT","GLD"]; iv = 1/re[a].rolling(60).std(); w = iv.div(iv.sum(1), axis=0)
    mk = np.array([d in mstart for d in idx]); w[~mk] = np.nan; w = w.ffill().shift(1).fillna(0)
    rpb = (w*re[a]).sum(1); lev = (0.12/(rpb.rolling(40).std()*np.sqrt(252))).clip(0,2.5).shift(1).fillna(0)
    rp = (lev*rpb).fillna(0)

    def smom_at(N):
        wsm = pd.DataFrame(0.0, index=idx, columns=stocks); cur=[]
        for dt in idx:
            if dt in mstart and dt in mom.index:
                m = mom.loc[dt].dropna(); cur = list(m.nlargest(N).index) if len(m)>=N else []
            if cur and bool(bull.loc[dt]):
                for t in cur: wsm.loc[dt, t] = 1/len(cur)
        return ((wsm.shift(1)*rs[stocks]).sum(1) - COST*wsm.diff().abs().sum(1)).fillna(0)

    print(f"\n=== US momentum: top-N stocks · {idx.min().date()}..{idx.max().date()} · {COST*1e4:.0f}bps/side ===\n")
    print(f"  {'N stocks':12} {'-- sleeve standalone --':>30} {'-- in full growth blend --':>32}")
    print(f"  {'':12} {'CAGR':>8} {'Sharpe':>7} {'maxDD':>8} {'CAGR':>10} {'Sharpe':>7} {'maxDD':>8}")
    for N in (10, 15, 20):
        sm = smom_at(N)
        cs, ss, ds = stats(sm)
        blend = 0.25*rp + 0.30*sm + 0.25*vtq + 0.10*qld + 0.10*spmo
        cb, sb, db = stats(blend)
        print(f"  top-{N:<8} {cs*100:7.1f}% {ss:7.2f} {ds*100:7.1f}% {cb*100:9.1f}% {sb:7.2f} {db*100:7.1f}%")
    print("\n  Fewer names = higher return + idiosyncratic risk; more names = smoother but signal-diluted.")


if __name__ == "__main__":
    main()
