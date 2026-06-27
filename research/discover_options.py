#!/usr/bin/env python3
"""Cross-LAYER discovery: do OPTIONS signals (IV level/change, skew, GEX, put/call) — alone and
INTERACTED with technicals — predict forward returns? Uses the backfilled EODHD options history
(~/autotrade_options_history) joined to price-derived returns. Same discipline as discover.py:
day-neutral cross-sectional IC, IS/OOS both-halves bar, trial-count tracked.

HONEST CAVEAT: only ~57 symbols x ~85 weekly dates so far -> low statistical power. Exploratory
first look; widen as the backfill universe grows. Run: python research/discover_options.py
"""
import sys, json
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe

OPT = Path.home() / "autotrade_options_history"
LEDGER = Path.home() / "autotrade_strategies.json"


def load_options():
    rows = []
    for f in OPT.glob("*.jsonl"):
        for l in f.read_text().splitlines():
            if l.strip(): rows.append(json.loads(l))
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    return df


def panel(df, col):
    return df.pivot_table(index="date", columns="symbol", values=col)


def fmb_ic(feat, fwd, days, min_n=12):
    ics = []
    for d in days:
        if d not in feat.index or d not in fwd.index: continue
        s = pd.concat([feat.loc[d], fwd.loc[d]], axis=1).dropna()
        if len(s) >= min_n and s.iloc[:, 0].nunique() > 4:
            ics.append(s.iloc[:, 0].corr(s.iloc[:, 1], method="spearman"))
    ics = np.array([x for x in ics if pd.notna(x)])
    if len(ics) < 6: return None
    t = ics.mean()/(ics.std(ddof=1)/np.sqrt(len(ics))) if ics.std(ddof=1) > 0 else 0
    return ics.mean(), t, len(ics)


def main():
    od = load_options()
    print(f"options history: {od['symbol'].nunique()} symbols, {od['date'].nunique()} dates "
          f"({od['date'].min().date()}..{od['date'].max().date()})")
    px = fetch(universe())
    px.index = pd.to_datetime(px.index).tz_localize(None).normalize()

    # OPTION features (cross-sectional panels, date x symbol)
    OF = {}
    for c in ["atm_iv", "skew", "gex_proxy", "pc_oi"]:
        if c in od: OF[c] = panel(od, c)
    OF["iv_chg"]  = OF["atm_iv"].pct_change()           # week-over-week IV change
    OF["skew_chg"] = OF["skew"].diff()
    OF["pc_oi_chg"] = OF["pc_oi"].pct_change()
    # a couple TECHNICALS on the same dates (for interactions)
    TECH = {"mom_20": px/px.shift(20)-1, "dist_50dma": px/px.rolling(50).mean()-1}

    dates = sorted(set(OF["atm_iv"].index))
    fwd = {h: None for h in (5, 20)}
    for h in (5, 20):
        f = px.shift(-h)/px - 1
        fwd[h] = f.reindex(dates)                        # forward return on option dates
    split = dates[int(len(dates)*0.6)]
    IS = [d for d in dates if d < split]; OOS = [d for d in dates if d >= split]
    print(f"IS<{split.date()}<=OOS  ({len(IS)}/{len(OOS)} weekly dates)\n")

    # build candidate features: options alone + options x technicals
    def zx(p):  # cross-sectional z-score
        return p.sub(p.mean(1), 0).div(p.std(1), 0)
    cands = dict(OF)
    for oc in ["iv_chg", "skew", "gex_proxy", "pc_oi", "atm_iv"]:
        for tc in TECH:
            cands[f"{oc}×{tc}"] = zx(OF[oc].reindex(dates)) * zx(TECH[tc].reindex(dates))

    n, res = 0, []
    for name, fdf in cands.items():
        fdf = fdf.reindex(dates)
        for h in (5, 20):
            n += 1
            ris, ros = fmb_ic(fdf, fwd[h], IS), fmb_ic(fdf, fwd[h], OOS)
            if not ris or not ros: continue
            res.append({"f": name, "h": h, "ic_is": ris[0], "t_is": ris[1], "ic_oos": ros[0], "t_oos": ros[1]})
    res.sort(key=lambda r: abs(r["t_oos"]), reverse=True)
    print(f"  tested {n} options/combo features. (sample is SMALL -> exploratory)\n")
    print(f"  {'feature':22} {'h':>3} {'IC_is':>7} {'t_is':>6} {'IC_oos':>7} {'t_oos':>6}  flag")
    surv = []
    for r in res[:16]:
        both = np.sign(r["t_is"]) == np.sign(r["t_oos"]) and abs(r["t_is"]) > 1.8 and abs(r["t_oos"]) > 1.8
        flag = "both-halves" if both else ("OOS-only⚠" if abs(r["t_oos"]) > 2.5 else "")
        if both: surv.append(r)
        print(f"  {r['f']:22} {r['h']:>3} {r['ic_is']:+.3f} {r['t_is']:+6.2f} {r['ic_oos']:+.3f} {r['t_oos']:+6.2f}  {flag}")

    led = json.loads(LEDGER.read_text())
    led["discovered_options"] = {"updated": str(od["date"].max().date()), "n_tested": n,
        "sample": f"{od['symbol'].nunique()} symbols x {len(dates)} weekly dates (low power)",
        "survivors": [{"feature": s["f"], "horizon": s["h"], "ic_oos": round(s["ic_oos"], 4),
                       "t_oos": round(s["t_oos"], 2), "ic_is": round(s["ic_is"], 4)} for s in surv]}
    LEDGER.write_text(json.dumps(led, indent=2))
    print(f"\n  {len(surv)} both-halves survivors (bar lowered to t>1.8 given small sample — treat as HINTS only).")


if __name__ == "__main__":
    main()
