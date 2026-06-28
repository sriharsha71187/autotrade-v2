#!/usr/bin/env python3
"""Backfill historical daily option-regime features (EODHD) into a panel we can join to
forward returns for Tier-0. Scoped + resumable + throttled to respect the 100k/day cap:
the macro/ETF complex + the most liquid single names, weekly-sampled over ~2.5yr (since the
data starts Q4 2023). Expand later as the call budget allows.

Output: ~/autotrade_options_history/<SYMBOL>.jsonl  (one row per sampled date, the features() dict)
Run: python research/eodhd_backfill.py
"""
import sys, json, time, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import eodhd_options as E

OUT = Path.home() / "autotrade_options_history"
ETFS = ["SPY","QQQ","IWM","DIA","XLK","XLF","XLE","XLV","XLI","XLY","XLP","XLU","XLB","XLRE","XLC",
        "TLT","IEF","HYG","LQD","GLD","SLV","USO","GDX","UUP","VXX","SMH","SOXX","XBI","ARKK","KRE"]
LIQUID = ["AAPL","NVDA","MSFT","AMZN","META","TSLA","GOOGL","AMD","AVGO","NFLX","COIN","MU","PLTR",
          "MSTR","UBER","SHOP","CRM","BABA","JPM","BAC","XOM","WMT","DIS","INTC","SMCI","ARM","SNOW"]
SYMBOLS = ETFS + LIQUID


def fridays(start, end):
    d = start + dt.timedelta((4 - start.weekday()) % 7)   # first Friday on/after start
    while d <= end:
        yield d.isoformat(); d += dt.timedelta(days=7)


def done_dates(sym):
    f = OUT / f"{sym}.jsonl"
    if not f.exists(): return set()
    return {json.loads(l)["date"] for l in f.read_text().splitlines() if l.strip()}


def main():
    OUT.mkdir(exist_ok=True)
    start = dt.date(2023, 11, 1)
    end = dt.date.today() - dt.timedelta(days=2)
    dates = list(fridays(start, end))
    print(f"backfill: {len(SYMBOLS)} symbols x {len(dates)} weekly dates ({dates[0]}..{dates[-1]})")
    for i, sym in enumerate(SYMBOLS):
        have = done_dates(sym)
        todo = [d for d in dates if d not in have]
        if not todo:
            print(f"  [{i+1}/{len(SYMBOLS)}] {sym}: complete ({len(have)})"); continue
        n = 0
        with open(OUT / f"{sym}.jsonl", "a") as fh:
            for d in todo:
                try:
                    feat = E.features(sym, d)
                    if feat:
                        fh.write(json.dumps(feat) + "\n"); fh.flush(); n += 1
                except Exception as e:
                    print(f"    {sym} {d}: {str(e)[:60]}")
                time.sleep(0.3)            # gentle on the API
        print(f"  [{i+1}/{len(SYMBOLS)}] {sym}: +{n} dates (total {len(have)+n})")
    print("backfill pass complete ->", OUT)


if __name__ == "__main__":
    main()
