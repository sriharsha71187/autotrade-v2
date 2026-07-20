#!/usr/bin/env python3
"""Broad cross-sectional correlation sweep on the CLEAN yfinance OHLCV panel:
~25 features x horizons {5,10,21,63}d, weekly sampling, Spearman IC, Newey-West t,
IS 2016-2021 / OOS 2022-2026, regime splits (VIX band, SPY vs 200dma), and a
long-only translation (top-decile excess) for anything that survives.

Discipline: ~200 cells tested -> expect ~10 false at p<.05. Only BOTH-HALVES |t|>2.5
with a consistent sign counts as a survivor; |t|>3 to be believed. Every cell is
counted in n_trials on the strategy board.

Run: python research/feature_sweep.py
"""
import math, pickle, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")

CACHE = Path(__file__).resolve().parent / ".yf_ohlcv.pkl"
MACRO = Path.home() / "autotrade_macro_history.csv"
ETFS = set("""SPY QQQ IWM DIA MDY RSP VTI XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC
TLT IEF SHY HYG LQD GLD SLV USO GDX GDXJ UUP FXE VXX UVXY SVXY SMH SOXX XBI IBB ARKK KRE
KBE ITB XHB XRT XME XOP OIH TAN ICLN URA LIT JETS IYT EEM EFA FXI EWZ EWJ INDA VNQ IYR
QLD TQQQ SSO UPRO SQQQ SDS PSQ SH EFV EFG VTV VUG IWD IWF IWO IWN BND AGG TIP EMB""".split())
HORIZONS = [5, 10, 21, 63]
OOS_START = pd.Timestamp("2022-01-01")


def nw_t(x, lag):
    """Newey-West t-stat of the mean of series x with given lag."""
    x = pd.Series(x).dropna().values
    n = len(x)
    if n < 30:
        return np.nan
    mu = x.mean()
    e = x - mu
    s = e @ e / n
    for l in range(1, lag + 1):
        w = 1 - l / (lag + 1)
        s += 2 * w * (e[l:] @ e[:-l]) / n
    return mu / math.sqrt(s / n) if s > 0 else np.nan


def build_features(O, H, L, C, V):
    r = C.pct_change()
    logc = np.log(C)
    spy = C["SPY"].pct_change()
    dollar = (C * V).rolling(20).mean()
    f = {}
    f["mom21"] = C / C.shift(21) - 1
    f["mom63"] = C / C.shift(63) - 1
    f["mom126"] = C / C.shift(126) - 1
    f["mom252ex21"] = C.shift(21) / C.shift(252) - 1
    f["rev5"] = C / C.shift(5) - 1
    f["rev10"] = C / C.shift(10) - 1
    f["dist200"] = C / C.rolling(200).mean() - 1
    f["dist52h"] = C / C.rolling(252).max() - 1
    f["vol20"] = r.rolling(20).std() * math.sqrt(252)
    f["vol60"] = r.rolling(60).std() * math.sqrt(252)
    f["volofvol"] = f["vol20"].rolling(60).std()
    f["dvol60"] = r.where(r < 0).rolling(60, min_periods=20).std() * math.sqrt(252)
    f["max21"] = r.rolling(21).max()                     # lottery / MAX effect
    f["skew60"] = r.rolling(60).skew()
    f["atrp"] = ((H - L) / C).rolling(14).mean()
    f["ldv"] = np.log(dollar.replace(0, np.nan))          # size/liquidity proxy
    f["amihud"] = (r.abs() / (C * V).replace(0, np.nan)).rolling(20).mean() * 1e9
    on = (O / C.shift(1) - 1)                              # overnight leg
    intr = (C / O - 1)                                     # intraday leg
    f["on21"] = on.rolling(21).sum()
    f["intra21"] = intr.rolling(21).sum()
    f["gapfreq"] = (on.abs() > 0.02).rolling(21).sum()
    ibs = ((C - L) / (H - L)).replace([np.inf, -np.inf], np.nan)
    f["ibs5"] = ibs.rolling(5).mean()
    cov = r.rolling(60).cov(spy)
    beta = cov / spy.rolling(60).var()
    f["beta60"] = beta
    resid_var = r.rolling(60).var().sub(beta**2 * spy.rolling(60).var(), axis=0)
    f["idio60"] = np.sqrt(resid_var.clip(lower=0) * 252)
    f["volt"] = V.rolling(20).mean() / V.rolling(120).mean()   # volume trend
    return f


