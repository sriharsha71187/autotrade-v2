#!/usr/bin/env python3
"""India momentum sleeve: top-10 NSE stocks by 6-month momentum, monthly rebalance, regime-gated
(Nifty>200DMA), equal-weight. 1.0x and 1.5x (MTF margin). From 2020. vs Nifty buy-hold.
Run: python research/backtest_india_momentum.py
"""
import math
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
COST = 0.0010; BORROW = 0.10   # India: ~10bps/side, MTF margin ~10%/yr

NSE = ("RELIANCE TCS INFY HDFCBANK ICICIBANK HINDUNILVR ITC SBIN BHARTIARTL KOTAKBANK LT AXISBANK "
       "BAJFINANCE ASIANPAINT MARUTI SUNPHARMA TITAN ULTRACEMCO WIPRO ONGC NTPC POWERGRID NESTLEIND "
       "HCLTECH TATASTEEL JSWSTEEL ADANIENT ADANIPORTS COALINDIA GRASIM DRREDDY CIPLA BRITANNIA "
       "EICHERMOT HEROMOTOCO BPCL INDUSINDBK TECHM HINDALCO SBILIFE HDFCLIFE DABUR PIDILITIND HAVELLS "
       "DLF SIEMENS AMBUJACEM BANKBARODA GAIL VEDL MARICO BIOCON LUPIN AUROPHARMA TORNTPHARM BERGEPAINT "
       "COLPAL GODREJCP TVSMOTOR").split()


def stats(r):
    r = r.dropna(); eq = (1+r).cumprod(); yrs = len(r)/252
    return eq.iloc[-1]**(1/yrs)-1, (r.mean()*252)/(r.std()*math.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()


def main():
    import yfinance as yf
    tk = [s+".NS" for s in NSE] + ["^NSEI"]
    px = yf.download(tk, start="2018-06-01", auto_adjust=True, progress=False)["Close"]
    px.index = pd.to_datetime(px.index).tz_localize(None)
    px = px.loc[:, px.notna().sum() > 252].ffill()
    r = px.pct_change().clip(-0.35, 0.35)
    idx = px.index; stocks = [c for c in px.columns if c.endswith(".NS")]
    mom = px[stocks].shift(21)/px[stocks].shift(147) - 1
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    REG = "^NSEI" if "^NSEI" in px else None
    bull = (px[REG] > px[REG].rolling(200).mean()).shift(1).fillna(False) if REG else pd.Series(True, index=idx)
    wsm = pd.DataFrame(0.0, index=idx, columns=stocks); cur = []
    for dt in idx:
        if dt in mstart and dt in mom.index:
            m = mom.loc[dt].dropna(); cur = list(m.nlargest(10).index) if len(m) >= 10 else []
        if cur and bool(bull.loc[dt]):
            for t in cur: wsm.loc[dt, t] = 1/len(cur)
    smom = ((wsm.shift(1)*r[stocks]).sum(1) - COST*wsm.diff().abs().sum(1)).fillna(0)
    lev15 = (1.5*smom - 0.5*(BORROW/252)).fillna(0)

    start = pd.Timestamp("2020-01-01")
    sub = smom[smom.index >= start]; sub15 = lev15[lev15.index >= start]
    nif = r["^NSEI"][r["^NSEI"].index >= start] if REG else None
    print(f"\n=== India momentum (top-10, 6mo, regime-gated) · {sub.index[0].date()}..{sub.index[-1].date()} ===\n")
    print(f"  {'strategy':30}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}")
    for lbl, s in [("momentum 1.0x (no margin)", sub), ("momentum 1.5x (MTF margin)", sub15)]:
        c, sh, dd = stats(s); print(f"  {lbl:30}{c*100:7.1f}%{sh:7.2f}{dd*100:7.0f}%")
    if nif is not None:
        c, sh, dd = stats(nif); print(f"  {'Nifty 50 buy-hold':30}{c*100:7.1f}%{sh:7.2f}{dd*100:7.0f}%")
    # current picks
    last = wsm.iloc[-1]; held = [t.replace('.NS','') for t in last[last > 0].index]
    print(f"\n  current picks: {', '.join(held) if held else '(regime off -> cash)'}")
    print("  CAVEAT: NSE universe = today's large-caps (SURVIVORSHIP-inflated, like the US version) + glitchy")
    print("  yfinance India data (clipped). Magnitude optimistic; not Alpaca/IBKR-US tradable (needs Indian broker).")


if __name__ == "__main__":
    main()
