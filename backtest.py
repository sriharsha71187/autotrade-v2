#!/usr/bin/env python3
"""
backtest.py — honest historical validation of the EQUITY/index edges.

Scope (be clear about it): this backtests the strategies that only need PRICE data
— the overnight-drift book and the overnight-vs-intraday decomposition it rests on.
The options books (condors/spreads/earnings/tail) need historical OPTIONS data
(chains+IV) which is not free; they require paid data or a forward paper sample, and
are NOT covered here. Don't read a passing overnight backtest as validating the
whole bot.

What it answers:
  1. Is the overnight (close->open) return anomaly actually present in the data, and
     has it decayed? (cumulative overnight vs intraday return.)
  2. Does the bot's regime-GATED overnight-drift strategy (price>200DMA, VIX<ceiling,
     skip Friday) beat the naive "hold every night" version — and, the real test,
     does EITHER beat just buying and holding SPY, after costs?

Run:  ./venv/bin/python3 backtest.py [SYMBOL] [YEARS]
"""

from __future__ import annotations

import sys
import math

import config as cfg

try:
    import yfinance as yf
    import numpy as np
    import pandas as pd
    _OK = True
except Exception as e:
    _OK = False
    _ERR = str(e)

TRADING_DAYS = 252
ROUND_TRIP_COST = 0.0004   # ~4 bps round-trip (SPY spread+slippage; commissions ~0)


def load(symbol: str, years: int):
    """Raw daily OHLC (un-dividend-adjusted so close->open gaps are real; SPY has no
    splits) plus aligned VIX close."""
    px = yf.download(symbol, period=f"{years}y", interval="1d",
                     auto_adjust=False, progress=False)
    if getattr(px.columns, "nlevels", 1) > 1:
        px.columns = px.columns.get_level_values(0)
    px = px[["Open", "High", "Low", "Close"]].dropna()
    vix = yf.download("^VIX", period=f"{years}y", interval="1d",
                      auto_adjust=False, progress=False)
    if getattr(vix.columns, "nlevels", 1) > 1:
        vix.columns = vix.columns.get_level_values(0)
    px["vix"] = vix["Close"].reindex(px.index).ffill()
    px["ma200"] = px["Close"].rolling(200).mean()
    px["dow"] = px.index.dayofweek            # 0=Mon .. 4=Fri
    # overnight = close[t-1] -> open[t]; intraday = open[t] -> close[t]
    px["overnight_ret"] = px["Open"] / px["Close"].shift(1) - 1.0
    px["intraday_ret"] = px["Close"] / px["Open"] - 1.0
    return px.dropna(subset=["overnight_ret", "intraday_ret"])


def stats(daily_ret: "pd.Series", exposure: float) -> dict:
    """Annualized stats for a daily return stream. exposure = fraction of days in mkt."""
    r = daily_ret.dropna()
    if len(r) < 30:
        return {}
    total = float((1 + r).prod() - 1)
    yrs = len(r) / TRADING_DAYS
    cagr = (1 + total) ** (1 / yrs) - 1 if yrs > 0 else 0.0
    nz = r[r != 0]
    vol = float(nz.std() * math.sqrt(TRADING_DAYS)) if len(nz) > 1 else 0.0
    sharpe = (cagr / vol) if vol > 0 else 0.0
    curve = (1 + r).cumprod()
    dd = float((curve / curve.cummax() - 1).min())
    wins = float((nz > 0).mean()) if len(nz) else 0.0
    return {"total": total, "cagr": cagr, "vol": vol, "sharpe": sharpe,
            "maxdd": dd, "winrate": wins, "exposure": exposure,
            "avg_trade_bps": float(nz.mean() * 1e4) if len(nz) else 0.0, "n": len(nz)}


def line(name, s):
    if not s:
        print(f"  {name:28s}  (insufficient data)")
        return
    print(f"  {name:28s}  CAGR {s['cagr']*100:6.2f}%  Sharpe {s['sharpe']:5.2f}  "
          f"maxDD {s['maxdd']*100:6.1f}%  win {s['winrate']*100:4.1f}%  "
          f"exp {s['exposure']*100:4.0f}%  avg {s['avg_trade_bps']:+5.1f}bps  n={s['n']}")


def run(symbol="SPY", years=8):
    if not _OK:
        print("backtest needs yfinance/pandas/numpy:", _ERR)
        return
    df = load(symbol, years)
    print(f"\n=== Backtest {symbol}  ({df.index[0].date()} -> {df.index[-1].date()}, "
          f"{len(df)} days) ===\n")

    # 1) The anomaly itself: cumulative overnight vs intraday vs buy&hold.
    on_cum = float((1 + df["overnight_ret"]).prod() - 1)
    id_cum = float((1 + df["intraday_ret"]).prod() - 1)
    bh_cum = float(df["Close"].iloc[-1] / df["Close"].iloc[0] - 1)
    print("Return decomposition (no costs, no gating):")
    print(f"  buy & hold (24h)        {bh_cum*100:8.1f}%")
    print(f"  overnight-only (close->open) {on_cum*100:8.1f}%")
    print(f"  intraday-only (open->close)  {id_cum*100:8.1f}%")
    print(f"  -> overnight is {on_cum/ (id_cum if id_cum else 1e-9):.1f}x intraday\n")

    # decay check: overnight edge in the first vs second half of the window.
    half = len(df) // 2
    on1 = float((1 + df["overnight_ret"].iloc[:half]).prod() - 1)
    on2 = float((1 + df["overnight_ret"].iloc[half:]).prod() - 1)
    print(f"Overnight edge decay:  first half {on1*100:+.1f}%  |  second half {on2*100:+.1f}%\n")

    # 2) Strategies as DAILY return streams (0 on days out of market).
    cost = ROUND_TRIP_COST
    # buy & hold: full 24h daily return every day.
    bh_daily = df["Close"] / df["Close"].shift(1) - 1.0
    # overnight-every-night (naive): capture overnight_ret, minus costs, every day.
    naive = df["overnight_ret"] - cost
    # regime-gated (the bot): hold overnight only when price>200DMA, VIX<ceiling, not Fri.
    gate = ((df["Close"] > df["ma200"]) & (df["vix"] < cfg.OVERNIGHT_VIX_CEILING)
            & (df["dow"] != 4))
    # the gate decided at close[t-1] governs overnight_ret[t]; shift the mask by 1.
    gate_eff = gate.shift(1).fillna(False)
    gated = df["overnight_ret"].where(gate_eff, 0.0) - cost * gate_eff.astype(float)
    # intraday-only for contrast
    intra = df["intraday_ret"] - cost

    print(f"Strategies (after {cost*1e4:.0f}bps round-trip cost):")
    line("Buy & hold SPY", stats(bh_daily, 1.0))
    line("Overnight every night", stats(naive, 1.0))
    line("Overnight regime-gated (bot)", stats(gated, float(gate_eff.mean())))
    line("Intraday only", stats(intra, 1.0))

    print("\nVerdict cues:")
    g = stats(gated, float(gate_eff.mean()))
    b = stats(bh_daily, 1.0)
    if g and b:
        print(f"  gated overnight Sharpe {g['sharpe']:.2f} vs buy&hold {b['sharpe']:.2f} "
              f"-> {'BETTER risk-adjusted' if g['sharpe']>b['sharpe'] else 'WORSE risk-adjusted'}")
        print(f"  gated avg trade {g['avg_trade_bps']:+.1f}bps vs {cost*1e4:.0f}bps cost "
              f"-> {'edge survives costs' if g['avg_trade_bps']>0 else 'EATEN BY COSTS'}")
    print()


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    yrs = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    run(sym, yrs)
