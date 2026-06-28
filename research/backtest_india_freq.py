#!/usr/bin/env python3
"""Rebalance-frequency test on the INDIA growth portfolio: monthly vs bi-weekly vs weekly.
India costs are higher (STT/stamp/brokerage ~10bps/side) so turnover bites harder. Cost-aware.
Run: python research/backtest_india_freq.py
"""
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
COST = 0.0010   # India: ~10bps/side

NSE = ("RELIANCE TCS INFY HDFCBANK ICICIBANK HINDUNILVR ITC SBIN BHARTIARTL KOTAKBANK LT AXISBANK "
       "BAJFINANCE ASIANPAINT MARUTI SUNPHARMA TITAN ULTRACEMCO WIPRO ONGC NTPC POWERGRID NESTLEIND "
       "HCLTECH TATASTEEL JSWSTEEL ADANIENT ADANIPORTS COALINDIA GRASIM DRREDDY CIPLA BRITANNIA "
       "EICHERMOT HEROMOTOCO BPCL INDUSINDBK TECHM HINDALCO SBILIFE HDFCLIFE DABUR PIDILITIND "
       "HAVELLS DLF SIEMENS AMBUJACEM BANKBARODA GAIL VEDL MARICO BIOCON LUPIN AUROPHARMA TORNTPHARM "
       "BERGEPAINT COLPAL GODREJCP TVSMOTOR").split()
ETFS = ["NIFTYBEES.NS", "JUNIORBEES.NS", "GOLDBEES.NS", "LIQUIDBEES.NS", "BANKBEES.NS"]


def stats(x):
    x = x.dropna(); eq = (1+x).cumprod(); yrs = len(x)/252
    return eq.iloc[-1]**(1/yrs)-1, (x.mean()*252)/(x.std()*np.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()


def main():
    import yfinance as yf
    tk = [s+".NS" for s in NSE] + ETFS + ["^NSEI"]
    px = yf.download(tk, start="2010-01-01", auto_adjust=True, progress=False)["Close"]
    px.index = pd.to_datetime(px.index).tz_localize(None)
    px = px.loc[:, px.notna().sum() > 252].ffill()
    r = px.pct_change().clip(-0.35, 0.35); idx = px.index
    NB, JB, GB, LB, BB = "NIFTYBEES.NS", "JUNIORBEES.NS", "GOLDBEES.NS", "LIQUIDBEES.NS", "BANKBEES.NS"
    REG = "^NSEI" if "^NSEI" in px else NB
    bull = (px[REG] > px[REG].rolling(200).mean()).shift(1).fillna(False)
    stocks = [c for c in px.columns if c.endswith(".NS") and c not in ETFS]
    mom = px[stocks].shift(21)/px[stocks].shift(252)-1
    junior = (bull*r[JB]).fillna(0); bank = (bull*r[BB]).fillna(0)

    def sleeves_at(step):
        rb = set(idx[::step])
        wsm = pd.DataFrame(0.0, index=idx, columns=stocks); cur=[]
        for dt in idx:
            if dt in rb and dt in mom.index:
                m = mom.loc[dt].dropna(); cur = list(m.nlargest(10).index) if len(m)>=10 else []
            if cur and bool(bull.loc[dt]):
                for t in cur: wsm.loc[dt, t] = 1/len(cur)
        smom = ((wsm.shift(1)*r[stocks]).sum(1) - COST*wsm.diff().abs().sum(1)).fillna(0)
        turn = wsm.diff().abs().sum(1).sum()/(len(idx)/252)
        a = [NB, GB, LB]; iv = 1/r[a].rolling(60).std(); w = iv.div(iv.sum(1), axis=0)
        mk = np.array([d in rb for d in idx]); w[~mk] = np.nan; w = w.ffill().shift(1).fillna(0)
        rpb = (w*r[a]).sum(1); lev = (0.12/(rpb.rolling(40).std()*np.sqrt(252))).clip(0,2.5).shift(1).fillna(0)
        rp = (lev*rpb - COST*(w.mul(lev,axis=0)).diff().abs().sum(1)).fillna(0)
        return rp, smom, turn

    print(f"\n=== INDIA growth portfolio · rebalance frequency · {idx.min().date()}..{idx.max().date()} · {COST*1e4:.0f}bps/side ===\n")
    print(f"  {'frequency':16} {'CAGR':>7} {'Sharpe':>6} {'maxDD':>8} {'mom-turnover/yr':>16}")
    for step, lbl in [(21, "monthly"), (10, "bi-weekly"), (5, "weekly")]:
        rp, smom, turn = sleeves_at(step)
        blend = 0.25*rp + 0.35*smom + 0.25*junior + 0.15*bank
        c, s, dd = stats(blend)
        print(f"  {lbl:16} {c*100:6.1f}% {s:6.2f} {dd*100:7.1f}% {turn*100:14.0f}%")
    print("\n  India 10bps/side (vs US 5bps) -> faster rebalance costs ~2x as much per unit turnover.")


if __name__ == "__main__":
    main()
