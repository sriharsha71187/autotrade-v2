#!/usr/bin/env python3
"""Rebalance-frequency test on the US growth portfolio: monthly vs bi-weekly vs weekly.
More frequent = faster reaction but more turnover/cost/whipsaw. Cost-aware. Reports CAGR/Sharpe/
maxDD + annualized turnover so the cost trade-off is explicit. Run: python research/backtest_freq.py
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
    bull = (E["SPY"] > E["SPY"].rolling(200).mean()).shift(1).fillna(False)
    stocks = [c for c in stk.columns if c not in ("SPY","QQQ")]
    mom = stk[stocks].shift(21)/stk[stocks].shift(252)-1

    # sleeves NOT affected by rebal cadence (daily signals): vol-target QQQ, leveraged QQQ, SPMO regime
    vtq = ((0.22/(re['SPY'].rolling(20).std()*np.sqrt(252))).clip(0,2).shift(1).fillna(0)*re['SPY']).fillna(0)
    qld = (bull*re["QLD"]).fillna(0); spmo = (bull*re["SPMO"]).fillna(0)

    def rebal_dates(step):
        return set(idx[::step])

    def sleeves_at(step):
        rb = rebal_dates(step)
        # momentum top-10 rebalanced every `step` days
        wsm = pd.DataFrame(0.0, index=idx, columns=stocks); cur=[]
        for i, dt in enumerate(idx):
            if dt in rb and dt in mom.index:
                m = mom.loc[dt].dropna(); cur = list(m.nlargest(10).index) if len(m)>=10 else []
            if cur and bool(bull.loc[dt]):
                for t in cur: wsm.loc[dt, t] = 1/len(cur)
        smom = ((wsm.shift(1)*rs[stocks]).sum(1) - COST*wsm.diff().abs().sum(1)).fillna(0)
        turn_sm = wsm.diff().abs().sum(1).sum() / (len(idx)/252)        # annualized one-way turnover
        # risk parity rebalanced every `step` days
        a = ["SPY","TLT","GLD"]; iv = 1/re[a].rolling(60).std(); w = iv.div(iv.sum(1), axis=0)
        mk = np.array([d in rb for d in idx]); w[~mk] = np.nan; w = w.ffill().shift(1).fillna(0)
        rpb = (w*re[a]).sum(1); lev = (0.12/(rpb.rolling(40).std()*np.sqrt(252))).clip(0,2.5).shift(1).fillna(0)
        rp = (lev*rpb - COST*(w.mul(lev,axis=0)).diff().abs().sum(1)).fillna(0)
        return rp, smom, turn_sm

    print(f"\n=== US growth portfolio · rebalance frequency · {idx.min().date()}..{idx.max().date()} · {COST*1e4:.0f}bps/side ===\n")
    print(f"  {'frequency':16} {'CAGR':>7} {'Sharpe':>6} {'maxDD':>8} {'mom-turnover/yr':>16}")
    for step, lbl in [(21, "monthly"), (10, "bi-weekly"), (5, "weekly")]:
        rp, smom, turn = sleeves_at(step)
        blend = 0.25*rp + 0.30*smom + 0.25*vtq + 0.10*qld + 0.10*spmo
        c, s, dd = stats(blend)
        print(f"  {lbl:16} {c*100:6.1f}% {s:6.2f} {dd*100:7.1f}% {turn*100:14.0f}%")
    print("\n  mom-turnover/yr = one-way turnover of the momentum sleeve (proxy for extra cost/whipsaw).")
    print("  Costs are in the returns (5bps/side). 12-1mo momentum is slow -> faster rebal usually adds cost without signal.")


if __name__ == "__main__":
    main()
