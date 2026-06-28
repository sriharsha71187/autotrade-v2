#!/usr/bin/env python3
"""Validate the research agent's TIER-1 documented strategies on our data, head-to-head with
the risk-parity winner. GEM dual momentum, turn-of-month, IBS mean-reversion, permanent
portfolio, quality/low-vol factors, and a TARGET-VOL levered risk parity (the practical upgrade).
Run: python research/backtest_tier1.py
"""
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
COST = 0.0005


def stats(ret):
    ret = ret.dropna()
    if len(ret) < 60: return (np.nan,)*3
    eq = (1+ret).cumprod(); yrs = len(ret)/252
    return eq.iloc[-1]**(1/yrs)-1, (ret.mean()*252)/(ret.std()*np.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()


def row(name, ret, note=""):
    c, s, d = stats(ret); print(f"  {name:34} {c*100:6.1f}% {s:6.2f} {d*100:7.1f}%   {note}")


def main():
    import yfinance as yf
    tk = ["SPY","VEU","BIL","AGG","VTI","TLT","GLD","SHY","IEF","QUAL","SPLV"]
    raw = yf.download(tk, start="2007-01-01", auto_adjust=True, progress=False)
    px, op, hi, lo = raw["Close"], raw["Open"], raw["High"], raw["Low"]
    for x in (px, op, hi, lo): x.index = pd.to_datetime(px.index).tz_localize(None)
    r = px.pct_change()
    idx = px.index; mp = idx.to_period("M")
    monthstart = set(idx[np.append([True], mp[1:] != mp[:-1])])

    print(f"\n=== TIER-1 documented strategies · {idx.min().date()}..{idx.max().date()} · {COST*1e4:.0f}bps/side ===\n")
    print(f"  {'strategy':34} {'CAGR':>7} {'Sharpe':>6} {'maxDD':>8}   rule")

    # 1. GEM dual momentum: SPY vs VEU vs AGG, abs filter vs BIL, 12mo, monthly
    tr12 = px/px.shift(252) - 1
    pos = pd.DataFrame(0.0, index=idx, columns=["SPY","VEU","AGG"]); cur=None
    for dt in idx:
        if dt in monthstart and dt in tr12.index:
            if tr12.loc[dt,"SPY"] > tr12.loc[dt,"BIL"]:
                cur = "SPY" if tr12.loc[dt,"SPY"] >= tr12.loc[dt,"VEU"] else "VEU"
            else: cur = "AGG"
        if cur: pos.loc[dt, cur] = 1.0
    gem = (pos.shift(1)*r[["SPY","VEU","AGG"]]).sum(1) - COST*pos.diff().abs().sum(1)
    row("1. GEM dual momentum", gem.fillna(0), "SPY/VEU if >T-bill 12mo, else AGG")

    # 2. Turn-of-month: SPY on [last trading day .. +3], else IEF
    dom_pos = pd.Series(0.0, index=idx)
    for i in range(len(idx)):
        # last trading day of month = today is monthstart-1 ... mark today if next day is monthstart OR within first 3
        pass
    nextstart = pd.Series([ (idx[i+1] in monthstart) if i+1 < len(idx) else False for i in range(len(idx))], index=idx)
    tom = np.zeros(len(idx))
    for i in range(len(idx)):
        if nextstart.iloc[i]:
            for j in range(0, 4):
                if i+j < len(idx): tom[i+j] = 1
    tom = pd.Series(tom, index=idx).shift(1).fillna(0)
    tom_ret = (tom*r["SPY"] + (1-tom)*r["IEF"]).fillna(0)
    row("2. Turn-of-month (SPY/IEF)", tom_ret, "SPY last-day..+3, else IEF")

    # 3. IBS mean-reversion on SPY: buy close IBS<0.2, exit IBS>0.8
    ibs = (px["SPY"]-lo["SPY"])/(hi["SPY"]-lo["SPY"]).replace(0,np.nan)
    p = np.zeros(len(idx)); st=0
    for i in range(len(idx)):
        if st==0 and ibs.iloc[i] < 0.2: st=1
        elif st==1 and ibs.iloc[i] > 0.8: st=0
        p[i]=st
    ibs_pos = pd.Series(p, index=idx)
    ibs_ret = (ibs_pos.shift(1)*r["SPY"] - COST*ibs_pos.diff().abs()).fillna(0)
    row("3. IBS mean-reversion (SPY)", ibs_ret, "buy IBS<0.2, sell IBS>0.8")

    # 4. Permanent portfolio: VTI/TLT/GLD/SHY 25%, annual rebalance
    pp = (0.25*(r["VTI"]+r["TLT"]+r["GLD"]+r["SHY"])).fillna(0)
    row("4. Permanent portfolio", pp, "VTI/TLT/GLD/SHY 25% each")

    # 5-6. factor buy-holds
    row("5. Quality factor (QUAL)", r["QUAL"].fillna(0), "buy-hold")
    row("6. Low-vol factor (SPLV)", r["SPLV"].fillna(0), "buy-hold")

    # 7. TARGET-VOL levered risk parity (the upgrade of the winner)
    rp_a = ["SPY","TLT","GLD"]; iv = 1/r[rp_a].rolling(60).std()
    w = iv.div(iv.sum(1), axis=0); mask = np.array([d in monthstart for d in idx])
    w[~mask] = np.nan; w = w.ffill().shift(1).fillna(0)
    rp = (w*r[rp_a]).sum(1)
    realvol = rp.rolling(40).std()*np.sqrt(252)
    lev = (0.12/realvol).clip(0, 2.5).shift(1).fillna(0)        # target 12% vol, cap 2.5x
    rp_tv = (lev*rp - COST*(w.mul(lev,axis=0)).diff().abs().sum(1)).fillna(0)
    row("7. Risk parity + 12% target-vol", rp_tv, "lever the Sharpe-1.0 RP to 12% vol")

    print()
    row("BENCH risk parity (unlevered)", ((w*r[rp_a]).sum(1)-COST*w.diff().abs().sum(1)).fillna(0))
    row("BENCH SPY buy-hold", r["SPY"].fillna(0))
    print("\n  Signals lagged 1 day. 2007-26 incl GFC. Long-only.")


if __name__ == "__main__":
    main()
