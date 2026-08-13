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
    PDS  put debit spread ATM->-0.75s     — return on debit
  friction: 10% of gross credit/debit round trip (20% sensitivity column).

Conditioning (NO lookahead: gap uses today's open vs prior close; macro uses
prior-close values only):
  gap bucket : dn_big <=-0.5% | dn (-0.5,-0.15] | flat | up [0.15,0.5) | up_big >=0.5%
  trend      : prior close vs its 200dma
  VIX band   : <16 | 16-22 | 22-30 | >=30 (prior close)

Method: mine cells on 2007-2018 (IS), require N>=80 and |t|>=2.0 after friction,
then test ONLY the surviving rules on 2019-2026 (OOS). A rule that dies OOS is
mining noise — reported as such. Additional sections: a LOOSENING LADDER (relax
each gate of the surviving rule one at a time — how fast does the edge dilute as
trade count rises?) and an AGGRESSION grid (tighter short strikes / bigger sizing
on the strict signal — is "fewer but bigger" better?).

Data: research/data/spy_vix_daily.csv (SPY OHLC split-adjusted + VIX close,
Robinhood MCP export 2007-2026; pass --csv PATH for another file, same header).
Run: python3 research/backtest_gap_premium.py [--csv PATH]   (pure stdlib)
"""
import csv, math, os, sys

FRICTIONS = [0.10, 0.20]      # of gross credit (credit legs) / debit (debit legs)
SHORT_S, WING_S = 0.75, 2.0   # default strikes in units of sigma-day
IS_END = "2019-01-01"         # IS = 2007..2018, OOS = 2019..2026
MIN_N, MIN_T = 80, 2.0        # IS survival bar per cell-rule
T = 1 / 252


def N(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def bs(S, K, Tx, sig, put=False):
    if Tx <= 0 or sig <= 0: return max((K - S) if put else (S - K), 0.0)
    d1 = (math.log(S / K) + (sig * sig / 2) * Tx) / (sig * math.sqrt(Tx)); d2 = d1 - sig * math.sqrt(Tx)
    return (K * N(-d2) - S * N(-d1)) if put else (S * N(d1) - K * N(d2))


def load(path):
    rows = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            rows.append({"d": r["date"], "o": float(r["spy_open"]), "c": float(r["spy_close"]),
                         "v": float(r["vix_close"])})
    return rows


def build_days(rows):
    """Per-day raw state + features (no lookahead)."""
    out = []
    for i in range(201, len(rows)):
        S, C = rows[i]["o"], rows[i]["c"]
        pc = rows[i - 1]["c"]
        ocs = [math.log(rows[j]["c"] / rows[j]["o"]) for j in range(i - 21, i)]
        m = sum(ocs) / 21
        iv = math.sqrt(sum((x - m) ** 2 for x in ocs) / 20) * math.sqrt(252) * 1.10
        if iv <= 0: continue
        gap = S / pc - 1
        ma200 = sum(r["c"] for r in rows[i - 200:i]) / 200
        vix = rows[i - 1]["v"]
        gb = ("dn_big" if gap <= -0.005 else "dn" if gap <= -0.0015 else
              "up_big" if gap >= 0.005 else "up" if gap >= 0.0015 else "flat")
        out.append({"d": rows[i]["d"], "S": S, "C": C, "iv": iv, "gap_val": gap,
                    "vix": vix, "gap": gb, "tr": "up" if pc > ma200 else "down",
                    "vb": ("calm" if vix < 16 else "mid" if vix < 22 else
                           "high" if vix < 30 else "extreme")})
    return out


def struct_ret(day, name, f, short_s=SHORT_S, wing_s=WING_S):
    """After-friction return of one structure on one day (on margin / on debit)."""
    S, C, iv = day["S"], day["C"], day["iv"]
    sd = iv * math.sqrt(T)
    Kc, Kp = S * math.exp(short_s * sd), S * math.exp(-short_s * sd)
    Wc, Wp = S * math.exp(wing_s * sd), S * math.exp(-wing_s * sd)
    cc = bs(S, Kc, T, iv) - bs(S, Wc, T, iv)
    pcred = bs(S, Kp, T, iv, True) - bs(S, Wp, T, iv, True)
    wc, wp = Wc - Kc, Kp - Wp
    if name == "IC":
        marg = min(wc, wp) - (cc + pcred)
        loss = min(max(C - Kc, 0) + max(Kp - C, 0), min(wc, wp))
        return ((cc + pcred) * (1 - f) - loss) / marg if marg > 0 else 0.0
    if name == "PCS":
        marg = wp - pcred
        return (pcred * (1 - f) - min(max(Kp - C, 0), wp)) / marg if marg > 0 else 0.0
    if name == "CCS":
        marg = wc - cc
        return (cc * (1 - f) - min(max(C - Kc, 0), wc)) / marg if marg > 0 else 0.0
    if name == "CDS":
        cost = (bs(S, S, T, iv) - bs(S, Kc, T, iv)) * (1 + f)
        return (min(max(C - S, 0), Kc - S) - cost) / cost if cost > 0 else 0.0
    if name == "PDS":
        cost = (bs(S, S, T, iv, True) - bs(S, Kp, T, iv, True)) * (1 + f)
        return (min(max(S - C, 0), S - Kp) - cost) / cost if cost > 0 else 0.0
    raise ValueError(name)


def tstat(rs):
    n = len(rs)
    if n < 2: return 0.0
    m = sum(rs) / n
    sdev = math.sqrt(sum((x - m) ** 2 for x in rs) / (n - 1))
    return m / (sdev / math.sqrt(n)) if sdev > 0 else 0.0


def fmt(rs):
    if not rs: return "N=   0"
    n = len(rs); m = sum(rs) / n
    win = sum(1 for r in rs if r > 0) / n
    return f"N={n:4d} avg={m*100:+6.2f}% t={tstat(rs):+5.1f} win={win*100:3.0f}% worst={min(rs)*100:+5.0f}%"


def equity(days_sel, name, f, size, short_s=SHORT_S):
    """Compound equity path trading `name` on the selected days at `size` of account."""
    eq, peak, dd, worst, wins = 1.0, 1.0, 0.0, 0.0, 0
    for d in days_sel:
        r = struct_ret(d, name, f, short_s) * size
        wins += r > 0; worst = min(worst, r)
        eq *= (1 + r); peak = max(peak, eq); dd = min(dd, eq / peak - 1)
    return eq, dd, worst, (wins / len(days_sel) if days_sel else 0.0)


def main():
    path = "research/data/spy_vix_daily.csv"
    if "--csv" in sys.argv:
        path = sys.argv[sys.argv.index("--csv") + 1]
    if not os.path.exists(path):
        print(f"data file not found: {path} (pass --csv PATH)"); return
    rows = load(path)
    days = build_days(rows)
    is_days = [d for d in days if d["d"] < IS_END]
    oos_days = [d for d in days if d["d"] >= IS_END]
    yrs = len({d["d"][:4] for d in days})
    print(f"\n=== gap+macro conditioned daily SPY structures · {rows[0]['d']}..{rows[-1]['d']} · "
          f"IS {len(is_days)}d / OOS {len(oos_days)}d ===")
    print("  returns are per-trade on the structure's own margin (credit) / debit, AFTER friction\n")

    # 0) unconditional baselines at 10% friction
    print("-- unconditional baselines (10% friction) --")
    for s in ["IC", "PCS", "CCS", "CDS", "PDS"]:
        rs = [struct_ret(d, s, 0.10) for d in days]
        print(f"  {s:4} every day          {fmt(rs)}")

    # 1) mine IS cells at 10% friction
    print(f"\n-- IS (2007-2018) cells passing N>={MIN_N}, |t|>={MIN_T}, avg>0 (10% friction) --")
    cells = {}
    for d in is_days:
        for s in ["IC", "PCS", "CCS", "CDS", "PDS"]:
            cells.setdefault(((d["gap"], d["tr"], d["vb"]), s), []).append(struct_ret(d, s, 0.10))
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
        for (cell, s) in survivors:
            rs = [struct_ret(d, s, f) for d in oos_days
                  if (d["gap"], d["tr"], d["vb"]) == cell]
            tag = "SURVIVES" if rs and sum(rs) > 0 and tstat(rs) >= 1.0 else "DIES"
            print(f"  fric={f:.0%} {s:4} gap={cell[0]:6} trend={cell[1]:4} vix={cell[2]:7} "
                  f"{fmt(rs)}  -> {tag}")

    # 3) context: gap-bucket only (full sample, 10% friction)
    print("\n-- context: gap-bucket only (full sample, 10% friction) --")
    gonly = {}
    for d in days:
        for s in ["IC", "PCS", "CCS"]:
            gonly.setdefault((d["gap"], s), []).append(struct_ret(d, s, 0.10))
    for (g, s), rs in sorted(gonly.items()):
        if len(rs) >= 100:
            print(f"  {s:4} gap={g:6} {fmt(rs)}")

    # 4) LOOSENING LADDER — relax the surviving rule's gates one at a time.
    #    More trades vs edge dilution, full sample + OOS, both frictions, IC + CCS.
    ladders = [
        ("L0 strict: flat gap & uptrend & VIX<16",
         lambda d: d["gap"] == "flat" and d["tr"] == "up" and d["vix"] < 16),
        ("L1 + small up-gaps (gap in flat/up)",
         lambda d: d["gap"] in ("flat", "up") and d["tr"] == "up" and d["vix"] < 16),
        ("L2 wider flat band (|gap|<0.30%)",
         lambda d: abs(d["gap_val"]) < 0.003 and d["tr"] == "up" and d["vix"] < 16),
        ("L3 calmer bar (VIX<20)",
         lambda d: d["gap"] == "flat" and d["tr"] == "up" and d["vix"] < 20),
        ("L4 drop the trend gate",
         lambda d: d["gap"] == "flat" and d["vix"] < 16),
        ("L5 loose: |gap|<0.30% & VIX<20, no trend",
         lambda d: abs(d["gap_val"]) < 0.003 and d["vix"] < 20),
    ]
    print("\n-- loosening ladder (IC; per-trade after friction; OOS = 2019-2026) --")
    for lbl, pred in ladders:
        sel = [d for d in days if pred(d)]
        sel_oos = [d for d in oos_days if pred(d)]
        for f in FRICTIONS:
            rs, ro = [struct_ret(d, "IC", f) for d in sel], [struct_ret(d, "IC", f) for d in sel_oos]
            print(f"  {lbl:42} fric={f:.0%} ~{len(sel)/yrs:3.0f}/yr  full {fmt(rs)}")
            print(f"  {'':42}          OOS  {fmt(ro)}")
    print("\n-- loosening ladder (CCS = call credit spread only) --")
    for lbl, pred in ladders[:3]:
        sel_oos = [d for d in oos_days if pred(d)]
        rs = [struct_ret(d, "CCS", 0.20) for d in sel_oos]
        print(f"  {lbl:42} fric=20% OOS  {fmt(rs)}")

    # 5) AGGRESSION GRID on the strict rule — tighter shorts x sizing.
    #    Tighter shorts collect more credit (more often breached); sizing scales
    #    the whole path. CAGR uses ALL years incl. the zero-trade ones (honest).
    print("\n-- aggression grid: strict rule days, IC, fric=20% --")
    strict = [d for d in days if ladders[0][1](d)]
    print(f"  {len(strict)} trade days over {yrs} years (~{len(strict)/yrs:.0f}/yr)")
    print(f"  {'shorts':>8} {'size':>5} {'total':>9} {'CAGR':>7} {'maxDD':>7} {'worst-day':>10} {'win':>5}")
    for ss in [0.50, 0.75, 1.00]:
        for size in [0.10, 0.20, 0.30]:
            eq, dd, worst, win = equity(strict, "IC", 0.20, size, short_s=ss)
            cagr = eq ** (1 / yrs) - 1
            print(f"  {ss:7.2f}s {size:4.0%} {eq:8.2f}x {cagr*100:+6.1f}% {dd*100:6.1f}% "
                  f"{worst*100:+9.1f}% {win*100:4.0f}%")

    print("\n  Verdict bar: a rule is deployable ONLY if it survives OOS at 20% friction")
    print("  with t>=1 — and even then it inherits the premium-selling crash factor the")
    print("  repo has already documented (BLOG.md: 0DTE income). Multiple-comparison")
    print("  caveat: ~120 cells were mined; expect ~3 false positives at t=2 by chance.")


if __name__ == "__main__":
    main()
