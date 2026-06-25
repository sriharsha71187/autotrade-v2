#!/usr/bin/env python3
"""Nightly POINT-IN-TIME capture of the PERISHABLE layer for a broad universe (S&P 500).

We deliberately capture ONLY what cannot be reconstructed later from price history:
technical features (RSI, MA distances, returns, range position, etc.) are pure functions of
the Alpaca bar series and are recomputed at analysis time — capturing them is dead weight.
What IS perishable — gone if we don't snapshot it tonight — is forward/analyst/sentiment data:

  - analyst:   mean/high/low price target, recommendation (mean + key), # analysts
  - estimates: forward & trailing EPS, fwd/trail PE, PEG, earnings & revenue growth
  - valuation: P/B, P/S, EV/EBITDA, margin, ROE      (semi-perishable; cheap to keep)
  - short:     shares short, short ratio, % of float, prior-month short, as-of date
  - float:     float shares, % insiders, % institutions
  - earnings:  next earnings date
  - ratings:   recent upgrade/downgrade actions (firm, from->to, price target) last 45d
  - news:      recent headlines + timestamps

Fetched FRESH every night (no cache) — the whole point is the time-series of these values
as analysts revise, short interest shifts, ratings change. Forward returns join separately.
`last`/`volume` are kept only as the return anchor.

Output: ~/autotrade_universe_capture/<YYYY-MM-DD>.jsonl   (one row per name)
Run:    python research/daily_universe_capture.py [--limit N] [--date YYYY-MM-DD]
"""
import sys, json, time, csv, io, urllib.request, datetime as dt
from pathlib import Path
import pandas as pd
import warnings; warnings.filterwarnings("ignore")

ENV = Path.home() / ".autotrade.env"
OUT = Path.home() / "autotrade_universe_capture"
SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
FALLBACK = ["AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO","AMD","NFLX","CRM","ADBE",
            "QCOM","INTC","MU","AMAT","LRCX","KLAC","ASML","PANW","CRWD","SNOW","UBER","COIN"]


def _keys():
    k = {}
    for ln in ENV.read_text().splitlines():
        if "=" in ln and not ln.startswith("#"):
            a, _, b = ln.partition("="); k[a.strip()] = b.strip().strip('"').strip("'")
    return k.get("ALPACA_API_KEY"), k.get("ALPACA_SECRET_KEY")


def _wiki_symbols(url):
    import pandas as pd
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    html = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
    for t in pd.read_html(io.StringIO(html)):
        for i, c in enumerate([str(x).lower() for x in t.columns]):
            if "symbol" in c or "ticker" in c:
                return [str(s).strip().replace(".", "-") for s in t[t.columns[i]].tolist()
                        if str(s).strip() and str(s) != "nan"]
    return []


