#!/usr/bin/env python3
"""Variant sweep for the (rejected) 14/21d double calendar — can ANY variation survive
real per-leg costs? Attacks the two killers found in backtest_double_calendar.py:
  cost drag  -> longer-dated structures (bigger debit vs same absolute per-leg cost)
  stop bleed -> no-stop (hold to short expiry, the video's 'Option A'), smaller size
Also: high-IV vs low-IV entry, and a light cost grid (real $0.03/leg vs frictionless).
SPY only (QQQ was negative in every spec). 3-point term structure ^VIX9D/^VIX/^VIX3M,
total-variance interpolation. Run: python3 research/backtest_calendar_variants.py
"""
import math
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")

R = 0.04; START = 25000.0; COMM = 0.65
CURVE_DAYS = [9, 30, 93]


def N(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs(S, K, T, sig, cp):
    if T <= 0 or sig <= 0:
        return max(S-K, 0.0) if cp == "c" else max(K-S, 0.0)
    d1 = (math.log(S/K)+(R+sig*sig/2)*T)/(sig*math.sqrt(T)); d2 = d1-sig*math.sqrt(T)
    if cp == "c": return S*N(d1)-K*math.exp(-R*T)*N(d2)
    return K*math.exp(-R*T)*N(-d2)-S*N(-d1)


def iv_at(ivs, days):
    """piecewise-linear total-variance interpolation on the [9,30,93]d curve."""
    days = max(days, 1)
    tv = [iv*iv*d for iv, d in zip(ivs, CURVE_DAYS)]
    if days <= CURVE_DAYS[0]:
        v = tv[0]*days/CURVE_DAYS[0]
    elif days >= CURVE_DAYS[-1]:
        v = tv[-1]*days/CURVE_DAYS[-1]
    else:
        j = 1 if days <= CURVE_DAYS[1] else 2
        w = (days-CURVE_DAYS[j-1])/(CURVE_DAYS[j]-CURVE_DAYS[j-1])
        v = tv[j-1]+(tv[j]-tv[j-1])*w
    return math.sqrt(max(v, 1e-6)/days)


def cal_val(S, Kc, Kp, ds, dl, ivs):
    v = 0.0
    if dl > 0:
        s = iv_at(ivs, dl); v += bs(S, Kc, dl/365, s, "c")+bs(S, Kp, dl/365, s, "p")
    if ds > 0:
        s = iv_at(ivs, ds); v -= bs(S, Kc, ds/365, s, "c")+bs(S, Kp, ds/365, s, "p")
    else:
        v -= max(S-Kc, 0)+max(Kp-S, 0)
    return v


def run(df, ts, tl, m=0.5, pt=0.30, sl=-0.30, use_stop=True, risk=0.10, hs=0.03, ivmode="low"):
    cash = START; posn = None; eq = []; trades = []
    for dt_, row in df.iterrows():
        S, ivs = row["S"], (row["iv9"], row["iv30"], row["iv93"])
        if posn is not None:
            ds = (posn["exp_s"]-dt_).days; dl = (posn["exp_l"]-dt_).days
            val = cal_val(S, posn["Kc"], posn["Kp"], ds, dl, ivs)
            f = (val-posn["debit"])/posn["debit"]
            hit = (f >= pt) or (use_stop and f <= sl) or ds <= 0
            if hit:
                nlegs = 2 if ds <= 0 else 4
                proceeds = (val - nlegs*(hs+COMM/100))*100*posn["n"]
                cash += proceeds
                why = "expiry" if ds <= 0 else ("target" if f >= pt else "stop")
                trades.append(dict(dt=dt_, pnl=proceeds-posn["cost"], why=why,
                                   ret=(proceeds-posn["cost"])/posn["cost"]))
                posn = None
        if posn is None:
            lowiv = row["iv30"] < row["ivmed"]
            ok = (not row["spike"]) and (lowiv if ivmode == "low" else
                                         (not lowiv) if ivmode == "high" else True)
            if ok:
                w = m*S*iv_at(ivs, ts)*math.sqrt(ts/365)
                Kc, Kp = S+w, S-w
                debit = cal_val(S, Kc, Kp, ts, tl, ivs)
                if debit > 0.01:
                    per = 4*(hs+COMM/100)
                    n = risk*cash/((debit+per)*100)
                    cost = (debit+per)*100*n; cash -= cost
                    posn = dict(Kc=Kc, Kp=Kp, exp_s=dt_+pd.Timedelta(days=ts),
                                exp_l=dt_+pd.Timedelta(days=tl), debit=debit, n=n, cost=cost)
        mark = cash if posn is None else cash+cal_val(S, posn["Kc"], posn["Kp"],
                (posn["exp_s"]-dt_).days, (posn["exp_l"]-dt_).days, ivs)*100*posn["n"]
        eq.append((dt_, mark)); cash *= (1+R/252)
    return pd.Series([e for _, e in eq], index=[t for t, _ in eq]), pd.DataFrame(trades)


def stats(eq):
    r = eq.pct_change().dropna(); yrs = (eq.index[-1]-eq.index[0]).days/365.25
    return ((eq.iloc[-1]/eq.iloc[0])**(1/yrs)-1, r.mean()/(r.std()+1e-12)*math.sqrt(252),
            (eq/eq.cummax()-1).min(), eq.iloc[-1])


def main():
    import yfinance as yf
    raw = yf.download(["SPY", "^VIX", "^VIX9D", "^VIX3M"], start="2011-01-01",
                      auto_adjust=True, progress=False)["Close"].ffill()
    raw.index = pd.to_datetime(raw.index).tz_localize(None)
    df = pd.DataFrame({"S": raw["SPY"], "iv9": raw["^VIX9D"]/100,
                       "iv30": raw["^VIX"]/100, "iv93": raw["^VIX3M"]/100})
    df["ivmed"] = df["iv30"].rolling(252, min_periods=60).median()
    df["spike"] = df["iv30"] > 1.15*df["iv30"].rolling(10).mean()
    df = df.dropna()
    print(f"\n=== Calendar VARIANTS · SPY · {df.index.min().date()}..{df.index.max().date()} · "
          f"real costs $0.03/leg half-spread + $0.65 comm ===\n")
    print(f"  {'variant':52}{'CAGR':>7}{'Sharpe':>8}{'maxDD':>7}{'final$':>11}{'trades':>7}{'win%':>6}{'avg ret':>8}")
    specs = [
        ("14/21 baseline (prior run, real costs)",        dict(ts=14, tl=21)),
        ("14/21 NO stop (hold to short expiry)",          dict(ts=14, tl=21, use_stop=False)),
        ("30/60 structure",                               dict(ts=30, tl=60)),
        ("30/60 NO stop",                                 dict(ts=30, tl=60, use_stop=False)),
        ("30/60 NO stop, 5% risk",                        dict(ts=30, tl=60, use_stop=False, risk=0.05)),
        ("30/60 NO stop, HIGH-IV entry",                  dict(ts=30, tl=60, use_stop=False, ivmode="high")),
        ("30/60 NO stop, any IV (no filter)",             dict(ts=30, tl=60, use_stop=False, ivmode="any")),
        ("30/45 NO stop",                                 dict(ts=30, tl=45, use_stop=False)),
        ("7/14 weekly (faster theta)",                    dict(ts=7,  tl=14)),
        ("30/60 NO stop, FRICTIONLESS (upper bound)",     dict(ts=30, tl=60, use_stop=False, hs=0.0)),
        ("14/21 FRICTIONLESS (upper bound)",              dict(ts=14, tl=21, hs=0.0)),
    ]
    for lbl, kw in specs:
        eq, tr = run(df, **kw)
        c, s, d, fin = stats(eq)
        win = (tr["pnl"] > 0).mean() if len(tr) else 0
        ar = tr["ret"].mean()*100 if len(tr) else 0
        print(f"  {lbl:52}{c*100:6.1f}%{s:8.2f}{d*100:6.0f}%{fin:11,.0f}{len(tr):7}{win*100:5.0f}%{ar:7.1f}%")
    # best-variant anatomy + subperiods
    eq, tr = run(df, ts=30, tl=60, use_stop=False)
    print("\n  30/60 no-stop anatomy:")
    for why, g in tr.groupby("why"):
        print(f"    {why:8} {len(g):4} · avg ret {g['ret'].mean()*100:6.1f}% · worst {g['ret'].min()*100:6.1f}%")
    tr["yr"] = pd.to_datetime(tr["dt"]).dt.year
    print("  P&L by yr: " + " ".join(f"{y}:{v:+,.0f}" for y, v in tr.groupby("yr")["pnl"].sum().items()))


if __name__ == "__main__":
    main()
