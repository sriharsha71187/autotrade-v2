#!/usr/bin/env python3
"""Two follow-ups on the calm-day condor (see backtest_gap_premium.py):

(A) STOP-LOSSES, 20 years. Daily highs/lows tell us with certainty whether the
short strike was TOUCHED intraday — exactly when a stop would fire. Policy
tested: exit the moment spot touches a short strike, paying the spread's
repriced value there (Black-Scholes at S=strike, T = f x remaining session,
f in {0.25, 0.5, 0.75} since the touch time is unknown; +10% exit slippage),
vs riding every trade to the close. The tension being measured: a stop caps the
tail BUT converts touch-and-recover days (which finish as FULL WINS unstopped —
the condor is OTM again by the close) into locked-in losses.

(B) AFTERNOON ENTRY (1:00 PM ET = 10:00 AM PT), real intraday data only.
Robinhood hourly bars before ~Dec 2025 are filler (flat OHLC — detected and
dropped), so this window is ~160 days — treat results as DIRECTIONAL, not
proof. Trade: sell the condor at the 13:00 ET price, strikes 0.75 sigma of the
REMAINING session (3h of 6.5h -> sigma_rem = sigma_day*sqrt(3/6.5)), settle at
the close. Conditions: all days / day-flat-so-far / flat+VIX<16 / day-trending
(the "I can gauge the day by 10 AM PT" hypothesis, tested both ways).

Data: research/data/spy_vix_daily.csv + research/data/spy_pm_hourly.csv
(pm_bars column: 'high|low|close' per hour bar 13/14/15 ET, ';'-joined).
Run: python3 research/backtest_pm_stops.py       (pure stdlib)
"""
import csv, math, os

SHORT_S, WING_S = 0.75, 2.0
FRICTIONS = [0.10, 0.20]
SLIP = 0.10                     # exit at 110% of modeled stop value (crossing the spread)
T = 1 / 252
PM_FRAC = 3.0 / 6.5             # session remaining at 13:00 ET


def N(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def bs(S, K, Tx, sig, put=False):
    if Tx <= 0 or sig <= 0: return max((K - S) if put else (S - K), 0.0)
    d1 = (math.log(S / K) + (sig * sig / 2) * Tx) / (sig * math.sqrt(Tx)); d2 = d1 - sig * math.sqrt(Tx)
    return (K * N(-d2) - S * N(-d1)) if put else (S * N(d1) - K * N(d2))


def condor_value(S, Kc, Wc, Kp, Wp, Tx, iv):
    return (bs(S, Kc, Tx, iv) - bs(S, Wc, Tx, iv)
            + bs(S, Kp, Tx, iv, True) - bs(S, Wp, Tx, iv, True))


def tstat(rs):
    n = len(rs)
    if n < 2: return 0.0
    m = sum(rs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in rs) / (n - 1))
    return m / (sd / math.sqrt(n)) if sd > 0 else 0.0


def fmt(rs):
    if not rs: return "N=   0"
    n = len(rs); m = sum(rs) / n
    return (f"N={n:4d} avg={m*100:+6.2f}% t={tstat(rs):+5.1f} "
            f"win={sum(1 for r in rs if r>0)/n*100:3.0f}% worst={min(rs)*100:+5.0f}%")


def eqstats(rs, size=0.10, yrs=None):
    eq, peak, dd = 1.0, 1.0, 0.0
    for r in rs:
        eq *= 1 + r * size; peak = max(peak, eq); dd = min(dd, eq / peak - 1)
    cagr = eq ** (1 / yrs) - 1 if yrs else None
    return eq, dd, cagr


