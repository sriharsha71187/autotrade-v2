#!/usr/bin/env python3
"""PEAD event study — the one literature-backed signal never yet tested in this repo.
Events: quarterly earnings with a consensus-vs-reported surprise (yfinance backfill,
~700 names 2017-2026). Forward EXCESS returns (vs the cross-sectional mean of the same
universe over the same window) from the first tradable close AFTER the announcement.

Hygiene: split-adjusted yfinance prices (NOT the Alpaca panel - split glitches);
BMO/AMC timing respected; monthly clustering for t-stats (events in the same month are
correlated); IS 2017-2022 / OOS 2023-2026; coverage split (low-analyst-coverage names =
the less-efficient half, where the literature says drift lives); |window return| > 150%
dropped as data glitches.

Run: python research/pead_study.py
"""
import json, math, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")

EARN = Path.home() / "autotrade_earnings_history"
RATE = Path.home() / "autotrade_ratings_history"
PX_CACHE = Path(__file__).resolve().parent / ".yf_adj_close.pkl"
HORIZONS = [1, 5, 10, 20, 40, 60]
OOS_START = "2023-01-01"


def tstat(x):
    x = pd.Series(x).dropna()
    return (x.mean() / (x.std(ddof=1) + 1e-12) * math.sqrt(len(x)), len(x)) if len(x) > 2 else (np.nan, len(x))


def monthly_t(df, col):
    """Cluster by calendar month: t-stat on monthly mean event returns."""
    m = df.groupby(df["entry"].dt.to_period("M"))[col].mean()
    return tstat(m)


def load_events(px):
    days = px.index
    rows = []
    for f in EARN.glob("*.jsonl"):
        sym = f.stem
        if sym not in px.columns:
            continue
        for ln in f.read_text().strip().split("\n"):
            if not ln:
                continue
            r = json.loads(ln)
            if r.get("surprise_pct") is None:
                continue
            ts = pd.Timestamp(r["ts"])
            # BMO (hour<12): the same day's close is already post-announcement.
            # AMC: first tradable close is the NEXT session.
            d0 = ts.tz_localize(None).normalize()
            entry_candidates = days[days >= d0] if ts.hour < 12 else days[days > d0]
            if len(entry_candidates) == 0:
                continue
            rows.append({"sym": sym, "event": d0, "entry": entry_candidates[0],
                         "surprise": r["surprise_pct"]})
    ev = pd.DataFrame(rows)
    return ev[(ev.entry >= px.index.min()) & (ev.entry <= px.index.max())]


def forward_excess(ev, px):
    """Excess return vs equal-weight universe over the identical window."""
    ret = px.pct_change()
    uni = ret.mean(axis=1)  # EW universe daily return
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
            out[h].append(raw - mkt if abs(raw) <= 1.5 else np.nan)  # glitch guard
    for h in HORIZONS:
        ev[f"x{h}"] = out[h]
    return ev


def coverage_map():
    """Median analyst actions/yr per name — proxy for attention/efficiency."""
    cov = {}
    for f in RATE.glob("*.jsonl"):
        n = sum(1 for ln in f.read_text().strip().split("\n") if ln)
        cov[f.stem] = n
    return cov


def report(ev, label):
    print(f"\n--- {label} (n={len(ev)}) ---")
    print("surprise-quintile mean excess % (monthly-clustered t) by horizon")
    ev = ev.copy()
    ev["q"] = ev.groupby(ev["entry"].dt.to_period("Q"))["surprise"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 5, labels=False) if len(s) >= 10 else np.nan)
    hdr = "Q    " + "".join(f"{'+' + str(h) + 'd':>16}" for h in HORIZONS)
    print(hdr)
    for q in range(5):
        sub = ev[ev.q == q]
        cells = []
        for h in HORIZONS:
            t, _ = monthly_t(sub.dropna(subset=[f"x{h}"]), f"x{h}")
            cells.append(f"{sub[f'x{h}'].mean()*100:+.2f} ({t:+.1f})")
        print(f"{q}   " + "".join(f"{c:>16}" for c in cells))
    # long-short top-bottom
    cells = []
    for h in HORIZONS:
        hi, lo = ev[ev.q == 4], ev[ev.q == 0]
        m = hi.groupby(hi["entry"].dt.to_period("M"))[f"x{h}"].mean() - \
            lo.groupby(lo["entry"].dt.to_period("M"))[f"x{h}"].mean()
        t, n = tstat(m)
        cells.append(f"{m.mean()*100:+.2f} ({t:+.1f})")
    print("Q5-Q1" + "".join(f"{c:>16}" for c in cells))


def main():
    px = pd.read_pickle(PX_CACHE)
    px.index = pd.to_datetime(px.index).tz_localize(None)
    ev = load_events(px)
    print(f"events {len(ev)} | names {ev.sym.nunique()} | {ev.entry.min().date()} -> {ev.entry.max().date()}")
    ev = forward_excess(ev, px)

    report(ev, "ALL")
    report(ev[ev.entry < OOS_START], "IN-SAMPLE 2017-2022")
    report(ev[ev.entry >= OOS_START], "OUT-OF-SAMPLE 2023-2026")

    cov = coverage_map()
    ev["covn"] = ev.sym.map(cov)
    med = ev.drop_duplicates("sym")["covn"].median()
    report(ev[ev["covn"] <= med], f"LOW-COVERAGE half (<= {med:.0f} rating-days)")
    report(ev[ev["covn"] > med], "HIGH-COVERAGE half")

    # sign-only view: big beats vs big misses (|surprise| >= 10%)
    for lbl, sub in [("BIG BEAT >=+10%", ev[ev.surprise >= 10]),
                     ("BIG MISS <=-10%", ev[ev.surprise <= -10])]:
        cells = []
        for h in HORIZONS:
            t, n = monthly_t(sub.dropna(subset=[f"x{h}"]), f"x{h}")
            cells.append(f"{sub[f'x{h}'].mean()*100:+.2f} ({t:+.1f})")
        print(f"\n{lbl} (n={len(sub)}): " + " ".join(f"+{h}d {c}" for h, c in zip(HORIZONS, cells)))


if __name__ == "__main__":
    main()
