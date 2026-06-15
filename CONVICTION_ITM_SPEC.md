# Conviction ITM book — deep-ITM, multi-day directional options

**Status:** SPEC for review (no code yet). Behind `CONVICTION_ITM_ENABLED` (default False).
**Motivation:** 2026-06-15 — near-ATM, nearest-expiry (~2–4 DTE) directional options bleed
theta and round-trip (COIN +$350 → −$96), and get force-closed same-day so a multi-day
catalyst can't play out. Deep-ITM (~0.75 delta, mostly intrinsic) held multiple days is the
right tool: low theta, ~1:1 with the stock, no spike-round-trip, survives overnight.

## 1. What it changes (vs today)
Today `option_chain_for` (autotrade.py:713) offers **nearest-to-ATM strikes** at the
**nearest expiry** (`expiries[0]`), and `passes_guardrails` (autotrade.py:2437) caps
**total premium** at `PER_OPTION_NOTIONAL_CAP` ($1,200) — which structurally **blocks**
deep-ITM (premium too high). This book adds a parallel directional mode that selects
deeper/longer contracts and sizes them on RISK, not premium.

## 2. Eligibility (when this book is used)
A name qualifies for a conviction-ITM entry (instead of the default ATM short-dated) when
ALL of:
- Regime permits directional (`long_option`/`stock_long` in `allowed`, not flat)
- The name has EITHER (a) a confirmed hard catalyst (event router / earnings drift) OR
  (b) a clean multi-day momentum trend — proposed test: STRONG_BULL/BEAR, making a new
  HOD/LOD, **positive** vwap_ext (trending intraday, not fading), RSI not blown off (<80 / >20)
- ATM IV ≤ `OPTION_MAX_ATM_IV` (120) — don't overpay
- Not already at the per-name / correlation caps

This is the entry path that would have caught SPCX (clean all-day trend) and held a
deeper/longer COIN call through its dip.

## 3. Strike & expiry selection (new selector)
Add `conviction_chain_for(odc, underlying, spot, bullish)` (or extend `option_chain_for`):
- **Strike depth:** target ~**0.75 delta**. The engine uses free data (no live greeks), so
  approximate via moneyness: call strike ≈ spot × (1 − `CONVICTION_ITM_DEPTH`), put strike ≈
  spot × (1 + `CONVICTION_ITM_DEPTH`), `CONVICTION_ITM_DEPTH` ≈ 0.07 (≈7% ITM ≈ ~0.75Δ for
  typical names). Widen the strike scan window (currently ±8% of spot) so ITM strikes are returned.
- **Expiry:** pick the expiry whose DTE is closest to the middle of
  [`CONVICTION_ITM_DTE_MIN`, `CONVICTION_ITM_DTE_MAX`] (e.g., 14–35 DTE), not `expiries[0]`.
- **Liquidity:** keep the existing wide-spread reject (illiquid contract guard).

