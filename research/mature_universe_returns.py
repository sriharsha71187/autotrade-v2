#!/usr/bin/env python3
"""Maturing forward-return join for the nightly universe capture.

For every ~/autotrade_universe_capture/<day>.jsonl, compute each name's forward return path
from the captured close (entry ref = that night's `last`) using daily bars. We DON'T cap at
a fixed short horizon — that would rebuild the very short-horizon bias we're escaping. Instead
we store the full DAILY forward path out to MAX_H trading days (open-ended within that window),
so any horizon is a lookup and the MODEL can declare its own intended hold per candidate and be
scored at exactly that point. A convenience ladder (1/3/5/10/20/40/60d) is derived for quick IC.
Idempotent and re-runnable: the path extends as each session closes.
Output: ~/autotrade_universe_returns/<day>.jsonl  (symbol, date, fwd_path[], fwd_<k>d ladder).

Run: python research/mature_universe_returns.py   (processes all captured days)
"""
import json, datetime as dt
from pathlib import Path
import pandas as pd
import warnings; warnings.filterwarnings("ignore")

ENV = Path.home() / ".autotrade.env"
CAP = Path.home() / "autotrade_universe_capture"
OUT = Path.home() / "autotrade_universe_returns"
MAX_H = 60                          # store the daily forward path out to ~3 months (extensible)
LADDER = [1, 3, 5, 10, 20, 40, 60]  # convenience horizons derived from the path


def _keys():
    k = {}
    for ln in ENV.read_text().splitlines():
        if "=" in ln and not ln.startswith("#"):
            a, _, b = ln.partition("="); k[a.strip()] = b.strip().strip('"').strip("'")
    return k.get("ALPACA_API_KEY"), k.get("ALPACA_SECRET_KEY")


def _daily(symbols, start):
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    cl = StockHistoricalDataClient(*_keys())
    out = {}
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        try:
            df = cl.get_stock_bars(StockBarsRequest(symbol_or_symbols=chunk,
                  timeframe=TimeFrame.Day, start=pd.Timestamp(start))).df
        except Exception as e:
            print("  bars fetch failed:", e); continue
        if df is None or df.empty: continue
        for s in chunk:
            try:
                c = df.loc[s]["close"].copy(); c.index = pd.to_datetime(c.index).normalize()
                out[s] = c
            except KeyError: pass
    return out


def main():
    days = sorted(p.stem for p in CAP.glob("*.jsonl"))
    if not days:
        print("no capture files yet"); return
    syms = set()
    cap = {}
    for d in days:
        rows = [json.loads(l) for l in (CAP / f"{d}.jsonl").read_text().splitlines() if l.strip()]
        cap[d] = rows
        syms.update(r["symbol"] for r in rows)
    bars = _daily(sorted(syms), start=days[0])
    OUT.mkdir(exist_ok=True)
    summary = []
    for d in days:
        d0 = pd.Timestamp(d).tz_localize("UTC").normalize()
        out_rows, n1 = [], 0
        for r in cap[d]:
            s, last = r["symbol"], r.get("last")
            c = bars.get(s)
            if c is None or not last:
                continue
            fut = c[c.index.normalize() > d0]
            # full daily forward path (open-ended within MAX_H), maturing as sessions close
            path = [round(float(fut.iloc[k]) / last - 1, 5) for k in range(min(len(fut), MAX_H))]
            row = {"date": d, "symbol": s, "n_mature": len(path), "fwd_path": path}
            for k in LADDER:
                row[f"fwd_{k}d"] = path[k-1] if len(path) >= k else None
            if path: n1 += 1
            out_rows.append(row)
        (OUT / f"{d}.jsonl").write_text("\n".join(json.dumps(o) for o in out_rows) + "\n")
        summary.append((d, len(out_rows), n1))
    print("matured returns written to", OUT)
    for d, n, n1 in summary:
        print(f"  {d}: {n} names, {n1} with fwd_1d mature")


if __name__ == "__main__":
    main()
