#!/usr/bin/env python3
"""Smarter CONSTRUCTION of the momentum sleeve (signal stays raw 6mo momentum — that fight is
settled). Tests: lookback ensemble (3/6/12 rank-avg), rank hysteresis (sell only when out of
top-K buffer), inverse-vol weighting, and combos. Baseline: top-10 / 6mo / equal-weight /
monthly / regime-gated / 1.0x. Reports CAGR / Sharpe / maxDD / annual turnover.
Run: python research/backtest_construction.py
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
    return eq.iloc[-1]**(1/yrs)-1, (r.mean()*252)/(r.std()*math.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()


def main():
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    idx = px.index; stocks = [c for c in px.columns if c not in ("SPY","QQQ")]
    rs = px[stocks].pct_change()
    m3 = px[stocks].shift(21)/px[stocks].shift(84) - 1
    m6 = px[stocks].shift(21)/px[stocks].shift(147) - 1
    m12 = px[stocks].shift(21)/px[stocks].shift(273) - 1
    vol = rs.rolling(63).std()
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    bull = (px["SPY"] > px["SPY"].rolling(200).mean()).shift(1).fillna(False)

    def sig_at(dt, ensemble):
        if not ensemble: return m6.loc[dt].dropna()
        parts = []
        for m in (m3, m6, m12):
            v = m.loc[dt].dropna(); parts.append(v.rank(pct=True))
        return pd.concat(parts, axis=1).mean(1).dropna()

    def run(ensemble=False, buffer_k=None, invvol=False):
        w = pd.DataFrame(0.0, index=idx, columns=stocks); held = []
        for dt in idx:
            if dt in mstart and dt in m6.index:
                s = sig_at(dt, ensemble)
                if len(s) >= 25:
                    ranked = list(s.sort_values(ascending=False).index)
                    if buffer_k is None:
                        held = ranked[:10]
                    else:
                        topK = set(ranked[:buffer_k])
                        held = [t for t in held if t in topK]            # keep survivors in buffer
                        for t in ranked:                                  # refill to 10 by rank
                            if len(held) == 10: break
                            if t not in held: held.append(t)
            if held and bool(bull.loc[dt]):
                if invvol:
                    iv = 1.0/vol.loc[dt, held].replace(0, np.nan)
                    iv = iv.fillna(iv.mean() if iv.notna().any() else 1.0)
                    ww = iv/iv.sum()
                    for t in held: w.loc[dt, t] = float(ww[t])
                else:
                    for t in held: w.loc[dt, t] = 0.1
        turn = w.diff().abs().sum(1)
        ret = ((w.shift(1)*rs).sum(1) - COST*turn).fillna(0)
        return ret, float(turn.sum()/ (len(idx)/252))

    print(f"\n=== Sleeve construction · signal=momentum · regime-gated · 1.0x · {idx.min().date()}..{idx.max().date()} ===\n")
    print(f"  {'construction':44}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}{'turn/yr':>9}")
    grid = [
        ("BASELINE: top-10 / 6mo / equal-wt",          dict()),
        ("lookback ensemble (3/6/12 rank-avg)",        dict(ensemble=True)),
        ("rank buffer (buy top-10, sell out of 20)",   dict(buffer_k=20)),
        ("rank buffer (sell out of 25)",               dict(buffer_k=25)),
        ("inverse-vol weights",                        dict(invvol=True)),
        ("ensemble + buffer20",                        dict(ensemble=True, buffer_k=20)),
        ("ensemble + invvol",                          dict(ensemble=True, invvol=True)),
        ("buffer20 + invvol",                          dict(buffer_k=20, invvol=True)),
        ("ensemble + buffer20 + invvol",               dict(ensemble=True, buffer_k=20, invvol=True)),
    ]
    for lbl, kw in grid:
        r, tn = run(**kw); c, sh, dd = stats(r)
        print(f"  {lbl:44}{c*100:7.1f}%{sh:7.2f}{dd*100:7.0f}%{tn:8.1f}x")
    print("\n  turn/yr = one-way turnover multiple of the book per year (baseline monthly re-pick ~ churny).")
    print("  Survivorship inflates all rows equally; read RELATIVE differences.")


if __name__ == "__main__":
    main()
