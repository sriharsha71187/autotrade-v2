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


def _tape(scan) -> tuple[float | None, str]:
    """Broad-market direction = mean day_pct of the major index ETFs present. Returns
    (tape_pct, bias) where bias is 'risk_on' / 'risk_off' / 'neutral'. This is the
    'don't fight the tape' signal — on a clearly green tape, lean long, not short."""
    vals = [float(r["day_pct"]) for r in (scan or [])
            if r.get("symbol") in ("SPY", "QQQ", "IWM", "DIA") and r.get("day_pct") is not None]
    if not vals:
        return None, "unknown"
    tape = sum(vals) / len(vals)
    if tape >= cfg.REGIME_TAPE_PCT:
        return tape, "risk_on"
    if tape <= -cfg.REGIME_TAPE_PCT:
        return tape, "risk_off"
    return tape, "neutral"


_INDEX_ETFS = ("SPY", "QQQ", "IWM", "DIA")


def _sector_trends(scan) -> dict:
    """Per-sector trend = mean day_pct of the IN-SCAN members of each sector
    (cfg.SECTOR_MAP), only for sectors with >= cfg.SECTOR_TREND_MIN_MEMBERS members
    present. The 'with the tape' fix: SPY-first can read RANGE while the book's actual
    sector (semis) is crashing. Fully defensive — a bad scan can't crash classify.
    Excludes the index_etf bucket (that's the broad tape, not a tradeable sector)."""
    out: dict = {}
    try:
        sector_map = getattr(cfg, "SECTOR_MAP", {}) or {}
        min_members = int(getattr(cfg, "SECTOR_TREND_MIN_MEMBERS", 2) or 2)
        # symbol -> day_pct for everything present in the scan (uppercased keys)
        present: dict = {}
        for r in (scan or []):
            try:
                sym = r.get("symbol")
                dp = r.get("day_pct")
                if sym is None or dp is None:
                    continue
                present[str(sym).upper()] = float(dp)
            except (TypeError, ValueError, AttributeError):
                continue
        for sect, syms in sector_map.items():
            if sect == "index_etf":
                continue
            vals = [present[str(s).upper()] for s in (syms or [])
                    if str(s).upper() in present]
            if len(vals) >= min_members:
                out[sect] = sum(vals) / len(vals)
    except Exception:
        return {}
    return out


def sector_trend_bias(sector_trends: dict, sector: str, threshold: float) -> "str | None":
    """Bias of a SECTOR's own trend, mirroring _tape's thresholds. Returns
    'risk_on' / 'risk_off' / 'neutral', or None if the sector has no reading
    (caller falls back to the broad tape_bias)."""
    if not sector_trends or sector is None:
        return None
    val = sector_trends.get(sector)
    if val is None:
        return None
    try:
        if val >= threshold:
            return "risk_on"
        if val <= -threshold:
            return "risk_off"
    except TypeError:
        return None
    return "neutral"


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


