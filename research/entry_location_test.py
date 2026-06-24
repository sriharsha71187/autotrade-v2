#!/usr/bin/env python3
"""Does ATTRACTIVE ENTRY LOCATION (not recent momentum) predict multi-day forward return?

Tests the hypothesis: among the names the bot scanned, do the ones sitting at an attractive
multi-day entry — deeper pullback off the 20d high, below/near the 50DMA, oversold daily RSI —
go on to beat over 1d/3d, EVEN IF they're not moving (or are down) today? And does the bot's
current frame (recent momentum: 5d/20d return, today's % move) actually predict, or not?

Method (confound-robust): the candidate_outcomes rows already carry fwd_1d/fwd_3d. We add
DAILY entry-location features per (symbol, date) from daily bars (no lookahead: features use
bars strictly before the entry day; entry ref price = the intraday `last`). Then for each
feature we compute the CROSS-SECTIONAL Spearman rank IC vs forward return WITHIN each day,
and average across days (Fama-MacBeth). Day-neutral IC strips out "the whole tape went up
that day," which is the main confound with only ~11 days. t-stat is across the day ICs.

Run: python research/entry_location_test.py
"""
import sys, json, datetime as dt
from pathlib import Path
import numpy as np, pandas as pd

OUT = Path.home() / "autotrade_candidate_outcomes"
ENV = Path.home() / ".autotrade.env"
LOOKBACK_START = "2026-02-01"   # enough history for 50DMA / 20d-high as of early June


def _keys():
    k = {}
    for ln in ENV.read_text().splitlines():
        if "=" in ln and not ln.startswith("#"):
            a, _, b = ln.partition("="); k[a.strip()] = b.strip().strip('"').strip("'")
    return k.get("ALPACA_API_KEY"), k.get("ALPACA_SECRET_KEY")


def _daily_bars(symbols):
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    cl = StockHistoricalDataClient(*_keys())
    out = {}
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        try:
            df = cl.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=chunk, timeframe=TimeFrame.Day,
                start=pd.Timestamp(LOOKBACK_START), end=pd.Timestamp("2026-06-30"))).df
        except Exception as e:
            print("  bars fetch failed:", e); continue
        if df is None or df.empty:
            continue
        for s in chunk:
            try:
                sub = df.loc[s].copy()
                sub.index = pd.to_datetime(sub.index).tz_convert("UTC").normalize()
                out[s] = sub
            except KeyError:
                pass
    return out


def _rsi(closes, n=14):
    d = closes.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def load_obs():
    """One observation per (symbol, day): first cycle of the day (entry-near-open frame)."""
    rows = []
    for f in sorted(OUT.glob("*.jsonl")):
        for l in f.read_text().splitlines():
            if l.strip():
                rows.append(json.loads(l))
    df = pd.DataFrame(rows)
    df = df.sort_values("ts").drop_duplicates(["symbol", "day"], keep="first")
    return df.reset_index(drop=True)


def add_features(obs, bars):
    feats = []
    for _, o in obs.iterrows():
        s, day, last = o["symbol"], o["day"], o.get("last")
        b = bars.get(s)
        if b is None or last is None:
            feats.append({}); continue
        d0 = pd.Timestamp(day).tz_localize("UTC").normalize()
        hist = b[b.index < d0]                      # strictly before entry day: no lookahead
        if len(hist) < 50:
            feats.append({}); continue
        c = hist["close"]; h = hist["high"]; lo = hist["low"]
        hi20 = h.iloc[-20:].max(); lo20 = lo.iloc[-20:].min()
        hi60 = h.iloc[-60:].max() if len(h) >= 60 else h.max()
        lo60 = lo.iloc[-60:].min() if len(lo) >= 60 else lo.min()
        ma50 = c.iloc[-50:].mean()
        rsi = _rsi(c).iloc[-1]
        feats.append({
            # --- ATTRACTIVE-LOCATION features (more negative / lower = "better entry") ---
            "pullback_20dhigh": last / hi20 - 1,        # how far below 20d high (deeper = more pullback)
            "dist_50dma": last / ma50 - 1,              # below 50dma = negative
            "rsi14_daily": rsi,                          # low = oversold
            "pct_in_60d_range": (last - lo60) / (hi60 - lo60) if hi60 > lo60 else np.nan,
            # --- RECENT-MOMENTUM features (the bot's current frame) ---
            "ret_5d": c.iloc[-1] / c.iloc[-6] - 1 if len(c) >= 6 else np.nan,
            "ret_20d": c.iloc[-1] / c.iloc[-21] - 1 if len(c) >= 21 else np.nan,
            # day_pct (today's intraday move) is already on the obs rows from backfill — reuse it
        })
    return pd.concat([obs.reset_index(drop=True), pd.DataFrame(feats)], axis=1)


def fama_macbeth_ic(df, feat, ret, min_n=8):
    """Mean cross-sectional Spearman IC of `feat` vs `ret`, averaged across days."""
    ics = []
    for day, g in df.groupby("day"):
        sub = g[[feat, ret]].dropna()
        if len(sub) >= min_n and sub[feat].nunique() > 3:
            ics.append(sub[feat].corr(sub[ret], method="spearman"))
    ics = [x for x in ics if pd.notna(x)]
    if len(ics) < 3:
        return None
    a = np.array(ics)
    t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if a.std(ddof=1) > 0 else 0.0
    return {"mean_ic": a.mean(), "t": t, "ndays": len(a), "hit": (a > 0).mean()}


def main():
    obs = load_obs()
    print(f"Loaded {len(obs)} (symbol,day) observations · "
          f"{obs['day'].nunique()} days · {obs['symbol'].nunique()} symbols")
    bars = _daily_bars(sorted(obs["symbol"].unique()))
    print(f"Daily bars for {len(bars)} symbols")
    df = add_features(obs, bars)

    LOC = ["pullback_20dhigh", "dist_50dma", "rsi14_daily", "pct_in_60d_range"]
    MOM = ["ret_5d", "ret_20d", "day_pct"]
    print("\n  Interpretation: IC>0 means HIGHER feature -> HIGHER forward return.")
    print("  For location features (pullback/dist_50dma/rsi/range-pos), a NEGATIVE IC")
    print("  means LOWER/cheaper entry -> BETTER forward return = a mean-reversion edge.\n")
    for ret in ["fwd_1d", "fwd_3d"]:
        n = df[ret].notna().sum()
        print(f"=== {ret}  (n={n} obs with mature return) ===")
        print("  --- ATTRACTIVE-LOCATION features ---")
        for f in LOC:
            r = fama_macbeth_ic(df, f, ret)
            if r: print(f"   {f:18} IC={r['mean_ic']:+.3f}  t={r['t']:+.2f}  "
                        f"days={r['ndays']}  pos-day%={r['hit']*100:.0f}")
        print("  --- RECENT-MOMENTUM features (bot's current frame) ---")
        for f in MOM:
            r = fama_macbeth_ic(df, f, ret)
            if r: print(f"   {f:18} IC={r['mean_ic']:+.3f}  t={r['t']:+.2f}  "
                        f"days={r['ndays']}  pos-day%={r['hit']*100:.0f}")
        print()
    # save the feature-enriched dataset for any follow-up
    df.to_csv(Path.home() / "autotrade_entryloc.csv", index=False)
    print("wrote ~/autotrade_entryloc.csv  ·  |t|>~2.5 over this few days = worth a closer look;")
    print("anything weaker is noise at this sample size. In-sample, exploratory — not a verdict.")


if __name__ == "__main__":
    main()
