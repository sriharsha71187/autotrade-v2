#!/usr/bin/env python3
"""Counter-audit of the Codex form4_cluster_reversal prototype: drawdown-matched
placebo on the liquid subset (their #1 self-declared unresolved test).

For each of their cluster events whose ticker exists in our split-adjusted panel,
sample up to 3 same-month names with prior-20-session SPY-excess return within
+/-2pp and no cluster that month; compare forward-20-session SPY-hedged returns,
paired by month.

Result 2026-07-20: cluster +0.48%/20d vs placebo +0.77%/20d, paired diff -0.30%
(t -0.5), flat in both halves -> in large/mid caps the cluster adds nothing over
generic dip-buying; the prototype P&L concentrates in the small-cap 79% where
coverage/identifier/cost risks are maximal.

Requires: work/sec_cluster_trade_ledger.csv from the Codex sandbox (path below).
Run: python research/form4_placebo.py
"""
import math, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")

LEDGER = Path("/Users/nirvaan/Documents/Codex/2026-07-10/che/work/sec_cluster_trade_ledger.csv")
PX_CACHE = Path(__file__).resolve().parent / ".yf_adj_close.pkl"


def main():
    tl = pd.read_csv(LEDGER, parse_dates=["entry", "exit"])
    tl.columns = [c.lower() for c in tl.columns]
    px = pd.read_pickle(PX_CACHE)
    px.index = pd.to_datetime(px.index).tz_localize(None)
    spy = px["SPY"]
    days = px.index

    def fwd20(sym, d):
        i0 = days.searchsorted(d, side="left")
        i1 = i0 + 20
        if i0 >= len(days) or i1 >= len(days):
            return np.nan
        p0, p1 = px[sym].iloc[i0], px[sym].iloc[i1]
        if np.isnan(p0) or np.isnan(p1):
            return np.nan
        r = p1 / p0 - 1 - (spy.iloc[i1] / spy.iloc[i0] - 1)
        return r if abs(r) <= 1.5 else np.nan

    ev = tl[tl.ticker.isin(px.columns)].copy()
    prior = []
    for r in ev.itertuples():
        i0 = days.searchsorted(r.entry, side="left")
        if i0 < 21 or i0 >= len(days):
            prior.append(np.nan)
            continue
        p = px[r.ticker].iloc[i0 - 1] / px[r.ticker].iloc[i0 - 21] - 1
        s = spy.iloc[i0 - 1] / spy.iloc[i0 - 21] - 1
        prior.append(p - s)
    ev["prior20x"] = prior
    ev["f20x"] = [fwd20(r.ticker, r.entry) for r in ev.itertuples()]
    ev = ev.dropna(subset=["prior20x", "f20x"])
    print(f"liquid-subset events: {len(ev)} / {len(tl)} | mean prior-20d excess {ev.prior20x.mean()*100:+.1f}%")

    by_month = ev.groupby(ev.entry.dt.to_period("M")).ticker.apply(set).to_dict()
    rng = np.random.default_rng(7)
    placebo = []
    for r in ev.itertuples():
        i0 = days.searchsorted(r.entry, side="left")
        if i0 < 21:
            continue
        pri = px.iloc[i0 - 1] / px.iloc[i0 - 21] - 1 - (spy.iloc[i0 - 1] / spy.iloc[i0 - 21] - 1)
        cand = [c for c in pri[(pri - r.prior20x).abs() < 0.02].index
                if c not in by_month.get(r.entry.to_period("M"), set()) and c != r.ticker]
        if not cand:
            continue
        for c in rng.choice(cand, size=min(3, len(cand)), replace=False):
            v = fwd20(c, r.entry)
            if v == v:
                placebo.append({"m": r.entry.to_period("M"), "f20x": v})
    pl = pd.DataFrame(placebo)
    ev["m"] = ev.entry.dt.to_period("M")

    def mt(df):
        m = df.groupby("m")["f20x"].mean()
        return m, m.mean() * 100, m.mean() / (m.std() + 1e-12) * math.sqrt(len(m))

    me, a, ta = mt(ev)
    mp, b, tb = mt(pl)
    print(f"CLUSTER fwd-20d hedged: {a:+.2f}% (month-t {ta:+.1f})")
    print(f"PLACEBO matched      : {b:+.2f}% (month-t {tb:+.1f})")
    mm = me.to_frame("ev").join(mp.to_frame("pl")).dropna()
    for lbl, seg in [("FULL", mm), ("2020-2022", mm[mm.index < "2023-01"]), ("2023-2026", mm[mm.index >= "2023-01"])]:
        d = seg.ev - seg.pl
        print(f"{lbl:9s} paired diff {d.mean()*100:+.2f}%/20d (t {d.mean()/(d.std()+1e-12)*math.sqrt(len(d)):+.1f}, months {len(d)})")


if __name__ == "__main__":
    main()
