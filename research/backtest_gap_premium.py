#!/usr/bin/env python3
"""Gap-conditioned daily defined-risk options on SPY: can a gap/macro playbook find
a surviving cell where the UNCONDITIONAL daily premium trade already died?

Prior art in this repo: backtest_0dte.py — the unconditional daily condor wins
73-93% of days and still ruins (worst day -103% of margin). This study asks the
narrower question the unconditional test can't: does conditioning on the OPENING
GAP (known at entry) plus macro state (trend / VIX band — known the prior close)
isolate any cell where a specific structure has positive after-cost expectancy?

Setup (all entered at the OPEN, exited at the CLOSE, defined risk, T=1 session):
  structures per day, priced Black-Scholes at intraday realized vol x 1.10
  (backtest_0dte.py convention; short strikes 0.75 sigma-day OTM, wings 2 sigma):
    IC   iron condor (both sides)          — return on margin (width - credit)
    PCS  put credit spread (bullish)       — return on margin
    CCS  call credit spread (bearish)      — return on margin
    CDS  call debit spread ATM->+0.75s     — return on debit
    PDS  put debit spread ATM->-0.75s      — return on debit
  friction: 10% of gross credit/debit round trip (20% sensitivity column).

Conditioning (NO lookahead: gap uses today's open vs prior close; macro uses
prior-close values only):
  gap bucket : dn_big <=-0.5% | dn (-0.5,-0.15] | flat | up [0.15,0.5) | up_big >=0.5%
  trend      : prior close vs its 200dma
  VIX band   : <16 | 16-22 | 22-30 | >=30 (prior close)

Method: mine cells on 2007-2018 (IS), require N>=80 and |t|>=2.0 after friction,
then test ONLY the surviving rules on 2019-2026 (OOS). A rule that dies OOS is
mining noise — reported as such. Data: research/data/spy_vix_daily.csv
(SPY OHLC split-adjusted + VIX close, Robinhood MCP export 2007-2026; pass
--csv PATH to use another file with the same header).

Run: python3 research/backtest_gap_premium.py [--csv PATH]
Pure stdlib — no pandas/numpy needed.
"""
import csv, math, os, sys

FRICTIONS = [0.10, 0.20]      # of gross credit (credit legs) / debit (debit legs)
SHORT_S, WING_S = 0.75, 2.0   # strikes in units of sigma-day
IS_END = "2019-01-01"         # IS = 2007..2018, OOS = 2019..2026
MIN_N, MIN_T = 80, 2.0        # IS survival bar per cell-rule


