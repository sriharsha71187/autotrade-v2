#!/usr/bin/env python3
"""Tier0 news-drift event study on the less-efficient slice (see news_drift_backfill.py).
Question: does DETERMINISTIC (free, lexicon) news sentiment predict multi-day drift in
small/mid caps — enough of a pulse to justify Tier1 LLM scoring?

Signals per symbol-day (headlines dated d, scored with an extended Loughran-McDonald-ish
lexicon + a few high-signal bigrams):
  sent  = mean headline score on d          (needs >=2 scored headlines)
  atten = n_headlines / trailing 63d mean   (attention spike)
Entry at NEXT day's close (no lookahead: news may post after hours). Abnormal return =
stock fwd return minus equal-weight universe fwd return.

Tests:
  1. Event CARs: strong-pos vs strong-neg days, horizons 5/21/63d, with t-stats.
  2. Daily cross-sectional IC (Spearman) of sent vs fwd 21d abnormal return; day-neutral.
  3. Tradable long-only proxy: monthly, long top-decile trailing-21d sentiment (>=3 scored
     headlines), 10bps/side, vs universe EW; IS 2017-2021 / OOS 2022-2026 split.
Run: python3 research/backtest_news_drift.py
"""
import json, math, re
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")

NEWS = Path.home() / "autotrade_news_drift"
CACHE = Path(__file__).resolve().parent / ".ohlcv_cache.pkl"
COST = 0.001  # 10bps/side on the monthly proxy

POS = set("""beat beats surge surges jump jumps soar soars rally rallies gain gains rise rises
top tops upgrade upgrades raised raises boost boosts strong record growth profit profits wins
win approval breakthrough outperform bullish expands expand accelerates exceeded exceeds
upbeat surpass surpasses awarded secures contract buyback dividend hike momentum""".split())
NEG = set("""miss misses fall falls drop drops plunge plunges sink sinks slump slumps cut cuts
downgrade downgrades lowered lowers weak weakness loss losses warns warning warn probe lawsuit
recall recalls decline declines bearish investigation halt halts slashes layoffs bankruptcy
restatement dilution offering resigns resign delist downgraded disappointing shortfall
underperform suspended fraud subpoena default""".split())
BIGRAM = [(re.compile(p, re.I), s) for p, s in [
    (r"price target (raised|boosted|hiked)", +2), (r"price target (cut|lowered|slashed)", -2),
    (r"beats? (estimates|expectations|consensus)", +2), (r"miss(es)? (estimates|expectations|consensus)", -2),
    (r"raises? (guidance|outlook|forecast)", +2), (r"(cuts?|lowers?|withdraws?) (guidance|outlook|forecast)", -2),
    (r"secondary offering|share offering|dilut", -2), (r"going concern", -3),
    (r"(acquire[sd]?|to acquire|takeover|merger)", +1), (r"upgraded? to (buy|overweight|outperform)", +2),
    (r"downgraded? to (sell|underweight|underperform)", -2)]]


def score(hl):
    w = set(re.sub(r"[^a-z ]", " ", hl.lower()).split())
    s = len(w & POS) - len(w & NEG)
    for rx, v in BIGRAM:
        if rx.search(hl): s += v
    return max(-3, min(3, s))


def load():
    import pickle
    d = pickle.load(open(CACHE, "rb")); C = d["C"]
    C.index = pd.to_datetime(C.index).tz_localize(None).normalize()
    rows = []
    for f in sorted(NEWS.glob("*.jsonl")):
        sym = f.stem
        if sym not in C.columns: continue
        for ln in f.read_text().splitlines():
            r = json.loads(ln); s = score(r["h"])
            rows.append((sym, r["d"], s, 1 if s != 0 else 0))
    n = pd.DataFrame(rows, columns=["sym", "d", "s", "scored"])
    n["d"] = pd.to_datetime(n["d"])
    day = n.groupby(["sym", "d"]).agg(sent=("s", "mean"), nsc=("scored", "sum"), n=("s", "size")).reset_index()
    sent = day.pivot(index="d", columns="sym", values="sent")
    nsc = day.pivot(index="d", columns="sym", values="nsc")
    cnt = day.pivot(index="d", columns="sym", values="n")
    syms = [c for c in sent.columns if c in C.columns]
    px = C[syms]
    idx = px.index
    sent = sent.reindex(idx)[syms]; nsc = nsc.reindex(idx)[syms]; cnt = cnt.reindex(idx)[syms].fillna(0)
    return px, sent, nsc, cnt


