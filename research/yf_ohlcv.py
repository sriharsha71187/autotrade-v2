#!/usr/bin/env python3
"""Full adjusted OHLCV panel from yfinance (extends yf_panel.py which is close-only).
Needed for overnight/intraday decomposition, gaps, IBS, ATR, dollar-volume/illiquidity
and lottery-effect features in the broad correlation sweep (feature_sweep.py).

Output: ~/autotrade/research/.yf_ohlcv.pkl  (dict of DataFrames: O,H,L,C,V dates x syms)
Run: python research/yf_ohlcv.py
"""
import pickle, warnings
from pathlib import Path
warnings.filterwarnings("ignore")

CACHE = Path(__file__).resolve().parent / ".yf_ohlcv.pkl"


def main():
    import yfinance as yf
    panel = pickle.load(open(Path.home() / ".trend_bars.pkl", "rb"))
    syms = sorted(set(panel.columns) | {"SPY", "MDY", "QQQ"})
    print(f"downloading OHLCV for {len(syms)} names 2016-01-01 ->")
    raw = yf.download(syms, start="2016-01-01", auto_adjust=True, progress=False, threads=True)
    out = {k: raw[k].dropna(axis=1, how="all") for k in ["Open", "High", "Low", "Close", "Volume"]}
    pickle.dump(out, open(CACHE, "wb"))
    print("saved", CACHE, {k: v.shape for k, v in out.items()})


if __name__ == "__main__":
    main()
