#!/usr/bin/env python3
"""SETUP discovery — not plain correlation. Tests STRUCTURED, conditional trigger->outcome
setups the way a trader frames them ("when X, the forward return is Y"). For each trigger
(a boolean condition, incl. cross-layer AND-combos), measure the CONDITIONAL forward 20d
EXCESS return vs that day's cross-section: mean edge, WIN-RATE, t across dates, # instances,
IS/OOS. Surfaces setups with edge that hold OOS. This is the form real strategies take
(e.g. 'oversold + cheap-IV -> buy'), and it composes with exits in a portfolio backtest.

Run: python research/discover_setups.py
"""
import sys, json
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe

LEDGER = Path.home() / "autotrade_strategies.json"


def rsi(px, n=14):
    d = px.diff(); up = d.clip(lower=0).rolling(n).mean(); dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100/(1 + up/dn.replace(0, np.nan))


def main():
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    r = px.pct_change()
    # building-block features
    f = {
        "rsi": rsi(px),
        "off_20hi": px/px.rolling(20).max() - 1,
        "off_52hi": px/px.rolling(252).max() - 1,
        "mom_20": px/px.shift(20) - 1,
        "mom_60": px/px.shift(60) - 1,
        "dist_200": px/px.rolling(200).mean() - 1,
        "vol_20": r.rolling(20).std(),
        "ret_5": px/px.shift(5) - 1,
    }
    fwd = px.shift(-20)/px - 1                         # 20-day forward
    xs_mean = fwd.mean(1)                              # that day's cross-sectional mean (de-market)

    # SETUP LIBRARY — conditional triggers (incl. multi-condition combos). My ideas + classics.
    S = {
        "oversold (rsi<30)":               f["rsi"] < 30,
        "deep dip (>10% off 20d high)":    f["off_20hi"] < -0.10,
        "deep dip + uptrend (>200dma)":   (f["off_20hi"] < -0.10) & (f["dist_200"] > 0),
        "oversold + uptrend":             (f["rsi"] < 35) & (f["dist_200"] > 0),
        "capitulation (rsi<25 & high vol)":(f["rsi"] < 25) & (f["vol_20"] > f["vol_20"].rolling(60).mean()),
        "breakout (at 52w high)":          f["off_52hi"] > -0.01,
        "pullback-in-strong-trend":       (f["mom_60"] > 0.10) & (f["off_20hi"] < -0.05) & (f["off_20hi"] > -0.15),
        "sharp 5d drop in uptrend":       (f["ret_5"] < -0.08) & (f["dist_200"] > 0),
        "momentum + low vol (defensive)": (f["mom_60"] > 0.05) & (f["vol_20"] < f["vol_20"].rolling(252).median()),
        "extended (rsi>75)":               f["rsi"] > 75,
    }
    days = px.index[252::3]
    split = days[int(len(days)*0.65)]
    IS = set(d for d in days if d < split); OOS = set(d for d in days if d >= split)

    def edge(trig, dayset):
        recs = []
        for d in days:
            if d not in dayset or d not in trig.index or d not in fwd.index: continue
            names = trig.columns[trig.loc[d].fillna(False)]
            v = fwd.loc[d, names].dropna()
            if len(v) >= 5:
                recs.append((v.mean() - xs_mean.loc[d], (v > xs_mean.loc[d]).mean(), len(v)))
        if len(recs) < 8: return None
        ex = np.array([x[0] for x in recs])
        t = ex.mean()/(ex.std(ddof=1)/np.sqrt(len(ex))) if ex.std(ddof=1) else 0
        wr = np.average([x[1] for x in recs], weights=[x[2] for x in recs])
        return ex.mean(), t, wr, int(sum(x[2] for x in recs)), len(recs)

    print(f"setup discovery · {px.shape[1]} names · {px.index.min().date()}..{px.index.max().date()} · "
          f"20d fwd excess vs cross-section\n")
    print(f"  {'setup':34} {'exc_is':>7} {'t_is':>6} {'exc_oos':>7} {'t_oos':>6} {'win%':>5} {'n_obs':>7}  flag")
    out = []
    for name, trig in S.items():
        ri, ro = edge(trig, IS), edge(trig, OOS)
        if not ri or not ro: continue
        both = np.sign(ri[1]) == np.sign(ro[1]) and abs(ri[1]) > 2 and abs(ro[1]) > 2
        flag = "BOTH-HALVES" if both else ("OOS-only⚠" if abs(ro[1]) > 2.5 else "")
        out.append({"setup": name, "exc_oos": ro[0], "t_oos": ro[1], "win": ro[2], "n": ro[3], "both": both})
        print(f"  {name:34} {ri[0]*100:+6.2f}% {ri[1]:+6.2f} {ro[0]*100:+6.2f}% {ro[1]:+6.2f} "
              f"{ro[2]*100:4.0f}% {ro[3]:>7}  {flag}")

    led = json.loads(LEDGER.read_text())
    led["discovered_setups"] = {"updated": str(px.index.max().date()), "horizon": "20d",
        "note": "conditional trigger->forward-excess-return (de-market). win% = beat-the-cross-section rate. NOTE survivorship-biased universe.",
        "survivors": [{"setup": o["setup"], "exc_oos_pct": round(o["exc_oos"]*100, 2), "t_oos": round(o["t_oos"], 2),
                       "win_pct": round(o["win"]*100, 1), "n": o["n"]} for o in out if o["both"]]}
    LEDGER.write_text(json.dumps(led, indent=2))
    print(f"\n  {sum(o['both'] for o in out)} both-halves setups. (survivorship caveat stands; cross-layer setups "
          f"with options/news conditions come as that coverage widens.)")


if __name__ == "__main__":
    main()
