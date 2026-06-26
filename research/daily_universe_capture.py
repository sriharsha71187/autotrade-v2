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

# Index / sector / bond / commodity / dollar ETFs — tradable trend vehicles AND regime context.
# Their option IV is the perishable gauge of the macro complex: SPY=market fear, TLT=rate vol,
# HYG=credit fear, sector ETFs=rotation. (Treasury yield LEVELS ^TNX/^FVX/curve are an index,
# not Alpaca-tradable -> pulled from yfinance at ANALYSIS time as reconstructable features.)
MACRO_ETFS = ["SPY","QQQ","IWM","DIA","MDY","RSP","VTI",
              "XLK","XLF","XLE","XLV","XLI","XLY","XLP","XLU","XLB","XLRE","XLC",
              "TLT","IEF","SHY","HYG","LQD","TIP","AGG","BND",
              "GLD","SLV","USO","GDX","DBC","UUP","VXX",
              "SMH","SOXX","XBI","ARKK","KRE","ITB","JETS"]


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
    stocks = sorted(s for s in syms if s and 1 <= len(s) <= 6 and s not in MACRO_ETFS)
    # ETFs FIRST so rate-limited social/options coverage never starves the macro/regime complex
    return (MACRO_ETFS + stocks) if (stocks or MACRO_ETFS) else FALLBACK


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
        "ex_div": _iso(g("exDividendDate")),          # price drops by div; early-assignment risk (stops)
        "div_yield": g("dividendYield"),
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


def _r(x):
    return round(float(x), 4) if isinstance(x, (int, float)) and pd.notna(x) else None


def social_attention(sym):
    """Retail attention + sentiment (StockTwits) — perishable, not reconstructable. watchlist_count
    is the attention level; bull/bear of recent messages is crowd sentiment. Best-effort (rate-limited)."""
    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{sym}.json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=12).read())
        msgs = d.get("messages", [])
        bull = sum(1 for m in msgs if (m.get("entities", {}).get("sentiment") or {}).get("basic") == "Bullish")
        bear = sum(1 for m in msgs if (m.get("entities", {}).get("sentiment") or {}).get("basic") == "Bearish")
        return {"watchlist_count": d.get("symbol", {}).get("watchlist_count"),
                "msgs": len(msgs), "bull": bull, "bear": bear,
                "bull_ratio": _r(bull / (bull + bear)) if (bull + bear) else None}
    except Exception:
        return {}


def options_snapshot(tk, spot):
    """PERISHABLE, not free-reconstructable: implied-vol level/skew + option positioning.
    Required for any options strategy or vol-based signal. Nearest expiry >= ~20 DTE."""
    try:
        exps = tk.options
        if not exps or not spot:
            return {}
        tgt = next((e for e in exps if (dt.date.fromisoformat(e) - dt.date.today()).days >= 20), exps[-1])
        oc = tk.option_chain(tgt); c, p = oc.calls, oc.puts
        if c.empty or p.empty:
            return {}
        def atm(df):
            return float(df.loc[(df.strike - spot).abs().idxmin(), "impliedVolatility"])
        def otm(df, k):
            sub = df[(df.strike - k).abs() < spot * 0.05]
            return float(sub["impliedVolatility"].mean()) if len(sub) else None
        pput, ccall = otm(p, spot * 0.9), otm(c, spot * 1.1)
        coi, poi = int(c.openInterest.fillna(0).sum()), int(p.openInterest.fillna(0).sum())
        cvol, pvol = int(c.volume.fillna(0).sum()), int(p.volume.fillna(0).sum())
        def spread(df):  # ATM relative bid/ask spread — option tradability for entry/stop fills
            row = df.loc[(df.strike - spot).abs().idxmin()]
            b, a = row.get("bid"), row.get("ask"); mid = (b + a) / 2 if b and a else None
            return (a - b) / mid if mid and mid > 0 else None
        sp = [x for x in (spread(c), spread(p)) if x is not None]
        atm_iv = _r((atm(c) + atm(p)) / 2)
        em = _r(atm_iv * (((dt.date.fromisoformat(tgt) - dt.date.today()).days / 365) ** 0.5)) if atm_iv else None
        return {"expiry": tgt, "dte": (dt.date.fromisoformat(tgt) - dt.date.today()).days,
                "atm_iv": atm_iv, "put_iv_otm": _r(pput), "call_iv_otm": _r(ccall),
                "skew": _r(pput - ccall) if pput is not None and ccall is not None else None,
                "expected_move_pct": em,                          # straddle-implied move to the expiry
                "atm_spread_pct": _r(sum(sp) / len(sp)) if sp else None,  # fill realism for entry/stops
                "pc_oi": _r(poi / max(coi, 1)), "pc_vol": _r(pvol / max(cvol, 1)),
                "total_oi": coi + poi, "total_vol": cvol + pvol}
    except Exception:
        return {}