def main():
    d = pickle.load(open(CACHE, "rb"))
    O, H, L, C, V = (d[k] for k in ["Open", "High", "Low", "Close", "Volume"])
    stocks = [c for c in C.columns if c not in ETFS]
    print(f"panel {C.shape}, stocks {len(stocks)}, {C.index.min().date()} -> {C.index.max().date()}")
    feats = build_features(O, H, L, C, V)
    C = C[stocks]

    macro = pd.read_csv(MACRO, parse_dates=["Date"]).set_index("Date")
    vix = macro["vix"].reindex(C.index).ffill()
    spy200 = d["Close"]["SPY"] > d["Close"]["SPY"].rolling(200).mean()

    fridays = C.index[C.index.weekday == 4]
    fwd = {h: (C.shift(-h) / C - 1) for h in HORIZONS}
    # cross-sectional demean per date (excess vs stock universe)
    for h in HORIZONS:
        fwd[h] = fwd[h].sub(fwd[h].mean(axis=1), axis=0)

    rows = []
    for name, F in feats.items():
        F = F[stocks]
        for h in HORIZONS:
            ics = {}
            for dt_ in fridays:
                if dt_ not in F.index:
                    continue
                x, y = F.loc[dt_], fwd[h].loc[dt_]
                m = x.notna() & y.notna()
                if m.sum() < 100:
                    continue
                ics[dt_] = x[m].rank().corr(y[m].rank())
            s = pd.Series(ics)
            if len(s) < 60:
                continue
            lag = max(1, h // 5)
            is_, oos = s[s.index < OOS_START], s[s.index >= OOS_START]
            rows.append({"feat": name, "h": h,
                         "ic_is": is_.mean(), "t_is": nw_t(is_, lag),
                         "ic_oos": oos.mean(), "t_oos": nw_t(oos, lag),
                         "ic_hivix": s[vix.reindex(s.index) >= 20].mean(),
                         "ic_lovix": s[vix.reindex(s.index) < 20].mean(),
                         "ic_bull": s[spy200.reindex(s.index).fillna(False)].mean(),
                         "ic_bear": s[~spy200.reindex(s.index).fillna(False)].mean()})
    res = pd.DataFrame(rows)
    res["surv"] = (res.t_is.abs() > 2.5) & (res.t_oos.abs() > 2.5) & (np.sign(res.t_is) == np.sign(res.t_oos))
    res["score"] = res[["t_is", "t_oos"]].abs().min(axis=1)
    res = res.sort_values("score", ascending=False)
    pd.set_option("display.width", 200)
    print(f"\ncells tested: {len(res)} (expect ~{len(res)*0.05:.0f} false at p<.05)")
    print("\n=== TOP 25 by min(|t_is|,|t_oos|) ===")
    print(res.head(25).to_string(index=False, float_format=lambda v: f"{v:+.3f}",
                                 columns=["feat", "h", "ic_is", "t_is", "ic_oos", "t_oos",
                                          "ic_hivix", "ic_lovix", "ic_bull", "ic_bear", "surv"]))
    surv = res[res.surv]
    print(f"\nBOTH-HALVES survivors (|t|>2.5 both, same sign): {len(surv)}")

    # long-only translation for survivors: top-decile mean excess (weekly rebal, h-day hold)
    if len(surv):
        print("\n=== long-only translation (top decile by feature, excess vs universe) ===")
        for _, rr in surv.iterrows():
            F = feats[rr.feat][stocks]
            sign = np.sign(rr.ic_is)
            vals = []
            for dt_ in fridays:
                if dt_ not in F.index:
                    continue
                x, y = F.loc[dt_] * sign, fwd[int(rr.h)].loc[dt_]
                m = x.notna() & y.notna()
                if m.sum() < 100:
                    continue
                thr = x[m].quantile(0.9)
                vals.append((dt_, y[m][x[m] >= thr].mean()))
            s = pd.Series(dict(vals))
            is_, oos = s[s.index < OOS_START], s[s.index >= OOS_START]
            lag = max(1, int(rr.h) // 5)
            ann = 252 / int(rr.h)
            print(f"{rr.feat:12s} h={int(rr.h):3d} sign={'+' if sign>0 else '-'}  "
                  f"IS {is_.mean()*100:+.2f}%/{int(rr.h)}d (t{nw_t(is_,lag):+.1f}, ~{is_.mean()*ann*100:+.0f}%/yr)  "
                  f"OOS {oos.mean()*100:+.2f}%/{int(rr.h)}d (t{nw_t(oos,lag):+.1f}, ~{oos.mean()*ann*100:+.0f}%/yr)")


if __name__ == "__main__":
    main()
