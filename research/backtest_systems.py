#!/usr/bin/env python3
"""The profitable-systems comparison. Stay-invested beta families + rotation + vol-targeting +
a core-with-dip-boost hybrid (which fixes the cash-drag that killed the pure dip strategies).
Each is a full algorithm with explicit entry/exit. vs buy-hold. Run: python research/backtest_systems.py
"""
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
COST = 0.0005


def rsi(s, n):
    d = s.diff(); up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100/(1 + up/dn.replace(0, np.nan))


def stats(ret):
    ret = ret.dropna(); eq = (1+ret).cumprod(); yrs = len(ret)/252
    cagr = eq.iloc[-1]**(1/yrs)-1; vol = ret.std()*np.sqrt(252)
    sh = (ret.mean()*252)/vol if vol > 0 else 0; dd = (eq/eq.cummax()-1).min()
    return cagr, sh, dd


def row(name, ret, extra=""):
    c, s, d = stats(ret)
    print(f"  {name:38} {c*100:6.1f}% {s:6.2f} {d*100:7.1f}%   {extra}")


def main():
    import yfinance as yf
    tk = ["SPY", "QQQ", "QLD", "TQQQ", "SPMO", "TLT", "GLD"]
    px = yf.download(tk, start="2014-01-01", auto_adjust=True, progress=False)["Close"]
    px.index = pd.to_datetime(px.index).tz_localize(None)
    r = px.pct_change()
    bull = (px["SPY"] > px["SPY"].rolling(200).mean()).shift(1).fillna(False)

    print(f"\n=== Profitable systems · {px.index.min().date()}..{px.index.max().date()} · {COST*1e4:.0f}bps/side ===\n")
    print(f"  {'strategy':38} {'CAGR':>7} {'Sharpe':>6} {'maxDD':>8}   entry/exit")

    # 1. Momentum + regime
    p = bull.astype(float)
    row("1. SPMO + 200DMA regime", (p.shift(1)*r["SPMO"] - COST*p.diff().abs()).fillna(0),
        "IN when SPY>200DMA, else cash")
    # 2. Leveraged momentum + regime
    row("2. QLD(2x) + regime", (p.shift(1)*r["QLD"] - COST*p.diff().abs()).fillna(0), "2x QQQ when bull, else cash")
    row("3. TQQQ(3x) + regime", (p.shift(1)*r["TQQQ"] - COST*p.diff().abs()).fillna(0), "3x QQQ when bull, else cash")
    # 4. Vol-targeted QQQ (lever calm, de-risk turbulent)
    rv = r["QQQ"].rolling(20).std()*np.sqrt(252)
    w = (0.22/rv).clip(0, 2.0).shift(1)
    row("4. Vol-targeted QQQ (22% tgt, cap 2x)", (w*r["QQQ"] - COST*w.diff().abs()).fillna(0),
        "weight=22%/realized-vol; lever calm, cut turbulent")
    # 5. Dual momentum rotation {QQQ,SPMO,TLT,GLD}
    assets = ["QQQ", "SPMO", "TLT", "GLD"]; mom = px[assets]/px[assets].shift(252)-1
    pos = pd.DataFrame(0.0, index=px.index, columns=assets); mth = px.index.to_period("M")
    rebal = set(px.index[np.append([True], mth[1:] != mth[:-1])]); cur = None
    for dt in px.index:
        if dt in rebal:
            m = mom.loc[dt].dropna()
            if len(m): cur = m.idxmax() if m.max() > 0 else "TLT"
        if cur: pos.loc[dt, cur] = 1.0
    dm = (pos.shift(1)*r[assets]).sum(1) - COST*pos.diff().abs().sum(1)
    row("5. Dual momentum {QQQ/SPMO/TLT/GLD}", dm.fillna(0), "monthly: hold strongest 12m trend, else TLT")
    # 6. Core QQQ + dip-boost to 2x (fixes cash-drag: stay invested, add leverage on dips)
    r2 = rsi(px["QQQ"], 2); ma5 = px["QQQ"].rolling(5).mean(); ma200 = px["QQQ"].rolling(200).mean()
    boost = np.zeros(len(px)); st = 0
    for i in range(len(px)):
        if st == 0 and r2.iloc[i] < 10 and px["QQQ"].iloc[i] > ma200.iloc[i]: st = 1
        elif st == 1 and px["QQQ"].iloc[i] > ma5.iloc[i]: st = 0
        boost[i] = st
    boost = pd.Series(boost, index=px.index)
    # base 1x QQQ always; when boost on, use QLD(2x) instead
    core = ((1-boost).shift(1)*r["QQQ"] + boost.shift(1)*r["QLD"] - COST*boost.diff().abs()).fillna(0)
    row("6. Core QQQ + dip-boost to 2x", core, "hold QQQ; RSI2<10 & >200DMA -> 2x until >5DMA")

    print()
    for b in ["QQQ", "SPMO", "SPY"]:
        row(f"BUY-HOLD {b}", r[b].fillna(0))
    print("\n  All long-only, daily, no shorting. Regime/vol signals lagged 1 day (no lookahead).")


if __name__ == "__main__":
    main()
