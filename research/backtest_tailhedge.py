#!/usr/bin/env python3
"""Does a monetized tail hedge improve the 1.5x momentum book? Each month spend ~1% of equity on
~10% OTM 2-month SPY puts (IV = realized x skew, marked daily incl. the vol spike in crashes ->
convex). Monetize when a put is up >=4x (sell, redeploy proceeds INTO the book at the lows). Compare
book-alone vs book+hedge: CAGR / Sharpe / maxDD. Run: python research/backtest_tailhedge.py
"""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe
R = 0.03; SKEW = 1.4; OTM = 0.90; TENOR = 42; MONETIZE = 4.0; COST = 0.0005; BORROW = 0.065


def N(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs_put(S, K, T, sig):
    if T <= 0 or sig <= 0: return max(K-S, 0.0)
    d1 = (math.log(S/K)+(R+sig*sig/2)*T)/(sig*math.sqrt(T)); d2 = d1-sig*math.sqrt(T)
    return K*math.exp(-R*T)*N(-d2) - S*N(-d1)


def curve_stats(eq):
    r = eq.pct_change().dropna(); yrs = len(r)/252
    return eq.iloc[-1]**(1/yrs)-1 if eq.iloc[-1] > 0 else -1, (r.mean()*252)/(r.std()*math.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()


def book_returns(lev):
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    idx = px.index; stocks = [c for c in px.columns if c not in ("SPY","QQQ")]
    rs = px[stocks].pct_change()
    mom = px[stocks].shift(21)/px[stocks].shift(147) - 1
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    bull = (px["SPY"] > px["SPY"].rolling(200).mean()).shift(1).fillna(False)
    w = pd.DataFrame(0.0, index=idx, columns=stocks); cur = []
    for dt in idx:
        if dt in mstart and dt in mom.index:
            m = mom.loc[dt].dropna(); cur = list(m.nlargest(10).index) if len(m) >= 10 else []
        if cur and bool(bull.loc[dt]):
            for t in cur: w.loc[dt, t] = 1/len(cur)
    base = ((w.shift(1)*rs).sum(1) - COST*w.diff().abs().sum(1)).fillna(0)
    return (lev*base - (lev-1)*(BORROW/252)).fillna(0), px["SPY"], idx, mstart


def main():
    lev = float(sys.argv[1]) if len(sys.argv) > 1 else 1.5
    rbook, spy, idx, mstart = book_returns(lev)
    rv = spy.pct_change().rolling(21).std()*math.sqrt(252)
    mset = set(mstart)

    def simulate(hedged, hpct=0.01):
        book = 1.0; puts = []; eq = []
        for k, dt in enumerate(idx):
            book *= (1 + rbook.loc[dt])                          # book compounds
            S = float(spy.loc[dt]); sig = float(rv.loc[dt]) if rv.loc[dt] == rv.loc[dt] else 0.2
            iv = max(sig, 0.08)*SKEW
            for p in puts: p["val"] = p["units"]*bs_put(S, p["K"], max((p["exp"]-k)/252, 0), iv)
            if hedged:
                for p in list(puts):                            # monetize spikes -> redeploy into book
                    if p["val"] >= MONETIZE*p["cost"]:
                        book += p["val"]; puts.remove(p)
                for p in list(puts):                            # expiry settle
                    if k >= p["exp"]:
                        book += p["val"]; puts.remove(p)
                if dt in mset:                                  # monthly roll
                    equity = book + sum(p["val"] for p in puts); budget = hpct*equity
                    K = OTM*S; prem = bs_put(S, K, TENOR/252, iv)
                    if prem > 0:
                        units = budget/prem; puts.append({"K": K, "exp": k+TENOR, "units": units, "cost": budget, "val": budget})
                        book -= budget
            eq.append(book + sum(p["val"] for p in puts))
        return pd.Series(eq, index=idx)

    base = simulate(False); hedged = simulate(True, 0.01); hedged2 = simulate(True, 0.005)
    print(f"\n=== Tail hedge on the {lev}x momentum book · 2017-2026 ===\n")
    print(f"  {'portfolio':30}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}")
    for lbl, e in [("book alone (no hedge)", base), ("book + 1.0%/mo tail hedge", hedged),
                   ("book + 0.5%/mo tail hedge", hedged2)]:
        c, sh, dd = curve_stats(e); print(f"  {lbl:30}{c*100:7.1f}%{sh:7.2f}{dd*100:7.0f}%")
    print("\n  Hedge = ~10% OTM 2mo SPY puts, IV=RV x1.4 (skew), marked w/ live vol (convex), monetized at 4x.")
    print("  Survivorship inflates the book level; the HEDGE EFFECT (dCAGR, dSharpe, dDD) is the real read.")
    print("  Caveat: SPY puts vs a tech/semis book = basis risk (QQQ puts hedge tighter but cost more).")


if __name__ == "__main__":
    main()
