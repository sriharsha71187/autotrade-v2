#!/usr/bin/env python3
"""
overnight_conviction.py — gate that decides whether a momentum DEBIT spread has
earned the right to ride OVERNIGHT instead of being force-closed at 15:45.

The thesis (post-news / post-earnings-announcement drift): a move continues
overnight only when it's an UNDER-reaction to a concrete CATALYST. A pop on no news
mean-reverts. So an overnight hold requires:

  #1 CATALYST (mandatory, model's call) — the model sets decision['overnight_hold']
     only after reading the news context and judging a HARD catalyst (earnings/
     guidance/M&A/contract/regulatory/substantive upgrade). This module trusts the
     model for the catalyst classification but ENFORCES the data below.
  #2-#5 DATA confirmation (need >= OVERNIGHT_MIN_DATA_CONFIRMS of 4):
       - RVOL >= OVERNIGHT_RVOL_MIN          (heavy volume = real participation)
       - closes strong (near HOD/LOD)         (buyers/sellers control the close)
       - sector breadth                       (a theme, not a lone pop)
       - not exhausted (RSI within bound)     (not a blow-off top/bottom)
  #6 NO binary against it — the name does not report its OWN earnings tonight.

Also: only judged in the "decide-near-the-close" window (>= OVERNIGHT_DECISION time),
so close-strength is meaningful, and only for single-name (non-index) momentum debit
spreads WITH the trend.

Pure-ish: reads yfinance for RVOL and the earnings calendar; no order side effects.
"""

from __future__ import annotations

import config as cfg

try:
    import yfinance as yf
    _YF_OK = True
except Exception:
    _YF_OK = False


def rvol(symbol: str):
    """Relative volume: today's volume / trailing 20-day average. None on failure."""
    if not _YF_OK:
        return None
    try:
        df = yf.download(symbol, period="1mo", interval="1d", progress=False)
        if getattr(df.columns, "nlevels", 1) > 1:
            df.columns = df.columns.get_level_values(0)
        vol = df["Volume"].dropna()
        if len(vol) < 6:
            return None
        today_v = float(vol.iloc[-1])
        avg = float(vol.iloc[-21:-1].mean())
        return today_v / avg if avg > 0 else None
    except Exception:
        return None


def evaluate(decision: dict, scan_row: dict, scan: list, now) -> tuple[bool, str]:
    """Return (hold_overnight_ok, reason). Gates a single-name momentum DEBIT spread
    for an overnight hold. Catalyst is the model's call (decision['overnight_hold']);
    here we enforce the data + binary-risk checks."""
    if not cfg.OVERNIGHT_MOMENTUM_ENABLED:
        return False, "overnight momentum disabled"
    if decision.get("action") != "multi_leg":
        return False, "not a spread"
    try:
        net = float(decision.get("net_price"))
    except Exception:
        return False, "no net_price"
    if net >= 0:
        return False, "credit spread — only momentum DEBIT spreads ride overnight"
    sym = decision.get("symbol")
    if not sym or sym in cfg.PREMIUM_INDEX_UNDERLYINGS:
        return False, "index/no-symbol — overnight hold is for single-name momentum"
    if not scan_row:
        return False, "no scan data for the name"
    sig = scan_row.get("signal") or ""
    if sig not in ("STRONG_BULL", "STRONG_BEAR"):
        return False, f"{sym} not a strong mover (signal={sig or 'none'})"

    # Decide-near-the-close window, so close-strength is real.
    mins = now.hour * 60 + now.minute
    if mins < cfg.OVERNIGHT_DECISION_HOUR * 60 + cfg.OVERNIGHT_DECISION_MIN:
        return False, (f"too early — overnight hold only judged after "
                       f"{cfg.OVERNIGHT_DECISION_HOUR}:{cfg.OVERNIGHT_DECISION_MIN:02d} ET")

    # #6 binary risk — own earnings tonight kills the overnight hold.
    try:
        import earnings_crush as ec
        if ec._earnings_today(sym, now.date()):
            return False, f"{sym} reports earnings tonight — no overnight hold (binary gap risk)"
    except Exception:
        pass

    bull = sig == "STRONG_BULL"
    confirms, fails = [], []

    # #2 RVOL
    rv = rvol(sym)
    if rv is not None and rv >= cfg.OVERNIGHT_RVOL_MIN:
        confirms.append(f"RVOL {rv:.1f}x")
    else:
        fails.append(f"RVOL {('%.1fx' % rv) if rv is not None else 'n/a'}")

    # #3 closes strong (near the day's extreme in the trend direction)
    off = scan_row.get("off_hod") if bull else scan_row.get("off_lod")
    if off is not None and abs(off) <= cfg.OVERNIGHT_OFF_HOD_MAX:
        confirms.append(f"near {'HOD' if bull else 'LOD'} ({abs(off):.1f}%)")
    else:
        fails.append(f"off {'HOD' if bull else 'LOD'} {off if off is not None else 'n/a'}")

    # #4 sector/market breadth — same-direction strong movers
    n = sum(1 for r in (scan or [])
            if r.get("signal") == sig and r.get("symbol") not in cfg.PREMIUM_INDEX_UNDERLYINGS)
    if n >= cfg.OVERNIGHT_BREADTH_MIN:
        confirms.append(f"breadth {n}")
    else:
        fails.append(f"breadth {n}<{cfg.OVERNIGHT_BREADTH_MIN}")

    # #5 not exhausted
    rsi = scan_row.get("rsi")
    if rsi is not None:
        ok_rsi = rsi <= cfg.OVERNIGHT_RSI_MAX if bull else rsi >= (100 - cfg.OVERNIGHT_RSI_MAX)
        (confirms if ok_rsi else fails).append(f"RSI {rsi:.0f}")
    else:
        fails.append("RSI n/a")

    if len(confirms) >= cfg.OVERNIGHT_MIN_DATA_CONFIRMS:
        return True, f"catalyst(model) + {', '.join(confirms)}"
    return False, (f"only {len(confirms)}/{cfg.OVERNIGHT_MIN_DATA_CONFIRMS} data confirms "
                   f"[{', '.join(confirms) or 'none'}] fails: {', '.join(fails)}")
