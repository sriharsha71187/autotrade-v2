#!/usr/bin/env python3
"""Macro / regime history from FRED (free, keyless, decades). These are MARKET-level series
(same for all names on a date) -> used for regime-conditioning, market-timing, and signal x
regime interactions (NOT cross-sectional IC). Output: ~/autotrade_macro_history.csv

Run: python research/macro_backfill.py
"""
from pathlib import Path
import pandas as pd
import warnings; warnings.filterwarnings("ignore")

OUT = Path.home() / "autotrade_macro_history.csv"
# yfinance index/ETF tickers (reliable here, unlike FRED) -> regime series
TK = {"^TNX": "y10", "^FVX": "y5", "^IRX": "y3mo", "^TYX": "y30", "^VIX": "vix",
      "DX-Y.NYB": "dollar", "HYG": "hyg", "IEF": "ief"}


def main():
    import yfinance as yf
    cols = {}
    for t, name in TK.items():
        try:
            h = yf.Ticker(t).history(period="10y")["Close"]
            h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
            if len(h):
                cols[name] = h
                print(f"  {name:8} {len(h)} obs, latest {h.index[-1].date()} = {round(float(h.iloc[-1]),3)}")
        except Exception as e:
            print("  FAILED:", t, str(e)[:50])
    df = pd.DataFrame(cols).sort_index().ffill()
    df["curve_10y3mo"] = df["y10"] - df["y3mo"]      # recession indicator (10y - 3mo)
    df["credit_proxy"] = df["hyg"] / df["ief"]       # HY vs Treasury ETF = credit-risk appetite
    # derived regime features
    df["vix_chg_5d"] = df["vix"].pct_change(5)
    df["y10_chg_20d"] = df["y10"].diff(20)
    df["credit_chg_20d"] = df["credit_proxy"].pct_change(20)   # falling = credit stress
    df["risk_off"] = ((df["vix"] > df["vix"].rolling(60).mean()) &
                      (df["credit_proxy"] < df["credit_proxy"].rolling(60).mean())).astype(int)
    df.to_csv(OUT)
    print(f"\nwrote {OUT}  ·  {df.shape[0]} days x {df.shape[1]} series ({df.index.min().date()}..{df.index.max().date()})")


if __name__ == "__main__":
    main()
