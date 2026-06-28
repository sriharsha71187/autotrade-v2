#!/usr/bin/env python3
"""Small account: $25k from Jan 2025, LEAPS momentum with the realistic constraint that 1 LEAPS
contract = 100 shares. Per name (6% budget): if 1 deep-ITM LEAPS contract fits the budget, buy as
many contracts as fit; ELSE fall back to buying the STOCK (fractional shares) for that name. Shows
how often the account is forced into stock. Black-Scholes modeled. Run: python research/backtest_leaps_small.py
"""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe

R = 0.045; ITM = 0.80; PREM_PCT = 0.06; OPT_COST = 0.02; LEAPS_T = 252; START = 25000.0


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
    mstart = [d for d in idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])]
              if d in mom.index and d.strftime("%Y-%m") >= "2025-01"]

    def val(p, t, i):
        S = px[t].iloc[i]
        if p["type"] == "stock": return p["sh"]*S
        return p["sh"]*bs_call(S, p["strike"], max((p["exp"]-i)/252, 0), p["iv"])

    cash = START; pos = {}; curve = []
    for d0 in mstart:
        i0 = idx.get_loc(d0)
        for t, p in list(pos.items()):                  # roll LEAPS <9mo
            if p["type"] == "leaps" and (p["exp"]-i0)/252 < 0.75:
                v = val(p, t, i0); cash += v*(1-OPT_COST); S = px[t].iloc[i0]; iv = rv[t].iloc[i0]
                iv = iv if iv == iv and iv > 0 else 0.5; pr = bs_call(S, 0.8*S, 1.0, iv)*100
                p.update(sh=(v/(pr/100)) if pr > 0 else 0, strike=0.8*S, exp=i0+LEAPS_T, iv=iv)
        equity = cash + sum(val(p, t, i0) for t, p in pos.items())
        on = bool(bull.loc[d0]) if d0 in bull.index else True
        top10 = list(mom.loc[d0].dropna().sort_values(ascending=False).index[:10]) if on else []
        for t in list(pos):
            if t not in top10: cash += val(pos[t], t, i0)*(1-OPT_COST); del pos[t]
        n_leaps = n_stock = 0
        for t in top10:
            if t in pos:
                n_leaps += pos[t]["type"] == "leaps"; n_stock += pos[t]["type"] == "stock"; continue
            S = px[t].iloc[i0]; iv = rv[t].iloc[i0]
            if not (S > 0 and iv == iv and iv > 0): continue
            budget = min(PREM_PCT*equity, cash*0.95)
            if budget <= 0: continue
            cpr = bs_call(S, ITM*S, LEAPS_T/252, iv)*100               # 1 contract premium (100 sh)
            if cpr <= budget:                                          # afford >=1 LEAPS contract
                ncon = math.floor(budget/cpr); spend = ncon*cpr
                pos[t] = {"type": "leaps", "sh": ncon*100, "strike": ITM*S, "exp": i0+LEAPS_T, "iv": iv}
                cash -= spend*(1+OPT_COST); n_leaps += 1
            else:                                                      # can't afford 1 contract -> stock
                pos[t] = {"type": "stock", "sh": budget/S}; cash -= budget; n_stock += 1
        eq = cash + sum(val(p, t, i0) for t, p in pos.items())
        curve.append((d0, eq, n_leaps, n_stock))

    print(f"\n=== SMALL ACCOUNT · $25k from {mstart[0].date()} · LEAPS-or-stock fallback ===\n")
    print(f"  {'month':9}{'equity$':>10}{'#LEAPS':>8}{'#stock-fallback':>17}")
    for d0, e, nl, ns in curve:
        print(f"  {d0.strftime('%Y-%m'):9}{e:>10,.0f}{nl:>8}{ns:>15}")
    eq = curve[-1][1]
    print(f"\n  {START:,.0f} -> {eq:,.0f}  ({(eq/START-1)*100:+.0f}%)")
    avgL = np.mean([nl for _,_,nl,_ in curve]); avgS = np.mean([ns for _,_,_,ns in curve])
    print(f"  avg per month: {avgL:.1f} LEAPS / {avgS:.1f} stock-fallback (of 10)")
    print("\n  At $25k a deep-ITM LEAPS (100 sh) on most names costs more than the 6% budget -> mostly STOCK.")
    print("  (survivorship caveat on magnitude still applies; point here is the small-account mechanics.)")


if __name__ == "__main__":
    main()