def classify(scan, vix, now=None, event_day: bool = False,
             catalyst_day: bool = False) -> dict:
    """Return the regime dict for this cycle. Pure function of (scan, vix, event_day,
    catalyst_day).

    event_day    -> whole-day FLAT (hard blackout; no new entries at all).
    catalyst_day -> NOT flat: a known binary (e.g. FOMC) is today, so SHORT-PREMIUM
                    (iron_condor / credit_spread) is disabled (don't sell into it), but
                    the DIRECTIONAL books (debit_spread / long_option / stock) stay open
                    so the bot can still RIDE a move pre- or post-event. event_day wins
                    if both are set.

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
    tape, tape_bias = _tape(scan)
    sector_trends = _sector_trends(scan)
    allowed: list[str] = []
    flat = False
    direction = None
    # Directional menu: a CAPPED debit spread AND an UNCAPPED long option (long call/put
    # — defined-risk = premium, but unlimited upside). The model picks: spread when IV is
    # rich / it's a grind, long option when there's conviction + room to run. This is the
    # "no upside caps" philosophy — winners are no longer structurally capped.
    DIRECTIONAL = ["debit_spread", "long_option"]

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
        allowed = list(DIRECTIONAL)
        direction = "long" if trend == "STRONG_UP" else "short"
        reason = (f"{idx} {move:+.1f}% STRONG {'up' if direction == 'long' else 'down'} "
                  f"— momentum WITH the trend (debit spread or uncapped long option)")
    elif trend in ("UP", "DOWN"):
        # Mild drift: PARTICIPATE with the drift (was FLAT — that sat out every orderly
        # trend day). Directional, with the move.
        allowed = list(DIRECTIONAL)
        direction = "long" if trend == "UP" else "short"
        reason = (f"{idx} {move:+.1f}% drift — trade WITH it "
                  f"(debit spread or uncapped long option)")
    else:  # RANGE
        if vol == "LOW":
            flat = True
            reason = (f"range-bound but VIX {vix:.1f} < {cfg.REGIME_VIX_LOW:.0f} — "
                      f"premium too thin to sell, flat")
        else:
            allowed = ["iron_condor", "credit_spread"]
            reason = (f"{idx} range-bound, VIX {vol} — short premium "
                      f"(0DTE condor in its window / mean-reversion credit spread)")

    # Dispersion overlay: even when the INDEX is range-bound, single names can be in a
    # strong DIRECTIONAL move (a semi breakout while SPY is flat). Enable single-name
    # momentum — both the capped debit spread AND the uncapped long option — WITH the
    # name's move. The `direction` hint and `tape` let the engine prefer trades that
    # don't fight the broad market.
    if not event_day and vol not in ("HIGH", "UNKNOWN"):
        strong = [r for r in (scan or [])
                  if r.get("symbol") not in ("SPY", "QQQ", "IWM", "DIA")
                  and abs(r.get("day_pct") or 0) >= cfg.REGIME_STRONG_PCT]
        if strong:
            for s in DIRECTIONAL:
                if s not in allowed:
                    allowed = allowed + [s]
            flat = False
            top = max(strong, key=lambda r: abs(r.get("day_pct") or 0))
            if direction is None:
                direction = "long" if (top.get("day_pct") or 0) > 0 else "short"
            reason += (f" | dispersion: {top['symbol']} {top['day_pct']:+.1f}% — "
                       f"single-name momentum (spread or uncapped long) enabled")

    # Uncapped STOCK trend trade, WITH the established direction. The fixed bracket target
    # is widened at order time so the trailing stop governs (manage_stops) — a runner runs.
    if direction and not flat:
        sl = "stock_long" if direction == "long" else "stock_short"
        if sl not in allowed:
            allowed = allowed + [sl]

    # Catalyst day (e.g. FOMC): don't sell premium INTO the binary, but keep the
    # directional books open so a real move — pre- or post-event — can still be ridden.
    if catalyst_day and not event_day and not flat:
        SHORT_PREMIUM = ("iron_condor", "credit_spread")
        stripped = [s for s in allowed if s in SHORT_PREMIUM]
        allowed = [s for s in allowed if s not in SHORT_PREMIUM]
        if stripped:
            reason += (" | CATALYST day (scheduled binary, e.g. FOMC): short premium "
                       "disabled — directional rides only (don't sell into the event)")
        else:
            reason += " | CATALYST day: directional rides only into the binary"

    return {
        "trend": trend, "vol": vol, "index": idx, "index_move": move, "vix": vix,
        "tape": tape, "tape_bias": tape_bias,
        "sector_trends": sector_trends,
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
        "tape_pct": regime.get("tape"), "tape_bias": regime.get("tape_bias"),
        "sector_trends": regime.get("sector_trends"),
        "allowed_strategies": regime.get("allowed"),
        "flat": regime.get("flat"),
        "direction": regime.get("direction"),
        "reason": regime.get("reason"),
    }
