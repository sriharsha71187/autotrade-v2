#!/usr/bin/env python3
"""Institutional-activity history from SEC EDGAR 13D/13G filings (free, point-in-time). When a
fund crosses or changes a >=5% stake it files SC 13D / 13G under the COMPANY's CIK, so we get it
from the submissions feed cheaply (one fetch/name, no XML parse). This is a PROXY for big-holder
activity — NOT full 13F ownership-change (that needs bulk 13F + a CUSIP map, a heavier build).
Per symbol per week: n_13d (activist-ish), n_13g (passive), n_inst (total). Resumable.

Output: ~/autotrade_institutional_history/<SYMBOL>.jsonl
Run: python research/inst_backfill.py [--full] [SYM ...]
"""
import sys, json
from pathlib import Path
import pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import insider_backfill as I                      # reuse _get, cik_map, START

OPT = Path.home() / "autotrade_options_history"
OUT = Path.home() / "autotrade_institutional_history"


def backfill_symbol(cik):
    sub = I._get(f"https://data.sec.gov/submissions/CIK{cik}.json", json_=True)
    if not sub: return None
    rec = sub.get("filings", {}).get("recent", {})
    rows = []
    for form, fd in zip(rec.get("form", []), rec.get("filingDate", [])):
        if fd < I.START: continue
        f = form.upper()
        if f.startswith("SC 13D"): rows.append((fd, 1, 0))
        elif f.startswith("SC 13G"): rows.append((fd, 0, 1))
    if not rows: return []
    df = pd.DataFrame(rows, columns=["date", "d", "g"])
    df["date"] = pd.to_datetime(df["date"])
    df["wk"] = df["date"].dt.to_period("W-FRI").dt.end_time.dt.normalize()
    g = df.groupby("wk").agg(n_13d=("d", "sum"), n_13g=("g", "sum")).reset_index()
    return [{"date": r["wk"].date().isoformat(), "n_13d": int(r["n_13d"]), "n_13g": int(r["n_13g"]),
             "n_inst": int(r["n_13d"] + r["n_13g"])} for _, r in g.iterrows()]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args: syms = args
    elif "--full" in sys.argv:
        import daily_universe_capture as c; syms = c.universe()
    else: syms = sorted(f.stem for f in OPT.glob("*.jsonl"))
    cm = I.cik_map(); OUT.mkdir(exist_ok=True)
    print(f"institutional (13D/G) backfill: {len(syms)} symbols")
    for i, s in enumerate(syms):
        if (OUT / f"{s}.jsonl").exists(): continue
        cik = cm.get(s.upper())
        if not cik:
            (OUT / f"{s}.jsonl").write_text(""); continue
        rows = backfill_symbol(cik)
        if rows is None:
            print(f"  {s}: fail"); continue
        (OUT / f"{s}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""))
        if i % 10 == 0: print(f"  [{i+1}/{len(syms)}] {s}: {len(rows)} weeks")
    print("institutional backfill done ->", OUT)


if __name__ == "__main__":
    main()
