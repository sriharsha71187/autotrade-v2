#!/usr/bin/env python3
"""MOMENTUM LEAPS BARBELL — the good-faith aggressive options strategy. Each month: top-10
6mo-momentum picks; spend LEAPS_FRAC of equity on 12-month deep-ITM (strike=0.75*S, ~0.8 delta)
calls, equal-weight; rest in cash at 4.3%. Regime gate: SPY<200DMA -> all cash. Names rotate with
the picks; positions roll when <2mo to expiry. Options valued daily (Black-Scholes, entry IV
frozen = rv30*1.10), 2% cost per option trade. Compare: stock 1.0x, margin 1.5x, barbell at
35/50/70% LEAPS. Run: python research/backtest_leaps_barbell.py
"""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe
R = 0.043; ITM = 0.75; TEN = 252; OPT_COST = 0.02; STK_COST = 0.0005; BORROW = 0.065


def N(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs_call(S, K, T, sig):
    if T <= 0 or sig <= 0 or S <= 0: return max(S-K, 0.0)
    d1 = (math.log(S/K)+(R+sig*sig/2)*T)/(sig*math.sqrt(T)); d2 = d1-sig*math.sqrt(T)
    return S*N(d1)-K*math.exp(-R*T)*N(d2)


def stats(eq):
    r = eq.pct_change().dropna(); yrs = len(r)/252
    cagr = eq.iloc[-1]**(1/yrs)-1 if eq.iloc[-1] > 0 else -1
    return cagr, (r.mean()*252)/(r.std()*math.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()


def main():
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    idx = px.index; stocks = [c for c in px.columns if c not in ("SPY","QQQ")]
    rs = px[stocks].pct_change()
    mom = px[stocks].shift(21)/px[stocks].shift(147)-1
    rv = rs.rolling(30).std()*math.sqrt(252)
    mstart = [d for d in idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])] if d in mom.index]
    bull = (px["SPY"] > px["SPY"].rolling(200).mean())
    mset = set(mstart)

    def run_barbell(frac):
        cash = 1.0; pos = {}; eq = []
        for k, dt in enumerate(idx):
            cash *= (1 + R/252)
            for t in list(pos):
                p = pos[t]; S = float(px[t].iloc[k]) if px[t].iloc[k] == px[t].iloc[k] else 0.0
                p["val"] = p["n"]*bs_call(S, p["K"], max((p["exp"]-k)/252, 0), p["iv"]) if S > 0 else 0.0
            if dt in mset:
                equity = cash + sum(p["val"] for p in pos.values())
                on = bool(bull.loc[dt])
                top = list(mom.loc[dt].dropna().sort_values(ascending=False).index[:10]) if on else []
                for t in list(pos):                                   # exit dropped names
                    if t not in top: cash += pos.pop(t)["val"]*(1-OPT_COST)
                for t in list(pos):                                   # roll if < 2mo left
                    p = pos[t]
                    if (p["exp"]-k)/252 < 2/12:
                        cash += p["val"]*(1-OPT_COST); del pos[t]
                budget_each = frac*equity/10
                for t in top:
                    S = float(px[t].iloc[k]); iv = float(rv[t].iloc[k]) if rv[t].iloc[k] == rv[t].iloc[k] else 0
                    if not (S > 0 and iv > 0): continue
                    cpr = bs_call(S, ITM*S, TEN/252, iv*1.10)
                    if t in pos:                                      # re-target to budget
                        p = pos[t]; diff = budget_each - p["val"]
                        if abs(diff) > 0.001*equity and p["val"] > 0:
                            scale = budget_each/p["val"]
                            cash -= diff*(1+OPT_COST if diff > 0 else 1-OPT_COST); p["n"] *= scale
                    elif cpr > 0 and cash > budget_each:
                        n = budget_each/cpr
                        pos[t] = {"n": n, "K": ITM*S, "exp": k+TEN, "iv": iv*1.10, "val": budget_each}
                        cash -= budget_each*(1+OPT_COST)
            eq.append(cash + sum(p["val"] for p in pos.values()))
        return pd.Series(eq, index=idx)

    # stock benchmarks
    w = pd.DataFrame(0.0, index=idx, columns=stocks); cur = []
    for dt in idx:
        if dt in mset:
            m = mom.loc[dt].dropna(); cur = list(m.nlargest(10).index) if len(m) >= 10 else []
        if cur and bool(bull.loc[dt]):
            for t in cur: w.loc[dt, t] = 0.1
    base = ((w.shift(1)*rs).sum(1) - STK_COST*w.diff().abs().sum(1)).fillna(0)
    eq10 = (1+base).cumprod()
    eq15 = (1+(1.5*base - 0.5*BORROW/252)).cumprod()

    print(f"\n=== MOMENTUM LEAPS BARBELL vs stock book · {idx.min().date()}..{idx.max().date()} ===\n")
    print(f"  {'strategy':40}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}{'2022':>8}{'floor':>9}")
    def yr22(eq):
        s = eq[eq.index.year == 2022]
        return (s.iloc[-1]/s.iloc[0]-1)*100 if len(s) > 20 else float("nan")
    rows = [("stocks 1.0x (deployed today)", eq10, "none"),
            ("stocks 1.5x margin", eq15, "none")]
    for f in (0.35, 0.50, 0.70):
        rows.append((f"LEAPS barbell {int(f*100)}% (~{f*1/0.35:.1f}x delta)", run_barbell(f), f"-{int(f*100)}%"))
    for lbl, eq, floor in rows:
        c, sh, dd = stats(eq)
        print(f"  {lbl:40}{c*100:7.1f}%{sh:7.2f}{dd*100:7.0f}%{yr22(eq):7.0f}%{floor:>9}")
    print("\n  floor = worst-case loss if every option expired worthless (cash never at risk).")
    print("  2022 = the macro-bear year, the stress test that matters. Survivorship caveat applies to ALL rows.")


if __name__ == "__main__":
    main()
