#!/usr/bin/env python3
"""Rigorous selection-skill test on the candidate-outcomes dataset.

The crude test (picks vs the whole passed universe) is unfair — picks are a biased
high-mover subset. This controls for that two ways, per cycle:

  1. WITHIN-CYCLE PAIRED: pick's forward return minus the mean of the OTHER candidates
     it could have taken that same cycle. Controls for the tape that cycle. Sign test +
     bootstrap CI -> does the pick beat the average alternative?
  2. MONTE-CARLO RANDOM BASELINE: replace each pick with a random candidate from the
     same cycle (and, separately, from that cycle's TOP-MOVERS only — the pool the model
     actually chooses among). 5000 draws -> where does the model's real mean fall in the
     null? p = P(random >= model). Skill = model beats random.

Run:  python research/analyze_picks.py     (reads autotrade_candidate_outcomes/)
"""
import json, glob
from pathlib import Path
import numpy as np
import pandas as pd

DIR = Path.home() / "autotrade_candidate_outcomes"
HZ = ["fwd_30m", "fwd_1h", "fwd_eod", "fwd_1d", "fwd_3d"]
TOPK = 6          # "top movers" pool = mover_rank <= TOPK
NTRIAL = 5000
rng = np.random.default_rng(7)


def load():
    rows = []
    for f in glob.glob(str(DIR / "*.jsonl")):
        for ln in Path(f).read_text().splitlines():
            if ln.strip():
                rows.append(json.loads(ln))
    return pd.DataFrame(rows)


def analyze(df, h):
    cells = []   # per picked-cycle: (pick_ret, all_pool[], top_pool[])
    for ts, g in df.groupby("ts"):
        g = g.dropna(subset=[h])
        if g.empty or g["picked"].sum() == 0:
            continue
        pick = g[g.picked == 1][h]
        if pick.empty:
            continue
        pr = float(pick.mean())                              # mean if >1 pick that cycle
        pool = g[h].to_numpy()                               # all candidates that cycle
        top = g[(g.mover_rank.notna()) & (g.mover_rank <= TOPK)][h].to_numpy()
        if len(pool) < 2:
            continue
        cells.append((pr, pool, top if len(top) else pool))
    if len(cells) < 5:
        return None
    picks = np.array([c[0] for c in cells])
    model_mean = picks.mean()
    # within-cycle paired edge (pick - mean of OTHER candidates)
    paired = np.array([c[0] - c[1][c[1] != c[0]].mean() if (c[1] != c[0]).any() else 0.0 for c in cells])
    win = float((paired > 0).mean())
    boot = np.array([rng.choice(paired, len(paired), replace=True).mean() for _ in range(2000)])
    ci = (np.percentile(boot, 2.5), np.percentile(boot, 97.5))
    # MC random baseline (all-cycle pool, and top-movers pool)
    def mc(pool_idx):
        null = np.empty(NTRIAL)
        for t in range(NTRIAL):
            null[t] = np.mean([rng.choice(c[pool_idx]) for c in cells])
        return float((null >= model_mean).mean())
    p_all, p_top = mc(1), mc(2)
    return dict(n=len(cells), model=model_mean, paired=paired.mean(), ci=ci,
                win=win, p_all=p_all, p_top=p_top)


def main():
    df = load()
    if df.empty:
        print("no candidate-outcome data yet — run backfill_outcomes.py first"); return
    print(f"dataset: {len(df)} obs · {df['symbol'].nunique()} symbols · "
          f"{int(df['picked'].sum())} picks · {df['day'].nunique()} days\n")
    print(f"{'horizon':8}{'n_cyc':6}{'pick%':>8}{'paired edge (95% CI)':>26}"
          f"{'win%':>7}{'p(rand≥)':>10}{'p(topmv≥)':>11}")
    for h in HZ:
        r = analyze(df, h)
        if not r:
            print(f"{h:8}  (insufficient)"); continue
        print(f"{h:8}{r['n']:<6}{r['model']*100:>+7.2f}%"
              f"{r['paired']*100:>+10.2f}% [{r['ci'][0]*100:+.2f},{r['ci'][1]*100:+.2f}]"
              f"{r['win']*100:>7.0f}{r['p_all']:>10.3f}{r['p_top']:>11.3f}")
    print("\nRead: paired edge<0 or win%<50 = picks worse than the alternatives that cycle.")
    print("p(rand≥) = chance a RANDOM picker matched/beat the model (high = no skill).")
    print("p(topmv≥) = same but random drawn only from that cycle's top movers (the fair test).")


if __name__ == "__main__":
    main()
