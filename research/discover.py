#!/usr/bin/env python3
"""OPEN-ENDED signal discovery — sweep a broad feature library against forward returns and
surface whatever predicts, INCLUDING signals we never hypothesized. The danger here is
data-mining: test enough features and noise looks predictive. So discipline is mandatory:
  - day-neutral CROSS-SECTIONAL IC (rank corr within each day), Fama-MacBeth across days
  - IN-SAMPLE vs OUT-OF-SAMPLE split — a signal must hold in BOTH halves to count
  - track the NUMBER of features tested; flag only |t|>3 survivors (Harvey-Liu-Zhu hurdle)
A surviving signal here is a LEAD, not a strategy — it earns trust only via OOS persistence
+ economic sense. Writes survivors to the ledger's 'discovered' section.

Run: python research/discover.py
"""
import sys, json
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe

LEDGER = Path.home() / "autotrade_strategies.json"
HORIZONS = [5, 20, 60]


def rsi(px, n=14):
    d = px.diff(); up = d.clip(lower=0).rolling(n).mean(); dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100/(1 + up/dn.replace(0, np.nan))


def feature_lib(px):
    r = px.pct_change()
    F = {
        "mom_12_1":   px.shift(21)/px.shift(252) - 1,     # classic momentum
        "mom_20":     px/px.shift(20) - 1,
        "mom_60":     px/px.shift(60) - 1,
        "mom_120":    px/px.shift(120) - 1,
        "rev_5":     -(px/px.shift(5) - 1),               # short-term reversal
        "rev_1":     -(px/px.shift(1) - 1),
        "accel":      (px/px.shift(20)-1) - (px.shift(20)/px.shift(40)-1),  # momentum acceleration
        "vol_20":     r.rolling(20).std(),
        "lowvol_60": -r.rolling(60).std(),                # low-vol anomaly (sign: low vol -> +)
        "dist_50dma": px/px.rolling(50).mean() - 1,
        "dist_200dma":px/px.rolling(200).mean() - 1,
        "pct_52w":   (px - px.rolling(252).min())/(px.rolling(252).max()-px.rolling(252).min()),
        "dist_52whi": px/px.rolling(252).max() - 1,
        "maxdd_60":   px/px.rolling(60).max() - 1,        # depth of recent drawdown
        "skew_60":    r.rolling(60).skew(),               # return skewness
        "rsi_14":     rsi(px),
        "vol_ratio":  r.rolling(10).std()/r.rolling(60).std(),   # vol expansion
    }
    return F


def fama_macbeth_ic(feat, fwd, days, min_n=20):
    ics = []
    for d in days:
        if d not in feat.index or d not in fwd.index: continue
        x, y = feat.loc[d], fwd.loc[d]
        s = pd.concat([x, y], axis=1).dropna()
        if len(s) >= min_n and s.iloc[:, 0].nunique() > 5:
            ics.append(s.iloc[:, 0].corr(s.iloc[:, 1], method="spearman"))
    ics = np.array([x for x in ics if pd.notna(x)])
    if len(ics) < 8: return None
    t = ics.mean()/(ics.std(ddof=1)/np.sqrt(len(ics))) if ics.std(ddof=1) > 0 else 0
    return ics.mean(), t, len(ics)


