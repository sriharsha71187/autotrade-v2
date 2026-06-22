#!/usr/bin/env python3
"""Distillation analysis — can we crystallize a DETERMINISTIC strategy from the data,
and does the LLM add anything beyond the observable features?

Treats the candidate-outcomes dataset (every candidate + features -> forward return) as a
supervised problem, independent of the LLM:
  1. Which FEATURES predict forward return? (univariate, directional)
  2. Is a deterministic feature RULE profitable, and does it hold OUT-OF-SAMPLE (train on
     early days, test on held-out later days)? <- the honesty gate against overfitting.
  3. Does the LLM's PICK add value BEYOND the features? (if not -> the LLM is removable.)

Run:  python research/distill.py     (reads autotrade_candidate_outcomes/)
Caveat: a few days of a few dozen symbols is heavily autocorrelated — treat everything as
a HYPOTHESIS to confirm as the sample grows. The framework is the deliverable.
"""
import json, glob
from pathlib import Path
import numpy as np
import pandas as pd

DIR = Path.home() / "autotrade_candidate_outcomes"
NUM = ["day_pct", "vwap_ext", "off_hod", "rsi", "from_open", "mover_rank"]
Y = "fwd_eod"


def load():
    rows = []
    for f in glob.glob(str(DIR / "*.jsonl")):
        rows += [json.loads(l) for l in Path(f).read_text().splitlines() if l.strip()]
    df = pd.DataFrame(rows)
    for c in NUM + [Y, "fwd_1d"]:
        if c in df: df[c] = pd.to_numeric(df[c], errors="coerce")
    df["hour"] = pd.to_datetime(df["ts"]).dt.tz_convert("US/Eastern").dt.hour
    return df


def univariate(df):
    print("== 1. which features predict forward EOD return? (Spearman, all candidates) ==")
    d = df.dropna(subset=[Y])
    for c in NUM + ["hour"]:
        s = d.dropna(subset=[c])
        if len(s) < 50: continue
        rho = s[c].corr(s[Y], method="spearman")
        print(f"  {c:11} rho={rho:+.3f}  n={len(s)}")
    print("  (rho sign = direction; |rho| tiny everywhere = no single feature carries it.)")


def buckets(df, col, q=5):
    d = df.dropna(subset=[col, Y]).copy()
    if len(d) < 100: return
    d["bk"] = pd.qcut(d[col].rank(method="first"), q, labels=False)
    print(f"\n== 2. forward EOD return by {col} quintile (low->high) ==")
    g = d.groupby("bk")[Y].agg(["mean", "count"])
    for bk, r in g.iterrows():
        print(f"  Q{int(bk)+1}: {r['mean']*100:+.2f}%  (n={int(r['count'])})")


def oos_rule(df):
    """Deterministic 'don't chase' rule: long only the LEAST-extended candidates.
    Fit the vwap_ext threshold on EARLY days, evaluate on HELD-OUT later days."""
    print("\n== 3. OOS test of a deterministic anti-chase rule (train early days / test late) ==")
    d = df.dropna(subset=["vwap_ext", Y]).copy()
    days = sorted(d["day"].unique())
    if len(days) < 4:
        print("  need >=4 days for a clean split — keep collecting."); return
    cut = days[len(days) * 2 // 3]
    tr, te = d[d.day < cut], d[d.day >= cut]
    # rule: enter only candidates with vwap_ext <= train-median (i.e., NOT extended)
    thr = tr["vwap_ext"].median()
    base_te = te[Y].mean()
    rule_te = te[te.vwap_ext <= thr][Y].mean()
    print(f"  train days {days[0]}..{days[len(days)*2//3-1]} -> threshold vwap_ext <= {thr:+.4f}")
    print(f"  TEST days {cut}..{days[-1]}:  all-candidates {base_te*100:+.2f}%  vs  "
          f"rule(not-extended) {rule_te*100:+.2f}%  edge {(rule_te-base_te)*100:+.2f}%")


def llm_value(df):
    print("\n== 4. does the LLM PICK add value beyond the features? ==")
    d = df.dropna(subset=[Y])
    pk, ps = d[d.picked == 1][Y], d[d.picked == 0][Y]
    print(f"  raw: picked {pk.mean()*100:+.2f}% (n={len(pk)})  vs  passed {ps.mean()*100:+.2f}%")
    # feature-controlled: within the same vwap_ext/mover_rank cell, do picks beat passes?
    d = d.dropna(subset=["vwap_ext", "mover_rank"]).copy()
    d["vb"] = pd.qcut(d["vwap_ext"].rank(method="first"), 4, labels=False)
    d["rb"] = pd.cut(d["mover_rank"], [0, 3, 6, 12, 999], labels=False)
    diffs = []
    for _, g in d.groupby(["vb", "rb"]):
        a, b = g[g.picked == 1][Y], g[g.picked == 0][Y]
        if len(a) and len(b) >= 5:
            diffs.append(a.mean() - b.mean())
    if diffs:
        print(f"  feature-matched: picks beat passes by {np.mean(diffs)*100:+.2f}% avg across "
              f"{len(diffs)} cells (positive = LLM adds something the features don't).")


def main():
    df = load()
    if df.empty:
        print("no data — run backfill_outcomes.py first"); return
    print(f"dataset: {len(df)} candidate-obs · {df['symbol'].nunique()} symbols · "
          f"{df['day'].nunique()} days · {int(df['picked'].sum())} LLM picks\n")
    univariate(df)
    buckets(df, "vwap_ext"); buckets(df, "mover_rank")
    oos_rule(df)
    llm_value(df)
    print("\nThe distillation question: if a deterministic rule reproduces the model's good "
          "entries (OOS), the LLM is removable. If the LLM adds value the features can't "
          "(section 4 positive), keep it as a feature, not the decider. Needs a bigger sample.")


if __name__ == "__main__":
    main()
