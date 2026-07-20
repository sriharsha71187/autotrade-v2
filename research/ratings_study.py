#!/usr/bin/env python3
"""Analyst rating-change / target-revision event study on the 10yr ratings backfill
(948 names, per-day aggregates: n_up, n_down, net, avg_target). The literature says
rec-changes carry a small post-event drift; this tests it on OUR universe with the
same hygiene as pead_study.py: split-adjusted prices, EW-universe excess returns,
monthly-clustered t, IS 2016-2022 / OOS 2023-2026, coverage split, glitch guard.

Events enter at the SAME day's close (sell-side actions publish pre-open), so the
announcement-day jump is excluded — we measure only the drift after.

Run: python research/ratings_study.py
"""
import json, math, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")

RATE = Path.home() / "autotrade_ratings_history"
PX_CACHE = Path(__file__).resolve().parent / ".yf_adj_close.pkl"
HORIZONS = [1, 5, 10, 20, 60]
OOS_START = "2023-01-01"


def tstat(x):
    x = pd.Series(x).dropna()
    return (x.mean() / (x.std(ddof=1) + 1e-12) * math.sqrt(len(x)), len(x)) if len(x) > 2 else (np.nan, len(x))


def monthly_t(df, col):
    m = df.groupby(df["entry"].dt.to_period("M"))[col].mean()
    return tstat(m)


def load_events(px):
    days = px.index
    rows = []
    for f in RATE.glob("*.jsonl"):
        sym = f.stem
        if sym not in px.columns:
            continue
        prev_tgt = None
        for ln in f.read_text().strip().split("\n"):
            if not ln:
                continue
            r = json.loads(ln)
            d = pd.Timestamp(r["date"])
            tgt = r.get("avg_target")
            tgt_chg = None
            if tgt and prev_tgt:
                tgt_chg = tgt / prev_tgt - 1
            if tgt:
                prev_tgt = tgt
            rows.append({"sym": sym, "date": d, "net": r.get("net", 0),
                         "n_up": r.get("n_up", 0), "n_down": r.get("n_down", 0),
                         "tgt_chg": tgt_chg})
    ev = pd.DataFrame(rows)
    pos = days.searchsorted(ev["date"].values, side="left")   # first close ON/after date
    ok = pos < len(days)
    ev = ev[ok].copy()
    ev["entry"] = days[pos[ok]]
    return ev[(ev.entry >= px.index.min() + pd.Timedelta(days=5))]


def forward_excess(ev, px):
    ret = px.pct_change()
    uni = ret.mean(axis=1)
    idx = {d: i for i, d in enumerate(px.index)}
    logpx = np.log(px.values)
    loguni = np.log((1 + uni).cumprod().values)
    cols = {c: j for j, c in enumerate(px.columns)}
    out = {h: [] for h in HORIZONS}
    for r in ev.itertuples():
        i0, j = idx[r.entry], cols[r.sym]
        for h in HORIZONS:
            i1 = i0 + h
            if i1 >= len(px.index) or np.isnan(logpx[i0, j]) or np.isnan(logpx[i1, j]):
                out[h].append(np.nan)
                continue
            raw = math.exp(logpx[i1, j] - logpx[i0, j]) - 1
            mkt = math.exp(loguni[i1] - loguni[i0]) - 1
            out[h].append(raw - mkt if abs(raw) <= 1.5 else np.nan)
    for h in HORIZONS:
        ev[f"x{h}"] = out[h]
    return ev


def row(label, sub):
    cells = []
    for h in HORIZONS:
        t, _ = monthly_t(sub.dropna(subset=[f"x{h}"]), f"x{h}")
        cells.append(f"{sub[f'x{h}'].mean()*100:+.2f} ({t:+.1f})")
    print(f"{label:34s} n={len(sub):6d} " + "".join(f"{c:>15}" for c in cells))


def report(ev, title):
    print(f"\n=== {title} ===")
    print(f"{'portfolio':34s} {'':8s}" + "".join(f"{'+' + str(h) + 'd':>15}" for h in HORIZONS))
    row("net upgrades (net>0)", ev[ev.net > 0])
    row("net downgrades (net<0)", ev[ev.net < 0])
    row("strong up (net>=2)", ev[ev.net >= 2])
    row("strong down (net<=-2)", ev[ev.net <= -2])
    tc = ev.dropna(subset=["tgt_chg"])
    row("target raise >= +10%", tc[tc.tgt_chg >= 0.10])
    row("target cut <= -10%", tc[tc.tgt_chg <= -0.10])
    row("upgrade + target raise", tc[(tc.net > 0) & (tc.tgt_chg > 0.05)])
    row("downgrade + target cut", tc[(tc.net < 0) & (tc.tgt_chg < -0.05)])


def main():
    px = pd.read_pickle(PX_CACHE)
    px.index = pd.to_datetime(px.index).tz_localize(None)
    ev = load_events(px)
    print(f"events {len(ev)} | names {ev.sym.nunique()} | {ev.entry.min().date()} -> {ev.entry.max().date()}")
    ev = forward_excess(ev, px)

    report(ev, "ALL 2016-2026")
    report(ev[ev.entry < OOS_START], "IN-SAMPLE 2016-2022")
    report(ev[ev.entry >= OOS_START], "OUT-OF-SAMPLE 2023-2026")

    cov = ev.groupby("sym").size()
    med = cov.median()
    ev["covn"] = ev.sym.map(cov)
    report(ev[ev["covn"] <= med], f"LOW-COVERAGE half (<= {med:.0f} events)")
    report(ev[ev["covn"] > med], "HIGH-COVERAGE half")


if __name__ == "__main__":
    main()
