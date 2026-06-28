#!/usr/bin/env python3
"""India analog of the growth-tilted portfolio. Same architecture adapted to NSE instruments:
  - Core: risk-parity+target-vol over Nifty50 / Gold / Cash (NIFTYBEES/GOLDBEES/LIQUIDBEES)
  - Growth: Nifty-Next-50 (JUNIORBEES, higher beta) + a STOCK-MOMENTUM basket of NSE names
  - High-beta tactical: Bank Nifty (BANKBEES) + 200DMA regime
  - "Leverage": India has NO leveraged ETFs (SEBI) -> via margin/MTF (shown as 1.5x synthetic)
Regime = Nifty > 200DMA. vs Nifty buy-hold. Run: python research/backtest_india.py
"""
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
COST = 0.0010   # India costs higher (STT, stamp, brokerage) -> 10bps/side

NSE = ("RELIANCE TCS INFY HDFCBANK ICICIBANK HINDUNILVR ITC SBIN BHARTIARTL KOTAKBANK LT AXISBANK "
       "BAJFINANCE ASIANPAINT MARUTI SUNPHARMA TITAN ULTRACEMCO WIPRO ONGC NTPC POWERGRID NESTLEIND "
       "HCLTECH TATAMOTORS TATASTEEL JSWSTEEL ADANIENT ADANIPORTS COALINDIA GRASIM DRREDDY CIPLA "
       "BRITANNIA EICHERMOT HEROMOTOCO BPCL INDUSINDBK TECHM HINDALCO SBILIFE HDFCLIFE DABUR "
       "PIDILITIND HAVELLS DLF SIEMENS AMBUJACEM BANKBARODA GAIL VEDL MARICO BIOCON LUPIN "
       "AUROPHARMA TORNTPHARM BERGEPAINT COLPAL GODREJCP TVSMOTOR").split()
ETFS = ["NIFTYBEES.NS", "JUNIORBEES.NS", "GOLDBEES.NS", "LIQUIDBEES.NS", "BANKBEES.NS"]


def stats(x):
    x = x.dropna(); eq = (1+x).cumprod(); yrs = len(x)/252
    return eq.iloc[-1]**(1/yrs)-1, (x.mean()*252)/(x.std()*np.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()


def row(n, x, extra=""):
    c, s, d = stats(x); print(f"  {n:38} {c*100:6.1f}% {s:6.2f} {d*100:7.1f}%   {extra}")


def main():
    import yfinance as yf
    tk = [s+".NS" for s in NSE] + ETFS + ["^NSEI"]
    px = yf.download(tk, start="2010-01-01", auto_adjust=True, progress=False)["Close"]
    px.index = pd.to_datetime(px.index).tz_localize(None)
    px = px.loc[:, px.notna().sum() > 252].ffill()
    r = px.pct_change().clip(-0.35, 0.35); idx = px.index   # clip spurious ticks (yfinance India ETF data is glitchy)
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    NB, JB, GB, LB, BB = "NIFTYBEES.NS", "JUNIORBEES.NS", "GOLDBEES.NS", "LIQUIDBEES.NS", "BANKBEES.NS"
    REG = "^NSEI" if "^NSEI" in px else NB
    bull = (px[REG] > px[REG].rolling(200).mean()).shift(1).fillna(False)
    stocks = [c for c in px.columns if c.endswith(".NS") and c not in ETFS]

    # Sleeve 1: risk parity + target vol (Nifty / Gold / Cash)
    a = [NB, GB, LB]; iv = 1/r[a].rolling(60).std(); w = iv.div(iv.sum(1), axis=0)
    mk = np.array([d in mstart for d in idx]); w[~mk] = np.nan; w = w.ffill().shift(1).fillna(0)
    rpb = (w*r[a]).sum(1); lev = (0.12/(rpb.rolling(40).std()*np.sqrt(252))).clip(0, 2.5).shift(1).fillna(0)
    rp = (lev*rpb).fillna(0)
    # Sleeve 2: stock momentum top-10 + regime
    mom = px[stocks].shift(21)/px[stocks].shift(252)-1; wsm = pd.DataFrame(0.0, index=idx, columns=stocks); cur=[]
    for dt in idx:
        if dt in mstart and dt in mom.index:
            m = mom.loc[dt].dropna(); cur = list(m.nlargest(10).index) if len(m) >= 10 else []
        if cur and bool(bull.loc[dt]):
            for t in cur: wsm.loc[dt, t] = 1/len(cur)
    smom = ((wsm.shift(1)*r[stocks]).sum(1) - COST*wsm.diff().abs().sum(1)).fillna(0)
    # Sleeve 3: Next-50 growth + regime ; Sleeve 4: Bank Nifty high-beta + regime
    junior = (bull*r[JB]).fillna(0); bank = (bull*r[BB]).fillna(0)

    print(f"\n=== INDIA growth-tilted analog · {idx.min().date()}..{idx.max().date()} · {COST*1e4:.0f}bps/side ===\n")
    print(f"  {'sleeve / portfolio':38} {'CAGR':>7} {'Sharpe':>6} {'maxDD':>8}")
    row("Risk-parity+TV (Nifty/Gold/Cash)", rp)
    row("Stock momentum top-10 + regime", smom)
    row("Next-50 (JUNIORBEES) + regime", junior)
    row("Bank Nifty (BANKBEES) + regime", bank)
    print()
    growth = 0.25*rp + 0.35*smom + 0.25*junior + 0.15*bank
    balanced = 0.40*rp + 0.30*smom + 0.30*junior
    row("INDIA GROWTH blend", growth, "25/35/25/15")
    row("INDIA GROWTH @ 1.5x (MTF margin)", (1.5*growth - 0.5*(0.07/252)).fillna(0), "no lev-ETFs -> margin")
    row("INDIA BALANCED blend", balanced, "40/30/30")
    print()
    row("Nifty 50 buy-hold (index ^NSEI)", r.get("^NSEI", r[NB]).fillna(0))
    row("Nifty Next-50 buy-hold (JUNIORBEES)", r[JB].fillna(0))
    # current momentum picks
    last = wsm.iloc[-1]; held = [t.replace(".NS","") for t in last[last > 0].index]
    print(f"\n  current momentum picks: {', '.join(held) if held else '(regime off)'}")
    print("  NOTE: backtest only — Alpaca can't trade India; live = Indian broker (Zerodha/Upstox). Survivorship caveat on stock basket.")


if __name__ == "__main__":
    main()
