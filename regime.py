#!/usr/bin/env python3
"""
regime.py — deterministic market-regime classifier + strategy gate.

The core of the evidence-driven redesign (research 2026-06-07). Instead of letting
the model pick a strategy freely from a menu, a PURE-CODE classifier reads the
current market state (index trend, VIX band, event-day flag) and decides WHICH
strategies are permitted this cycle — or forces the book FLAT. The model then only
fills in strikes within the pre-authorized structure; it can never trade a strategy
the regime disallows (autotrade.passes_guardrails enforces `entry_allowed`).

This module is intentionally side-effect free (no broker, no logging, no I/O) so it
is trivially unit-testable and deterministic: same inputs -> same regime, always.

Regime -> action map (the approved design):
  - Event/econ-blackout day .................. FLAT (wait for the print)
  - VIX HIGH (>= REGIME_VIX_HIGH) ............ FLAT intraday (short-vol is lethal;
                                               only the tail-hedge book stays on)
  - STRONG trend (|index move| >= STRONG) .... momentum DEBIT spread only
  - mild drift (TREND..STRONG) ............... FLAT (chop / no clean edge)
  - RANGE + VIX >= LOW ....................... short premium: iron condor +
                                               mean-reversion credit spread
  - RANGE + VIX < LOW ........................ FLAT (premium too thin to sell)
  - missing VIX / index data ................. FLAT (don't trade blind)

`allowed` holds strategy labels matching autotrade._decision_strategy:
  "iron_condor", "credit_spread", "debit_spread". Naked directional stock trades
  ("stock_long"/"stock_short") and bare "long_option" are never permitted under the
  regime engine — that is the momentum demotion (defined-risk structures only).
"""

from __future__ import annotations

import config as cfg


def _index_move(scan) -> tuple[float | None, str | None]:
    """Today's % move of the broad index, preferring SPY then QQQ, from the scan."""
    for sym in ("SPY", "QQQ"):
        for r in scan or []:
            if r.get("symbol") == sym and r.get("day_pct") is not None:
                return float(r["day_pct"]), sym
    return None, None


def _vol_band(vix) -> str:
    if vix is None:
        return "UNKNOWN"
    if vix >= cfg.REGIME_VIX_HIGH:
        return "HIGH"
    if vix >= cfg.REGIME_VIX_ELEVATED:
        return "ELEVATED"
    if vix >= cfg.REGIME_VIX_LOW:
        return "NORMAL"
    return "LOW"


def _trend_band(move) -> str:
    if move is None:
        return "UNKNOWN"
    if move >= cfg.REGIME_STRONG_PCT:
        return "STRONG_UP"
    if move <= -cfg.REGIME_STRONG_PCT:
        return "STRONG_DOWN"
    if move >= cfg.REGIME_TREND_PCT:
        return "UP"
    if move <= -cfg.REGIME_TREND_PCT:
        return "DOWN"
    return "RANGE"


def classify(scan, vix, now=None, event_day: bool = False) -> dict:
    """Return the regime dict for this cycle. Pure function of (scan, vix, event_day).

    Keys:
      trend, vol .... band labels (see above)
      index, index_move ... which index drove the trend call, and its % move
      allowed ....... list of permitted strategy labels (possibly empty)
      flat .......... True -> no new intraday entries this cycle
      direction ..... "long"/"short" hint for momentum debit spreads (else None)
      reason ........ human-readable explanation (logged + shown to the model)
    """
    move, idx = _index_move(scan)
    vol = _vol_band(vix)
    trend = _trend_band(move)
    allowed: list[str] = []
    flat = False
    direction = None

    if event_day:
        flat = True
        reason = "econ blackout / event day — flat until the print"
    elif vol == "UNKNOWN" or trend == "UNKNOWN":
        flat = True
        reason = "missing VIX or index data — standing down (won't trade blind)"
    elif vol == "HIGH":
        flat = True
        reason = (f"VIX {vix:.1f} >= {cfg.REGIME_VIX_HIGH:.0f} (HIGH) — short-vol "
                  f"lethal here; intraday flat, tail hedge only")
    elif trend in ("STRONG_UP", "STRONG_DOWN"):
        allowed = ["debit_spread"]
        direction = "long" if trend == "STRONG_UP" else "short"
        reason = (f"{idx} {move:+.1f}% STRONG {'up' if direction == 'long' else 'down'} "
                  f"— momentum defined-risk debit spread only")
    elif trend in ("UP", "DOWN"):
        flat = True
        reason = (f"{idx} {move:+.1f}% mild drift (chop, not a clean trend or range) "
                  f"— no high-quality setup, flat")
    else:  # RANGE
        if vol == "LOW":
            flat = True
            reason = (f"range-bound but VIX {vix:.1f} < {cfg.REGIME_VIX_LOW:.0f} — "
                      f"premium too thin to sell, flat")
        else:
            allowed = ["iron_condor", "credit_spread"]
            reason = (f"{idx} range-bound, VIX {vol} — short premium "
                      f"(0DTE condor in its window / mean-reversion credit spread)")

    return {
        "trend": trend, "vol": vol, "index": idx, "index_move": move, "vix": vix,
        "allowed": allowed, "flat": flat, "direction": direction, "reason": reason,
    }


def entry_allowed(regime: dict, strategy_label: str) -> tuple[bool, str]:
    """Gate one entry decision against the regime. `strategy_label` is the output of
    autotrade._decision_strategy(decision). Returns (ok, reason-if-blocked)."""
    if regime.get("flat"):
        return False, f"regime FLAT — {regime.get('reason', '')}"
    if strategy_label in regime.get("allowed", []):
        return True, ""
    allowed = regime.get("allowed") or ["none"]
    return False, (f"strategy '{strategy_label}' not allowed in regime "
                   f"{regime.get('trend')}/{regime.get('vol')} "
                   f"(allowed: {', '.join(allowed)})")


def summary(regime: dict) -> dict:
    """Compact form for the model context / status line."""
    return {
        "trend": regime.get("trend"), "vol": regime.get("vol"),
        "index_move_pct": regime.get("index_move"),
        "allowed_strategies": regime.get("allowed"),
        "flat": regime.get("flat"),
        "direction": regime.get("direction"),
        "reason": regime.get("reason"),
    }
