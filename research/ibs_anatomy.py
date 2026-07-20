#!/usr/bin/env python3
"""Trade-level anatomy of the IBS mean-reversion edge (deployment view): per-trade
expectancy, hold length, trade count/yr, worst trades, year-by-year consistency.
Also the Connors RSI-2 variant as an independent cross-check of the same effect.
Config under test: SPY & QQQ, enter IBS<0.1, exit IBS>0.9 (best grid cell, robust
neighborhood). Costs 1bp/side.
Run: python research/ibs_anatomy.py
"""
import math, pickle, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")

CACHE = Path(__file__).resolve().parent / ".yf_ohlcv.pkl"
COST = 0.0001


def rsi(series, n=2):
    d = series.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / (dn + 1e-12))


def run(c, h, l, mode):
    ibs = ((c - l) / (h - l)).replace([np.inf, -np.inf], np.nan)
    r2 = rsi(c)
    idx = c.index
    pos = np.zeros(len(idx)); st = 0
    for i in range(len(idx)):
        v, q = ibs.iloc[i], r2.iloc[i]
        if mode == "ibs":
            if st == 0 and v < 0.1: st = 1
            elif st == 1 and v > 0.9: st = 0
        else:  # rsi2
            if st == 0 and q < 10: st = 1
            elif st == 1 and q > 70: st = 0
        pos[i] = st
    return pd.Series(pos, index=idx)


def trades(pos, r):
    """Extract per-trade compounded returns."""
    out = []
    inpos = False; acc = 1.0; start = None; hold = 0
    strat = pos.shift(1).fillna(0)
    for i in range(len(strat)):
        if strat.iloc[i] > 0:
            if not inpos:
                inpos, acc, start, hold = True, 1.0, strat.index[i], 0
            acc *= 1 + r.iloc[i]; hold += 1
        elif inpos:
            out.append({"entry": start, "ret": acc - 1 - 2 * COST, "hold": hold})
            inpos = False
    return pd.DataFrame(out)


def main():
    d = pickle.load(open(CACHE, "rb"))
    C, H, L = d["Close"], d["High"], d["Low"]
    for sym in ["SPY", "QQQ"]:
        c, h, l = C[sym], H[sym], L[sym]
        r = c.pct_change()
        for mode in ["ibs", "rsi2"]:
            pos = run(c, h, l, mode)
            t = trades(pos, r)
            wr = (t.ret > 0).mean()
            print(f"\n=== {sym} {mode}: {len(t)} trades ({len(t)/10.5:.0f}/yr) | win {wr*100:.0f}% | "
                  f"avg {t.ret.mean()*100:+.2f}% | med hold {t.hold.median():.0f}d | "
                  f"avg win {t.ret[t.ret>0].mean()*100:+.2f}% avg loss {t.ret[t.ret<=0].mean()*100:+.2f}%")
            print(f"    worst 5: {', '.join(f'{x*100:+.1f}%' for x in t.ret.nsmallest(5))}")
            t["yr"] = pd.to_datetime(t.entry).dt.year
            yr = t.groupby("yr").ret.agg(["sum", "count", lambda s: (s > 0).mean()])
            print("    year: " + "  ".join(f"{y}:{v*100:+.1f}%({int(n)})" for y, (v, n, _) in
                                           zip(yr.index, yr.values)))


if __name__ == "__main__":
    main()
