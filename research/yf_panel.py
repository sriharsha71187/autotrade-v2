#!/usr/bin/env python3
"""Build a SPLIT/DIVIDEND-ADJUSTED daily close panel from yfinance for the research
universe (the Alpaca panel has known unadjusted split glitches — momentum artifacts).
Cached; event studies (PEAD, ratings) join against this, not the raw Alpaca panel.

Output: ~/autotrade/research/.yf_adj_close.pkl  (DataFrame dates x symbols)
Run: python research/yf_panel.py
"""
import pickle, warnings
from pathlib import Path
warnings.filterwarnings("ignore")

CACHE = Path(__file__).resolve().parent / ".yf_adj_close.pkl"


def main():
    import yfinance as yf
    panel = pickle.load(open(Path.home() / ".trend_bars.pkl", "rb"))
    syms = sorted(set(panel.columns) | {"SPY", "MDY"})
    print(f"downloading {len(syms)} names 2016-01-01 ->")
    px = yf.download(syms, start="2016-01-01", auto_adjust=True, progress=False,
                     threads=True)["Close"]
    px = px.dropna(axis=1, how="all")
    px.to_pickle(CACHE)
    print("saved", CACHE, px.shape, px.index.min(), "->", px.index.max())


if __name__ == "__main__":
    main()
