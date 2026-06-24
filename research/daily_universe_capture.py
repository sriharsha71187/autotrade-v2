#!/usr/bin/env python3
"""Nightly POINT-IN-TIME capture of a BROAD universe — the substrate for testing whether
an attractive multi-day ENTRY (technical location + a forward thesis from fundamentals &
news) predicts 1d/3d/5d forward returns.

Why this exists: the live bot only ever records names that are MOVING TODAY (its intraday
scan). So we can't test "quiet name at attractive multi-week support, with a fundamental/
news thesis, pays off over days" — those names were never captured. This walks a broad
liquid universe (S&P 500) every night and snapshots, per name, AS OF TONIGHT:
  - technical entry-location  (pullback off highs, dist to 50/200DMA, daily RSI, range pos)
  - fundamentals              (PE/fwdPE/PEG/PB, rev & eps growth, margin, ROE, sector)
  - recent news headlines     (point-in-time — captured today, so honest for forward tests)

Because we capture today, the news/fundamentals are genuinely point-in-time going forward
(the gap the project has never had). A separate maturing step joins fwd_1d/3d/5d later.

Output: ~/autotrade_universe_capture/<YYYY-MM-DD>.jsonl   (one row per name)
Run:    python research/daily_universe_capture.py [--limit N] [--date YYYY-MM-DD]
"""
import os, sys, json, time, csv, io, urllib.request, datetime as dt
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")

ENV  = Path.home() / ".autotrade.env"
OUT  = Path.home() / "autotrade_universe_capture"
FCACHE = Path.home() / ".universe_fundamentals.json"   # slow-moving; refresh weekly
FUND_TTL_DAYS = 7
SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
FALLBACK = ["AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO","AMD","NFLX","CRM","ADBE",
            "QCOM","INTC","MU","AMAT","LRCX","KLAC","ASML","PANW","CRWD","SNOW","UBER","COIN"]


def _keys():
    k = {}
    for ln in ENV.read_text().splitlines():
        if "=" in ln and not ln.startswith("#"):
            a, _, b = ln.partition("="); k[a.strip()] = b.strip().strip('"').strip("'")
    return k.get("ALPACA_API_KEY"), k.get("ALPACA_SECRET_KEY")


def universe():
    try:
        req = urllib.request.Request(SP500_CSV, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
        rows = list(csv.DictReader(io.StringIO(data)))
        syms = [r["Symbol"].strip().replace(".", "-") for r in rows if r.get("Symbol")]
        return syms or FALLBACK
    except Exception as e:
        print("universe fetch failed -> fallback:", e); return FALLBACK


def daily_bars(symbols):
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    cl = StockHistoricalDataClient(*_keys())
    out = {}
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        try:
            df = cl.get_stock_bars(StockBarsRequest(symbol_or_symbols=chunk,
                  timeframe=TimeFrame.Day, start=pd.Timestamp.now() - pd.Timedelta(days=400))).df
        except Exception as e:
            print("  bars fetch failed:", e); continue
        if df is None or df.empty: continue
        for s in chunk:
            try: out[s] = df.loc[s].copy()
            except KeyError: pass
    return out


def _rsi(c, n=14):
    d = c.diff(); up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).iloc[-1]


def technicals(b):
    if b is None or len(b) < 60: return None
    c, h, lo = b["close"], b["high"], b["low"]; last = float(c.iloc[-1])
    hi52, lo52 = float(h.iloc[-252:].max()), float(lo.iloc[-252:].min())
    return {
        "last": round(last, 2),
        "pullback_20dhigh": round(last / float(h.iloc[-20:].max()) - 1, 4),
        "dist_50dma":  round(last / float(c.iloc[-50:].mean()) - 1, 4),
        "dist_200dma": round(last / float(c.iloc[-200:].mean()) - 1, 4) if len(c) >= 200 else None,
        "rsi14": round(float(_rsi(c)), 1),
        "ret_5d":  round(last / float(c.iloc[-6]) - 1, 4) if len(c) >= 6 else None,
        "ret_20d": round(last / float(c.iloc[-21]) - 1, 4) if len(c) >= 21 else None,
        "ret_60d": round(last / float(c.iloc[-61]) - 1, 4) if len(c) >= 61 else None,
        "pct_in_52w_range": round((last - lo52) / (hi52 - lo52), 3) if hi52 > lo52 else None,
        "dist_52w_high": round(last / hi52 - 1, 4) if hi52 else None,
    }


def fundamentals(symbols):
    """yfinance .info, cached; only (re)fetch names missing or older than FUND_TTL_DAYS."""
    import yfinance as yf
    cache = {}
    if FCACHE.exists():
        try: cache = json.loads(FCACHE.read_text())
        except Exception: cache = {}
    today = dt.date.today().isoformat()
    def stale(s):
        e = cache.get(s)
        if not e: return True
        try: return (dt.date.fromisoformat(today) - dt.date.fromisoformat(e["asof"])).days >= FUND_TTL_DAYS
        except Exception: return True
    todo = [s for s in symbols if stale(s)]
    print(f"  fundamentals: {len(symbols)-len(todo)} cached, fetching {len(todo)} …")
    for i, s in enumerate(todo):
        try:
            info = yf.Ticker(s).info
            cache[s] = {"asof": today,
                "trailingPE": info.get("trailingPE"), "forwardPE": info.get("forwardPE"),
                "peg": info.get("trailingPegRatio"), "priceToBook": info.get("priceToBook"),
                "revenueGrowth": info.get("revenueGrowth"), "earningsGrowth": info.get("earningsGrowth"),
                "profitMargins": info.get("profitMargins"), "roe": info.get("returnOnEquity"),
                "marketCap": info.get("marketCap"), "sector": info.get("sector"),
                "industry": info.get("industry")}
        except Exception:
            cache[s] = {"asof": today}
        if i % 25 == 24: time.sleep(1)   # be gentle
    FCACHE.write_text(json.dumps(cache))
    return cache


def news_headlines(s):
    import yfinance as yf
    try:
        out = []
        for n in (yf.Ticker(s).news or [])[:6]:
            con = n.get("content") if isinstance(n.get("content"), dict) else None
            if con:                                   # new yfinance schema
                out.append({"title": con.get("title"), "pub": (con.get("provider") or {}).get("displayName"),
                            "ts": con.get("pubDate")})
            elif n.get("title"):                      # old schema
                out.append({"title": n.get("title"), "pub": n.get("publisher"),
                            "ts": n.get("providerPublishTime")})
        return out
    except Exception:
        return []


def main():
    args = sys.argv[1:]
    limit = int(args[args.index("--limit")+1]) if "--limit" in args else None
    day = args[args.index("--date")+1] if "--date" in args else dt.date.today().isoformat()
    syms = universe()
    if limit: syms = syms[:limit]
    print(f"Capturing {len(syms)} names for {day} …")
    bars = daily_bars(syms)
    fund = fundamentals(syms)
    OUT.mkdir(exist_ok=True)
    rows, with_news = [], 0
    for i, s in enumerate(syms):
        tech = technicals(bars.get(s))
        if tech is None: continue
        nh = news_headlines(s)
        if nh: with_news += 1
        rows.append({"date": day, "symbol": s, **tech,
                     "fundamentals": fund.get(s, {}), "news": nh})
        if i % 25 == 24: time.sleep(1)
    (OUT / f"{day}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    print(f"wrote {OUT/(day+'.jsonl')}  ·  {len(rows)} names ·  {with_news} with news")
    print("Point-in-time snapshot stored. Forward returns (1d/3d/5d) join later as they mature.")


if __name__ == "__main__":
    main()
