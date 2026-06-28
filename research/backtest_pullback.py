#!/usr/bin/env python3
"""ACTUAL portfolio backtest (not just IC) of the 'pullback-in-uptrend' lead. Long-only,
full universe, real costs, an equity curve vs the market. Rule: when a name drops sharply
(5d < -8%) while still in an uptrend (>200DMA and >50DMA), buy it equal-weight and hold ~20
trading days; portfolio = equal-weight across all open positions, else cash. This is the
'does it actually make money' test, with a cost haircut and a benchmark.

Run: python research/backtest_pullback.py
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe

HOLD = 20          # trading days
COST_BPS = 15      # per entry/exit


def metrics(ret, name):
    ret = ret.fillna(0)
    eq = (1 + ret).cumprod()
    yrs = len(ret) / 252
    cagr = eq.iloc[-1] ** (1/yrs) - 1
    vol = ret.std() * np.sqrt(252)
    sharpe = (ret.mean() * 252) / vol if vol > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    return f"{name:24} CAGR {cagr*100:6.1f}%  vol {vol*100:5.1f}%  Sharpe {sharpe:5.2f}  maxDD {dd*100:6.1f}%"


def run():
    px = fetch(universe()); px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    spy = px["SPY"] if "SPY" in px else None
    px = px.loc[:, px.notna().sum() > 252]
    rets = px.pct_change()

    ret5 = px / px.shift(5) - 1
    ma200, ma50 = px.rolling(200).mean(), px.rolling(50).mean()
    trig = (ret5 < -0.08) & (px > ma200) & (px > ma50)             # the setup
    # held: a name is in the book for HOLD days after a trigger (signal lagged 1 day -> no lookahead)
    held = (trig.shift(1).fillna(False).rolling(HOLD, min_periods=1).max() > 0)
    nheld = held.sum(1)
    port = (held * rets).sum(1) / nheld.replace(0, np.nan)
    # cost on turnover (names entering/leaving the book)
    turn = held.astype(int).diff().abs().sum(1)
    cost = (turn * COST_BPS / 1e4) / nheld.replace(0, np.nan)
    strat = (port - cost).fillna(0)

    # --- VARIANT: vol-scaled weights + market-regime filter (only when SPY > 200DMA) ---
    invvol = 1.0 / rets.rolling(20).std()
    w = (held * invvol)
    if spy is not None:
        bull = (spy > spy.rolling(200).mean()).reindex(px.index).ffill().fillna(False)
        w = w.mul(bull.astype(float), axis=0)                     # flat in bear regimes
    wsum = w.sum(1)
    port_v = (w * rets).sum(1) / wsum.replace(0, np.nan)
    turn_v = w.div(wsum.replace(0, np.nan), axis=0).fillna(0).diff().abs().sum(1)
    strat_v = (port_v - turn_v * COST_BPS / 1e4).fillna(0)
    # target ~15% annual vol so we compare risk-adjusted, not just raw
    scale = 0.15 / (strat_v.std() * np.sqrt(252))
    strat_v = strat_v * min(scale, 3)

    # benchmarks
    bench_ew = rets.mean(1).fillna(0)                              # equal-weight universe (always in)
    out = []
    out.append(metrics(strat, "PULLBACK (naive eq-wt)"))
    out.append(metrics(strat_v, "PULLBACK (vol-scaled+regime)"))
    out.append(metrics(bench_ew, "universe equal-weight"))
    if spy is not None: out.append(metrics(spy.pct_change(), "SPY buy & hold"))

    print(f"\n=== Pullback-in-uptrend backtest · {px.shape[1]} names · {px.index.min().date()}..{px.index.max().date()} "
          f"· hold {HOLD}d · {COST_BPS}bps cost ===\n")
    for o in out: print("  " + o)
    inmkt = (nheld > 0).mean(); avgn = nheld[nheld > 0].mean()
    # per-trade stats: 20d forward return of each trigger
    fwd20 = px.shift(-HOLD) / px - 1
    trades = fwd20[trig.shift(1).fillna(False)].stack().dropna()
    print(f"\n  in-market {inmkt*100:.0f}% of days · avg {avgn:.0f} positions when in ·"
          f" {len(trades):,} trades · avg trade {trades.mean()*100:+.2f}% · win {(trades>0).mean()*100:.0f}%")
    print("  NOTE: survivorship-biased universe (today's members) -> absolute return optimistic;")
    print("  the honest read is the SPREAD vs the equal-weight benchmark + the Sharpe/maxDD shape.")


if __name__ == "__main__":
    run()
