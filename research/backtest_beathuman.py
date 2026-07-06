#!/usr/bin/env python3
"""BEAT THE HUMAN: my own constructions on top of the deployed 7+3 sleeve. Variants:
  A. baseline 7+3 equal-weight (the human's)
  B. conviction weights: w ∝ momentum z-score of each pick (winner-tilted, not equal)
  C. acceleration fast-slots: fast-3 ranked by (3mo pace − 6mo pace) — inflection, not level
  D. 15% position trailing stop: a holding 15% off its month-high goes to cash till next rebal
  E. dispersion gate: exposure 1.0 when cross-sectional momentum spread (q90−median) is above
     its trailing 24m median, else 0.6 — the momentum-crash regime detector
  F. best combo of the above
Monthly, SPY>200DMA gate, 1.0x, 5bp. Split-half shown for anything that beats A.
Run: python research/backtest_beathuman.py
"""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe
COST = 0.0005


def stats(r):
    r = r.dropna(); eq = (1+r).cumprod(); yrs = len(r)/252
    return (eq.iloc[-1]**(1/yrs)-1)*100, (r.mean()*252)/(r.std()*math.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()*100


def main():
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    idx = px.index; stocks = [c for c in px.columns if c not in ("SPY","QQQ")]
    rs = px[stocks].pct_change()
    m6 = px[stocks].shift(21)/px[stocks].shift(147) - 1
    m3 = px[stocks].shift(21)/px[stocks].shift(84) - 1
    mstart = set(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    bull = (px["SPY"] > px["SPY"].rolling(200).mean()).shift(1).fillna(False)
    # dispersion series (monthly): q90 - median of 6mo momentum cross-section
    disp = m6.quantile(0.9, axis=1) - m6.median(axis=1)
    disp_gate = (disp > disp.rolling(504).median()).fillna(True)

    def run(conviction=False, accel=False, tstop=None, dispgate=False):
        w = pd.DataFrame(0.0, index=idx, columns=stocks)
        cur = {}; peaks = {}
        for k, dt in enumerate(idx):
            if dt in mstart and dt in m6.index:
                s6 = m6.loc[dt].dropna(); s3 = m3.loc[dt].dropna()
                if len(s6) >= 10 and len(s3) >= 10:
                    core = list(s6.nlargest(7).index)
                    if accel:
                        acc = (s3 - 0.5*s6).dropna()
                        fast = [t for t in acc.sort_values(ascending=False).index if t not in core][:3]
                    else:
                        fast = [t for t in s3.sort_values(ascending=False).index if t not in core][:3]
                    picks = core + fast
                    if conviction:
                        z = s6.reindex(picks).fillna(s3.reindex(picks))
                        z = (z - z.min() + 0.1); ww = (z/z.sum()).clip(upper=0.20)  # cap 20%/name
                        ww = ww/ww.sum(); cur = dict(ww)
                    else:
                        cur = {t: 0.1 for t in picks}
                    peaks = {t: float(px[t].iloc[k]) for t in cur}
            live = dict(cur)
            if tstop:
                for t in list(live):
                    p = float(px[t].iloc[k]) if px[t].iloc[k] == px[t].iloc[k] else None
                    if p is None: continue
                    peaks[t] = max(peaks.get(t, p), p)
                    if p < peaks[t]*(1-tstop): del cur[t]; live.pop(t, None)
            expo = 1.0
            if dispgate and dt in disp_gate.index and not bool(disp_gate.loc[dt]): expo = 0.6
            if live and bool(bull.loc[dt]):
                for t, ww in live.items(): w.loc[dt, t] = ww*expo
        return ((w.shift(1)*rs).sum(1) - COST*w.diff().abs().sum(1)).fillna(0)

    grid = [("A. baseline 7+3 equal-wt (human)", dict()),
            ("B. conviction weights", dict(conviction=True)),
            ("C. acceleration fast-slots", dict(accel=True)),
            ("D. 15% position trailing stop", dict(tstop=0.15)),
            ("E. dispersion-gated exposure", dict(dispgate=True)),
            ("F. B+C+E combo", dict(conviction=True, accel=True, dispgate=True))]
    print(f"\n=== BEAT THE HUMAN · 7+3 base · gated · 1.0x · {idx.min().date()}..{idx.max().date()} ===\n")
    print(f"  {'construction':38}{'CAGR':>7}{'Sharpe':>8}{'maxDD':>7}")
    res = {}
    for lbl, kw in grid:
        r = run(**kw); res[lbl] = r
        c, sh, dd = stats(r); print(f"  {lbl:38}{c:6.1f}%{sh:8.2f}{dd:6.0f}%")
    base_sh = stats(res[grid[0][0]])[1]; mid = idx[len(idx)//2]
    win = [l for l in res if l != grid[0][0] and stats(res[l])[1] > base_sh]
    if win:
        print(f"\n  split-half on beats:")
        for l in win:
            for tag in ("1st", "2nd"):
                sl = res[l][res[l].index < mid] if tag == "1st" else res[l][res[l].index >= mid]
                bb = res[grid[0][0]][res[grid[0][0]].index < mid] if tag == "1st" else res[grid[0][0]][res[grid[0][0]].index >= mid]
                c, sh, _ = stats(sl); cb, sb, _ = stats(bb)
                print(f"    {l[:36]:38} {tag} half: {c:6.1f}%/{sh:5.2f}  (baseline {cb:6.1f}%/{sb:5.2f})")


if __name__ == "__main__":
    main()
