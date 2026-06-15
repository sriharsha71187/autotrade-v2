#!/usr/bin/env python3
"""cf_eod.py — end-of-day counterfactual: would entering the momentum names the bot
PASSED ON at ~10:32 ET have beaten its actual (hold/COIN-only) result by the close?

Reads the entry baseline frozen midday (counterfactual_2026-06-15.json), re-runs the
bot's own signal_scan to get CLOSING prices (so 'last' = close, 'day_pct' = full-day),
and computes each candidate's entry->close return. Also pulls the intraday path from the
day's snapshots (max favorable / adverse excursion after entry) so a name that ended flat
but offered a real swing isn't misjudged. Pure read-only analysis; places no orders.

Run AFTER the close (>=16:05 ET):  ~/autotrade/venv/bin/python3 ~/autotrade/cf_eod.py
"""
import json, glob
from pathlib import Path
import autotrade as at, config as cfg

DAY = "2026-06-15"
base = json.loads((Path(cfg.OUTCOMES_DIR) / f"counterfactual_{DAY}.json").read_text())
cands = base["candidates"]
cap_et = base.get("captured_et")
max_day = getattr(cfg, "MOMENTUM_MAX_DAY_PCT", 25.0)

# --- closing prices via the bot's own scanner ---
tc = at.trading_client()
uni, _ = at.build_universe(tc)
# ensure every candidate symbol is in the scanned universe
syms = {c["symbol"] for c in cands}
scan = {r["symbol"]: r for r in at.signal_scan(list(syms | set(uni)))}

# --- intraday path from today's snapshots (day_pct per symbol after entry) ---
paths: dict = {s: [] for s in syms}
snap = Path(cfg.SNAPSHOT_DIR) / f"{DAY}.jsonl"
if snap.exists():
    for ln in snap.read_text().splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except Exception:
            continue
        t = rec.get("t") or ""
        if t <= base["captured_at"]:
            continue
        for r in rec.get("scan") or []:
            s = r.get("symbol")
            if s in paths and r.get("day_pct") is not None:
                paths[s].append(float(r["day_pct"]))

print(f"=== EOD counterfactual ({DAY}) — entry @ {cap_et} ===\n")
print(f"{'SYM':7s}{'entry':>10s}{'close':>10s}{'e→close%':>10s}{'MFE%':>7s}{'MAE%':>7s}  note")
rows = []
for c in cands:
    s = c["symbol"]; e = c["entry_price"]; den = c.get("day_pct_at_entry")
    r = scan.get(s)
    close = float(r["last"]) if r and r.get("last") else None
    ret = ((close - e) / e * 100) if (close and e) else None
    # intraday excursion in PRICE-% terms ≈ (day_pct_after - day_pct_at_entry)
    pth = paths.get(s) or []
    mfe = (max(pth) - den) if (pth and den is not None) else None  # max favorable
    mae = (min(pth) - den) if (pth and den is not None) else None  # max adverse
    excluded = (den is not None and den > max_day)
    note = "EXCLUDED (extreme mover, bot-filtered)" if excluded else ""
    rows.append({**c, "close": close, "ret": ret, "mfe": mfe, "mae": mae, "excluded": excluded})
    def f(x, w=10, p=2): return ("%*.*f" % (w, p, x)) if x is not None else (" " * (w - 1) + "—")
    print(f"{s:7s}{f(e)}{f(close)}{f(ret)}{(('%+6.1f'%mfe) if mfe is not None else '     —')}"
          f"{(('%+6.1f'%mae) if mae is not None else '     —')}  {note}")

fair = [x for x in rows if not x["excluded"] and x["ret"] is not None]
if fair:
    up = [x for x in fair if x["ret"] > 0.5]
    dn = [x for x in fair if x["ret"] < -0.5]
    avg = sum(x["ret"] for x in fair) / len(fair)
    best = max(fair, key=lambda x: x["ret"]); worst = min(fair, key=lambda x: x["ret"])
    print(f"\n--- VERDICT over {len(fair)} fair candidates (entry @ {cap_et} -> close) ---")
    print(f"  would've been GREEN (> +0.5%): {len(up)}/{len(fair)}   RED (< -0.5%): {len(dn)}/{len(fair)}")
    print(f"  avg entry→close return: {avg:+.2f}%")
    print(f"  best : {best['symbol']} {best['ret']:+.2f}%   worst: {worst['symbol']} {worst['ret']:+.2f}%")
    # a name that ran THEN gave it back (high MFE, low close) = the bot was right to be patient
    runners = [x for x in fair if x["mfe"] is not None and x["mfe"] >= 1.5 and (x["ret"] or 0) < x["mfe"] - 1.0]
    if runners:
        print(f"  ran-then-faded (MFE>=1.5% but closed lower): {', '.join(x['symbol'] for x in runners)}")

# --- bot's actual result for comparison ---
acct = tc.get_account()
day_pl = float(acct.equity) - float(acct.last_equity)
print(f"\n--- BOT ACTUAL ---")
print(f"  day P&L: {day_pl:+.2f}   equity {float(acct.equity):,.2f}")
print(f"  at capture, bot held: {[p['symbol'] for p in base['bot_actual_at_capture']['positions']]}")
print(f"  (compare: an equal-weight long of the fair candidates would have returned ~{(avg if fair else 0):+.2f}% on capital deployed)")