def universe():
    # S&P 500 (large) + S&P 400 (MidCap). Research: surviving anomaly edge concentrates in
    # LESS-EFFICIENT names; mid-caps are the sweet spot (edge persists, still tradable). Small
    # -cap 600 deferred (worst data quality / least tradable). Union, deduped.
    syms = set()
    try:
        req = urllib.request.Request(SP500_CSV, headers={"User-Agent": "Mozilla/5.0"})
        rows = list(csv.DictReader(io.StringIO(urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore"))))
        syms.update(r["Symbol"].strip().replace(".", "-") for r in rows if r.get("Symbol"))
    except Exception as e:
        print("SP500 fetch failed:", e)
    try:
        syms.update(_wiki_symbols("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"))
    except Exception as e:
        print("SP400 fetch failed:", e)
    syms = sorted(s for s in syms if s and 1 <= len(s) <= 6)
    return syms or FALLBACK


def anchor_prices(symbols):
    """last close + volume from Alpaca — the return anchor (everything technical is recomputed
    from Alpaca at analysis time, so we deliberately don't store derived indicators here)."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    cl = StockHistoricalDataClient(*_keys())
    out = {}
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        try:
            df = cl.get_stock_bars(StockBarsRequest(symbol_or_symbols=chunk,
                  timeframe=TimeFrame.Day, start=pd.Timestamp.now() - pd.Timedelta(days=10))).df
        except Exception as e:
            print("  bars fetch failed:", e); continue
        if df is None or df.empty: continue
        for s in chunk:
            try:
                sub = df.loc[s]
                out[s] = {"last": round(float(sub["close"].iloc[-1]), 2),
                          "volume": int(sub["volume"].iloc[-1])}
            except (KeyError, IndexError): pass
    return out


def _iso(ts):
    try: return dt.datetime.utcfromtimestamp(int(ts)).date().isoformat()
    except Exception: return None


def earnings_surprise(tk):
    """Most recent REPORTED earnings: reported vs consensus EPS + surprise %, days since.
    This is the PEAD input — the strongest documented signal cluster (under-reaction drift)."""
    try:
        ed = tk.get_earnings_dates(limit=8)
        if ed is None or ed.empty:
            return {}
        rep = ed.dropna(subset=["Reported EPS"])
        if rep.empty:
            return {}
        d, row = rep.index[0], rep.iloc[0]
        days = (pd.Timestamp.now(tz="UTC") - d.tz_convert("UTC")).days
        return {"last_earnings": d.date().isoformat(),
                "eps_estimate": float(row["EPS Estimate"]) if pd.notna(row["EPS Estimate"]) else None,
                "reported_eps": float(row["Reported EPS"]),
                "surprise_pct": float(row["Surprise(%)"]) if pd.notna(row["Surprise(%)"]) else None,
                "days_since": int(days)}
    except Exception:
        return {}


def perishable(info):
    g = lambda k: info.get(k)
    out = {
        "analyst": {"target_mean": g("targetMeanPrice"), "target_high": g("targetHighPrice"),
                    "target_low": g("targetLowPrice"), "rec_mean": g("recommendationMean"),
                    "rec_key": g("recommendationKey"), "n_analysts": g("numberOfAnalystOpinions")},
        "estimates": {"fwd_eps": g("forwardEps"), "trail_eps": g("trailingEps"),
                      "fwd_pe": g("forwardPE"), "trail_pe": g("trailingPE"),
                      "peg": g("trailingPegRatio") or g("pegRatio"),
                      "earnings_growth": g("earningsGrowth"), "rev_growth": g("revenueGrowth"),
                      "eps_q_growth": g("earningsQuarterlyGrowth")},
        "valuation": {"pb": g("priceToBook"), "ps": g("priceToSalesTrailing12Months"),
                      "ev_ebitda": g("enterpriseToEbitda"), "margin": g("profitMargins"),
                      "roe": g("returnOnEquity")},
        "short": {"shares_short": g("sharesShort"), "short_ratio": g("shortRatio"),
                  "short_pct_float": g("shortPercentOfFloat"),
                  "shares_short_prior": g("sharesShortPriorMonth"),
                  "asof": _iso(g("dateShortInterest"))},
        "float": {"float_shares": g("floatShares"), "pct_insiders": g("heldPercentInsiders"),
                  "pct_institutions": g("heldPercentInstitutions")},
        "next_earnings": _iso(g("earningsTimestamp")),
        "market_cap": g("marketCap"), "sector": g("sector"), "industry": g("industry"),
    }
    # (rating CHANGES are derived from the nightly rec_mean / target_mean series, so we don't
    #  pay a separate upgrades/downgrades scrape per name — and the research ranks that edge tiny.)
    return out


def news_headlines(tk):
    try:
        out = []
        for n in (tk.news or [])[:6]:
            con = n.get("content") if isinstance(n.get("content"), dict) else None
            if con:
                out.append({"title": con.get("title"), "pub": (con.get("provider") or {}).get("displayName"),
                            "ts": con.get("pubDate")})
            elif n.get("title"):
                out.append({"title": n.get("title"), "pub": n.get("publisher"),
                            "ts": n.get("providerPublishTime")})
        return out
    except Exception:
        return []


def main():
    import yfinance as yf
    args = sys.argv[1:]
    limit = int(args[args.index("--limit")+1]) if "--limit" in args else None
    day = args[args.index("--date")+1] if "--date" in args else dt.date.today().isoformat()
    syms = universe()
    if limit: syms = syms[:limit]
    print(f"Capturing perishable layer for {len(syms)} names · {day} …")
    anchor = anchor_prices(syms)
    OUT.mkdir(exist_ok=True)
    rows, with_news, with_analyst = [], 0, 0
    for i, s in enumerate(syms):
        a = anchor.get(s)
        if not a:
            continue
        try:
            tk = yf.Ticker(s); info = tk.info
        except Exception:
            info = {}
        per = perishable(info) if info else {}
        earn = earnings_surprise(tk) if info else {}
        nh = news_headlines(tk) if info else []
        if nh: with_news += 1
        if per.get("analyst", {}).get("n_analysts"): with_analyst += 1
        rows.append({"date": day, "symbol": s, **a, **per, "earnings": earn, "news": nh})
        if i % 20 == 19: time.sleep(1)   # be gentle on yfinance
    (OUT / f"{day}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    print(f"wrote {OUT/(day+'.jsonl')}  ·  {len(rows)} names · {with_analyst} w/ analyst · {with_news} w/ news")
    print("Perishable point-in-time snapshot stored (technicals recomputed from Alpaca at analysis time).")


if __name__ == "__main__":
    main()
