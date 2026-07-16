#!/usr/bin/env python3
"""Video strategy #1: double calendar spread (delta-neutral weekly income).
Codified rules:
  - 4 legs: sell 14d call+put, buy 21d call+put at SAME strikes, strikes +/- w from spot.
  - w swept as a multiple of the 14d expected move (m*IV*sqrt(14/365)*S) plus a
    literal "SPX 10 points" equivalent (~0.2% of spot).
  - Enter when flat, only in LOW-IV, NON-SPIKING vol: IV < 1yr median AND VIX < 1.15x its 10DMA.
  - Exit: +30% of debit (mid of the 20-40% band), -30% of debit (the video's mental stop ->
    close & re-enter), or at short-leg expiry.
Pricing: Black-Scholes with a REAL term structure — IV(T) interpolated in total variance
between ^VIX9D (9d) and ^VIX (30d); QQQ uses ^VXN shaped by the same 9d/30d ratio.
So contango bleed and spike inversion both hit the position like they do live.
Costs: ABSOLUTE per-leg half-spread ($0.02/share base, swept to $0.06) + $0.65/leg commission,
8 leg-sides per round trip — costs scale with the LEGS, not the (small) net debit.
Sizing: debit = max risk = 10% of equity per trade; cash earns R.
Run: python research/backtest_double_calendar.py
"""
import math
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")

R = 0.04; START = 25000.0; RISK_PCT = 0.10; HALF_SPREAD = 0.02; COMM = 0.65  # $/share per leg
TS, TL = 14, 21                      # calendar days, short/long legs
PT, SL = 0.30, -0.30                 # profit target / stop as fraction of debit


