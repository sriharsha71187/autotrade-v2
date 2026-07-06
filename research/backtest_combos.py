#!/usr/bin/env python3
"""COMBINATION sweep: does any technical x volume x fundamental-event overlay improve momentum
selection? Base pool each month = top-25 by 6mo momentum; each overlay picks the 10 "best" from
the pool by a secondary score (baseline = plain top-10). Overlays, all from real OHLCV history:
  V1 volume-trend      : 1mo dollar-volume vs 6mo (rising participation)
  V2 accumulation      : up-day volume / down-day volume, 3mo (buyers vs sellers)
  V3 illiquidity       : Amihud |ret|/$vol (edge-in-less-efficient-names tilt)
  F1 PEAD overlay      : last earnings-type event (|1d move|>5% on >2.5x volume, past 70d)
                         was POSITIVE -> prefer; negative -> avoid
  X  triple interaction: momentum rank, but only names passing accumulation>1 AND PEAD>=0
Equal-weight, monthly, regime-gated, 1.0x, costs 5bp. OHLCV cached on first run (~minutes).
Run: python research/backtest_combos.py
"""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import universe
COST = 0.0005
CACHE = Path(__file__).with_name(".ohlcv_cache.pkl")


def load_panel():
    import yfinance as yf
    if CACHE.exists():
        d = pd.read_pickle(CACHE); print(f"  loaded OHLCV cache {d['C'].shape}")
        return d
    tks = universe(); out = {"C": [], "O": [], "V": []}
    for i in range(0, len(tks), 100):
        chunk = tks[i:i+100]
        df = yf.download(chunk, start="2016-06-01", auto_adjust=True, progress=False)
        for k, col in [("C","Close"),("O","Open"),("V","Volume")]:
            out[k].append(df[col])
        print(f"  fetched {i+len(chunk)}/{len(tks)}")
    d = {k: pd.concat(v, axis=1) for k, v in out.items()}
    for k in d:
        d[k].index = pd.to_datetime(d[k].index).tz_localize(None).normalize()
        d[k] = d[k].loc[:, ~d[k].columns.duplicated()]
    pd.to_pickle(d, CACHE); print(f"  built + cached OHLCV {d['C'].shape}")
    return d


def stats(r):
    r = r.dropna(); eq = (1+r).cumprod(); yrs = len(r)/252
    return (eq.iloc[-1]**(1/yrs)-1)*100, (r.mean()*252)/(r.std()*math.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()*100


def main():
    d = load_panel()
    C, O, V = d["C"], d["O"], d["V"]
    keep = C.columns[C.notna().sum() > 252]
    C, O, V = C[keep], O[keep], V[keep]
    stocks = [c for c in C.columns if c not in ("SPY","QQQ")]
    idx = C.index
    r = C[stocks].pct_change()
    mom = C[stocks].shift(21)/C[stocks].shift(147) - 1
    dvol = (C[stocks]*V[stocks])
    # V1 volume trend: 21d avg $vol / 126d avg $vol
    vtrend = dvol.rolling(21).mean()/dvol.rolling(126).mean()
    # V2 accumulation: up-day vol / down-day vol over 63d
    upv = V[stocks].where(r > 0, 0).rolling(63).sum()
    dnv = V[stocks].where(r < 0, 0).rolling(63).sum()
    accum = upv/(dnv+1)
    # V3 Amihud illiquidity (higher = less efficient)
    amihud = (r.abs()/(dvol+1)).rolling(63).mean()
    # F1 PEAD: last event day = |1d ret|>5% with vol>2.5x 63d avg, within past 70d; sign of that day
    volx = V[stocks]/(V[stocks].rolling(63).mean()+1)
    event = (r.abs() > 0.05) & (volx > 2.5)
    evsign = pd.DataFrame(np.where(event, np.sign(r), np.nan), index=idx, columns=stocks)
    pead = evsign.ffill(limit=70)                       # sign of most recent event, valid 70d
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    spy = C["SPY"]; bull = (spy > spy.rolling(200).mean()).shift(1).fillna(False)

    def run(select):
        w = pd.DataFrame(0.0, index=idx, columns=stocks); cur = []
        for dt in idx:
            if dt in mstart and dt in mom.index:
                m = mom.loc[dt].dropna()
                if len(m) >= 25:
                    pool = list(m.nlargest(25).index)
                    cur = select(dt, pool, m) or cur
            if cur and bool(bull.loc[dt]):
                for t in cur: w.loc[dt, t] = 1/len(cur)
        return ((w.shift(1)*r).sum(1) - COST*w.diff().abs().sum(1)).fillna(0)

    def top10(dt, pool, m): return pool[:10]
    def by(sig, reverse=False):
        def f(dt, pool, m):
            s = sig.loc[dt, pool].dropna()
            if len(s) < 10: return pool[:10]
            return list((s if reverse else -s).sort_values().index[:10])
        return f
    def pead_sel(dt, pool, m):
        s = pead.loc[dt, pool]
        pos = [t for t in pool if s.get(t, 0) == 1 or pd.isna(s.get(t))]   # positive or no recent event
        return (pos + [t for t in pool if t not in pos])[:10]
    def triple(dt, pool, m):
        a = accum.loc[dt, pool]; p = pead.loc[dt, pool]
        ok = [t for t in pool if (a.get(t, 1) or 1) > 1 and (p.get(t, 0) != -1 or pd.isna(p.get(t)))]
        return (ok + [t for t in pool if t not in ok])[:10]

    print(f"\n=== COMBINATIONS · pool=top-25 momentum, overlay picks 10 · gated · 1.0x · {idx.min().date()}..{idx.max().date()} ===\n")
    print(f"  {'selection overlay':44}{'CAGR':>7}{'Sharpe':>8}{'maxDD':>7}")
    grid = [("BASELINE: plain top-10 momentum", top10),
            ("V1 highest volume-trend", by(vtrend)),
            ("V2 highest accumulation (up/down vol)", by(accum)),
            ("V3 most illiquid (Amihud)", by(amihud)),
            ("F1 PEAD: positive last earnings-event", pead_sel),
            ("X  triple: mom + accum>1 + PEAD not-neg", triple)]
    results = {}
    for lbl, sel in grid:
        rr = run(sel); results[lbl] = rr
        c, sh, dd = stats(rr); print(f"  {lbl:44}{c:6.1f}%{sh:8.2f}{dd:6.0f}%")
    # split-halves for anything that beat baseline
    base_sh = stats(results[grid[0][0]])[1]
    mid = idx[len(idx)//2]
    winners = [l for l in results if l != grid[0][0] and stats(results[l])[1] > base_sh]
    if winners:
        print(f"\n  split-half check on overlays beating baseline Sharpe:")
        for l in winners:
            for tag, sl in [("1st half", results[l][results[l].index < mid]), ("2nd half", results[l][results[l].index >= mid])]:
                b = results[grid[0][0]]; b = b[b.index < mid] if tag == "1st half" else b[b.index >= mid]
                c, sh, _ = stats(sl); cb, sb, _ = stats(b)
                print(f"    {l[:40]:42} {tag}: {c:6.1f}%/{sh:5.2f}  (baseline {cb:6.1f}%/{sb:5.2f})")


if __name__ == "__main__":
    main()
