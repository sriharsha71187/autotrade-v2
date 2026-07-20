#!/usr/bin/env python3
"""Index-level ACTIVE-timing sweep on clean OHLCV: the documented calendar/microstructure
effects, each tested IS 2016-2021 / OOS 2022-2026 with costs (1bp/side index ETFs).
 1. Turn-of-month (long last 4 + first 3 trading days)
 2. IBS mean-reversion (enter <0.2 / exit >0.8) on SPY QQQ MDY IWM
 3. Overnight-only vs intraday-only decomposition
 4. Day-of-week
 5. IBS x TOM combo, IBS on down-days-only variant
 6. VIX-spike mean reversion (VIX +20% in 5d -> long 5d)
Run: python research/timing_sweep.py
"""
import math, pickle, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")

CACHE = Path(__file__).resolve().parent / ".yf_ohlcv.pkl"
MACRO = Path.home() / "autotrade_macro_history.csv"
COST = 0.0001
OOS = pd.Timestamp("2022-01-01")


def stats(r):
    r = r.dropna()
    if len(r) < 50:
        return "n<50"
    ann = r.mean() * 252
    sh = r.mean() / (r.std() + 1e-12) * math.sqrt(252)
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    tim = (r != 0).mean()
    return f"ann {ann*100:+6.1f}%  Sh {sh:+5.2f}  dd {dd*100:5.1f}%  inMkt {tim*100:3.0f}%"


def show(name, pos, r):
    """pos = position series (0/1) decided at close t, earns r at t+1."""
    strat = pos.shift(1) * r - COST * pos.diff().abs()
    print(f"{name:44s} IS: {stats(strat[strat.index < OOS])}   OOS: {stats(strat[strat.index >= OOS])}")


def main():
    d = pickle.load(open(CACHE, "rb"))
    O, H, L, C = d["Open"], d["High"], d["Low"], d["Close"]
    macro = pd.read_csv(MACRO, parse_dates=["Date"]).set_index("Date")

    for sym in ["SPY", "QQQ", "MDY", "IWM"]:
        c, h, l, o = C[sym], H[sym], L[sym], O[sym]
        r = c.pct_change()
        idx = c.index
        print(f"\n=== {sym} ===  buy-hold IS: {stats(r[r.index < OOS])}  OOS: {stats(r[r.index >= OOS])}")

        # 1. turn-of-month: last 4 + first 3 trading days
        mon = pd.Series(idx.month, index=idx)
        tdom = mon.groupby(mon.ne(mon.shift()).cumsum()).cumcount() + 1       # trading day of month
        rem = tdom.groupby(mon.ne(mon.shift()).cumsum()).transform("max") - tdom  # days to month end
        tom = ((rem <= 3) | (tdom <= 3)).astype(float)
        show("turn-of-month (last4+first3)", tom, r)

        # 2. IBS
        ibs = ((c - l) / (h - l)).replace([np.inf, -np.inf], np.nan)
        p = np.zeros(len(idx)); st = 0
        for i in range(len(idx)):
            v = ibs.iloc[i]
            if st == 0 and v < 0.2: st = 1
            elif st == 1 and v > 0.8: st = 0
            p[i] = st
        show("IBS <0.2 -> >0.8", pd.Series(p, index=idx), r)

        # 5a. IBS entry only on down day
        p = np.zeros(len(idx)); st = 0
        for i in range(len(idx)):
            v = ibs.iloc[i]
            if st == 0 and v < 0.2 and r.iloc[i] < 0: st = 1
            elif st == 1 and v > 0.8: st = 0
            p[i] = st
        show("IBS + down-day entry", pd.Series(p, index=idx), r)

        # 3. overnight vs intraday (no cost netting possible at 2x daily turnover: show gross+cost)
        on = (o / c.shift(1) - 1) - 2 * COST
        intr = (c / o - 1) - 2 * COST
        print(f"{'overnight-only (net 2bp/d)':44s} IS: {stats(on[on.index < OOS])}   OOS: {stats(on[on.index >= OOS])}")
        print(f"{'intraday-only (net 2bp/d)':44s} IS: {stats(intr[intr.index < OOS])}   OOS: {stats(intr[intr.index >= OOS])}")

        # 4. day-of-week (position = 1 on given weekday)
        best = []
        for wd, nm in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri"]):
            pos = pd.Series((idx.weekday == wd).astype(float), index=idx)
            s = pos.shift(1) * r
            best.append(f"{nm} {s[s.index>=OOS].mean()*1e4:+.1f}bp")
        print(f"{'day-of-week OOS mean/day':44s} " + "  ".join(best))

        # 6. VIX spike reversion
        vix = macro["vix"].reindex(idx).ffill()
        spike = (vix / vix.shift(5) - 1 > 0.20)
        pos = spike.astype(float).rolling(5).max().fillna(0)   # long 5d after spike
        show("VIX +20%/5d -> long 5d", pos, r)


if __name__ == "__main__":
    main()
