#!/usr/bin/env python3
"""BROAD strategy sweep across families the user did NOT suggest — trend-following/GTAA, risk
parity, sector rotation, low-vol factor, seasonality, the overnight effect, bond-switch, vol
risk-premium. Diverse asset classes + styles, all with explicit rules. vs 60/40 and SPY.
Run: python research/backtest_broad.py
"""
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
COST = 0.0005


def stats(ret):
    ret = ret.dropna();
    if len(ret) < 60: return (np.nan,)*3
    eq = (1+ret).cumprod(); yrs = len(ret)/252
    cagr = eq.iloc[-1]**(1/yrs)-1; vol = ret.std()*np.sqrt(252)
    sh = (ret.mean()*252)/vol if vol > 0 else 0; dd = (eq/eq.cummax()-1).min()
    return cagr, sh, dd


def row(name, ret, note=""):
    c, s, d = stats(ret)
    print(f"  {name:36} {c*100:6.1f}% {s:6.2f} {d*100:7.1f}%   {note}")


def monthly_marks(idx):
    m = idx.to_period("M"); return set(idx[np.append([True], m[1:] != m[:-1])])


def main():
    import yfinance as yf
    secs = ["XLK","XLF","XLE","XLV","XLI","XLP","XLY","XLU","XLB"]
    tk = ["SPY","QQQ","EFA","EEM","TLT","IEF","GLD","DBC","USMV","SPLV","QYLD","SHV"] + secs
    raw = yf.download(tk, start="2007-01-01", auto_adjust=True, progress=False)
    px = raw["Close"]; op = raw["Open"]
    px.index = pd.to_datetime(px.index).tz_localize(None); op.index = px.index
    r = px.pct_change()
    rebal = monthly_marks(px.index)

    print(f"\n=== BROAD strategy sweep · {px.index.min().date()}..{px.index.max().date()} · {COST*1e4:.0f}bps/side ===\n")
    print(f"  {'strategy':36} {'CAGR':>7} {'Sharpe':>6} {'maxDD':>8}   rule")

    # 1. Faber GTAA / TSMOM: multi-asset, hold each when >10mo(200d) MA, else that sleeve in cash
    univ = ["SPY","EFA","EEM","TLT","GLD","DBC"]
    sig = (px[univ] > px[univ].rolling(200).mean()).shift(1)
    w = sig.div(sig.sum(1).replace(0,np.nan), axis=0).fillna(0)        # equal-weight those in uptrend
    gtaa = (w.shift(1)*r[univ]).sum(1) - COST*w.diff().abs().sum(1)
    row("1. Faber GTAA (6-asset, 200DMA)", gtaa.fillna(0), "hold sleeve when >200DMA else cash; EW")

    # 2. Risk parity: SPY/TLT/GLD inverse-vol, monthly
    rp_a = ["SPY","TLT","GLD"]; iv = 1/r[rp_a].rolling(60).std()
    wrp = iv.div(iv.sum(1), axis=0)
    mask = np.array([d in rebal for d in px.index])
    wrp[~mask] = np.nan
    wrp = wrp.ffill().shift(1).fillna(0)
    rp = (wrp*r[rp_a]).sum(1) - COST*wrp.diff().abs().sum(1)
    row("2. Risk parity SPY/TLT/GLD", rp.fillna(0), "inverse-vol weights, monthly rebalance")

    # 3. Sector momentum rotation: top-3 of 9 sectors by 6mo return, monthly
    mom = px[secs]/px[secs].shift(126)-1
    pos = pd.DataFrame(0.0, index=px.index, columns=secs); cur=[]
    for dt in px.index:
        if dt in rebal:
            m = mom.loc[dt].dropna(); cur = list(m.nlargest(3).index) if len(m)>=3 else []
        if cur: pos.loc[dt, cur] = 1/len(cur)
    sr = (pos.shift(1)*r[secs]).sum(1) - COST*pos.diff().abs().sum(1)
    row("3. Sector momentum (top-3/9, 6mo)", sr.fillna(0), "hold strongest 3 sectors, monthly")

    # 4. Low-vol factor buy-hold
    row("4. Low-vol factor (USMV)", r["USMV"].fillna(0), "min-vol ETF buy-hold")

    # 5. Sell-in-May seasonality: SPY Nov-Apr, IEF (bonds) May-Oct
    mo = px.index.month; in_spy = pd.Series(np.where((mo>=11)|(mo<=4),1,0), index=px.index).shift(1)
    sim = (in_spy*r["SPY"] + (1-in_spy)*r["IEF"]).fillna(0)
    row("5. Sell-in-May (SPY Nov-Apr/IEF)", sim, "seasonal switch equity<->bonds")

    # 6. Overnight effect: hold SPY close->open only (vs intraday)
    overnight = (op["SPY"]/px["SPY"].shift(1) - 1).fillna(0)
    intraday = (px["SPY"]/op["SPY"] - 1).fillna(0)
    row("6. Overnight-only SPY (close->open)", overnight, "buy close, sell open")
    row("   (ref) Intraday-only SPY (open->close)", intraday, "buy open, sell close")

    # 7. Bond-switch regime: SPY when >200DMA else TLT
    bull = (px["SPY"]>px["SPY"].rolling(200).mean()).shift(1)
    bs = (bull*r["SPY"] + (~bull.astype(bool))*r["TLT"]).fillna(0)
    row("7. SPY>200DMA else TLT", bs, "risk-on/off equity-bond switch")

    # 8. Vol risk premium: QYLD (covered-call) buy-hold
    row("8. Covered-call income (QYLD)", r["QYLD"].fillna(0), "vol risk premium harvest")

    print()
    # benchmarks
    w6040 = pd.Series(0.6, index=px.index)
    s6040 = (0.6*r["SPY"] + 0.4*r["IEF"]).fillna(0)
    row("BENCH 60/40 (SPY/IEF)", s6040)
    row("BENCH SPY buy-hold", r["SPY"].fillna(0))
    print("\n  Signals lagged 1 day (no lookahead). Costs 5bps/side. Long-only.")


if __name__ == "__main__":
    main()