## 4. Risk-based sizing (the key change)
Deep-ITM premium is large but realistic risk = stop distance, not full premium. Add a
SEPARATE guardrail branch for this book (don't reuse the $1,200 ATM cap):
- Define `CONVICTION_ITM_STOP_PCT` (stop on the option, e.g., −30% of premium) — for a
  ~0.75Δ option this ≈ a meaningful underlying move, not noise.
- `risk_per_contract = premium × 100 × |CONVICTION_ITM_STOP_PCT|`
- `contracts = floor(CONVICTION_ITM_RISK_TARGET / risk_per_contract)` (e.g., risk target ~$900)
- Hard ceiling on capital outlay: `premium × 100 × contracts ≤ CONVICTION_ITM_NOTIONAL_CAP`
  (e.g., $3,000) so a single name can't eat the book.
- Still counts toward `MAX_DEPLOYED_CAPITAL` ($40k) — larger premiums consume it faster (note).
- If `contracts < 1`, skip (too expensive even for one).

## 5. Multi-day holding
- Tag the position: `active_options[i]["book"] = "conviction_itm"`, `"opened_day"`, `"max_hold_days"`.
- **Exempt from the 15:45 force-close** and from `sweep_orphan_options` — exactly like the
  existing shielded books (tail hedge / earnings condor already use `held_books`/skip). Add
  `conviction_itm` symbols to that shielded set so EOD flatten leaves them alone.
- Re-judge each day (the "reassess not rigid" rule): hold while thesis intact; exit on
  trailing stop (now activate 0.25), thesis break (underlying loses the trend level /
  catalyst invalidated), `max_hold_days` reached, or DTE < `CONVICTION_ITM_MIN_DTE_EXIT`
  (e.g., 5) — close/roll before the gamma-theta cliff.

## 6. New config (all override-able; defaults conservative)
```
CONVICTION_ITM_ENABLED       = False   # master flag
CONVICTION_ITM_DEPTH         = 0.07    # strike ~7% ITM (~0.75 delta proxy)
CONVICTION_ITM_DTE_MIN       = 14
CONVICTION_ITM_DTE_MAX       = 35
CONVICTION_ITM_STOP_PCT      = -0.30   # option stop (basis for risk sizing)
CONVICTION_ITM_RISK_TARGET   = 900.0   # max $ risk per position (stop-distance based)
CONVICTION_ITM_NOTIONAL_CAP  = 3000.0  # max premium outlay per position
CONVICTION_ITM_MAX_HOLD_DAYS = 5
CONVICTION_ITM_MIN_DTE_EXIT  = 5       # close/roll under this DTE
```

## 7. Integration points (files/functions)
- `config.py` — new params + add to the override cast-map.
- `autotrade.py`:
  - `option_chain_for` / new `conviction_chain_for` — strike-depth + DTE-target selection.
  - `build_option_chains` (≈766) — when a name qualifies (§2), offer the ITM contract set.
  - decision/guardrail path (`passes_guardrails` ≈2435) — new sizing branch for `book=conviction_itm`.
  - EOD close + `sweep_orphan_options` (≈1946) — add `conviction_itm` to the shielded/`held_books` set.
  - daily management (manage_options ≈1591–1779) — multi-day re-judge + max-hold + DTE-exit.
  - state tracking — tag entries; persist `opened_day`.
- Dashboard — show conviction-ITM holds distinctly (optional, later).

## 8. Guardrails preserved
Defined risk (max loss = premium, always); PAPER hardcoded; correlation cap; deployed-capital
cap; anti-chase; IV ceiling. The tail hedge already covers crash/gap convexity.

## 9. Testing (offline, before any live enable)
1. Unit: `conviction_chain_for` returns ITM strikes at the target DTE (synthetic chain).
2. Sizing: a $25 deep-ITM call sizes to ~$900 risk (≈1 contract) and is NOT rejected by the
   old $1,200 cap; a too-expensive case returns <1 → skip.
3. EOD-flatten exemption: a `conviction_itm` position is NOT force-closed at 15:45.
4. Management: max-hold-days and DTE-exit trigger; trailing stop works on the position.
5. Dry-run a full cycle with the flag on (no orders placed) — no tracebacks, correct routing.
   (Note: a true historical P&L backtest is limited — we don't have deep-ITM price history in
   the snapshots; the value case rests on the theta/round-trip logic, validated by COIN.)

## 10. Rollout
Ship with `CONVICTION_ITM_ENABLED=False`. After review + offline tests pass, enable via the
override on a day with a clean catalyst/trend setup, and watch the first 1–2 entries live
(same bug-watch tooling). Tie the size-up decision to evidence per [[sizing-evidence-gate]].

## 11. Tradeoffs / risks (honest)
- Less leverage per dollar (deep-ITM costs more) — fewer, bigger positions; consumes
  MAX_DEPLOYED_CAPITAL faster.
- Overnight/weekend GAP risk — a 0.75Δ call still loses ~delta×gap on an overnight reversal;
  the day-only design avoided this. Mitigated by max-hold-days, daily thesis re-judge, defined
  risk, and the standing tail hedge.
- More complexity in the option book (a second sizing/holding regime to maintain).

## 12. Decisions needed from the user (defaults proposed above)
1. Target delta / depth — 0.75 (~7% ITM) ok, or deeper (0.80) / shallower (0.70)?
2. DTE window — 14–35, or longer (e.g., 21–45) for more multi-day runway?
3. Max hold days — 5 trading days, or longer?
4. Routing — DECIDED 2026-06-15: engine AUTO-selects ITM-vs-ATM by setup type (deep-ITM/
   multi-day for hard-catalyst or clean multi-day trend; ATM short-dated stays for intraday
   scalps). Deterministic; model never reasons about option structure.
5. Risk/notional per position — $900 risk / $3,000 outlay, or different?