def N(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs(S, K, T, sig, cp):
    if T <= 0 or sig <= 0:
        return max(S-K, 0.0) if cp == "c" else max(K-S, 0.0)
    d1 = (math.log(S/K)+(R+sig*sig/2)*T)/(sig*math.sqrt(T)); d2 = d1-sig*math.sqrt(T)
    if cp == "c": return S*N(d1)-K*math.exp(-R*T)*N(d2)
    return K*math.exp(-R*T)*N(-d2)-S*N(-d1)


def iv_at(iv9, iv30, days):
    """total-variance interpolation/extrapolation from the 9d and 30d points."""
    days = max(days, 1)
    v9, v30 = iv9*iv9*9, iv30*iv30*30
    w = (days-9)/21.0
    v = v9 + (v30-v9)*w
    return math.sqrt(max(v, 1e-6)/days)


def calendar_value(S, Kc, Kp, ds, dl, iv9, iv30):
    """long-legs minus short-legs value; ds/dl = calendar days left on short/long."""
    v = 0.0
    if dl > 0:
        sl_ = iv_at(iv9, iv30, dl)
        v += bs(S, Kc, dl/365, sl_, "c") + bs(S, Kp, dl/365, sl_, "p")
    if ds > 0:
        ss = iv_at(iv9, iv30, ds)
        v -= bs(S, Kc, ds/365, ss, "c") + bs(S, Kp, ds/365, ss, "p")
    elif ds <= 0:
        v -= max(S-Kc, 0) + max(Kp-S, 0)
    return v


def run(df, width_mult, fixed_w=None, iv_filter=True, cost_mult=1.0):
    cash = START; posn = None; eq = []; trades = []
    hs = HALF_SPREAD*cost_mult; comm = COMM*cost_mult
    for dt_, row in df.iterrows():
        S, iv9, iv30, ivmed, spike = row["S"], row["iv9"], row["iv30"], row["ivmed"], row["spike"]
        if posn is not None:
            ds = (posn["exp_s"]-dt_).days; dl = (posn["exp_l"]-dt_).days
            val = calendar_value(S, posn["Kc"], posn["Kp"], ds, dl, iv9, iv30)
            pnl_frac = (val-posn["debit"])/posn["debit"]
            if pnl_frac >= PT or pnl_frac <= SL or ds <= 0:
                nlegs = 2 if ds <= 0 else 4          # shorts expire -> only long legs to close
                exit_cost = nlegs*(hs + comm/100)
                proceeds = (val - exit_cost)*100*posn["n"]
                cash += proceeds
                why = "target" if pnl_frac >= PT else ("stop" if pnl_frac <= SL else "expiry")
                trades.append(dict(dt=dt_, pnl=proceeds-posn["cost"], why=why,
                                   ret=(proceeds-posn["cost"])/posn["cost"], vix=iv30))
                posn = None
        if posn is None:
            ok = (not iv_filter) or (iv30 < ivmed and not spike)
            if ok:
                w = fixed_w*S if fixed_w else width_mult*S*iv_at(iv9, iv30, TS)*math.sqrt(TS/365)
                Kc, Kp = S+w, S-w
                debit = calendar_value(S, Kc, Kp, TS, TL, iv9, iv30)
                if debit > 0.01:
                    entry_cost_per = 4*(hs + comm/100)
                    n = (RISK_PCT*(cash))/((debit+entry_cost_per)*100)
                    cost = (debit+entry_cost_per)*100*n
                    cash -= cost
                    posn = dict(Kc=Kc, Kp=Kp, exp_s=dt_+pd.Timedelta(days=TS),
                                exp_l=dt_+pd.Timedelta(days=TL), debit=debit, n=n, cost=cost)
        mark = cash if posn is None else cash + calendar_value(
            S, posn["Kc"], posn["Kp"], (posn["exp_s"]-dt_).days, (posn["exp_l"]-dt_).days, iv9, iv30)*100*posn["n"]
        eq.append((dt_, mark))
        cash *= (1+R/252)
    return pd.Series([e for _, e in eq], index=[t for t, _ in eq]), pd.DataFrame(trades)


def stats(eq):
    r = eq.pct_change().dropna(); yrs = (eq.index[-1]-eq.index[0]).days/365.25
    cagr = (eq.iloc[-1]/eq.iloc[0])**(1/yrs)-1
    sh = r.mean()/(r.std()+1e-12)*math.sqrt(252)
    return cagr, sh, (eq/eq.cummax()-1).min(), eq.iloc[-1]


def main():
    import yfinance as yf
    raw = yf.download(["SPY", "QQQ", "^VIX", "^VIX9D", "^VXN"], start="2011-01-01",
                      auto_adjust=True, progress=False)["Close"].ffill()
    raw.index = pd.to_datetime(raw.index).tz_localize(None)
    vix, vix9, vxn = raw["^VIX"]/100, raw["^VIX9D"]/100, raw["^VXN"]/100
    ratio = (vix9/vix)                                    # SPX term shape applied to QQQ
    frames = {}
    for und, iv30s in [("SPY", vix), ("QQQ", vxn)]:
        df = pd.DataFrame({"S": raw[und], "iv30": iv30s, "iv9": iv30s*ratio})
        df["ivmed"] = df["iv30"].rolling(252, min_periods=60).median()
        df["spike"] = df["iv30"] > 1.15*df["iv30"].rolling(10).mean()
        frames[und] = df.dropna()

    print(f"\n=== Double calendar 14d/21d · {frames['SPY'].index.min().date()}..{frames['SPY'].index.max().date()} · "
          f"+{PT:.0%} target / {SL:.0%} stop · {RISK_PCT:.0%} risk/trade · IV-filtered ===\n")
    print(f"  {'variant':44}{'CAGR':>7}{'Sharpe':>8}{'maxDD':>7}{'final$':>11}{'trades':>7}{'win%':>6}{'avg ret':>9}")
    keep = {}
    for und in ["SPY", "QQQ"]:
        for lbl, m, fw in [("width=0.5x expected move", 0.5, None),
                           ("width=1.0x expected move", 1.0, None),
                           ("width=0.2% (video '10 SPX pts')", None, 0.002)]:
            eq, tr = run(frames[und], m, fw)
            c, s, d, fin = stats(eq)
            win = (tr["pnl"] > 0).mean() if len(tr) else 0
            print(f"  {und+' '+lbl:44}{c*100:6.1f}%{s:8.2f}{d*100:6.0f}%{fin:11,.0f}{len(tr):7}{win*100:5.0f}%{tr['ret'].mean()*100 if len(tr) else 0:8.1f}%")
            keep[(und, lbl)] = (eq, tr)

    # robustness: no IV filter, and 2x costs, on the best-looking spec
    print("\n  robustness (SPY, width=0.5x):")
    for lbl, kw in [("no IV filter (enter always)", dict(iv_filter=False)),
                    ("3x costs ($0.06 half-spread/leg)", dict(cost_mult=3.0))]:
        eq, tr = run(frames["SPY"], 0.5, None, **kw)
        c, s, d, fin = stats(eq)
        win = (tr["pnl"] > 0).mean() if len(tr) else 0
        print(f"  {lbl:44}{c*100:6.1f}%{s:8.2f}{d*100:6.0f}%{fin:11,.0f}{len(tr):7}{win*100:5.0f}%{tr['ret'].mean()*100:8.1f}%")

    # anatomy: exit reasons + what vol spikes do
    eq, tr = keep[("SPY", "width=0.5x expected move")]
    print("\n  exit anatomy (SPY 0.5x):")
    for why, g in tr.groupby("why"):
        print(f"    {why:8} {len(g):5} trades · avg ret on debit {g['ret'].mean()*100:6.1f}% · avg $ {g['pnl'].mean():8,.0f}")
    tr["yr"] = tr["dt"].dt.year
    ann = tr.groupby("yr")["pnl"].sum()
    print("\n  P&L by year (SPY 0.5x): " + "  ".join(f"{y}:{v:+,.0f}" for y, v in ann.items()))


if __name__ == "__main__":
    main()
