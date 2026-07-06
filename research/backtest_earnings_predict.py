#!/usr/bin/env python3
"""Do PRE-event indicators predict earnings-reaction outcomes? Events = |1d ret|>5% on >2.5x
volume (earnings-type reactions), detected across the full OHLCV panel. For each event with a
prior event 40-90 trading days earlier (quarterly cadence => these are predominantly earnings):
  features at t-1: 20d market-adjusted drift, accumulation (up/dn vol 63d), volume trend,
                   6mo momentum, prior event's sign
  outcomes: event-day return; post-event 21d drift
Reports hit-rates + mean returns by feature bucket with t-stats, then a TRADABLE test: enter 10d
before the EXPECTED next event (prior + 63d), exit 5d after it fires (or +75d timeout), only when
indicators align; vs unconditional same-window baseline. Run: python research/backtest_earnings_predict.py
"""
import math
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
CACHE = Path(__file__).with_name(".ohlcv_cache.pkl")


def tstat(x):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    return x.mean()/(x.std(ddof=1)/math.sqrt(len(x))+1e-12) if len(x) > 2 else 0.0


def main():
    d = pd.read_pickle(CACHE)
    C, V = d["C"], d["V"]
    keep = C.columns[C.notna().sum() > 400]
    C, V = C[keep], V[keep]
    stocks = [c for c in C.columns if c not in ("SPY","QQQ")]
    idx = C.index; r = C.pct_change(); spy_r = r["SPY"]
    volx = V[stocks]/(V[stocks].rolling(63).mean()+1)
    event = (r[stocks].abs() > 0.05) & (volx > 2.5)
    drift20 = (C[stocks].shift(1)/C[stocks].shift(22) - 1).sub((C["SPY"].shift(1)/C["SPY"].shift(22) - 1), axis=0)
    upv = V[stocks].where(r[stocks] > 0, 0).rolling(63).sum()
    dnv = V[stocks].where(r[stocks] < 0, 0).rolling(63).sum()
    accum = (upv/(dnv+1)).shift(1)
    mom6 = (C[stocks].shift(22)/C[stocks].shift(148) - 1)
    post21 = (C[stocks].shift(-22)/C[stocks].shift(-1) - 1)

    rows = []
    for t in stocks:
        ev = list(np.where(event[t].fillna(False).values)[0])
        for j in range(1, len(ev)):
            i, ip = ev[j], ev[j-1]
            gap = i - ip
            if not (40 <= gap <= 90): continue                      # quarterly cadence -> earnings-like
            rows.append(dict(ret=r[t].iloc[i], prev=np.sign(r[t].iloc[ip]),
                             drift=drift20[t].iloc[i], acc=accum[t].iloc[i],
                             mom=mom6[t].iloc[i], post=post21[t].iloc[i]))
    df = pd.DataFrame(rows).replace([np.inf,-np.inf], np.nan)
    print(f"\n=== {len(df)} quarterly-cadence earnings-type events · {idx.min().date()}..{idx.max().date()} ===\n")
    up = (df.ret > 0)
    print(f"  base rate: P(event up) = {up.mean()*100:.0f}%   mean event-day ret {df.ret.mean()*100:+.2f}%\n")
    print(f"  {'pre-event indicator':36}{'P(up)':>7}{'mean ret':>10}{'post-21d':>10}{'t(ret)':>8}{'N':>7}")
    for lbl, m in [("20d drift POSITIVE", df.drift > 0), ("20d drift NEGATIVE", df.drift < 0),
                   ("drift top quintile", df.drift > df.drift.quantile(0.8)),
                   ("drift bottom quintile", df.drift < df.drift.quantile(0.2)),
                   ("accumulation > 1", df.acc > 1), ("accumulation < 1", df.acc < 1),
                   ("prior event was UP", df.prev > 0), ("prior event was DOWN", df.prev < 0),
                   ("6mo momentum > 0", df.mom > 0), ("6mo momentum < 0", df.mom < 0),
                   ("ALL aligned (drift+,acc>1,prev+)", (df.drift > 0) & (df.acc > 1) & (df.prev > 0)),
                   ("ALL negative (drift-,acc<1,prev-)", (df.drift < 0) & (df.acc < 1) & (df.prev < 0))]:
        s = df[m]
        if len(s) > 30:
            print(f"  {lbl:36}{(s.ret>0).mean()*100:6.0f}%{s.ret.mean()*100:+9.2f}%{s.post.mean()*100:+9.2f}%{tstat(s.ret):8.1f}{len(s):7}")

    # ---- tradable: hold through EXPECTED next event when indicators align ----
    print(f"\n  TRADABLE (enter expected-event -10d, exit event +5d or timeout; net 10bp/side):")
    def window_test(cond_fn, lbl):
        rets = []
        for t in stocks:
            ev = list(np.where(event[t].fillna(False).values)[0])
            for j in range(1, len(ev)):
                ip = ev[j-1]; exp_i = ip + 63; ent = exp_i - 10
                if ent + 25 >= len(idx) or ent <= 148: continue
                nxt = [k for k in ev if ent < k <= ip + 90]
                exit_i = (nxt[0] + 5) if nxt else min(ip + 75, len(idx)-1)
                if exit_i <= ent: continue
                if not cond_fn(t, ent): continue
                rr = C[t].iloc[exit_i]/C[t].iloc[ent] - 1 - 0.002
                if rr == rr: rets.append(rr)
        rets = np.array(rets)
        if len(rets) > 50:
            print(f"    {lbl:42} N={len(rets):5}  avg {rets.mean()*100:+.2f}%/trade  win {100*(rets>0).mean():.0f}%  t={tstat(rets):.1f}")
    window_test(lambda t, i: True, "unconditional (every expected event)")
    window_test(lambda t, i: (drift20[t].iloc[i] or 0) > 0 and (mom6[t].iloc[i] or 0) > 0,
                "drift>0 AND mom>0 (aligned only)")
    window_test(lambda t, i: (drift20[t].iloc[i] or 0) < 0 and (mom6[t].iloc[i] or 0) < 0,
                "drift<0 AND mom<0 (would AVOID/short)")
    print("\n  Caveat: events detected ex-post => sample skews to BIG reactions; quiet earnings invisible.")
    print("  Survivorship applies. Direction findings are the robust part; magnitudes optimistic.")


if __name__ == "__main__":
    main()