def N(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def bs(S, K, T, sig, put=False):
    if T <= 0 or sig <= 0: return max((K - S) if put else (S - K), 0.0)
    d1 = (math.log(S / K) + (sig * sig / 2) * T) / (sig * math.sqrt(T)); d2 = d1 - sig * math.sqrt(T)
    return (K * N(-d2) - S * N(-d1)) if put else (S * N(d1) - K * N(d2))


def load(path):
    rows = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            rows.append({"d": r["date"], "o": float(r["spy_open"]), "c": float(r["spy_close"]),
                         "v": float(r["vix_close"])})
    return rows


def day_returns(rows):
    """Per-day: features (no lookahead) + after-friction return of each structure."""
    T = 1 / 252
    out = []
    for i in range(201, len(rows)):
        S, C = rows[i]["o"], rows[i]["c"]
        pc = rows[i - 1]["c"]
        # intraday realized vol, trailing 21d ending YESTERDAY (x1.10 VRP, 0dte convention)
        ocs = [math.log(rows[j]["c"] / rows[j]["o"]) for j in range(i - 21, i)]
        m = sum(ocs) / 21
        iv = math.sqrt(sum((x - m) ** 2 for x in ocs) / 20) * math.sqrt(252) * 1.10
        if iv <= 0: continue
        sd = iv * math.sqrt(T)
        gap = S / pc - 1
        ma200 = sum(r["c"] for r in rows[i - 200:i]) / 200
        vix = rows[i - 1]["v"]
        gb = ("dn_big" if gap <= -0.005 else "dn" if gap <= -0.0015 else
              "up_big" if gap >= 0.005 else "up" if gap >= 0.0015 else "flat")
        tr = "up" if pc > ma200 else "down"
        vb = ("calm" if vix < 16 else "mid" if vix < 22 else "high" if vix < 30 else "extreme")
        Kc, Kp = S * math.exp(SHORT_S * sd), S * math.exp(-SHORT_S * sd)
        Wc, Wp = S * math.exp(WING_S * sd), S * math.exp(-WING_S * sd)
        cc = bs(S, Kc, T, iv) - bs(S, Wc, T, iv)              # call-side credit
        pcred = bs(S, Kp, T, iv, True) - bs(S, Wp, T, iv, True)  # put-side credit
        wc, wp = Wc - Kc, Kp - Wp
        # debit spreads: long ATM, short the 0.75-sigma strike
        cds_cost = bs(S, S, T, iv) - bs(S, Kc, T, iv)
        pds_cost = bs(S, S, T, iv, True) - bs(S, Kp, T, iv, True)
        day = {"d": rows[i]["d"], "gap": gb, "tr": tr, "vb": vb, "structs": {}}
        for f in FRICTIONS:
            ic_credit = (cc + pcred) * (1 - f)
            ic_loss = min(max(C - Kc, 0) + max(Kp - C, 0), min(wc, wp))
            ic_m = min(wc, wp) - (cc + pcred)
            pcs_loss = min(max(Kp - C, 0), wp); pcs_m = wp - pcred
            ccs_loss = min(max(C - Kc, 0), wc); ccs_m = wc - cc
            cds_pay = min(max(C - S, 0), Kc - S); cds_c = cds_cost * (1 + f)
            pds_pay = min(max(S - C, 0), S - Kp); pds_c = pds_cost * (1 + f)
            day["structs"][f] = {
                "IC":  (ic_credit - ic_loss) / ic_m if ic_m > 0 else 0.0,
                "PCS": (pcred * (1 - f) - pcs_loss) / pcs_m if pcs_m > 0 else 0.0,
                "CCS": (cc * (1 - f) - ccs_loss) / ccs_m if ccs_m > 0 else 0.0,
                "CDS": (cds_pay - cds_c) / cds_c if cds_c > 0 else 0.0,
                "PDS": (pds_pay - pds_c) / pds_c if pds_c > 0 else 0.0,
            }
        out.append(day)
    return out


def agg(days, f, key=None):
    """{(cell, struct): [returns]} for friction f; cell = key(day) or coarse default."""
    key = key or (lambda d: (d["gap"], d["tr"], d["vb"]))
    cells = {}
    for d in days:
        for s, r in d["structs"][f].items():
            cells.setdefault((key(d), s), []).append(r)
    return cells


def tstat(rs):
    n = len(rs)
    if n < 2: return 0.0
    m = sum(rs) / n
    sdev = math.sqrt(sum((x - m) ** 2 for x in rs) / (n - 1))
    return m / (sdev / math.sqrt(n)) if sdev > 0 else 0.0


def fmt(rs):
    n = len(rs); m = sum(rs) / n
    win = sum(1 for r in rs if r > 0) / n
    return f"N={n:4d} avg={m*100:+6.2f}% t={tstat(rs):+5.1f} win={win*100:3.0f}% worst={min(rs)*100:+5.0f}%"


def main():
    path = "research/data/spy_vix_daily.csv"
    if "--csv" in sys.argv:
        path = sys.argv[sys.argv.index("--csv") + 1]
    if not os.path.exists(path):
        print(f"data file not found: {path} (pass --csv PATH)"); return
    rows = load(path)
    days = day_returns(rows)
    is_days = [d for d in days if d["d"] < IS_END]
    oos_days = [d for d in days if d["d"] >= IS_END]
    print(f"\n=== gap+macro conditioned daily SPY structures · {rows[0]['d']}..{rows[-1]['d']} · "
          f"IS {len(is_days)}d / OOS {len(oos_days)}d ===")
    print("  returns are per-trade on the structure's own margin (credit) / debit, AFTER friction\n")

    # 0) unconditional baselines at 10% friction (the already-known result, for anchoring)
    print("-- unconditional baselines (10% friction) --")
    for s in ["IC", "PCS", "CCS", "CDS", "PDS"]:
        rs = [d["structs"][0.10][s] for d in days]
        print(f"  {s:4} every day          {fmt(rs)}")

    # 1) mine IS cells at 10% friction
    print(f"\n-- IS (2007-2018) cells passing N>={MIN_N}, |t|>={MIN_T}, avg>0 (10% friction) --")
    cells = agg(is_days, 0.10)
    survivors = []
    for (cell, s), rs in sorted(cells.items()):
        if len(rs) >= MIN_N and tstat(rs) >= MIN_T and sum(rs) > 0:
            survivors.append((cell, s))
            print(f"  {s:4} gap={cell[0]:6} trend={cell[1]:4} vix={cell[2]:7} {fmt(rs)}")
    if not survivors:
        print("  (none)")

    # 2) OOS test of survivors, both frictions
    print("\n-- OOS (2019-2026) test of IS survivors --")
    for f in FRICTIONS:
        oc = agg(oos_days, f)
        for (cell, s) in survivors:
            rs = oc.get((cell, s), [])
            tag = "SURVIVES" if rs and sum(rs) > 0 and tstat(rs) >= 1.0 else "DIES"
            print(f"  fric={f:.0%} {s:4} gap={cell[0]:6} trend={cell[1]:4} vix={cell[2]:7} "
                  f"{fmt(rs) if rs else 'N=0'}  -> {tag}")
    if not survivors:
        print("  (nothing to test)")

    # 3) coarse single-factor views (context, 10% friction, FULL sample): gap-only and vix-only
    print("\n-- context: gap-bucket only (full sample, 10% friction) --")
    gc = agg(days, 0.10, key=lambda d: d["gap"])
    for (cell, s), rs in sorted(gc.items()):
        if s in ("IC", "PCS", "CCS") and len(rs) >= 100:
            print(f"  {s:4} gap={cell:6} {fmt(rs)}")
    # 4) deep-dive: the headline rule, sized like an account would actually trade it.
    #    RULE: sell the IC ONLY when gap is flat (|gap|<0.15%), prior close > 200dma,
    #    prior VIX < 16. Margin at risk = 10% of account on trade days, cash otherwise.
    #    ANTI-RULE (from the context table): NEVER sell premium on a >=0.5% gap-down.
    print("\n-- deep-dive: IC on flat-gap + uptrend + calm-VIX, 10% of account/day --")
    for f in FRICTIONS:
        eq, peak, dd, worst, wins, n = 1.0, 1.0, 0.0, 0.0, 0, 0
        by_year = {}
        for d in days:
            if (d["gap"], d["tr"], d["vb"]) != ("flat", "up", "calm"): continue
            r = d["structs"][f]["IC"] * 0.10
            n += 1; wins += r > 0; worst = min(worst, r)
            eq *= (1 + r); peak = max(peak, eq); dd = min(dd, eq / peak - 1)
            y = d["d"][:4]; by_year[y] = by_year.get(y, 1.0) * (1 + r)
        yrs = len({d['d'][:4] for d in days})
        cagr = eq ** (1 / yrs) - 1
        print(f"  fric={f:.0%}: {n} trades/{yrs}y (~{n/yrs:.0f}/yr) · total {eq:8.2f}x · "
              f"CAGR {cagr*100:+.1f}% · maxDD {dd*100:.1f}% · worst day {worst*100:+.1f}% · "
              f"win {wins/n*100:.0f}%")
        ys = sorted(by_year)
        line = "   " + " ".join(f"{y[2:]}:{(by_year[y]-1)*100:+.0f}%" for y in ys)
        print(line)

    print("\n  Verdict bar: a rule is deployable ONLY if it survives OOS at 20% friction")
    print("  with t>=1 — and even then it inherits the premium-selling crash factor the")
    print("  repo has already documented (BLOG.md: 0DTE income). Multiple-comparison")
    print("  caveat: ~120 cells were mined; expect ~3 false positives at t=2 by chance.")


if __name__ == "__main__":
    main()