def main():
    daily = []
    with open("research/data/spy_vix_daily.csv") as fh:
        for r in csv.DictReader(fh):
            daily.append({"d": r["date"], "o": float(r["spy_open"]), "h": float(r["spy_high"]),
                          "l": float(r["spy_low"]), "c": float(r["spy_close"]),
                          "v": float(r["vix_close"])})
    by_date = {r["d"]: r for r in daily}

    # -------- (A) touch-stop vs no-stop, 20y, strict-rule days --------------
    trades = []
    for i in range(201, len(daily)):
        S, H, L, C = daily[i]["o"], daily[i]["h"], daily[i]["l"], daily[i]["c"]
        pc = daily[i - 1]["c"]
        if abs(S / pc - 1) >= 0.0015 or daily[i - 1]["v"] >= 16:   # strict rule
            continue
        ocs = [math.log(daily[j]["c"] / daily[j]["o"]) for j in range(i - 21, i)]
        m = sum(ocs) / 21
        iv = math.sqrt(sum((x - m) ** 2 for x in ocs) / 20) * math.sqrt(252) * 1.10
        if iv <= 0: continue
        sd = iv * math.sqrt(T)
        Kc, Kp = S * math.exp(SHORT_S * sd), S * math.exp(-SHORT_S * sd)
        Wc, Wp = S * math.exp(WING_S * sd), S * math.exp(-WING_S * sd)
        credit = condor_value(S, Kc, Wc, Kp, Wp, T, iv)
        marg = min(Wc - Kc, Kp - Wp) - credit
        if marg <= 0: continue
        loss_close = min(max(C - Kc, 0) + max(Kp - C, 0), min(Wc - Kc, Kp - Wp))
        touch_c, touch_p = H >= Kc, L <= Kp
        stop_vals = {}
        for f in (0.25, 0.50, 0.75):
            vals = []
            if touch_c: vals.append(condor_value(Kc, Kc, Wc, Kp, Wp, T * f, iv))
            if touch_p: vals.append(condor_value(Kp, Kc, Wc, Kp, Wp, T * f, iv))
            stop_vals[f] = max(vals) * (1 + SLIP) if vals else None   # worse side if both
        trades.append({"d": daily[i]["d"], "credit": credit, "marg": marg,
                       "loss_close": loss_close, "stop": stop_vals,
                       "touched": touch_c or touch_p})
    yrs = len({t["d"][:4] for t in trades})
    touched = sum(1 for t in trades if t["touched"])
    print(f"\n=== (A) stop-loss on the strict-rule condor · {len(trades)} trades "
          f"2010-2026 · short strike touched on {touched} ({touched/len(trades)*100:.0f}%) ===")
    print("  policy: exit at short-strike touch, repriced at T_rem = f x session, +10% slip\n")
    for fric in FRICTIONS:
        rows = {"no stop (ride to close)": [(t["credit"] * (1 - fric) - t["loss_close"]) / t["marg"]
                                            for t in trades]}
        for f in (0.25, 0.50, 0.75):
            rows[f"stop at touch (f={f:.2f})"] = [
                (t["credit"] * (1 - fric) - (t["stop"][f] if t["stop"][f] is not None
                                             else t["loss_close"])) / t["marg"]
                for t in trades]
        for lbl, rs in rows.items():
            eq, dd, cagr = eqstats(rs, 0.10, yrs)
            print(f"  fric={fric:.0%} {lbl:28} {fmt(rs)} | 10%-sized: "
                  f"CAGR {cagr*100:+5.1f}% maxDD {dd*100:5.1f}%")
        print()

    # -------- (B) 1PM-ET entry, real intraday window ------------------------
    if not os.path.exists("research/data/spy_pm_hourly.csv"):
        print("(B) skipped: no intraday file"); return
    pm_days = []
    with open("research/data/spy_pm_hourly.csv") as fh:
        for r in csv.DictReader(fh):
            if r["date"] not in by_date: continue
            bars = [tuple(map(float, seg.split("|"))) for seg in r["pm_bars"].split(";")]
            pm_days.append({**by_date[r["date"]], "p13": float(r["p13"]),
                            "hi_am": float(r["hi_am"]), "lo_am": float(r["lo_am"]),
                            "pm": bars})
    didx = {r["d"]: k for k, r in enumerate(daily)}
    variants = {"all days": lambda x: True,
                "flat so far (|move|<0.25%, range<0.6%)":
                    lambda x: abs(x["p13"] / x["o"] - 1) < 0.0025
                    and (x["hi_am"] - x["lo_am"]) / x["o"] < 0.006,
                "flat so far + VIX<16":
                    lambda x: abs(x["p13"] / x["o"] - 1) < 0.0025
                    and (x["hi_am"] - x["lo_am"]) / x["o"] < 0.006 and x["pv"] < 16,
                "day trending (|move|>=0.5%)":
                    lambda x: abs(x["p13"] / x["o"] - 1) >= 0.005}
    print(f"=== (B) afternoon condor · entry 13:00 ET · REAL intraday days only: "
          f"{len(pm_days)} ({pm_days[0]['d']}..{pm_days[-1]['d']}) — DIRECTIONAL, small N ===\n")
    for fric in FRICTIONS:
        res = {k: [] for k in variants}
        res["  ^ same but with touch-stop"] = []
        for x in pm_days:
            i = didx[x["d"]]
            if i < 202: continue
            x["pv"] = daily[i - 1]["v"]
            ocs = [math.log(daily[j]["c"] / daily[j]["o"]) for j in range(i - 21, i)]
            m = sum(ocs) / 21
            iv = math.sqrt(sum((v - m) ** 2 for v in ocs) / 20) * math.sqrt(252) * 1.10
            if iv <= 0: continue
            S, C = x["p13"], x["c"]
            Trem = T * PM_FRAC
            sd = iv * math.sqrt(Trem)
            Kc, Kp = S * math.exp(SHORT_S * sd), S * math.exp(-SHORT_S * sd)
            Wc, Wp = S * math.exp(WING_S * sd), S * math.exp(-WING_S * sd)
            credit = condor_value(S, Kc, Wc, Kp, Wp, Trem, iv)
            marg = min(Wc - Kc, Kp - Wp) - credit
            if marg <= 0: continue
            loss = min(max(C - Kc, 0) + max(Kp - C, 0), min(Wc - Kc, Kp - Wp))
            r_ns = (credit * (1 - fric) - loss) / marg
            for k, pred in variants.items():
                if pred(x): res[k].append(r_ns)
            # touch-stop variant on the flat-so-far rule, using pm bar highs/lows
            if variants["flat so far (|move|<0.25%, range<0.6%)"](x):
                hit = next(((h, l) for h, l, _ in x["pm"] if h >= Kc or l <= Kp), None)
                if hit:
                    Sx = Kc if hit[0] >= Kc else Kp
                    v = condor_value(Sx, Kc, Wc, Kp, Wp, Trem * 0.5, iv) * (1 + SLIP)
                    res["  ^ same but with touch-stop"].append((credit * (1 - fric) - v) / marg)
                else:
                    res["  ^ same but with touch-stop"].append(r_ns)
        for k, rs in res.items():
            print(f"  fric={fric:.0%} {k:42} {fmt(rs)}")
        print()
    print("  Caveats: (B) is ~8 months of data — a regime sample, not a backtest;")
    print("  synthetic BS pricing throughout; stop fills modeled at touch +10% slip,")
    print("  real fast-market fills are worse. Hourly touch detection understates")
    print("  intrabar touches in (B); (A) uses true daily H/L so touches are exact.")
    print("  (A)'s f=0.25 row (touch assumed LATE in the day) is a best-case artifact —")
    print("  little time value left makes every stop-exit cheap, hence the fake 100% win")
    print("  rate. The believable base case is f=0.50; f=0.75 is the adverse case. The")
    print("  honest reading: stops trade ~1-2% of per-trade expectancy for a worst-day")
    print("  cap of ~-10-20% of margin instead of -105%. Afternoon-vol note for (B):")
    print("  sigma_rem assumes uniform intraday variance; the real afternoon carries")
    print("  LESS than its pro-rata share (vol U-shape), so (B)'s credits — and edge —")
    print("  are overstated by construction. Validate against live 0DTE quotes.")


if __name__ == "__main__":
    main()
