#!/usr/bin/env python3
"""EXECUTABLE IBS variant (audit fix 2026-07-19): the original backtests fill at the
same close whose IBS defines the signal — not executable (final IBS unknown until the
bar closes). This version fills BOTH entry and exit at the NEXT session's OPEN, plus a
10-session time-stop. This is the deployable statistic (spec: spy_ibs_next_open_v1).
Costs: 1bp and 5bp per side. IS 2016-2021 / OOS 2022-2026.
Run: python research/ibs_next_open.py
"""
import math, pickle, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")

CACHE = Path(__file__).resolve().parent / ".yf_ohlcv.pkl"
OOS = pd.Timestamp("2022-01-01")


def stats(r, days):
    r = pd.Series(r).dropna()
    if len(r) < 5:
        return "n<5"
    ann = r.mean() * 252 / max(days, 1) if False else r.mean()
    return r


def main():
    d = pickle.load(open(CACHE, "rb"))
    O, H, L, C = d["Open"], d["High"], d["Low"], d["Close"]
    for sym in ["SPY", "QQQ"]:
        o, h, l, c = O[sym], H[sym], L[sym], C[sym]
        ibs = ((c - l) / (h - l)).replace([np.inf, -np.inf], np.nan)
        idx = c.index
        n = len(idx)
        for cost in [0.0001, 0.0005]:
            daily = pd.Series(0.0, index=idx)
            i = 0
            trades = []
            while i < n - 2:
                if ibs.iloc[i] < 0.10:                     # signal at close i
                    ei = i + 1                             # enter at open i+1
                    entry_px = o.iloc[ei]
                    # first partial day: open -> close
                    daily.iloc[ei] += c.iloc[ei] / o.iloc[ei] - 1 - cost
                    j = ei
                    held = 1
                    while j < n - 1:
                        if ibs.iloc[j] > 0.90 or held >= 10:   # exit signal at close j
                            daily.iloc[j + 1] += o.iloc[j + 1] / c.iloc[j] - 1 - cost
                            trades.append(o.iloc[j + 1] / entry_px - 1 - 2 * cost)
                            i = j + 1
                            break
                        j += 1
                        held += 1
                        daily.iloc[j] += c.iloc[j] / c.iloc[j - 1] - 1
                    else:
                        i = n
                else:
                    i += 1
            t = pd.Series(trades)
            for lbl, seg in [("IS", daily[daily.index < OOS]), ("OOS", daily[daily.index >= OOS])]:
                sh = seg.mean() / (seg.std() + 1e-12) * math.sqrt(252)
                eq = (1 + seg).cumprod()
                dd = (eq / eq.cummax() - 1).min()
                print(f"{sym} next-open cost {cost*1e4:.0f}bp/side {lbl:4s}: "
                      f"ann {seg.mean()*252*100:+6.1f}%  Sh {sh:+5.2f}  dd {dd*100:5.1f}%")
            print(f"{sym} cost {cost*1e4:.0f}bp: {len(t)} trades, win {(t>0).mean()*100:.0f}%, "
                  f"avg {t.mean()*100:+.2f}%, worst {t.min()*100:+.1f}%")


if __name__ == "__main__":
    main()
