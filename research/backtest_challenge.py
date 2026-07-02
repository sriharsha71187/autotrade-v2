#!/usr/bin/env python3
"""THE CHALLENGE: is there an intraday/multi-day strategy that actually works, net of costs?
Tests the five best-documented short-horizon effects (not the already-rejected ORB/VWAP/0DTE):
  A. RSI-2 mean reversion on SPY (Connors): buy RSI2<10 while SPY>200DMA, exit RSI2>65.
  B. Turn-of-month: hold SPY only last-4 + first-3 trading days of each month.
  C. Overnight edge: hold SPY close->open only (gross AND net of 2bp/day round trip).
  D. VIX term-structure gate: long SPY when VIX3M>VIX (contango), cash in backwardation.
  E. Gap continuation (PEAD proxy): stocks with a +8% day -> buy, hold 21d; also the FADE side.
All report CAGR / Sharpe / maxDD / %days-exposed, net of realistic costs. Benchmark: SPY B&H.
Run: python research/backtest_challenge.py
"""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe
SPY_COST = 0.0001          # 1bp per side SPY
STK_COST = 0.0010          # 10bp per side single stocks


def stats(r, expo=None):
    r = r.dropna(); eq = (1+r).cumprod(); yrs = len(r)/252
    cagr = eq.iloc[-1]**(1/yrs)-1; sh = (r.mean()*252)/(r.std()*math.sqrt(252)+1e-9)
    dd = (eq/eq.cummax()-1).min(); ex = (expo if expo is not None else (r != 0)).mean()
    return cagr, sh, dd, ex


def line(lbl, r, expo=None):
    c, sh, dd, ex = stats(r, expo)
    print(f"  {lbl:44}{c*100:7.1f}%{sh:7.2f}{dd*100:7.0f}%{ex*100:6.0f}%")


def main():
    import yfinance as yf
    d = yf.download(["SPY","^VIX","^VIX3M"], start="2007-01-01", auto_adjust=True, progress=False)
    cl = d["Close"]; op = d["Open"]
    spy_c = cl["SPY"].dropna(); spy_o = op["SPY"].reindex(spy_c.index)
    vix = cl["^VIX"].reindex(spy_c.index).ffill(); v3m = cl["^VIX3M"].reindex(spy_c.index).ffill()
    r_cc = spy_c.pct_change()                                   # close-to-close
    idx = spy_c.index

    print(f"\n=== THE CHALLENGE · short-horizon strategies, net of costs · {idx[0].date()}..{idx[-1].date()} ===\n")
    print(f"  {'strategy':44}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}{'expo':>6}")
    line("SPY buy & hold (benchmark)", r_cc)

    # A. RSI-2 mean reversion, regime-gated
    delta = spy_c.diff(); up = delta.clip(lower=0); dn = -delta.clip(upper=0)
    rs = up.ewm(alpha=0.5, adjust=False).mean() / (dn.ewm(alpha=0.5, adjust=False).mean()+1e-12)
    rsi2 = 100 - 100/(1+rs)
    ma200 = spy_c.rolling(200).mean()
    pos = pd.Series(0.0, index=idx); hold = False
    for i in range(200, len(idx)):
        if not hold and rsi2.iloc[i] < 10 and spy_c.iloc[i] > ma200.iloc[i]: hold = True
        elif hold and rsi2.iloc[i] > 65: hold = False
        pos.iloc[i] = 1.0 if hold else 0.0
    ra = (pos.shift(1)*r_cc - SPY_COST*pos.diff().abs()).fillna(0)
    line("A. RSI-2 dip-buy (SPY>200DMA, exit RSI>65)", ra, pos.shift(1).fillna(0) > 0)

    # B. Turn-of-month
    month = idx.to_period("M"); tom = pd.Series(0.0, index=idx)
    for m in month.unique():
        days = idx[month == m]
        tom.loc[days[-4:]] = 1.0                                # last 4 trading days
        tom.loc[days[:3]] = 1.0                                 # first 3 trading days
    rb = (tom.shift(1)*r_cc - SPY_COST*tom.diff().abs()).fillna(0)
    line("B. Turn-of-month (last4+first3 days)", rb, tom.shift(1).fillna(0) > 0)

    # C. Overnight edge (close -> next open)
    r_on = (spy_o.shift(-1)/spy_c - 1).shift(1).fillna(0)       # aligned to the day the open lands
    line("C. Overnight only (close->open, GROSS)", r_on)
    line("C. Overnight only (NET 2bp/day)", r_on - 0.0002)

    # D. VIX term-structure gate
    contango = (v3m > vix).shift(1).fillna(False)
    rd = (contango.astype(float)*r_cc - SPY_COST*contango.astype(float).diff().abs()).fillna(0)
    line("D. VIX contango gate (long SPY else cash)", rd, contango)

    # E. Gap continuation on the stock panel (PEAD proxy)
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px.loc[:, px.notna().sum() > 252]
    stocks = [c for c in px.columns if c not in ("SPY","QQQ")]
    rp = px[stocks].pct_change()
    event = rp > 0.08                                           # +8% day
    HOLD = 21
    w = pd.DataFrame(0.0, index=px.index, columns=stocks)
    ev = event.shift(1).fillna(False)                           # enter next day
    for k in range(HOLD):                                       # held for 21 days after entry
        w += ev.shift(k).fillna(False).astype(float)
    w = w.clip(0, 1)
    n = w.sum(1).replace(0, np.nan)
    wn = w.div(n, axis=0).fillna(0)                             # equal-weight across live events
    re_ = ((wn.shift(1)*rp).sum(1) - STK_COST*wn.diff().abs().sum(1)).fillna(0)
    expo_e = (wn.sum(1) > 0)
    line("E. +8% gap CONTINUATION (buy, hold 21d)", re_, expo_e)
    rf = ((-wn.shift(1)*rp).sum(1) - STK_COST*wn.diff().abs().sum(1)).fillna(0)
    line("E'. +8% gap FADE (short, hold 21d)", rf, expo_e)

    print("\n  expo = fraction of days with a position. Sharpe is on the full curve (cash days = 0).")
    print("  Panel strategies (E) carry the survivorship caveat; SPY strategies (A-D) do not.")


if __name__ == "__main__":
    main()
