#!/usr/bin/env python3
"""Tactical ETF strategies with EXPLICIT entry + exit/profit-taking rules, backtested vs
buy-and-hold. No cross-sectional alpha — known, sound systems with timing. Each prints its
entry signal, exit signal, and full stats (CAGR/Sharpe/maxDD/#trades/win%/avg-trade).

Strategies:
  A. Connors RSI2 mean-reversion (QQQ) — buy deep oversold in an uptrend, sell on bounce
  B. Dip-buy + profit-target (QQQ) — buy the pullback, book +8% (or stop/time out)
  C. Dual momentum rotation (SPY/QQQ/TLT) — hold the strongest 12m trend, else bonds
  D. Momentum + regime (SPMO, SPY>200DMA) — the validated beta tilt, for reference
Run: python research/backtest_tactical.py
"""
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")

COST = 0.0005   # 5 bps per side


def rsi(s, n):
    d = s.diff(); up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100/(1 + up/dn.replace(0, np.nan))


def stats(ret, pos=None):
    ret = ret.dropna()
    eq = (1+ret).cumprod(); yrs = len(ret)/252
    cagr = eq.iloc[-1]**(1/yrs)-1; vol = ret.std()*np.sqrt(252)
    sh = (ret.mean()*252)/vol if vol > 0 else 0; dd = (eq/eq.cummax()-1).min()
    inmkt = (pos.shift(1) > 0).mean() if pos is not None else 1.0
    return cagr, sh, dd, inmkt


def ret_from_pos(px, pos):
    r = px.pct_change()
    return (pos.shift(1)*r - COST*pos.diff().abs()).fillna(0)


def trades_from_pos(px, pos):
    """per-trade returns: from each entry (0->1) to its exit."""
    p = pos.values; pr = px.values; out = []; entry = None
    for i in range(len(p)):
        if p[i] > 0 and (i == 0 or p[i-1] == 0): entry = pr[i]
        if entry is not None and (i == len(p)-1 or p[i+1] == 0) and p[i] > 0:
            out.append(pr[i]/entry-1); entry = None
    return np.array(out)


def connors_rsi2(px):
    r2 = rsi(px, 2); ma200 = px.rolling(200).mean(); ma5 = px.rolling(5).mean()
    pos = np.zeros(len(px)); st = 0
    for i in range(len(px)):
        if st == 0 and r2.iloc[i] < 10 and px.iloc[i] > ma200.iloc[i]: st = 1
        elif st == 1 and px.iloc[i] > ma5.iloc[i]: st = 0
        pos[i] = st
    return pd.Series(pos, index=px.index)


def dipbuy_target(px, dip=-0.05, target=0.08, stop=0.07, maxhold=25):
    r5 = px/px.shift(5)-1; ma200 = px.rolling(200).mean()
    pos = np.zeros(len(px)); st = 0; entry = 0; held = 0
    for i in range(len(px)):
        if st == 1:
            held += 1; chg = px.iloc[i]/entry-1
            if chg >= target or chg <= -stop or held >= maxhold: st = 0
        if st == 0 and r5.iloc[i] < dip and px.iloc[i] > ma200.iloc[i]:
            st = 1; entry = px.iloc[i]; held = 0
        pos[i] = st
    return pd.Series(pos, index=px.index)


def dual_momentum(px_df, assets=("SPY", "QQQ"), safe="TLT", look=252):
    mom = px_df/px_df.shift(look)-1
    pos = pd.DataFrame(0.0, index=px_df.index, columns=px_df.columns)
    month = px_df.index.to_period("M")
    rebal = px_df.index[np.append([True], month[1:] != month[:-1])]   # month starts
    cur = None
    for dt in px_df.index:
        if dt in rebal:
            m = mom.loc[dt, list(assets)].dropna()
            if len(m):
                best = m.idxmax()
                cur = best if m[best] > mom.loc[dt, safe] and m[best] > 0 else safe
        if cur: pos.loc[dt, cur] = 1.0
    return pos


def main():
    import yfinance as yf
    tk = ["SPY", "QQQ", "TLT", "SPMO"]
    px = yf.download(tk, start="2014-01-01", auto_adjust=True, progress=False)["Close"]
    px.index = pd.to_datetime(px.index).tz_localize(None)
    qqq = px["QQQ"].dropna()

    print(f"\n=== Tactical ETF backtest · {px.index.min().date()}..{px.index.max().date()} · {COST*1e4:.0f}bps/side ===\n")
    print(f"  {'strategy':34} {'CAGR':>7} {'Sharpe':>7} {'maxDD':>7} {'in-mkt':>7} {'trades':>7} {'win%':>5} {'avg':>6}")

    def show(name, pos, base):
        ret = ret_from_pos(base, pos); c, s, d, im = stats(ret, pos)
        tr = trades_from_pos(base, pos)
        wr = (tr > 0).mean()*100 if len(tr) else 0; av = tr.mean()*100 if len(tr) else 0
        print(f"  {name:34} {c*100:6.1f}% {s:7.2f} {d*100:6.1f}% {im*100:6.0f}% {len(tr):>7} {wr:4.0f}% {av:+5.1f}%")

    show("A. Connors RSI2 (QQQ)", connors_rsi2(qqq), qqq)
    show("B. Dip-buy +8% target (QQQ)", dipbuy_target(qqq), qqq)
    dm = dual_momentum(px.dropna())
    dm_ret = (dm.shift(1)*px.pct_change()).sum(1) - COST*dm.diff().abs().sum(1)
    cd, sd, dd, _ = stats(dm_ret.fillna(0))
    print(f"  {'C. Dual momentum SPY/QQQ/TLT':34} {cd*100:6.1f}% {sd:7.2f} {dd*100:6.1f}% {'~100%':>7} {'monthly':>7}")
    # D + benchmarks
    spy_bull = (px["SPY"] > px["SPY"].rolling(200).mean()).shift(1)
    show("D. SPMO + regime", spy_bull.astype(float).reindex(px["SPMO"].dropna().index).fillna(0), px["SPMO"].dropna())
    for b in ["QQQ", "SPY"]:
        c, s, d, _ = stats(px[b].pct_change())
        print(f"  {'BUY-HOLD '+b:34} {c*100:6.1f}% {s:7.2f} {d*100:6.1f}% {'100%':>7}")
    print("\n  win% = fraction of completed trades that were profitable. avg = mean return per trade.")


if __name__ == "__main__":
    main()
