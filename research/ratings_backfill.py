#!/usr/bin/env python3
"""Analyst rating-change history from yfinance upgrades_downgrades (free, back to ~2012).
Per symbol per week: net rating actions (upgrades-downgrades), counts, avg price target — a
perishable signal that's actually HISTORICAL. Scoped to the options/news universe so it joins
the cross-layer panel (extend to full universe later — it's free, just slower).

Output: ~/autotrade_ratings_history/<SYMBOL>.jsonl  (weekly: n_up, n_down, net, n_actions, avg_target)
Run: python research/ratings_backfill.py [--full]
"""
import sys, json
from pathlib import Path
import pandas as pd
import warnings; warnings.filterwarnings("ignore")

OPT = Path.home() / "autotrade_options_history"
OUT = Path.home() / "autotrade_ratings_history"
UP = {"up"}; DOWN = {"down"}


def features(ud):
    ud = ud.reset_index()
    ud["GradeDate"] = pd.to_datetime(ud["GradeDate"], utc=True).dt.tz_localize(None)
    ud = ud[ud["GradeDate"] >= "2016-01-01"]
    if ud.empty: return None
    act = ud["Action"].astype(str).str.lower()
    ud["up"] = act.isin(UP).astype(int)
    ud["down"] = act.isin(DOWN).astype(int)
    ud["tgt"] = pd.to_numeric(ud.get("currentPriceTarget"), errors="coerce")
    ud["wk"] = ud["GradeDate"].dt.to_period("W-FRI").dt.end_time.dt.normalize()
    g = ud.groupby("wk").agg(n_up=("up", "sum"), n_down=("down", "sum"),
                             n_actions=("up", "size"), avg_target=("tgt", "mean")).reset_index()
    g["net"] = g["n_up"] - g["n_down"]
    return g


def main():
    import yfinance as yf
    full = "--full" in sys.argv
    if full:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import daily_universe_capture as c
        syms = c.universe()
    else:
        syms = sorted(f.stem for f in OPT.glob("*.jsonl"))
    OUT.mkdir(exist_ok=True)
    print(f"ratings backfill: {len(syms)} symbols")
    for i, s in enumerate(syms):
        if (OUT / f"{s}.jsonl").exists(): continue
        try:
            ud = yf.Ticker(s).upgrades_downgrades
        except Exception as e:
            print(f"  {s}: {str(e)[:50]}"); continue
        if ud is None or len(ud) == 0:
            (OUT / f"{s}.jsonl").write_text(""); continue
        g = features(ud)
        if g is None:
            (OUT / f"{s}.jsonl").write_text(""); continue
        with open(OUT / f"{s}.jsonl", "w") as fh:
            for _, r in g.iterrows():
                fh.write(json.dumps({"date": r["wk"].date().isoformat(), "n_up": int(r["n_up"]),
                    "n_down": int(r["n_down"]), "net": int(r["net"]), "n_actions": int(r["n_actions"]),
                    "avg_target": round(float(r["avg_target"]), 2) if pd.notna(r["avg_target"]) else None}) + "\n")
        if i % 10 == 0: print(f"  [{i+1}/{len(syms)}] {s}: {len(g)} weeks")
    print("ratings backfill done ->", OUT)


if __name__ == "__main__":
    main()
