#!/usr/bin/env python3
"""Backfill historical NEWS features from Alpaca (free, ~10yr) into a weekly panel we can join
to the options/technical discovery panel. Per symbol per week: news_count (attention) and a
crude lexicon sentiment (real sentiment = LLM Tier-1 later). Scoped to the options-covered
universe so `options x news` is testable on the same names/dates.

Output: ~/autotrade_news_history/<SYMBOL>.jsonl  (one row per week: date(Fri), n_news, sentiment)
Run: python research/news_backfill.py
"""
import json, datetime as dt
from pathlib import Path
import pandas as pd
import warnings; warnings.filterwarnings("ignore")

ENV = Path.home() / ".autotrade.env"
OPT = Path.home() / "autotrade_options_history"
OUT = Path.home() / "autotrade_news_history"
POS = set("beat beats surge surges jump jumps soar rally gain gains rise rises top tops upgrade "
          "raised boost strong record high growth profit wins win approval breakthrough outperform "
          "bullish buy momentum expands expand".split())
NEG = set("miss misses fall falls drop drops plunge plunges sink slump cut cuts downgrade lowered "
          "weak loss losses warns warning probe lawsuit recall decline declines bearish sell "
          "investigation halt slashes layoffs".split())


def _keys():
    k = {}
    for ln in ENV.read_text().splitlines():
        if "=" in ln and not ln.startswith("#"):
            a, _, b = ln.partition("="); k[a.strip()] = b.strip().strip('"').strip("'")
    return k.get("ALPACA_API_KEY"), k.get("ALPACA_SECRET_KEY")


def sentiment(text):
    w = set(text.lower().replace(",", " ").replace(".", " ").split())
    p, n = len(w & POS), len(w & NEG)
    return (p - n) / (p + n) if (p + n) else 0.0


def main():
    from alpaca.data.historical.news import NewsClient
    from alpaca.data.requests import NewsRequest
    nc = NewsClient(*_keys())
    syms = sorted(f.stem for f in OPT.glob("*.jsonl"))     # options-covered universe
    start = dt.datetime(2024, 9, 1)
    OUT.mkdir(exist_ok=True)
    print(f"news backfill: {len(syms)} symbols since {start.date()}")
    for i, s in enumerate(syms):
        if (OUT / f"{s}.jsonl").exists():
            print(f"  [{i+1}/{len(syms)}] {s}: cached"); continue
        # NewsSet exposes no page token -> fetch in disjoint bi-weekly windows (each rarely >50)
        arts = []
        try:
            w0, now = start, dt.datetime.now()
            while w0 < now:
                w1 = w0 + dt.timedelta(days=14)
                r = nc.get_news(NewsRequest(symbols=s, start=w0, end=w1, limit=50))
                arts += r.data.get("news", []) if hasattr(r, "data") else r.news
                w0 = w1
        except Exception as e:
            print(f"  {s}: {str(e)[:60]}"); continue
        if not arts:
            (OUT / f"{s}.jsonl").write_text(""); continue
        df = pd.DataFrame([{"ts": a.created_at, "txt": (a.headline or "") + " " + (a.summary or "")} for a in arts])
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_localize(None)
        df["sent"] = df["txt"].map(sentiment)
        df["wk"] = df["ts"].dt.to_period("W-FRI").dt.end_time.dt.normalize()
        g = df.groupby("wk").agg(n_news=("sent", "size"), sentiment=("sent", "mean")).reset_index()
        with open(OUT / f"{s}.jsonl", "w") as fh:
            for _, row in g.iterrows():
                fh.write(json.dumps({"date": row["wk"].date().isoformat(),
                                     "n_news": int(row["n_news"]), "sentiment": round(row["sentiment"], 3)}) + "\n")
        print(f"  [{i+1}/{len(syms)}] {s}: {len(arts)} articles -> {len(g)} weeks")
    print("news backfill done ->", OUT)


if __name__ == "__main__":
    main()
