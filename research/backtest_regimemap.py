#!/usr/bin/env python3
"""WHEN does WHAT work? Conditional map of every strategy family tested, by market state:
  states = TREND (SPY>200DMA?) x VOL STRUCTURE (VIX3M>VIX contango?)  -> 4 states
  strategies = SPY b&h, overnight, RSI-2 dip-buy, 0DTE condor (10%/day), gap-continuation,
               momentum sleeve.
Prints ann-return/Sharpe per (strategy, state). Then the honest test: TRAIN the best-per-state
rule on the first half of each stream's history, run the switched combo OUT-OF-SAMPLE on the
second half vs static benchmarks. Run: python research/backtest_regimemap.py
"""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe


def N(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs(S, K, T, sig, put=False):
    if T <= 0 or sig <= 0: return max((K-S) if put else (S-K), 0.0)
    d1 = (math.log(S/K)+(sig*sig/2)*T)/(sig*math.sqrt(T)); d2 = d1-sig*math.sqrt(T)
    return (K*N(-d2)-S*N(-d1)) if put else (S*N(d1)-K*N(d2))


def main():
    import yfinance as yf
    d = yf.download(["SPY","^VIX","^VIX3M"], start="2007-01-01", auto_adjust=True, progress=False)
    cl, op = d["Close"], d["Open"]
    spy = cl["SPY"].dropna(); spyo = op["SPY"].reindex(spy.index)
    vix = cl["^VIX"].reindex(spy.index).ffill(); v3m = cl["^VIX3M"].reindex(spy.index).ffill()
    idx = spy.index; r_spy = spy.pct_change()
    ma200 = spy.rolling(200).mean()

    trend = (spy > ma200).shift(1).fillna(False)
    contango = (v3m > vix).shift(1).fillna(True)
    state = pd.Series(np.where(trend & contango, "UP+CONTANGO",
                      np.where(trend & ~contango, "UP+BACKWARD",
                      np.where(~trend & contango, "DOWN+CONTANGO", "DOWN+BACKWARD"))), index=idx)

    streams = {"SPY buy&hold": r_spy.fillna(0)}
    streams["overnight (net)"] = ((spyo.shift(-1)/spy - 1).shift(1) - 0.0002).fillna(0)
    # RSI-2
    delta = spy.diff(); up = delta.clip(lower=0); dn = -delta.clip(upper=0)
    rsi2 = 100 - 100/(1 + up.ewm(alpha=0.5, adjust=False).mean()/(dn.ewm(alpha=0.5, adjust=False).mean()+1e-12))
    pos = pd.Series(0.0, index=idx); hold = False
    for i in range(200, len(idx)):
        if not hold and rsi2.iloc[i] < 10: hold = True                 # NO trend gate: the map decides
        elif hold and rsi2.iloc[i] > 65: hold = False
        pos.iloc[i] = 1.0 if hold else 0.0
    streams["RSI-2 dip-buy"] = (pos.shift(1)*r_spy - 0.0001*pos.diff().abs()).fillna(0)
    # 0DTE condor sized 10%/day (intraday-vol priced)
    oc = np.log(cl["SPY"]/op["SPY"]).reindex(idx)
    ivn = (oc.rolling(21).std()*math.sqrt(252)*1.10).shift(1)
    cond = pd.Series(0.0, index=idx); T = 1/252
    for i in range(200, len(idx)):
        S = float(spyo.iloc[i]); C = float(spy.iloc[i]); iv = float(ivn.iloc[i])
        if not (S > 0 and iv == iv and iv > 0): continue
        sd = iv*math.sqrt(T)
        Kc, Kp = S*math.exp(0.75*sd), S*math.exp(-0.75*sd)
        Wc, Wp = S*math.exp(2.0*sd), S*math.exp(-2.0*sd)
        credit = bs(S,Kc,T,iv)+bs(S,Kp,T,iv,True)-bs(S,Wc,T,iv)-bs(S,Wp,T,iv,True)
        width = min(Wc-Kc, Kp-Wp); margin = width - credit
        loss = min(max(C-Kc,0)+max(Kp-C,0), width)
        cond.iloc[i] = 0.10*(credit*0.9 - loss)/margin
    streams["0DTE condor (10%/day)"] = cond
    # panel strategies (2017+)
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    pidx = px.index; stocks = [c for c in px.columns if c not in ("SPY","QQQ")]
    rp = px[stocks].pct_change()
    ev = (rp > 0.08).shift(1).fillna(False)
    wg = pd.DataFrame(0.0, index=pidx, columns=stocks)
    for k in range(21): wg += ev.shift(k).fillna(False).astype(float)
    wg = wg.clip(0,1); n = wg.sum(1).replace(0,np.nan); wg = wg.div(n,axis=0).fillna(0)
    streams["gap continuation"] = ((wg.shift(1)*rp).sum(1) - 0.001*wg.diff().abs().sum(1)).fillna(0).reindex(idx).fillna(0)
    mom = px[stocks].shift(21)/px[stocks].shift(147)-1
    ms = list(pidx[np.append([True], pidx.to_period("M")[1:] != pidx.to_period("M")[:-1])])
    wm = pd.DataFrame(0.0, index=pidx, columns=stocks); cur = []
    for dt in pidx:
        if dt in ms and dt in mom.index:
            m = mom.loc[dt].dropna(); cur = list(m.nlargest(10).index) if len(m) >= 10 else []
        if cur:
            for t in cur: wm.loc[dt, t] = 0.1                          # UNGATED: the map decides
    streams["momentum sleeve (ungated)"] = ((wm.shift(1)*rp).sum(1) - 0.0005*wm.diff().abs().sum(1)).fillna(0).reindex(idx).fillna(0)

    STATES = ["UP+CONTANGO","UP+BACKWARD","DOWN+CONTANGO","DOWN+BACKWARD"]
    print(f"\n=== WHEN WHAT WORKS · ann.return / Sharpe by state · {idx[0].date()}..{idx[-1].date()} ===\n")
    cnt = state.value_counts()
    print("  state days:  " + "  ".join(f"{s}:{cnt.get(s,0)}" for s in STATES) + "\n")
    hdr = "  " + f"{'strategy':26}" + "".join(f"{s:>16}" for s in STATES)
    print(hdr)
    for name, r in streams.items():
        live = r[r != 0].index.min()
        row = f"  {name:26}"
        for s in STATES:
            sel = r[(state == s) & (r.index >= live)]
            if len(sel) > 60:
                ann = sel.mean()*252; sh = sel.mean()/(sel.std()+1e-12)*math.sqrt(252)
                row += f"{ann*100:+8.0f}%/{sh:5.1f}"
            else:
                row += f"{'--':>16}"
        print(row)

    # OUT-OF-SAMPLE switch test: train best-per-state on 1st half, run 2nd half
    print("\n=== OUT-OF-SAMPLE test: pick best-per-state on TRAIN (1st half), run TEST (2nd half) ===\n")
    live0 = max(r[r != 0].index.min() for r in streams.values())        # common window (panel-limited)
    common = idx[idx >= live0]
    mid = common[len(common)//2]
    choice = {}
    for s in STATES:
        best, bsh = "cash", 0.30                                        # must beat cash+hurdle
        for name, r in streams.items():
            sel = r[(state == s) & (r.index >= live0) & (r.index < mid)]
            if len(sel) > 60:
                sh = sel.mean()/(sel.std()+1e-12)*math.sqrt(252)
                if sh > bsh: best, bsh = name, sh
        choice[s] = best
        print(f"  {s:15} -> {best}")
    switched = pd.Series(0.0, index=common)
    for s in STATES:
        if choice[s] != "cash":
            sel = (state == s) & (state.index.isin(common))
            switched[state[sel].index] = streams[choice[s]][state[sel].index]
    test = switched[switched.index >= mid]
    def st(r):
        r = r.dropna(); eq = (1+r).cumprod(); yrs = len(r)/252
        return (eq.iloc[-1]**(1/yrs)-1)*100, r.mean()/(r.std()+1e-12)*math.sqrt(252), (eq/eq.cummax()-1).min()*100
    print(f"\n  {'TEST period ('+str(mid.date())+'..)':34}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}")
    c, sh, dd = st(test);                                print(f"  {'state-SWITCHED combo':34}{c:7.1f}%{sh:8.2f}{dd:7.0f}%")
    mg = streams["momentum sleeve (ungated)"]; gated = mg.where(trend, 0.0)
    c, sh, dd = st(gated[gated.index >= mid]);           print(f"  {'static momentum + 200DMA gate':34}{c:7.1f}%{sh:8.2f}{dd:7.0f}%")
    c, sh, dd = st(r_spy[r_spy.index >= mid].fillna(0)); print(f"  {'SPY buy & hold':34}{c:7.1f}%{sh:8.2f}{dd:7.0f}%")


if __name__ == "__main__":
    main()
