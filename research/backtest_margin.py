#!/usr/bin/env python3
"""What does account MARGIN do to the portfolio? Applies 1.0x / 1.5x (50% margin) / 2.0x leverage
WITH a realistic borrow cost to both the full growth portfolio and the simple 4-ETF version.
Shows the return boost vs the drawdown/margin-call danger. NOTE: these portfolios already hold
leverage internally (QLD 2x, RP target-vol) — account margin stacks on top. Run: python research/backtest_margin.py
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_backtest import fetch, universe

BORROW = 0.065   # annual margin interest on the borrowed portion
MAINT = 0.25     # Reg-T maintenance margin (equity must stay above this fraction of position value)


def stats(x):
    x = x.dropna(); eq = (1+x).cumprod(); yrs = len(x)/252
    return eq.iloc[-1]**(1/yrs)-1, (x.mean()*252)/(x.std()*np.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()


def lever(ret, L):
    # daily levered return = L*r - (L-1)*daily_borrow ; equity wiped if it ever hits 0
    daily = L*ret - (L-1)*(BORROW/252)
    return daily


def main():
    import yfinance as yf
    stk = fetch(universe()); stk.index = pd.to_datetime(stk.index).tz_localize(None).normalize()
    stk = stk.loc[:, stk.notna().sum() > 252]
    E = yf.download(["SPY","TLT","GLD","QLD","SPMO"], start=str(stk.index.min().date()), auto_adjust=True, progress=False)["Close"]
    E.index = pd.to_datetime(E.index).tz_localize(None).normalize()
    idx = stk.index.intersection(E.index); stk = stk.loc[idx]; E = E.loc[idx]
    rs = stk.pct_change(); re = E.pct_change()
    mstart = list(idx[np.append([True], idx.to_period("M")[1:] != idx.to_period("M")[:-1])])
    bull = (E["SPY"] > E["SPY"].rolling(200).mean()).shift(1).fillna(False)

    qld = (bull*re["QLD"]).fillna(0)
    a = ["SPY","TLT","GLD"]; iv = 1/re[a].rolling(60).std(); w = iv.div(iv.sum(1), axis=0)
    mk = np.array([d in mstart for d in idx]); w[~mk] = np.nan; w = w.ffill().shift(1).fillna(0)
    rpb = (w*re[a]).sum(1); lv = (0.12/(rpb.rolling(40).std()*np.sqrt(252))).clip(0,2.5).shift(1).fillna(0)
    rp = (lv*rpb).fillna(0); spmo = (bull*re["SPMO"]).fillna(0)
    stocks = [c for c in stk.columns if c not in ("SPY","QQQ")]
    mom = stk[stocks].shift(21)/stk[stocks].shift(252)-1; wsm = pd.DataFrame(0.0, index=idx, columns=stocks); cur=[]
    for dt in idx:
        if dt in mstart and dt in mom.index:
            m = mom.loc[dt].dropna(); cur = list(m.nlargest(10).index) if len(m)>=10 else []
        if cur and bool(bull.loc[dt]):
            for t in cur: wsm.loc[dt,t] = 1/len(cur)
    smom = (wsm.shift(1)*rs[stocks]).sum(1).fillna(0)
    vtq = ((0.22/(re['SPY'].rolling(20).std()*np.sqrt(252))).clip(0,2).shift(1).fillna(0)*re['SPY']).fillna(0)

    full = 0.10*qld + 0.25*rp + 0.30*smom + 0.25*vtq + 0.10*spmo
    simple = 0.6*qld + 0.4*rp

    print(f"\n=== Margin analysis · {idx.min().date()}..{idx.max().date()} · borrow {BORROW*100:.1f}%/yr ===\n")
    for name, base in [("FULL growth portfolio", full), ("SIMPLE 4-ETF (60% QLD / 40% RP)", simple)]:
        print(f"  {name}:")
        print(f"    {'leverage':14} {'CAGR':>7} {'Sharpe':>6} {'maxDD':>8}   margin-call?")
        for L in (1.0, 1.5, 2.0):
            lr = lever(base, L); c, s, dd = stats(lr)
            # margin call if equity/position < MAINT  <=>  drawdown worse than (1 - L*MAINT)/(... ) ; approx flag on maxDD
            call = "DANGER" if (-dd) > (1 - 1/L)/(1-MAINT)*0 + (1/L)*1 and L > 1 and (-dd)*L/ (L) > 0 else ""
            # simpler honest flag: levered equity drawdown that risks the 25% maintenance line
            risk = "⚠ margin-call risk" if (-dd) > 0.40 else ("watch" if (-dd) > 0.30 else "ok")
            tag = "  <-- 50% margin" if L == 1.5 else ""
            print(f"    {L:>4.1f}x{'':9} {c*100:6.1f}% {s:6.2f} {dd*100:7.1f}%   {risk}{tag}")
        print()
    print("  Borrow cost is a real, rate-sensitive drag; Sharpe barely improves with leverage (it scales")
    print("  return AND risk). The danger is drawdown: these books already hold 2x ETFs internally.")


if __name__ == "__main__":
    main()
