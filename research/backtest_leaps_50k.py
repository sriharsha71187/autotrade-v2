#!/usr/bin/env python3
"""$50k account. (1) Fresh snapshot today: how many LEAPS vs stock on the current 10 momentum
picks. (2) Backtest from Jan 2025 WITH the stock->LEAPS UPGRADE rule (a stock-fallback name is
upgraded to a LEAPS once the 6% budget can afford a contract). Black-Scholes modeled.
Run: python research/backtest_leaps_50k.py
"""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe

R = 0.045; ITM = 0.80; PREM_PCT = 0.06; OPT_COST = 0.02; LEAPS_T = 252; START = 50000.0


def N(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs_call(S, K, T, sig):
    if T <= 0 or sig <= 0: return max(S-K, 0.0)
    d1 = (math.log(S/K)+(R+sig*sig/2)*T)/(sig*math.sqrt(T)); d2 = d1-sig*math.sqrt(T)
    return S*N(d1)-K*math.exp(-R*T)*N(d2)


def main():
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    idx = px.index; stocks = [c for c in px.columns if c not in ("SPY","QQQ")]
    mom = px[stocks].shift(21)/px[stocks].shift(147)-1
    rv = px[stocks].pct_change().rolling(30).std()*math.sqrt(252)
    bull = (px["SPY"] > px["SPY"].rolling(200).mean())
    mstart = [d for d in idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])] if d in mom.index]

    # ---------- (1) fresh $50k snapshot on the latest picks ----------
    dn = mstart[-1]; iN = idx.get_loc(dn)
    top = list(mom.loc[dn].dropna().sort_values(ascending=False).index[:10])
    budget = PREM_PCT*START
    print(f"\n=== SNAPSHOT: fresh $50k as of {dn.date()} · 6% budget = ${budget:,.0f}/name ===\n")
    print(f"  {'stock':7}{'price':>8}{'1 LEAPS contract':>18}  decision")
    nL = 0
    for t in top:
        S = px[t].iloc[iN]; iv = rv[t].iloc[iN]
        if not (S > 0 and iv == iv and iv > 0): continue
        cpr = bs_call(S, ITM*S, 1.0, iv)*100
        if cpr <= budget:
            n = math.floor(budget/cpr); nL += 1
            dec = f"BUY {n} LEAPS (~${n*cpr:,.0f})"
        else:
            dec = f"STOCK ({budget/S:.0f} sh) — 1 contract ${cpr:,.0f} > budget"
        print(f"  {t:7}{S:>8.0f}{cpr:>16,.0f}   {dec}")
    print(f"\n  -> {nL} of {len(top)} names affordable as LEAPS today; the rest = stock fallback.")

    # ---------- (2) backtest from Jan 2025 WITH upgrade ----------
    def val(p, t, i):
        S = px[t].iloc[i]
        return p["sh"]*S if p["type"] == "stock" else p["sh"]*bs_call(S, p["strike"], max((p["exp"]-i)/252, 0), p["iv"])
    cash = START; pos = {}; curve = []
    for d0 in [d for d in mstart if d.strftime("%Y-%m") >= "2025-01"]:
        i0 = idx.get_loc(d0)
        for t, p in list(pos.items()):
            if p["type"] == "leaps" and (p["exp"]-i0)/252 < 0.75:
                v = val(p, t, i0); cash += v*(1-OPT_COST); S = px[t].iloc[i0]; iv = rv[t].iloc[i0] or 0.5
                pr = bs_call(S, 0.8*S, 1.0, iv)*100; p.update(sh=v/(pr/100) if pr > 0 else 0, strike=0.8*S, exp=i0+LEAPS_T, iv=iv)
        equity = cash + sum(val(p, t, i0) for t, p in pos.items())
        on = bool(bull.loc[d0]) if d0 in bull.index else True
        t10 = list(mom.loc[d0].dropna().sort_values(ascending=False).index[:10]) if on else []
        for t in list(pos):
            if t not in t10: cash += val(pos[t], t, i0)*(1-OPT_COST); del pos[t]
        ups = 0
        for t in t10:
            S = px[t].iloc[i0]; iv = rv[t].iloc[i0]
            if not (S > 0 and iv == iv and iv > 0): continue
            bud = min(PREM_PCT*equity, cash*0.95); cpr = bs_call(S, ITM*S, 1.0, iv)*100
            if t in pos and pos[t]["type"] == "stock" and cpr <= bud:          # UPGRADE stock -> LEAPS
                cash += val(pos[t], t, i0)*(1-OPT_COST); n = math.floor(bud/cpr)
                pos[t] = {"type": "leaps", "sh": n*100, "strike": ITM*S, "exp": i0+LEAPS_T, "iv": iv}; cash -= n*cpr*(1+OPT_COST); ups += 1
            elif t not in pos:                                                 # new add
                if bud <= 0: continue
                if cpr <= bud:
                    n = math.floor(bud/cpr); pos[t] = {"type": "leaps", "sh": n*100, "strike": ITM*S, "exp": i0+LEAPS_T, "iv": iv}; cash -= n*cpr*(1+OPT_COST)
                else:
                    pos[t] = {"type": "stock", "sh": bud/S}; cash -= bud
        eq = cash + sum(val(p, t, i0) for t, p in pos.items())
        nl = sum(p["type"] == "leaps" for p in pos.values()); ns = sum(p["type"] == "stock" for p in pos.values())
        curve.append((d0, eq, nl, ns, ups))

    print(f"\n=== $50k from 2025-01 · with stock->LEAPS upgrade ===\n  {'month':9}{'equity$':>10}{'#LEAPS':>8}{'#stock':>8}{'upgrades':>10}")
    for d0, e, nl, ns, up in curve:
        print(f"  {d0.strftime('%Y-%m'):9}{e:>10,.0f}{nl:>8}{ns:>8}{up:>10}")
    print(f"\n  {START:,.0f} -> {curve[-1][1]:,.0f} ({(curve[-1][1]/START-1)*100:+.0f}%) · avg {np.mean([c[2] for c in curve]):.1f} LEAPS / {np.mean([c[3] for c in curve]):.1f} stock")
    print("  (survivorship caveat on magnitude; point = how many LEAPS the budget supports + the upgrade path.)")


if __name__ == "__main__":
    main()
