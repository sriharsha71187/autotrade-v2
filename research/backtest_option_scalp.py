#!/usr/bin/env python3
"""Option scalping, measured BEFORE it is ever enabled (option_scalp.py ships dark).

Strategy under test: on SPY/QQQ 5m bars, a momentum BURST (close beyond the prior
12-bar extreme on >=1.5x median volume, tape-aligned) buys a ~1%-ITM (~0.6-delta)
1-DTE option in the break direction; exits at +25% / -20% of premium, a 45-min
time-stop, or 15:30 ET. The option is priced by Black-Scholes at intraday realized
vol x 1.15 (same convention as backtest_0dte.py) and repriced along subsequent 5m
closes with decaying T, so the P&L includes gamma AND theta — not just delta.

Friction grid: {0.5%, 1%, 2%} of premium PER SIDE. 0.5% is the BEST case (penny-wide
SPY chain, mid-ish fills); 2% is what marketable-limit fills against a 1-2c spread
plus fees actually cost on a ~$2 premium. A second variant re-checks exits only
every 4 bars (~20min) to mirror the engine's cycle cadence — the live bot cannot
watch ticks, so if the edge dies under exit lag it dies in production.

Data: yfinance 5m bars (60d max — its intraday history limit) + daily VIX floor on
IV. Run locally (needs network): python research/backtest_option_scalp.py
Verdict rule (same bar as ORB): positive expectancy in the 2%/side column WITH
exit lag, robust to dropping the best single day — else the flag stays False.
"""
import math
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")

LOOKBACK   = 12          # burst = close beyond prior-12-bar extreme (~1h of 5m bars)
VOL_MULT   = 1.5
TAPE_MIN   = 0.0010
ITM_PCT    = 0.010       # ~0.6-delta proxy
TP, SL     = 0.25, 0.20  # on premium
MAX_HOLD_B = 9           # 45min = 9 x 5m bars
ENTRY_S, ENTRY_E, FLAT = 10*60, 14*60+30, 15*60+30
T_DTE      = 1.5/252     # ~1 DTE remaining at entry
FRICTIONS  = [0.005, 0.01, 0.02]        # per side, % of premium
SYMS       = ["SPY", "QQQ"]


