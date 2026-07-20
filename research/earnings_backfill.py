#!/usr/bin/env python3
"""Backfill historical earnings events (date, EPS estimate, reported, surprise%) per name
via yfinance get_earnings_dates — ~100 quarters/name back to ~2001. Resumable: skips names
already on disk. Feeds the PEAD event study (research/pead_study.py).

Output: ~/autotrade_earnings_history/<SYM>.jsonl  (one row per earnings event)
Run: python research/earnings_backfill.py
"""
import json, time, warnings
from pathlib import Path
warnings.filterwarnings("ignore")

OUT = Path.home() / "autotrade_earnings_history"
OUT.mkdir(exist_ok=True)


def universe():
    """Stocks = ratings-history names that are also in the price panel (drop ETFs)."""
    import pickle
    panel = pickle.load(open(Path.home() / ".trend_bars.pkl", "rb"))
    rated = {p.stem for p in (Path.home() / "autotrade_ratings_history").glob("*.jsonl")}
    return sorted(rated & set(panel.columns))


def fetch(sym):
    import yfinance as yf
    ed = yf.Ticker(sym).get_earnings_dates(limit=100)
    if ed is None or ed.empty:
        return []
    rows = []
    for ts, r in ed.iterrows():
        est, rep, sur = r.get("EPS Estimate"), r.get("Reported EPS"), r.get("Surprise(%)")
        if rep is None or (rep != rep):          # future / unreported quarters
            continue
        rows.append({
            "ts": ts.isoformat(),                 # tz-aware: 16:00 = AMC, 07:00 = BMO
            "est": None if est != est else float(est),
            "rep": float(rep),
            "surprise_pct": None if sur != sur else float(sur),
        })
    return rows


def main():
    syms = universe()
    todo = [s for s in syms if not (OUT / f"{s}.jsonl").exists()]
    print(f"universe {len(syms)}, todo {len(todo)}")
    for i, s in enumerate(todo):
        try:
            rows = fetch(s)
            (OUT / f"{s}.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
            if i % 25 == 0:
                print(f"[{i}/{len(todo)}] {s}: {len(rows)} events", flush=True)
        except Exception as e:
            print(f"{s} FAILED: {e}", flush=True)
        time.sleep(0.4)                           # stay under yahoo rate limits
    print("done")


if __name__ == "__main__":
    main()