def estimate_dispersion(tk):
    """Analyst disagreement on next-quarter EPS — perishable (revises over time)."""
    try:
        ee = tk.get_earnings_estimate()
        if ee is None or ee.empty:
            return {}
        r = ee.iloc[0]; avg = float(r["avg"])
        return {"eps_est_avg": _r(avg), "eps_est_low": _r(r["low"]), "eps_est_high": _r(r["high"]),
                "eps_dispersion": _r((float(r["high"]) - float(r["low"])) / abs(avg)) if avg else None,
                "n_eps_analysts": int(r["numberOfAnalysts"]) if pd.notna(r["numberOfAnalysts"]) else None}
    except Exception:
        return {}


def insider_summary(tk):
    """Recent insider buy/sell activity — perishable signal."""
    try:
        ins = tk.insider_transactions
        if ins is None or ins.empty or "Text" not in ins:
            return {}
        r = ins.head(25); t = r["Text"].astype(str)
        buys = r[t.str.contains("Buy|Purchase", case=False, na=False)]
        sells = r[t.str.contains("Sale|Sell", case=False, na=False)]
        nv = (buys["Value"].fillna(0).sum() if "Value" in buys else 0) - \
             (sells["Value"].fillna(0).sum() if "Value" in sells else 0)
        return {"insider_buys": len(buys), "insider_sells": len(sells), "insider_net_value": int(nv)}
    except Exception:
        return {}


def main():
    import yfinance as yf
    args = sys.argv[1:]
    limit = int(args[args.index("--limit")+1]) if "--limit" in args else None
    day = args[args.index("--date")+1] if "--date" in args else dt.date.today().isoformat()
    syms = universe()
    # Keep the macro/ETF complex pinned at the front (always captured); rotate only the stock
    # tail by day so the rate-limited social/options pass covers a fair slice each night.
    ne = len(MACRO_ETFS)
    if not limit and len(syms) > ne:
        tail = syms[ne:]
        k = dt.date.fromisoformat(day).timetuple().tm_yday % max(len(tail), 1)
        syms = syms[:ne] + tail[k:] + tail[:k]
    if limit: syms = syms[:limit]
    print(f"Capturing perishable layer for {len(syms)} names · {day} …")
    anchor = anchor_prices(syms)
    OUT.mkdir(exist_ok=True)
    rows, with_news, with_analyst, with_opts, with_social = [], 0, 0, 0, 0
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
        opts = options_snapshot(tk, a.get("last")) if info else {}
        disp = estimate_dispersion(tk) if info else {}
        ins = insider_summary(tk) if info else {}
        soc = social_attention(s)                     # StockTwits — independent of yfinance
        nh = news_headlines(tk) if info else []
        if nh: with_news += 1
        if opts.get("atm_iv"): with_opts += 1
        if soc.get("watchlist_count"): with_social += 1
        if per.get("analyst", {}).get("n_analysts"): with_analyst += 1
        rows.append({"date": day, "symbol": s, **a, **per, "earnings": earn, "options": opts,
                     "dispersion": disp, "insider": ins, "social": soc, "news": nh})
        if i % 20 == 19: time.sleep(1)   # be gentle on yfinance
    (OUT / f"{day}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    print(f"wrote {OUT/(day+'.jsonl')}  ·  {len(rows)} names · {with_analyst} w/ analyst · {with_opts} w/ options · {with_social} w/ social · {with_news} w/ news")
    print("Perishable point-in-time snapshot stored (technicals recomputed from Alpaca at analysis time).")


if __name__ == "__main__":
    main()
