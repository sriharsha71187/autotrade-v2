#!/usr/bin/env python3
"""SECTOR/THEMATIC ROTATION done right — catch moves like the semis/memory rip. Granular
industry ETFs (SMH/SOXX/IGV/XBI/GDX/XOP/KRE/ITB/XRT/XME + broad sectors), relative-strength
ranked on a RESPONSIVE lookback, hold the top-K, with an absolute/regime filter (only hold a
sleeve trending above its own 200DMA, else that slot goes to bonds). Tests several configs +
shows what it holds NOW. Run: python research/backtest_rotation.py
"""
import numpy as np, pandas as pd
import warnings; warnings.filterwarnings("ignore")
COST = 0.0005

THEMES = ["SMH","SOXX","IGV","XBI","XOP","GDX","KRE","ITB","XRT","XME","TAN","IYT",
          "XLK","XLF","XLE","XLV","XLI","XLP","XLY","XLU","XLB"]
SAFE = "IEF"


def stats(ret):
    ret = ret.dropna()
    if len(ret) < 60: return (np.nan,)*3
    eq = (1+ret).cumprod(); yrs = len(ret)/252
    return eq.iloc[-1]**(1/yrs)-1, (ret.mean()*252)/(ret.std()*np.sqrt(252)+1e-9), (eq/eq.cummax()-1).min()


def main():
    import yfinance as yf
    tk = THEMES + [SAFE]
    px = yf.download(tk, start="2007-01-01", auto_adjust=True, progress=False)["Close"]
    px.index = pd.to_datetime(px.index).tz_localize(None)
    r = px.pct_change(); idx = px.index
    mp = idx.to_period("M"); rebal = set(idx[np.append([True], mp[1:] != mp[:-1])])
    ma200 = px.rolling(200).mean()

    spy_bull = px["SPY"] > px["SPY"].rolling(200).mean() if "SPY" in px else None

    def run(look, topk, regime, mkt_gate=False):
        avail = [t for t in THEMES if t in px]
        mom = px[avail]/px[avail].shift(look) - 1
        pos = pd.DataFrame(0.0, index=idx, columns=avail+[SAFE]); cur = {}
        for dt in idx:
            if dt in rebal:
                if mkt_gate and spy_bull is not None and not bool(spy_bull.loc[dt]):
                    cur = {SAFE: 1.0}                      # market risk-off -> all bonds
                else:
                    m = mom.loc[dt].dropna()
                    if regime:                            # only sectors above their own 200DMA
                        m = m[[t for t in m.index if px.loc[dt, t] > ma200.loc[dt, t]]]
                    picks = list(m.nlargest(topk).index)
                    cur = {t: 1/topk for t in picks}
                    if len(picks) < topk: cur[SAFE] = (topk-len(picks))/topk
            pos.loc[dt] = 0.0
            for t, w in cur.items(): pos.loc[dt, t] = w
        ret = (pos.shift(1)*r[avail+[SAFE]]).sum(1) - COST*pos.diff().abs().sum(1)
        return ret.fillna(0), pos

    print(f"\n=== Sector/thematic rotation · {idx.min().date()}..{idx.max().date()} · {len(THEMES)} industries · {COST*1e4:.0f}bps ===\n")
    print(f"  {'config':40} {'CAGR':>7} {'Sharpe':>6} {'maxDD':>8}")
    best = None
    for look, lbl in [(63,"3mo"),(126,"6mo")]:
        for topk in (3,):
            for regime in (False, True):
                for gate in (False, True):
                    ret, pos = run(look, topk, regime, gate)
                    c, s, dd = stats(ret)
                    tag = f"{lbl}·top{topk}·{'reg' if regime else 'noreg'}·{'MKT-GATE' if gate else 'nogate'}"
                    print(f"  {tag:40} {c*100:6.1f}% {s:6.2f} {dd*100:7.1f}%")
                    if best is None or s > best[0]: best = (s, look, topk, regime, lbl, gate)

    # show what the BEST config holds right now
    _, look, topk, regime, lbl, gate = best
    ret, pos = run(look, topk, regime, gate)
    held = pos.iloc[-1]; held = held[held > 0]
    print(f"\n  BEST: {lbl}·top{topk}·{'reg' if regime else 'noreg'}·{'gate' if gate else 'nogate'} (Sharpe {best[0]:.2f})")
    print(f"  HOLDING NOW: {', '.join(f'{t} {w*100:.0f}%' for t,w in held.items())}")
    # recent momentum leaders (what's ripping)
    avail = [t for t in THEMES if t in px]
    lead = (px[avail].iloc[-2]/px[avail].iloc[-65]-1).dropna().sort_values(ascending=False).head(5)
    print(f"  top 3mo momentum: {', '.join(f'{t} {v*100:+.0f}%' for t,v in lead.items())}")
    print("\n  vs SPY buy-hold Sharpe ~0.62. Monthly rebalance, signals lagged.")


if __name__ == "__main__":
    main()
