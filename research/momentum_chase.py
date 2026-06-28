#!/usr/bin/env python3
"""Are we chasing momentum (buying tops) or capturing continuation? For every holding spell of
the momentum sleeve: gain BEFORE inclusion (the 12-1mo momentum that selected it), gain WHILE HELD
(entry->drop), and gain AFTER we dropped it (next ~60d). If held-gain << pre-gain or post-gain is
strongly positive, we're chasing / exiting too early. Run: python research/momentum_chase.py
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe
POST = 60   # trading days after drop


def main():
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    idx = px.index
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    stocks = [c for c in px.columns if c not in ("SPY","QQQ")]
    mom = px[stocks].shift(21)/px[stocks].shift(252)-1

    # monthly top-10 picks
    picks = {}
    for d in mstart:
        if d in mom.index:
            m = mom.loc[d].dropna()
            if len(m) >= 10: picks[d] = list(m.nlargest(10).index)
    rebs = sorted(picks.keys())
    pos = {r: i for i, r in enumerate(rebs)}

    # build per-stock spells (consecutive months in top-10)
    spells = []   # (stock, entry_reb, exit_reb_or_None)
    held = {}     # stock -> entry_reb index of current spell
    for i, r in enumerate(rebs):
        cur = set(picks[r])
        for s in list(held):
            if s not in cur:                       # dropped at r
                spells.append((s, rebs[held[s]], r)); del held[s]
        for s in cur:
            if s not in held: held[s] = i          # entered at r
    for s, i in held.items():                       # still held at end
        spells.append((s, rebs[i], None))

    def at(series, date):
        a = series.reindex(idx).ffill()
        return float(a.loc[date]) if date in a.index else np.nan

    rows = []
    for s, entry, exit_ in spells:
        e_px = at(px[s], entry)
        pre = at(mom[s], entry)                      # momentum that selected it
        if exit_ is None:                            # still held -> use last px, no post
            held_g = at(px[s], idx[-1])/e_px - 1; post = np.nan; xdate = idx[-1]
        else:
            x_px = at(px[s], exit_); held_g = x_px/e_px - 1; xdate = exit_
            xi = idx.get_loc(idx[idx.get_indexer([exit_], method="ffill")[0]])
            post_idx = idx[min(xi+POST, len(idx)-1)]
            post = at(px[s], post_idx)/x_px - 1
        rows.append({"stock": s, "entry": entry.date(), "exit": (exit_.date() if exit_ else "held"),
                     "pre%": pre*100, "held%": held_g*100, "post%": post*100 if post==post else np.nan})
    df = pd.DataFrame(rows)
    closed = df[df["exit"] != "held"]

    print(f"\n=== Momentum sleeve: chasing check · {idx.min().date()}..{idx.max().date()} · {len(df)} holding spells ===\n")
    print(f"  AVERAGE per spell:")
    print(f"    gain BEFORE inclusion (12-1mo mom):  {df['pre%'].mean():+6.1f}%")
    print(f"    gain WHILE HELD (entry->drop):       {closed['held%'].mean():+6.1f}%   (median {closed['held%'].median():+.1f}%, win {(closed['held%']>0).mean()*100:.0f}%)")
    print(f"    gain AFTER DROP (next {POST}d):          {closed['post%'].mean():+6.1f}%   (median {closed['post%'].median():+.1f}%, kept-rising {(closed['post%']>0).mean()*100:.0f}%)")
    print(f"\n  read: held% > 0 = continuation captured; held% << pre% = momentum cools after we buy;")
    print(f"        post% > 0 = we exit too early (it keeps running); post% < 0 = good exits (we left near tops).")
    # recent 12mo spells, named
    rec = df[pd.to_datetime(df["entry"]) >= (pd.Timestamp(idx[-1]) - pd.Timedelta(days=400))].sort_values("entry")
    print(f"\n  recent spells (last ~12mo):")
    print(f"    {'stock':7}{'entry':>11}{'exit':>11}{'pre%':>8}{'held%':>8}{'post%':>8}")
    for _, r in rec.iterrows():
        print(f"    {r['stock']:7}{str(r['entry']):>11}{str(r['exit']):>11}{r['pre%']:>7.0f}%{r['held%']:>7.0f}%"
              + (f"{r['post%']:>7.0f}%" if r['post%']==r['post%'] else f"{'--':>8}"))


if __name__ == "__main__":
    main()