def N(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs(S, K, T, sig, put=False):
    if T <= 0 or sig <= 0: return max((K-S) if put else (S-K), 0.0)
    d1 = (math.log(S/K)+(sig*sig/2)*T)/(sig*math.sqrt(T)); d2 = d1-sig*math.sqrt(T)
    return (K*N(-d2)-S*N(-d1)) if put else (S*N(d1)-K*N(d2))


def scalp_pnl(px, i, put, iv, lag=1):
    """Enter at bar i close; exit on TP/SL/time/15:30 checking every `lag` bars.
    Returns option return on premium BEFORE friction."""
    S0 = px["c"].iloc[i]
    K = S0*(1-ITM_PCT) if not put else S0*(1+ITM_PCT)
    prem0 = bs(S0, K, T_DTE, iv, put)
    if prem0 <= 0.01: return None
    n = len(px)
    for j in range(i+1, min(i+MAX_HOLD_B+1, n)):
        if (j-i) % lag and px["t"].iloc[j] < FLAT: continue
        T = T_DTE - (j-i)*(5/390)/252
        r = bs(px["c"].iloc[j], K, T, iv, put)/prem0 - 1
        if r >= TP or r <= -SL or px["t"].iloc[j] >= FLAT or j == i+MAX_HOLD_B:
            return r
    T = T_DTE - MAX_HOLD_B*(5/390)/252
    return bs(px["c"].iloc[min(i+MAX_HOLD_B, n-1)], K, T, iv, put)/prem0 - 1


def main():
    import yfinance as yf
    data = yf.download(SYMS, period="60d", interval="5m", progress=False,
                       group_by="ticker", auto_adjust=True, prepost=False)
    vix = yf.download("^VIX", period="90d", progress=False, auto_adjust=True)["Close"]
    frames = {}
    for s in SYMS:
        df = data[s].dropna(subset=["Open","High","Low","Close"]).copy()
        idx = df.index.tz_convert("America/New_York") if df.index.tz is not None \
            else df.index.tz_localize("UTC").tz_convert("America/New_York")
        df.index = idx
        df["t"] = idx.hour*60 + idx.minute
        df["d"] = idx.date
        frames[s] = df.rename(columns=str.lower)

    spy = frames["SPY"]
    trades = []            # (day, sym, put, raw_ret, raw_ret_lagged)
    for s in SYMS:
        df = frames[s]
        for day, px in df.groupby("d"):
            px = px.reset_index(drop=True)
            sd = spy[spy["d"] == day].reset_index(drop=True)
            if len(px) < LOOKBACK+2 or sd.empty: continue
            # intraday realized vol (session open->close log-ret, 21d) -> IV proxy
            taken = 0
            for i in range(LOOKBACK, len(px)-1):
                if taken >= 2: break
                t = px["t"].iloc[i]
                if not (ENTRY_S <= t < ENTRY_E): continue
                w = px.iloc[i-LOOKBACK:i]
                c = px["close"].iloc[i]
                med_v = w["volume"].median()
                if med_v <= 0 or px["volume"].iloc[i] < VOL_MULT*med_v: continue
                tape_row = sd[sd["t"] <= t]
                if tape_row.empty: continue
                tape = tape_row["close"].iloc[-1]/sd["open"].iloc[0] - 1
                if c > w["high"].max() and tape >= TAPE_MIN: put = False
                elif c < w["low"].min() and tape <= -TAPE_MIN: put = True
                else: continue
                try: iv = max(float(vix.loc[:pd.Timestamp(day)].iloc[-1])/100*0.9, 0.08)
                except Exception: iv = 0.15
                r1 = scalp_pnl(px, i, put, iv, lag=1)
                r4 = scalp_pnl(px, i, put, iv, lag=4)
                if r1 is None: continue
                trades.append((day, s, put, r1, r4))
                taken += 1

    if not trades:
        print("No trades triggered in the sample."); return
    df = pd.DataFrame(trades, columns=["day","sym","put","r","r_lag"])
    print(f"\n=== option scalp · SPY/QQQ 5m bursts · 1-DTE ~0.6d long option · "
          f"{df['day'].nunique()} days · {len(df)} trades ===\n")
    print(f"  {'variant':38}{'avg/trade':>10}{'win%':>7}{'PF':>7}{'total':>9}")
    for lbl, col in [("exit checked every bar", "r"), ("exit lag 20min (live cadence)", "r_lag")]:
        for f in FRICTIONS:
            r = df[col].dropna() - 2*f          # friction per side on premium
            g, l = r[r > 0].sum(), -r[r <= 0].sum()
            pf = g/l if l > 0 else float("inf")
            print(f"  {lbl+f' · {f:.1%}/side':38}{r.mean()*100:9.2f}%"
                  f"{(r>0).mean()*100:6.0f}%{pf:7.2f}{r.sum()*100:8.1f}%")
    # single-day robustness: drop the best day, does the 2%/side lagged cell survive?
    r = (df["r_lag"].dropna() - 2*0.02)
    by_day = df.assign(rr=df["r_lag"] - 2*0.02).groupby("day")["rr"].sum()
    ex_best = r.sum() - by_day.max()
    print(f"\n  2%/side + lag: total {r.sum()*100:+.1f}% -> {ex_best*100:+.1f}% "
          f"without best day ({by_day.idxmax()})")
    print("  Verdict bar: enable ONLY if avg/trade > 0 at 2%/side WITH lag and it")
    print("  survives best-day removal. Returns are % of premium per trade.")


if __name__ == "__main__":
    main()
