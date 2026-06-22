#!/usr/bin/env python3
"""Candidate-outcomes backfiller — the Y for EVERY candidate, picked AND passed.

The bot already logs the full choice set per cycle (autotrade_snapshots/: scan = every
candidate + features + the decision). But compute_outcomes only records P&L for what we
TRADED. To prove selection skill / build a strategy you need the forward return of the
names we PASSED too. This reads the snapshots and computes each candidate's forward return
at several horizons from price history, emitting a flat X+Y dataset:

  autotrade_candidate_outcomes/<day>.jsonl  — one row per (cycle, candidate):
     ts, symbol, picked, action, mover_rank, <features>, fwd_30m/1h/2h/eod/1d/3d

With this you can finally answer: did the model's picks beat the names it passed (and a
random baseline), cost-adjusted? Which features predict forward return? — i.e. is there a
buildable signal, or just discipline. Run:  python research/backfill_outcomes.py [YYYY-MM-DD]
Backfillable: re-run as horizons mature (+1d/+3d fill in once those sessions close).
"""
import os, sys, json, datetime as dt
from pathlib import Path
import pandas as pd

ENV = Path.home() / ".autotrade.env"
SNAP = Path.home() / "autotrade_snapshots"
OUT = Path.home() / "autotrade_candidate_outcomes"
FEAT = ["day_pct", "vwap_ext", "off_hod", "off_lod", "rsi", "from_open", "signal", "last"]


def _keys():
    k = {}
    for ln in ENV.read_text().splitlines():
        if "=" in ln and not ln.startswith("#"):
            a, _, b = ln.partition("="); k[a.strip()] = b.strip().strip('"').strip("'")
    return k.get("ALPACA_API_KEY"), k.get("ALPACA_SECRET_KEY")


def _client():
    from alpaca.data.historical import StockHistoricalDataClient
    return StockHistoricalDataClient(*_keys())


def _bars(client, symbols, start, end, minute):
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    tf = TimeFrame.Minute if minute else TimeFrame.Day
    out = {}
    # chunk symbols so a request isn't enormous
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        try:
            df = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=chunk, timeframe=tf, start=start, end=end)).df
        except Exception as e:
            print("  bars fetch failed:", e); continue
        if df is None or df.empty:
            continue
        for s in chunk:
            try:
                sub = df.loc[s]["close"]
                sub.index = pd.to_datetime(sub.index, utc=True)
                out[s] = sub
            except KeyError:
                pass
    return out


def process_day(day):
    f = SNAP / f"{day}.jsonl"
    if not f.exists():
        print("no snapshot for", day); return []
    recs = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    obs, syms = [], set()
    for r in recs:
        scan = r.get("scan") or []
        dec = r.get("decision") or {}
        picked, act = dec.get("symbol"), dec.get("action")
        movers = sorted([c for c in scan if c.get("day_pct") is not None],
                        key=lambda c: abs(c.get("day_pct") or 0), reverse=True)
        rank = {c.get("symbol"): i + 1 for i, c in enumerate(movers)}
        for c in scan:
            s, last = c.get("symbol"), c.get("last")
            if not s or not last:
                continue
            syms.add(s)
            row = {"ts": r["t"], "day": day, "symbol": s, "mover_rank": rank.get(s),
                   "picked": int(s == picked and act not in (None, "hold", "abort")),
                   "action": act if s == picked else None}
            row.update({k: c.get(k) for k in FEAT})
            obs.append(row)
    if not obs:
        print(day, "no candidates"); return []
    d0 = dt.date.fromisoformat(day)
    cl = _client()
    minute = _bars(cl, list(syms), d0, d0 + dt.timedelta(days=1), minute=True)
    daily = _bars(cl, list(syms), d0, d0 + dt.timedelta(days=9), minute=False)

    for o in obs:
        ts = pd.Timestamp(o["ts"]).tz_convert("UTC")
        last = o["last"]
        m = minute.get(o["symbol"])
        def fwd_min(mins):
            if m is None: return None
            aft = m[m.index >= ts + pd.Timedelta(minutes=mins)]
            return round(float(aft.iloc[0]) / last - 1, 5) if len(aft) else None
        o["fwd_30m"] = fwd_min(30); o["fwd_1h"] = fwd_min(60); o["fwd_2h"] = fwd_min(120)
        o["fwd_eod"] = round(float(m.iloc[-1]) / last - 1, 5) if (m is not None and len(m)) else None
        d = daily.get(o["symbol"])
        if d is not None:
            fut = d[d.index.date > d0]
            o["fwd_1d"] = round(float(fut.iloc[0]) / last - 1, 5) if len(fut) >= 1 else None
            o["fwd_3d"] = round(float(fut.iloc[2]) / last - 1, 5) if len(fut) >= 3 else None
        else:
            o["fwd_1d"] = o["fwd_3d"] = None
    OUT.mkdir(exist_ok=True)
    (OUT / f"{day}.jsonl").write_text("\n".join(json.dumps(o) for o in obs) + "\n")
    return obs


def _summary(rows):
    df = pd.DataFrame(rows)
    if df.empty:
        return
    print(f"\n  {len(df)} candidate-observations · {int(df['picked'].sum())} picked · "
          f"{df['symbol'].nunique()} symbols")
    for h in ["fwd_30m", "fwd_1h", "fwd_eod", "fwd_1d", "fwd_3d"]:
        if h not in df: continue
        pk = df[df.picked == 1][h].dropna()
        ps = df[df.picked == 0][h].dropna()
        if len(pk) and len(ps):
            print(f"  {h:7}: PICKED mean {pk.mean()*100:+.2f}% (n={len(pk)})  vs  "
                  f"PASSED mean {ps.mean()*100:+.2f}% (n={len(ps)})  "
                  f"edge={ (pk.mean()-ps.mean())*100:+.2f}%")


if __name__ == "__main__":
    days = sys.argv[1:] or sorted(p.stem for p in SNAP.glob("*.jsonl"))
    allrows = []
    for day in days:
        print("processing", day, "...")
        allrows += process_day(day)
    print("wrote", OUT)
    _summary(allrows)