def main():
    px, sent, nsc, cnt = load()
    print(f"\n=== Tier0 news drift · {len(px.columns)} less-efficient names · "
          f"{px.index.min().date()}..{px.index.max().date()} ===")
    print(f"  symbol-days with >=1 headline: {int((cnt>0).sum().sum()):,} · "
          f"with >=2 SCORED headlines: {int((nsc>=2).sum().sum()):,}\n")
    r = px.pct_change()
    uni = r.mean(axis=1)                                   # EW universe daily return
    # forward abnormal returns, entry NEXT close (t+1) to t+1+h; abnormal = minus row-mean
    def ab(h):
        f = px.shift(-(1+h))/px.shift(-1) - 1
        return f.sub(f.mean(axis=1), axis=0)

    # --- 1. event CARs
    print(f"  {'event CARs (abnormal, entry t+1 close)':46}{'N':>7}{'+5d':>8}{'+21d':>8}{'+63d':>8}{'t(21d)':>8}")
    for lbl, mask in [("strong POSITIVE (sent>=+1, >=2 scored)", (sent >= 1) & (nsc >= 2)),
                      ("strong NEGATIVE (sent<=-1, >=2 scored)", (sent <= -1) & (nsc >= 2)),
                      ("attention spike only (n>=5 headlines)", (cnt >= 5))]:
        line = f"  {lbl:46}{int(mask.sum().sum()):7,}"
        tv = None
        for h in (5, 21, 63):
            v = ab(h)[mask].stack().dropna()
            line += f"{v.mean()*100:7.2f}%"
            if h == 21: tv = v.mean()/(v.std()/math.sqrt(len(v))+1e-12)
        print(line + f"{tv:8.1f}")

    # --- 2. daily cross-sectional IC (sent vs fwd 21d abnormal)
    f21 = ab(21)
    ics = []
    for dt_ in px.index[:-30]:
        s = sent.loc[dt_].dropna(); s = s[nsc.loc[dt_].reindex(s.index) >= 2]
        if len(s) < 8: continue
        fv = f21.loc[dt_].reindex(s.index).dropna()
        s = s.reindex(fv.index)
        if len(s) < 8 or s.std() == 0: continue
        ics.append(s.rank().corr(fv.rank()))
    ics = pd.Series(ics).dropna()
    print(f"\n  daily cross-sectional IC (sent vs +21d abnormal): mean {ics.mean():+.3f} · "
          f"t = {ics.mean()/(ics.std()/math.sqrt(len(ics))+1e-12):.1f} · days {len(ics)}")

    # --- 3. tradable monthly long-only proxy
    s21 = sent.fillna(0).rolling(21).sum(); n21 = nsc.fillna(0).rolling(21).sum()
    idx = px.index
    mstart = list(idx[np.append([True], idx.to_period('M')[1:] != idx.to_period('M')[:-1])])
    def run_proxy(a, b):
        rets = []; prev = []
        for i, d0 in enumerate(mstart):
            if not (a <= d0.year <= b) or i+1 >= len(mstart): continue
            sig = s21.loc[:d0].iloc[-1]; nn = n21.loc[:d0].iloc[-1]
            elig = sig[(nn >= 3)].dropna()
            if len(elig) < 20: continue
            picks = list(elig.nlargest(max(len(elig)//10, 5)).index)
            d1 = mstart[i+1]
            pr = px.loc[d0:d1, picks].iloc[:]
            rr = (pr.iloc[-1]/pr.iloc[0]-1).mean()
            bench = (px.loc[d0:d1].iloc[-1]/px.loc[d0:d1].iloc[0]-1).mean()
            turn = len(set(picks)-set(prev))/max(len(picks), 1)
            rets.append((d0, rr - 2*COST*turn, bench)); prev = picks
        df = pd.DataFrame(rets, columns=["d", "strat", "bench"]).set_index("d")
        act = df["strat"]-df["bench"]
        ir = act.mean()/(act.std()+1e-12)*math.sqrt(12)
        return (df["strat"].mean()*12, df["bench"].mean()*12, act.mean()*12, ir, len(df))
    print(f"\n  {'monthly top-decile 21d-sentiment long-only':44}{'strat':>8}{'bench':>8}{'active':>8}{'IR':>6}{'mo':>5}")
    for lbl, a, b in [("IS  2017-2021", 2017, 2021), ("OOS 2022-2026", 2022, 2026), ("ALL 2017-2026", 2017, 2026)]:
        st, be, acv, ir, nm = run_proxy(a, b)
        print(f"  {lbl:44}{st*100:7.1f}%{be*100:7.1f}%{acv*100:+7.1f}%{ir:6.2f}{nm:5}")
    print("\n  NOTE: survivor-tilted panel -> ABSOLUTE returns inflated; the ACTIVE spread vs the")
    print("  same-universe EW bench and the IC are the honest Tier0 readouts.")


if __name__ == "__main__":
    main()
