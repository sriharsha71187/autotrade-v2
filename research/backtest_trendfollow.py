#!/usr/bin/env python3
"""TACTICAL trend-following breakout overlay on thematic ETFs — designed to CATCH moves like
the semis/memory rip and RIDE them, cutting failed breakouts fast (asymmetric payoff).

ENTRY:  close breaks above its prior 50-day high (Donchian breakout) AND close > 200DMA (uptrend).
RIDE:   hold with a chandelier trailing stop = (highest close since entry) - 3*ATR(20).
EXIT:   close < trailing stop, OR close < 200DMA. Book the move / cut the loser.

Portfolio: equal-weight all open positions (cap N), else cash. Reports per-trade asymmetry +
the biggest winners + what it holds NOW. Run: python research/backtest_trendfollow.py
"""
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
COST = 0.0005
THEMES = ["SMH","SOXX","IGV","XBI","XOP","GDX","KRE","ITB","XRT","XME","TAN","IYT","ARKK","JETS","LIT","URA","IBB","KBE","XHB","PAVE"]
BRK, STOPN, ATRN = 50, 3.0, 20


def atr(h, l, c, n):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(1)
    return tr.rolling(n).mean()


def trend_trades(c, h, l, exit_mode="chandelier"):
    """Return position series (0/1) + per-trade returns. exit_mode: 'chandelier' (3*ATR trail)
    or 'ma50' (ride through pullbacks, exit only on close<50DMA)."""
    ma200 = c.rolling(200).mean(); ma50 = c.rolling(50).mean()
    hi50 = c.rolling(BRK).max().shift(1); a = atr(h, l, c, ATRN)
    pos = np.zeros(len(c)); trades = []; st = 0; peak = 0; entry = 0
    for i in range(len(c)):
        if st == 1:
            peak = max(peak, c.iloc[i])
            if exit_mode == "ma50":
                out = c.iloc[i] < ma50.iloc[i]
            else:
                out = c.iloc[i] < peak - STOPN*a.iloc[i] or c.iloc[i] < ma200.iloc[i]
            if out:
                st = 0; trades.append((c.index[i], c.iloc[i]/entry - 1))
        if st == 0 and c.iloc[i] >= hi50.iloc[i] and c.iloc[i] > ma200.iloc[i] and not np.isnan(hi50.iloc[i]):
            st = 1; entry = c.iloc[i]; peak = c.iloc[i]
        pos[i] = st
    return pd.Series(pos, index=c.index), trades


def main():
    import yfinance as yf
    raw = yf.download(THEMES+["SPY","IEF"], start="2007-01-01", auto_adjust=True, progress=False)
    C, H, L = raw["Close"], raw["High"], raw["Low"]
    for x in (C, H, L): x.index = pd.to_datetime(C.index).tz_localize(None)
    avail = [t for t in THEMES if t in C and C[t].notna().sum() > 300]
    r = C.pct_change()

    def stats(x):
        x = x.dropna(); eq = (1+x).cumprod(); yrs = len(x)/252
        return eq.iloc[-1]**(1/yrs)-1, (x.mean()*252)/(x.std()*np.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()

    print(f"\n=== Tactical trend-following on {len(avail)} thematic ETFs · {C.index.min().date()}..{C.index.max().date()} ===")
    for mode in ("chandelier", "ma50"):
        posmat = pd.DataFrame(0.0, index=C.index, columns=avail); alltr = []
        for t in avail:
            p, tr = trend_trades(C[t].dropna(), H[t].dropna(), L[t].dropna(), mode)
            posmat[t] = p.reindex(C.index).fillna(0)
            alltr += [(t, dt, ret) for dt, ret in tr]
        nopen = posmat.sum(1)
        w = posmat.div(nopen.replace(0, np.nan), axis=0).fillna(0)
        strat = (w.shift(1)*r[avail]).sum(1) + (nopen.shift(1) == 0)*r["IEF"]
        strat = (strat - COST*w.diff().abs().sum(1)).fillna(0)
        c, s, dd = stats(strat)
        rets = np.array([t[2] for t in alltr]); wins = rets[rets > 0]; losses = rets[rets <= 0]
        big = sorted(alltr, key=lambda x: x[2], reverse=True)[:5]
        held = posmat.iloc[-1]; held = list(held[held > 0].index)
        print(f"\n  --- exit = {mode} ---")
        print(f"  CAGR {c*100:.1f}%   Sharpe {s:.2f}   maxDD {dd*100:.1f}%   (SPY ~10.9%/0.62/-55%)")
        print(f"  {len(alltr)} trades · win {len(wins)/len(rets)*100:.0f}% · avg WIN +{wins.mean()*100:.1f}% · "
              f"avg LOSS {losses.mean()*100:.1f}% · ratio {abs(wins.mean()/losses.mean()):.1f}x")
        print(f"  biggest rides: " + ", ".join(f"{t} +{ret*100:.0f}%" for t, dt, ret in big))
        print(f"  HOLDING NOW: {', '.join(held) if held else 'cash'}")
    lead = (C[avail].iloc[-2]/C[avail].iloc[-65]-1).dropna().sort_values(ascending=False).head(5)
    print(f"\n  current 3mo leaders: {', '.join(f'{t} {v*100:+.0f}%' for t,v in lead.items())}")


if __name__ == "__main__":
    main()
