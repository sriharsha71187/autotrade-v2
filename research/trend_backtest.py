#!/usr/bin/env python3
"""Backtest the TREND-PARTICIPATION reframe on historical price data (available NOW — no need
to wait for the perishable capture to mature). Question: does DISCIPLINED trend-participation
(ride confirmed uptrends, EXIT on trend break, gate by market regime) beat buy-and-hold and
naive always-in momentum — net of costs — especially in drawdowns? The reframe says the edge
is execution discipline (entry/exit/sizing/regime), not a secret signal.

Long-only. Monthly rebalance. Universe = current S&P 500 + 400 (NOTE: survivorship-biased —
uses today's membership; affects all strategies similarly so RELATIVE comparison is the honest
read, absolute CAGR is optimistic). Costs haircut applied on turnover.

Strategies:
  A  BENCH      equal-weight buy-and-hold of the universe (always fully invested)
  B  XSMOM      top-quintile by 12-1 momentum, equal-weight, always in
  C  TREND+EXIT own names with close>200DMA AND 12-1 mom>0; drop a name when it closes <50DMA;
                + REGIME gate: hold cash whenever SPY < its own 200DMA (step off the bus in bears)

Run: python research/trend_backtest.py
"""
import json, datetime as dt
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")

ENV = Path.home() / ".autotrade.env"
CACHE = Path.home() / ".trend_bars.pkl"
START = "2017-01-01"
COST_BPS = 15          # round-trip-ish per-name cost haircut on turnover (15 bps)


def _keys():
    k = {}
    for ln in ENV.read_text().splitlines():
        if "=" in ln and not ln.startswith("#"):
            a, _, b = ln.partition("="); k[a.strip()] = b.strip().strip('"').strip("'")
    return k.get("ALPACA_API_KEY"), k.get("ALPACA_SECRET_KEY")


def universe():
    import sys; sys.path.insert(0, str(Path(__file__).resolve().parent))
    import daily_universe_capture as c
    return c.universe()


def fetch(symbols):
    if CACHE.exists():
        px = pd.read_pickle(CACHE)
        print(f"  loaded cached panel {px.shape} from {CACHE.name}")
        return px
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    cl = StockHistoricalDataClient(*_keys())
    frames = {}
    syms = symbols + ["SPY"]
    for i in range(0, len(syms), 100):
        chunk = syms[i:i+100]
        try:
            df = cl.get_stock_bars(StockBarsRequest(symbol_or_symbols=chunk,
                  timeframe=TimeFrame.Day, start=pd.Timestamp(START))).df
        except Exception as e:
            print("  fetch fail:", e); continue
        if df is None or df.empty: continue
        for s in chunk:
            try:
                c = df.loc[s]["close"]; c.index = pd.to_datetime(c.index).tz_convert("UTC").normalize()
                frames[s] = c
            except KeyError: pass
        print(f"  fetched {i+len(chunk)}/{len(syms)} …")
    px = pd.DataFrame(frames).sort_index()
    px.to_pickle(CACHE)
    print(f"  built + cached panel {px.shape}")
    return px


def metrics(ret):
    ret = ret.dropna()
    if len(ret) < 60: return {}
    eq = (1 + ret).cumprod()
    yrs = len(ret) / 252
    cagr = eq.iloc[-1] ** (1/yrs) - 1
    vol = ret.std() * np.sqrt(252)
    sharpe = (ret.mean() * 252) / vol if vol > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    return {"CAGR": cagr, "vol": vol, "Sharpe": sharpe, "maxDD": dd}


def run():
    syms = universe()
    print(f"Universe {len(syms)} names; fetching history since {START} …")
    px = fetch(syms)
    spy = px["SPY"]; px = px.drop(columns=["SPY"], errors="ignore")
    # require reasonable history
    px = px.loc[:, px.notna().sum() > 252]
    rets = px.pct_change()
    # features
    ma200 = px.rolling(200).mean(); ma50 = px.rolling(50).mean()
    mom = px.shift(21) / px.shift(252) - 1          # 12-1 momentum (skip last month)
    spy_ma200 = spy.rolling(200).mean()
    # monthly rebalance dates
    month_end = px.resample("M").last().index
    rebal = [px.index[px.index.get_indexer([d], method="ffill")[0]] for d in month_end if d >= px.index[252]]

    def backtest(weight_fn):
        w = pd.DataFrame(0.0, index=px.index, columns=px.columns)
        cur = pd.Series(0.0, index=px.columns)
        turnover_dates = {}
        for i, d in enumerate(rebal):
            tgt = weight_fn(d)
            turnover_dates[d] = (tgt - cur).abs().sum()
            cur = tgt
            nxt = rebal[i+1] if i+1 < len(rebal) else px.index[-1]
            w.loc[d:nxt] = tgt.values
        w = w.shift(1).fillna(0)                     # trade next day -> no lookahead
        gross = (w * rets).sum(axis=1)
        # cost: haircut per unit turnover on rebalance days
        cost = pd.Series(0.0, index=px.index)
        for d, to in turnover_dates.items():
            if d in cost.index: cost.loc[d] = to * COST_BPS / 1e4
        return gross - cost

    def w_bench(d):
        live = px.columns[px.loc[d].notna()]
        return pd.Series(1.0/len(live), index=px.columns).where(px.columns.isin(live), 0).fillna(0)

    def w_xsmom(d):
        m = mom.loc[d].dropna()
        if len(m) < 20: return pd.Series(0.0, index=px.columns)
        top = m[m >= m.quantile(0.8)].index
        s = pd.Series(0.0, index=px.columns); s[top] = 1.0/len(top); return s

    def w_trend(d):
        if spy.loc[d] < spy_ma200.loc[d]:            # REGIME gate: market below 200DMA -> cash
            return pd.Series(0.0, index=px.columns)
        up = (px.loc[d] > ma200.loc[d]) & (mom.loc[d] > 0) & (px.loc[d] > ma50.loc[d])
        held = px.columns[up.fillna(False)]
        if len(held) == 0: return pd.Series(0.0, index=px.columns)
        s = pd.Series(0.0, index=px.columns); s[held] = 1.0/len(held); return s

    out = {}
    for name, fn in [("A_BENCH", w_bench), ("B_XSMOM", w_xsmom), ("C_TREND+EXIT+REGIME", w_trend)]:
        r = backtest(fn)
        out[name] = metrics(r)
        out[name]["%in_mkt"] = float((r != 0).mean())
    out["SPY_buyhold"] = metrics(spy.pct_change())

    print("\n=== Trend-participation backtest (long-only, monthly, %d bps cost) ===" % COST_BPS)
    print(f"{'strategy':22} {'CAGR':>7} {'vol':>6} {'Sharpe':>7} {'maxDD':>7} {'%inMkt':>7}")
    for k, m in out.items():
        if not m: continue
        print(f"{k:22} {m.get('CAGR',0)*100:6.1f}% {m.get('vol',0)*100:5.1f}% "
              f"{m.get('Sharpe',0):6.2f} {m.get('maxDD',0)*100:6.1f}% {m.get('%in_mkt',1)*100:6.0f}%")
    print("\nNote: survivorship-biased universe (today's members) -> absolute CAGR optimistic;")
    print("the RELATIVE read (does C's exit/regime discipline cut maxDD & lift Sharpe vs A/B) is honest.")


if __name__ == "__main__":
    run()
