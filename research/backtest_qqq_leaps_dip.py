#!/usr/bin/env python3
"""Video strategy #2: QQQ LEAPS dip-buyer ("set and forget").
Rules as codified from the video:
  - Every day QQQ drops >=1% (gap down or intraday touch vs prior close): buy one 12-month
    ~70-delta call (sweep 60/70/80).
  - GTC 50% profit target on the option premium, checked daily (close marks = conservative).
  - NO stop loss. Hold to expiry -> settle intrinsic.
  - "Step aside in prolonged bear markets": variant using QQQ<200DMA -> no new entries,
    and a stricter variant that also liquidates everything below the 200DMA.
Pricing: Black-Scholes, IV = blend of ^VXN and its 2-yr mean (long-dated vol mean-reverts).
Costs: 1% of premium per side + $0.65/contract. Cash earns R.
Portfolio sim: spend PCT_PER_TRADE of current equity per signal (fractional contracts OK).
Benchmarks: QQQ buy-hold, QLD buy-hold on the same dates.
Run: python research/backtest_qqq_leaps_dip.py
"""
import math
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")

R = 0.04; START = 25000.0; PCT_PER_TRADE = 0.05; OPT_COST = 0.01; COMM = 0.65
LEAPS_T = 1.0  # years


def N(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs_call(S, K, T, sig):
    if T <= 0 or sig <= 0: return max(S-K, 0.0)
    d1 = (math.log(S/K)+(R+sig*sig/2)*T)/(sig*math.sqrt(T)); d2 = d1-sig*math.sqrt(T)
    return S*N(d1)-K*math.exp(-R*T)*N(d2)
def call_delta(S, K, T, sig):
    if T <= 0 or sig <= 0: return 1.0 if S > K else 0.0
    return N((math.log(S/K)+(R+sig*sig/2)*T)/(sig*math.sqrt(T)))
def strike_for_delta(S, T, sig, tgt):
    lo, hi = S*0.3, S*1.5
    for _ in range(60):
        mid = (lo+hi)/2
        if call_delta(S, mid, T, sig) > tgt: lo = mid
        else: hi = mid
    return (lo+hi)/2


def stats(eq):
    eq = eq.dropna(); r = eq.pct_change().dropna()
    yrs = (eq.index[-1]-eq.index[0]).days/365.25
    cagr = (eq.iloc[-1]/eq.iloc[0])**(1/yrs)-1
    sh = r.mean()/(r.std()+1e-12)*math.sqrt(252)
    return cagr, sh, (eq/eq.cummax()-1).min(), eq.iloc[-1]


def run(px, iv, delta_tgt, bear_mode, start=None, end=None):
    """bear_mode: 'none' | 'noentry' (no new buys <200DMA) | 'flat' (liquidate <200DMA)."""
    d = px.loc[start:end].copy()
    dma = px["QQQ"].rolling(200).mean().loc[d.index]
    cash = START; pos = []  # dicts: strike, expiry(ts), iv_ent, contracts, cost
    eq_out = []; trades = []
    for i, (dt_, row) in enumerate(d.iterrows()):
        S, Sl, So, Sp = row["QQQ"], row["low"], row["open"], row["prev"]
        sig = iv.loc[dt_]
        if not (S > 0 and sig == sig): continue
        bull = S > dma.loc[dt_] if dma.loc[dt_] == dma.loc[dt_] else True
        # mark & manage existing positions on today's close
        still = []
        for p in pos:
            T = (p["expiry"]-dt_).days/365.25
            val = bs_call(S, p["strike"], T, sig)
            if val >= 1.5*p["entry_px"]:                       # 50% GTC target
                proceeds = 1.5*p["entry_px"]*100*p["n"]*(1-OPT_COST) - COMM*p["n"]
                cash += proceeds; trades.append((dt_, proceeds - p["cost"], "target", (dt_-p["dt"]).days))
            elif T <= 0:                                       # expiry -> intrinsic
                proceeds = max(S-p["strike"], 0)*100*p["n"]*(1-OPT_COST)
                cash += proceeds; trades.append((dt_, proceeds - p["cost"], "expiry", (dt_-p["dt"]).days))
            elif bear_mode == "flat" and not bull:
                proceeds = val*100*p["n"]*(1-OPT_COST) - COMM*p["n"]
                cash += proceeds; trades.append((dt_, proceeds - p["cost"], "bear-exit", (dt_-p["dt"]).days))
            else: still.append(p)
        pos = still
        # entry trigger: gap down >=1% or intraday -1% touch vs prior close
        drop = min(So/Sp-1, Sl/Sp-1)
        if drop <= -0.01 and (bear_mode == "none" or bull):
            S_ent = min(So, 0.99*Sp)                           # first-touch fill
            K = strike_for_delta(S_ent, LEAPS_T, sig, delta_tgt)
            prem = bs_call(S_ent, K, LEAPS_T, sig)
            mark = cash + sum(bs_call(S, p["strike"], (p["expiry"]-dt_).days/365.25, sig)*100*p["n"] for p in pos)
            budget = PCT_PER_TRADE*mark
            n = budget/(prem*100)
            if n > 0 and cash >= n*prem*100*(1+OPT_COST):
                cost = n*prem*100*(1+OPT_COST) + COMM*n
                cash -= cost
                pos.append(dict(dt=dt_, strike=K, expiry=dt_+pd.Timedelta(days=365),
                                iv_ent=sig, n=n, entry_px=prem, cost=cost))
        eq_out.append((dt_, cash + sum(bs_call(S, p["strike"], max((p["expiry"]-dt_).days/365.25, 0), sig)*100*p["n"] for p in pos)))
        cash *= (1+R/252)
    eq = pd.Series([e for _, e in eq_out], index=[t for t, _ in eq_out])
    return eq, trades


def main():
    import yfinance as yf
    raw = yf.download(["QQQ", "QLD", "^VXN"], start="2001-01-01", auto_adjust=True, progress=False)
    cl = raw["Close"].ffill(); op = raw["Open"]; lo = raw["Low"]
    px = pd.DataFrame({"QQQ": cl["QQQ"], "QLD": cl.get("QLD"),
                       "open": op["QQQ"], "low": lo["QQQ"]})
    px["prev"] = px["QQQ"].shift(1)
    px.index = pd.to_datetime(px.index).tz_localize(None)
    vxn = (cl["^VXN"]/100).reindex(px.index).ffill()
    iv = 0.6*vxn + 0.4*vxn.rolling(504, min_periods=100).mean()   # long-dated IV: mean-reverting blend
    px = px.dropna(subset=["QQQ", "prev"]); iv = iv.reindex(px.index).ffill()

    ndip = ((px[["open", "low"]].min(axis=1)/px["prev"]-1) <= -0.01).sum()
    print(f"\n=== QQQ LEAPS dip-buyer · {px.index.min().date()}..{px.index.max().date()} · "
          f"{ndip} trigger days · ${START:,.0f} start, {PCT_PER_TRADE:.0%}/trade ===\n")

    print(f"  {'variant':46}{'CAGR':>7}{'Sharpe':>8}{'maxDD':>8}{'final$':>12}{'trades':>7}{'win%':>6}{'avgP&L':>9}")
    rows = []
    for lbl, dlt, bm in [("70Δ, no bear filter (video baseline)", 0.70, "none"),
                         ("70Δ, no NEW entries <200DMA", 0.70, "noentry"),
                         ("70Δ, liquidate <200DMA", 0.70, "flat"),
                         ("60Δ, no NEW entries <200DMA", 0.60, "noentry"),
                         ("80Δ, no NEW entries <200DMA", 0.80, "noentry")]:
        eq, tr = run(px, iv, dlt, bm)
        c, s, dmax, fin = stats(eq)
        pnl = [p for _, p, _, _ in tr]; win = np.mean([p > 0 for p in pnl]) if pnl else 0
        print(f"  {lbl:46}{c*100:6.1f}%{s:8.2f}{dmax*100:7.0f}%{fin:12,.0f}{len(tr):7}{win*100:5.0f}%{np.mean(pnl):9,.0f}")
        rows.append((lbl, eq, tr))

    # benchmarks
    for b in ["QQQ", "QLD"]:
        ser = px[b].dropna()
        if len(ser) < 100: continue
        eq = ser/ser.iloc[0]*START
        c, s, dmax, fin = stats(eq)
        print(f"  {'buy&hold '+b:46}{c*100:6.1f}%{s:8.2f}{dmax*100:7.0f}%{fin:12,.0f}")

    # sub-periods for the baseline + filtered variant
    print("\n  sub-periods (CAGR / maxDD):")
    print(f"  {'period':14}{'70Δ no-filter':>18}{'70Δ no-entry<200DMA':>22}{'QQQ B&H':>16}")
    for a, b in [("2001-01-01", "2007-12-31"), ("2008-01-01", "2009-12-31"), ("2010-01-01", "2019-12-31"),
                 ("2020-01-01", "2021-12-31"), ("2022-01-01", "2022-12-31"), ("2023-01-01", None)]:
        cells = []
        for _, dlt, bm in [(0, 0.70, "none"), (0, 0.70, "noentry")]:
            eq, _ = run(px, iv, dlt, bm, a, b)
            c, s, dmax, _ = stats(eq); cells.append(f"{c*100:6.1f}%/{dmax*100:4.0f}%")
        ser = px["QQQ"].loc[a:b]; eqb = ser/ser.iloc[0]*START
        c, s, dmax, _ = stats(eqb)
        print(f"  {a[:4]+'-'+(b[:4] if b else 'now'):14}{cells[0]:>18}{cells[1]:>22}{c*100:8.1f}%/{dmax*100:4.0f}%")

    # trade-duration / expiry-loss anatomy on the filtered variant
    eq, tr = run(px, iv, 0.70, "noentry")
    dur = [d_ for _, _, why, d_ in tr]; exp_losses = [p for _, p, why, _ in tr if why == "expiry" and p < 0]
    tgt = sum(1 for _, _, w, _ in tr if w == "target")
    print(f"\n  anatomy (70Δ, no-entry filter): {len(tr)} closed trades · {tgt} hit +50% target "
          f"({tgt/len(tr)*100:.0f}%) · median days held {np.median(dur):.0f}")
    print(f"  expiry losers: {len(exp_losses)} (avg {np.mean(exp_losses) if exp_losses else 0:,.0f}) — the no-stop tail")


if __name__ == "__main__":
    main()
