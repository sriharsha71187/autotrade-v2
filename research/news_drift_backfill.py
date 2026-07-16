#!/usr/bin/env python3
"""Tier0 news-drift backfill: per-ARTICLE daily headlines (Alpaca/Benzinga, ~10yr free) for a
stratified sample of the LESS-EFFICIENT slice of the panel (below 60th pct dollar-volume,
every 3rd name by rank -> ~190 symbols). Raw headlines stored; scoring happens at study time.
Output: ~/autotrade_news_drift/<SYMBOL>.jsonl  rows: {"d": "YYYY-MM-DD", "h": headline}
Resume-safe (skips symbols with a final file). ~22k requests, throttled under 200/min => ~2h.
Run: ~/autotrade/venv/bin/python3 research/news_drift_backfill.py
"""
import json, time, datetime as dt, pickle
from pathlib import Path
import warnings; warnings.filterwarnings("ignore")

ENV = Path.home() / ".autotrade.env"
OUT = Path.home() / "autotrade_news_drift"
CACHE = Path(__file__).resolve().parent / ".ohlcv_cache.pkl"
START = dt.datetime(2016, 6, 1)
SLEEP = 0.42                       # per-worker; 2 sharded workers stay ~150 req/min combined


def _keys():
    k = {}
    for ln in ENV.read_text().splitlines():
        if "=" in ln and not ln.startswith("#"):
            a, _, b = ln.partition("="); k[a.strip()] = b.strip().strip('"').strip("'")
    return k["ALPACA_API_KEY"], k["ALPACA_SECRET_KEY"]


def tier0_universe():
    d = pickle.load(open(CACHE, "rb")); C, V = d["C"], d["V"]
    dv = (C*V).rolling(63).mean().iloc[-252:].median().dropna()
    q = dv.rank(pct=True)
    small = sorted(q[q < 0.60].index, key=lambda s: q[s])
    return small[::3]              # stratified every-3rd by rank


def fetch_window(nc, NewsRequest, sym, w0, w1, depth=0):
    """one window; recursively split if the 50-article cap is hit (truncation guard)."""
    time.sleep(SLEEP)
    try:
        r = nc.get_news(NewsRequest(symbols=sym, start=w0, end=w1, limit=50,
                                    include_content=False))
        arts = r.data.get("news", []) if hasattr(r, "data") else r.news
    except Exception as e:
        if depth == 0:
            time.sleep(5)
            return fetch_window(nc, NewsRequest, sym, w0, w1, depth=1)
        print(f"    {sym} {w0.date()}: {e}"); return []
    if len(arts) >= 50 and (w1-w0).days > 2:
        mid = w0 + (w1-w0)/2
        return (fetch_window(nc, NewsRequest, sym, w0, mid, depth) +
                fetch_window(nc, NewsRequest, sym, mid, w1, depth))
    return arts


def main():
    import sys
    from alpaca.data.historical.news import NewsClient
    from alpaca.data.requests import NewsRequest
    nc = NewsClient(*_keys())
    syms = tier0_universe()
    if len(sys.argv) > 1:                      # shard arg "k/n"
        k, n = map(int, sys.argv[1].split("/"))
        syms = [s for i, s in enumerate(syms) if i % n == k]
    OUT.mkdir(exist_ok=True)
    todo = [s for s in syms if not (OUT/f"{s}.jsonl").exists()]
    print(f"tier0 news backfill: {len(syms)} symbols, {len(todo)} to fetch, since {START.date()}", flush=True)
    for i, s in enumerate(todo):
        rows = []
        w0, now = START, dt.datetime.now()
        while w0 < now:
            w1 = min(w0 + dt.timedelta(days=30), now)
            for a in fetch_window(nc, NewsRequest, s, w0, w1):
                ts = getattr(a, "created_at", None)
                hl = getattr(a, "headline", "") or ""
                if ts and hl:
                    rows.append({"d": str(ts.date()), "h": hl[:220]})
            w0 = w1
        tmp = OUT/f"{s}.jsonl.tmp"
        with open(tmp, "w") as f:
            for r in rows: f.write(json.dumps(r)+"\n")
        tmp.rename(OUT/f"{s}.jsonl")
        print(f"  [{i+1}/{len(todo)}] {s}: {len(rows)} headlines", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
