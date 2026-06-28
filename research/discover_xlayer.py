#!/usr/bin/env python3
"""CROSS-LAYER discovery: technicals + OPTIONS + NEWS together, plus every cross-layer
interaction (options×news, options×technical, news×technical) — open-ended, vs forward returns.
Same discipline: day-neutral cross-sectional IC, IS/OOS BOTH-halves bar, trial-count tracked.
Small sample (options-covered universe) -> exploratory. Run: python research/discover_xlayer.py
"""
import sys, json
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe

OPTD = Path.home() / "autotrade_options_history"
NEWSD = Path.home() / "autotrade_news_history"
LEDGER = Path.home() / "autotrade_strategies.json"


def load_dir(d, cols):
    rows = []
    for f in d.glob("*.jsonl"):
        sym = f.stem
        for l in f.read_text().splitlines():
            if l.strip():
                r = json.loads(l); r["symbol"] = sym; rows.append(r)
    df = pd.DataFrame(rows)
    if df.empty: return df
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    return df


def pan(df, c): return df.pivot_table(index="date", columns="symbol", values=c)
def zx(p): return p.sub(p.mean(1), 0).div(p.std(1), 0)


def fmb(feat, fwd, days, min_n=12):
    ics = []
    for d in days:
        if d not in feat.index or d not in fwd.index: continue
        s = pd.concat([feat.loc[d], fwd.loc[d]], axis=1).dropna()
        if len(s) >= min_n and s.iloc[:, 0].nunique() > 4:
            ics.append(s.iloc[:, 0].corr(s.iloc[:, 1], method="spearman"))
    ics = np.array([x for x in ics if pd.notna(x)])
    if len(ics) < 6: return None
    return ics.mean(), (ics.mean()/(ics.std(ddof=1)/np.sqrt(len(ics))) if ics.std(ddof=1) else 0)


def main():
    od, nd = load_dir(OPTD, ["atm_iv"]), load_dir(NEWSD, ["n_news"])
    rd = load_dir(Path.home() / "autotrade_ratings_history", ["net"])   # analyst rating-changes
    ind = load_dir(Path.home() / "autotrade_insider_history", ["net_buy"])   # insider buying
    instd = load_dir(Path.home() / "autotrade_institutional_history", ["n_inst"])   # 13D/G activity
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()

    L = {}  # layered feature panels
    for c in ["atm_iv", "skew", "gex_proxy", "pc_oi"]:
        if c in od: L[f"opt_{c}"] = pan(od, c)
    L["opt_iv_chg"] = L["opt_atm_iv"].pct_change()
    if not nd.empty:
        L["news_n"] = pan(nd, "n_news"); L["news_sent"] = pan(nd, "sentiment")
        L["news_n_chg"] = L["news_n"].diff()
    if not rd.empty:                                  # rating momentum (upgrades-downgrades)
        L["rate_net"] = pan(rd, "net").rolling(4).sum()
        if "avg_target" in rd: L["rate_tgt_chg"] = pan(rd, "avg_target").pct_change()
    if not ind.empty:
        L["insider_netbuy"] = pan(ind, "net_buy").rolling(4).sum()
    if not instd.empty:
        L["inst_activity"] = pan(instd, "n_inst").rolling(8).sum()
    L["tech_mom20"] = px/px.shift(20) - 1
    L["tech_dist50"] = px/px.rolling(50).mean() - 1
    L["tech_vol20"] = px.pct_change().rolling(20).std()

    dates = sorted(set(L["opt_atm_iv"].index))
    fwd = {h: (px.shift(-h)/px - 1).reindex(dates) for h in (5, 20)}
    split = dates[int(len(dates)*0.6)]
    IS = [d for d in dates if d < split]; OOS = [d for d in dates if d >= split]
    layer = lambda k: k.split("_")[0]

    # singles + CROSS-layer interactions (skip same-layer pairs to focus on cross-layer combos)
    cands = {k: v.reindex(dates) for k, v in L.items()}
    keys = list(L.keys())
    for i in range(len(keys)):
        for j in range(i+1, len(keys)):
            a, b = keys[i], keys[j]
            if layer(a) != layer(b):                      # cross-layer only
                cands[f"{a} × {b}"] = zx(L[a].reindex(dates)) * zx(L[b].reindex(dates))

    n, res = 0, []
    for name, fdf in cands.items():
        for h in (5, 20):
            n += 1
            ris, ros = fmb(fdf, fwd[h], IS), fmb(fdf, fwd[h], OOS)
            if not ris or not ros: continue
            res.append({"f": name, "h": h, "ic_is": ris[0], "t_is": ris[1], "ic_oos": ros[0], "t_oos": ros[1]})
    res.sort(key=lambda r: abs(r["t_oos"]), reverse=True)
    print(f"options {od['symbol'].nunique()} names · news {0 if nd.empty else nd['symbol'].nunique()} names · "
          f"{len(dates)} weeks · IS/OOS {len(IS)}/{len(OOS)} · tested {n}\n")
    print(f"  {'feature':30} {'h':>3} {'IC_is':>7} {'t_is':>6} {'IC_oos':>7} {'t_oos':>6}  flag")
    surv = []
    for r in res[:20]:
        both = np.sign(r["t_is"]) == np.sign(r["t_oos"]) and abs(r["t_is"]) > 1.8 and abs(r["t_oos"]) > 1.8
        flag = "BOTH-HALVES" if both else ("OOS-only⚠" if abs(r["t_oos"]) > 2.5 else "")
        if both: surv.append(r)
        print(f"  {r['f']:30} {r['h']:>3} {r['ic_is']:+.3f} {r['t_is']:+6.2f} {r['ic_oos']:+.3f} {r['t_oos']:+6.2f}  {flag}")

    led = json.loads(LEDGER.read_text())
    led["discovered_xlayer"] = {"updated": str(max(dates).date()), "n_tested": n,
        "sample": f"~{od['symbol'].nunique()} names x {len(dates)} weeks (low power)",
        "survivors": [{"feature": s["f"], "horizon": s["h"], "ic_oos": round(s["ic_oos"], 4),
                       "t_oos": round(s["t_oos"], 2), "ic_is": round(s["ic_is"], 4)} for s in surv]}
    LEDGER.write_text(json.dumps(led, indent=2))
    print(f"\n  {len(surv)} both-halves survivors (t>1.8 bar, small sample -> HINTS). cross-layer combos incl options×news.")


if __name__ == "__main__":
    main()
