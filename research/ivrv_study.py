#!/usr/bin/env python3
"""Cross-sectional implied-vs-realized vol spread test (Bali-Hovakimian style) on the
EODHD weekly options panel (57 ETFs+liquid names, Q4'23-present). The nightly discovery
flagged opt_atm_iv (+) and opt_atm_iv x tech_vol20 (-) as its two strongest OOS hits;
this reformulates them as the published anomaly: IVRV = atm_iv - realized vol (20d,
annualized). Rank cross-sectionally each Friday, measure next-week and next-4-week
forward returns by tercile, single-name stocks and ETFs separately.

Small panel -> this is a VALIDATION of the hint, not a full backtest.
Run: python research/ivrv_study.py
"""
import json, math, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")

OPT = Path.home() / "autotrade_options_history"
PX_CACHE = Path(__file__).resolve().parent / ".yf_adj_close.pkl"
ETFS = {"SPY","QQQ","IWM","DIA","XLK","XLF","XLE","XLV","XLI","XLY","XLP","XLU","XLB","XLRE","XLC",
        "TLT","IEF","HYG","LQD","GLD","SLV","USO","GDX","UUP","VXX","SMH","SOXX","XBI","ARKK","KRE"}


def tstat(x):
    x = pd.Series(x).dropna()
    return x.mean() / (x.std(ddof=1) + 1e-12) * math.sqrt(len(x)) if len(x) > 2 else np.nan


def main():
    px = pd.read_pickle(PX_CACHE)
    px.index = pd.to_datetime(px.index).tz_localize(None)
    rv = px.pct_change().rolling(20).std() * math.sqrt(252)

    rows = []
    for f in OPT.glob("*.jsonl"):
        sym = f.stem
        if sym not in px.columns:
            continue
        for ln in f.read_text().strip().split("\n"):
            if not ln:
                continue
            r = json.loads(ln)
            if not r.get("atm_iv"):
                continue
            rows.append({"sym": sym, "date": pd.Timestamp(r["date"]), "iv": r["atm_iv"],
                         "skew": r.get("skew"), "pc_oi": r.get("pc_oi")})
    df = pd.DataFrame(rows)
    days = px.index

    def fwd(sym, d, h):
        i0 = days.searchsorted(d, side="left")
        i1 = i0 + h
        if i0 >= len(days) or i1 >= len(days):
            return np.nan
        p0, p1 = px[sym].iloc[i0], px[sym].iloc[i1]
        if np.isnan(p0) or np.isnan(p1):
            return np.nan
        ret = p1 / p0 - 1
        return ret if abs(ret) <= 1.0 else np.nan

    def rv_at(sym, d):
        i = days.searchsorted(d, side="left")
        return rv[sym].iloc[i] if i < len(days) else np.nan

    df["rv20"] = [rv_at(r.sym, r.date) for r in df.itertuples()]
    df["ivrv"] = df["iv"] - df["rv20"]
    for h, col in [(5, "f5"), (20, "f20")]:
        df[col] = [fwd(r.sym, r.date, h) for r in df.itertuples()]

    for label, sub in [("SINGLE NAMES", df[~df.sym.isin(ETFS)]), ("ETFs", df[df.sym.isin(ETFS)])]:
        sub = sub.dropna(subset=["ivrv", "f5"])
        print(f"\n=== {label}: {sub.sym.nunique()} names, {sub.date.nunique()} weeks, n={len(sub)} ===")
        # demean forward returns within each week (cross-sectional excess)
        for col in ["f5", "f20"]:
            sub[f"x{col}"] = sub[col] - sub.groupby("date")[col].transform("mean")
        sub["ter"] = sub.groupby("date")["ivrv"].transform(
            lambda s: pd.qcut(s.rank(method="first"), 3, labels=False) if len(s) >= 9 else np.nan)
        print(f"{'IVRV tercile':16s} {'xf5%':>14} {'xf20%':>14}")
        for t in range(3):
            g = sub[sub.ter == t]
            c5 = g.groupby("date")["xf5"].mean(); c20 = g.groupby("date")["xf20"].mean()
            print(f"T{t} ({'low' if t==0 else 'high' if t==2 else 'mid'}) "
                  f"{'':6s} {c5.mean()*100:+.2f} ({tstat(c5):+.1f}) {c20.mean()*100:+.2f} ({tstat(c20):+.1f})")
        hi = sub[sub.ter == 2].groupby("date")["xf5"].mean() - sub[sub.ter == 0].groupby("date")["xf5"].mean()
        hi20 = sub[sub.ter == 2].groupby("date")["xf20"].mean() - sub[sub.ter == 0].groupby("date")["xf20"].mean()
        print(f"T2-T0 spread    {'':6s} {hi.mean()*100:+.2f} ({tstat(hi):+.1f}) {hi20.mean()*100:+.2f} ({tstat(hi20):+.1f})")
        # raw IV rank for comparison (the discovery's opt_atm_iv hit)
        sub["teriv"] = sub.groupby("date")["iv"].transform(
            lambda s: pd.qcut(s.rank(method="first"), 3, labels=False) if len(s) >= 9 else np.nan)
        hiv = sub[sub.teriv == 2].groupby("date")["xf5"].mean() - sub[sub.teriv == 0].groupby("date")["xf5"].mean()
        hiv20 = sub[sub.teriv == 2].groupby("date")["xf20"].mean() - sub[sub.teriv == 0].groupby("date")["xf20"].mean()
        print(f"raw-IV T2-T0    {'':6s} {hiv.mean()*100:+.2f} ({tstat(hiv):+.1f}) {hiv20.mean()*100:+.2f} ({tstat(hiv20):+.1f})")


if __name__ == "__main__":
    main()