def main():
    px = fetch(universe())
    print(f"panel {px.shape}, {px.index.min().date()}..{px.index.max().date()}")
    F = feature_lib(px)
    fwd = {h: px.shift(-h)/px - 1 for h in HORIZONS}
    # weekly sampling reduces overlap; split into in-sample (first 65%) vs OOS (last 35%)
    days = px.index[252::5]
    split = days[int(len(days)*0.65)]
    IS = [d for d in days if d < split]; OOS = [d for d in days if d >= split]
    print(f"testing {len(F)} features x {len(HORIZONS)} horizons; IS<{split.date()}<=OOS  "
          f"({len(IS)}/{len(OOS)} weekly dates)")

    n_tested, results = 0, []
    for name, fdf in F.items():
        for h in HORIZONS:
            n_tested += 1
            ris = fama_macbeth_ic(fdf, fwd[h], IS)
            ros = fama_macbeth_ic(fdf, fwd[h], OOS)
            if not ris or not ros: continue
            ic_is, t_is, _ = ris; ic_os, t_os, _ = ros
            consistent = np.sign(ic_is) == np.sign(ic_os)
            results.append({"feature": name, "horizon": h, "ic_is": ic_is, "t_is": t_is,
                            "ic_oos": ic_os, "t_oos": t_os, "consistent": consistent})

    # --- OPEN-ENDED INTERACTION SWEEP: test pairwise COMBINATIONS, not just single features.
    # We don't predefine which pairs matter (e.g. options×news vs options×SMA) — we search them.
    # Cross-sectional z-score each feature per day, form the product, test its IC. As the options
    # / news / insider layers backfill into the panel, the SAME loop spans those cross-layer combos.
    def zscore_day(fdf, d):
        x = fdf.loc[d] if d in fdf.index else None
        if x is None: return None
        return (x - x.mean()) / x.std() if x.std() else None
    names = list(F.keys())
    inter = {}
    for ia in range(len(names)):
        for ib in range(ia+1, len(names)):
            a, b = names[ia], names[ib]
            za, zb = F[a].sub(F[a].mean(1), 0).div(F[a].std(1), 0), F[b].sub(F[b].mean(1), 0).div(F[b].std(1), 0)
            inter[f"{a}×{b}"] = za * zb
    for name, fdf in inter.items():
        for h in (20, 60):
            n_tested += 1
            ris = fama_macbeth_ic(fdf, fwd[h], IS); ros = fama_macbeth_ic(fdf, fwd[h], OOS)
            if not ris or not ros: continue
            ic_is, t_is, _ = ris; ic_os, t_os, _ = ros
            results.append({"feature": name, "horizon": h, "ic_is": ic_is, "t_is": t_is,
                            "ic_oos": ic_os, "t_oos": t_os, "consistent": np.sign(ic_is) == np.sign(ic_os),
                            "interaction": True})

    results.sort(key=lambda r: abs(r["ic_oos"]) if r["consistent"] else 0, reverse=True)
    print(f"\n  tested {n_tested} feature-horizon pairs. Survivor bar: same sign IS&OOS, |t_oos|>2.\n")
    print(f"  {'feature':12} {'h':>3} {'IC_is':>7} {'t_is':>6} {'IC_oos':>7} {'t_oos':>6}  flag")
    survivors = []
    for r in results[:18]:
        # PROPER bar: meaningful signal in BOTH halves (not just matching sign), same direction.
        both = np.sign(r["t_is"]) == np.sign(r["t_oos"]) and abs(r["t_is"]) > 2.0 and abs(r["t_oos"]) > 2.0
        strong = both and min(abs(r["t_is"]), abs(r["t_oos"])) > 3.0
        flag = "ROBUST(both>3)" if strong else ("survivor(both>2)" if both else
               ("OOS-only⚠regime?" if abs(r["t_oos"]) > 3 else ""))
        if both: survivors.append(r)
        print(f"  {r['feature']:12} {r['horizon']:>3} {r['ic_is']:+.3f} {r['t_is']:+6.2f} "
              f"{r['ic_oos']:+.3f} {r['t_oos']:+6.2f}  {flag}")

    # write to ledger's 'discovered' section
    led = json.loads(LEDGER.read_text())
    led["discovered"] = {
        "updated": str(px.index.max().date()), "n_tested": n_tested,
        "method": "day-neutral cross-sectional IC, Fama-MacBeth, IS/OOS split, weekly sampled, technical features only (v1)",
        "survivors": [{"feature": s["feature"], "horizon": s["horizon"],
                       "ic_oos": round(s["ic_oos"], 4), "t_oos": round(s["t_oos"], 2),
                       "ic_is": round(s["ic_is"], 4)} for s in survivors]}
    LEDGER.write_text(json.dumps(led, indent=2))
    print(f"\n  {len(survivors)} survivors written to ledger. (n_tested={n_tested} -> expect ~{n_tested*0.05:.0f} false at p<.05; "
          f"only |t_oos|>3 + economic sense should be trusted.)")


if __name__ == "__main__":
    main()
