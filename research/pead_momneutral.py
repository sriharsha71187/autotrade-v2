#!/usr/bin/env python3
"""Momentum-neutral PEAD follow-up — COMMITTED for reproducibility (audit finding
2026-07-19: this analysis previously lived only in a session transcript).

Within the HIGH-analyst-coverage half, stratify events by 12-1 momentum quintile and
measure the surprise Q5-Q1 spread within momentum strata; month-clustered t (one
observation per month = mean of that month's within-stratum spreads). Reverse control:
momentum spread within surprise strata (should be ~0 if surprise drives the effect).

Trial-count disclosure: this configuration was found AFTER inspecting the raw quintile
tables (coverage split x mom-neutralization = post-selection). IS-half t is only ~1.1-1.4;
the effect is 2023-26-concentrated and the size pattern inverts the PEAD literature.
Status: watch-only candidate. Do not deploy on this evidence.

Run: python research/pead_momneutral.py
"""
import warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pead_study as P


def qcut5(s):
    s2 = s.dropna()
    if len(s2) < 10:
        return pd.Series(np.nan, index=s.index)
    return pd.qcut(s2.rank(method="first"), 5, labels=False).reindex(s.index)


def mspread(sub, h, qcol="q", strat="mq"):
    vals = {}
    for mth, g in sub.groupby(sub["entry"].dt.to_period("M")):
        sp = []
        for _, gg in g.groupby(strat):
            a, b = gg[gg[qcol] == 4][f"x{h}"].dropna(), gg[gg[qcol] == 0][f"x{h}"].dropna()
            if len(a) and len(b):
                sp.append(a.mean() - b.mean())
        if sp:
            vals[mth] = np.mean(sp)
    s = pd.Series(vals)
    t, n = P.tstat(s)
    return s.mean() * 100, t, n


def main():
    px = pd.read_pickle(P.PX_CACHE)
    px.index = pd.to_datetime(px.index).tz_localize(None)
    ev = P.load_events(px)
    ev = P.forward_excess(ev, px)
    cov = P.coverage_map()
    ev["covn"] = ev.sym.map(cov)
    med = ev.drop_duplicates("sym")["covn"].median()
    mom = (px.shift(21) / px.shift(252) - 1)

    for grp, dat in [("HIGH-COV", ev[ev["covn"] > med]),
                     ("LOW-COV", ev[ev["covn"] <= med]),
                     ("ALL", ev)]:
        dat = dat.copy()
        dat["mom"] = [mom.at[r.entry, r.sym] if r.sym in mom.columns else np.nan
                      for r in dat.itertuples()]
        dat["q"] = dat.groupby(dat["entry"].dt.to_period("Q"))["surprise"].transform(qcut5)
        dat["mq"] = dat.groupby(dat["entry"].dt.to_period("Q"))["mom"].transform(qcut5)
        dat = dat.dropna(subset=["q", "mq"])
        for lbl, sub in [("IS", dat[dat.entry < "2023-01-01"]),
                         ("OOS", dat[dat.entry >= "2023-01-01"]),
                         ("FULL", dat)]:
            out = []
            for h in [20, 40, 60]:
                m, t, n = mspread(sub, h)
                out.append(f"+{h}d {m:+.2f}% (t{t:+.1f})")
            print(f"{grp:9s} {lbl:5s} mom-neutral Q5-Q1: " + "  ".join(out) + f"  [months {n}]")
        # reverse control on the full period
        m, t, n = mspread(dat, 60, qcol="mq", strat="q")
        print(f"{grp:9s} CONTROL surprise-neutral mom Q5-Q1 +60d: {m:+.2f}% (t{t:+.1f})")


if __name__ == "__main__":
    main()
