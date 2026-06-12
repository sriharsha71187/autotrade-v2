# Event Router — design spec

Status: PROPOSED (not built). Author: pairing session 2026-06-11.

## 1. Why

Today the bot is ~90% technical. It reacts to price *after* a move, and our
(recently strengthened) anti-chase guard then *blocks* the very event-driven
continuations it should ride (it saw the post-Iran energy/defense spike as
"extended", refused it). Event/news data is currently one headline feed the model
skims — no structure, no posture, no opportunity routing.

The insight that shapes this: **one event datum is two-sided.** The *same* CPI print
is a reason to NOT sell a blind condor beforehand AND a reason to trade the reaction
afterward. A geopolitical shock is risk-off for the index AND a long catalyst for
energy/defense. The router's job is to decide *which side* applies, by whether the
move's direction is already known.

Design stays inside the house rules: **deterministic code detects/maps/sets posture;
the model picks the trade; code protects** (defined-risk only, −$1500 floor, all
flag-gated, all runtime-settable).

## 2. Architecture

New module `events.py`, called once per cycle from `run_cycle` right after the scan
is built and before `build_universe` is finalized. It produces an `EventState` that
flows into three places:

```
run_cycle:
  raw_events   = events.detect(news, econ_calendar, now)      # both feeds
  event_state  = events.assess(raw_events, scan, vix, now)    # posture + targets
  universe    += event_state.inject_symbols                   # pull affected names in
  scan         = signal_scan(universe)                         # now includes them
  context["events"] = events.summary(event_state)             # model sees it
  ... passes_guardrails(..., event_state=event_state)          # posture changes gates
```

No new broker surface. No new always-on risk. It only *adds* candidates and *adjusts*
existing gates; with the flag off it is a no-op.

## 3. Data sources (3)

### 3a. Breaking-event detection (the opportunity half — build first)
Source: the existing Alpaca news feed (`breaking_news`), widened to market-wide
(not just held names), plus a high-impact keyword/classifier pass.
- Match event signatures: geopolitical (`strike`, `sanctions`, `Iran`, `war`,
  `ceasefire`, `attack`), monetary (`Fed`, `rate cut/hike`, `Powell`), shock macro
  (`tariff`, `default`, `downgrade` of US debt), big single-name (`M&A`, `acquires`,
  `guidance`, `halts`, `recall`, `FDA`).
- Dedup + freshness: only events in the last `EVENT_FRESH_MIN` minutes count as
  "live" (a 3-hour-old headline is priced in).
- Confidence: keyword hit = candidate; the model confirms it's a real catalyst (same
  division of labor as the overnight-conviction gate — code screens, model judges).

### 3b. Scheduled econ calendar (the posture half)
Source: a free economic-calendar feed (e.g. a daily pull cached to disk like the
growth screen). Each entry: `{time_et, name, impact: high|med|low, actual?, consensus?}`.
- Pre-event window: `EVENT_PRE_MIN` minutes before a HIGH-impact release.
- Post-event: once `actual` posts, the surprise (actual vs consensus) is the
  directional signal.
- MVP fallback if no clean feed: a hand-maintained `ECON_CALENDAR` in config (FOMC
  dates, CPI/PPI/NFP are known months ahead) — strictly better than today's single
  blackout-date list, and upgradeable to a live feed later.

### 3c. IV rank (the flip enabler)
The same vol signal, read both ways depending on timing. Premium-selling wants RICH
IV; directional debit spreads are fine in cheap IV.
- Per-index IV proxy: store a rolling 30–60d history of VIX (and per-underlying ATM
  IV from the option chain snapshot when available) → compute `iv_rank` (0–100
  percentile). Persist daily like the growth screen cache.
- Honest caveat (same as `backtest.py`): true per-name historical IV needs paid data.
  MVP uses VIX percentile for the index books + a stored ATM-IV baseline we accumulate
  ourselves over time. Label it as a proxy in the context.

## 4. Event → affected-instrument map

Two mechanisms, used together:
1. **Theme map** (deterministic, in config) — fast, no model call:
   ```
   EVENT_THEME_MAP = {
     "oil_geopolitical": {long: [XLE, USO, XOM, CVX, ITA, LMT, RTX, GLD],
                          short: [JETS, AAL, DAL, UAL, CCL],  risk: "off"},
     "rate_dovish":      {long: [SPY, QQQ, XLK, IWM, GLD],     short: [],  risk: "on"},
     "rate_hawkish":     {long: [],  short: [QQQ, IWM, XLK],   risk: "off"},
     ...
   }
   ```
2. **Model fallback** — for an event with no theme match, hand the headline to the
   model: "name the 3–5 most-affected LIQUID instruments and direction." Cached per
   event so it costs one call, not one per cycle.

Output: a set of `(symbol, direction, catalyst_text)` injected into the universe so
the scan *sees them now*, not after they climb the movers list.

## 5. Decision logic — the two-sided posture

`assess()` resolves each live event into one of these postures, surfaced to the model
and enforced in `passes_guardrails`:

| Posture | Trigger | Effect on trading |
|---|---|---|
| **RIDE** (offensive) | breaking directional event, fresh, direction known | inject affected names; **relax anti-chase** for them (see §6); tag as hard catalyst → momentum debit spread WITH the move, defined-risk; **overnight-hold eligible** |
| **BRACE** (defensive) | scheduled HIGH-impact release within `EVENT_PRE_MIN` | no new short-premium (condors/credit spreads) into the binary; trim new size; existing defined-risk positions ride (already capped) |
| **FADE-VOL** (premium) | post-event, vol spiked, direction now choppy/digested, `iv_rank` high | favor INDEX premium-selling (sell the rich IV into the crush) — the existing condor/credit path, now *encouraged* instead of VIX-band-gated |
| **NEUTRAL** | no live event | today's behavior, unchanged |

Notes:
- RIDE and BRACE can co-exist (Iran RIDE on energy + BRACE the index condor).
- Posture is per-cycle, recomputed; nothing latches except the once-per-event model
  mapping call.

## 6. Anti-chase relaxation (the precise mechanism)

Today `anti_chase_reason(bullish, row)` blocks if `vwap_ext > 4%` OR within `1%` of
the day extreme OR RSI > 80. For a RIDE name with a fresh hard catalyst, widen those
bounds (not remove them) via event-aware thresholds:

```
ANTI_CHASE_MAX_VWAP_EXT_EVENT = 0.08   # 4% -> 8% for a catalyst name
ANTI_CHASE_MIN_OFF_EXTREME_EVENT = 0.003
RSI_OVERBOUGHT_EVENT = 90
```

`anti_chase_reason` gains an optional `event=True` that swaps in the wider bounds.
Still bounded — we allow a *continuation* entry, not a blow-off-top chase, and it's a
defined-risk debit spread regardless. This is the single change that would have let
the Iran trades through.

## 7. What the model sees (context schema)

```
context["events"] = {
  "posture": "RIDE" | "BRACE" | "FADE_VOL" | "NEUTRAL",
  "live": [ {headline, theme, age_min, affected:[{symbol,dir}], catalyst: true} ],
  "scheduled_next": {name, time_et, mins_until, impact},   # e.g. CPI in 22m
  "iv_rank": {SPY: 0-100 (proxy), QQQ: ...},
  "guidance": "<one line: e.g. 'RIDE energy/defense (Iran); BRACE index condor; CPI in 22m'>"
}
```
Plus injected catalyst names appear in `signal_scan` tagged `event_catalyst: true`.

## 8. Config additions (all RUNTIME_SETTABLE)

```
EVENT_ROUTER_ENABLED   = False        # master flag (ship OFF, enable after a watch)
EVENT_FRESH_MIN        = 90           # a headline older than this is priced in
EVENT_PRE_MIN          = 30           # brace this long before a HIGH release
EVENT_MAX_RIDE_TRADES  = 2            # cap new catalyst trades per event (anti-overtrade)
EVENT_THEME_MAP        = {...}
ECON_CALENDAR          = [...]        # MVP hand-list; later a live feed
ANTI_CHASE_*_EVENT     = (wider bounds above)
IV_RANK_HIGH           = 70           # FADE_VOL threshold
```

## 9. Risk containment (how it stays safe)

- Every event trade is still a **defined-risk** structure under `PER_OPTION_NOTIONAL_CAP`
  and the per-name daily cap; the −$1500 intraday floor is unchanged.
- RIDE only *widens* anti-chase, never removes the cap on how extended; `EVENT_MAX_RIDE_TRADES`
  stops an event from becoming a churn spree.
- BRACE only *removes* opportunities (short-premium into a binary); it cannot add risk.
- Master flag OFF = exact current behavior. Enable and watch like every other book.
- Honest limits logged: IV rank is a proxy; the keyword classifier will miss/misfire —
  the model confirmation is the backstop, and a missed event is a no-op (today's behavior),
  not a loss.

## 10. Build phases

1. **Phase 1 — the offensive half (catches the Iran case).** Breaking-event detection
   + theme/model mapping + universe injection + anti-chase relaxation + `context["events"]`.
   Flag-gated. This is the highest-value slice and self-contained.
2. **Phase 2 — the defensive half.** Econ calendar (hand-list MVP) + BRACE posture in
   `passes_guardrails`.
3. **Phase 3 — IV rank + FADE_VOL.** Rolling IV baseline cache + the premium-selling flip.
4. **Phase 4 — polish.** Live econ feed, per-name IV when/if paid data, model-mapping cache tuning.

## 11. Worked example — the Iran move

1. Headline hits the news feed: "Iran ... strike ...". `detect()` flags it
   (geopolitical signature), age 2 min < `EVENT_FRESH_MIN`.
2. `assess()`: theme = `oil_geopolitical` → posture **RIDE** for {XLE, USO, ITA, LMT,
   GLD long; JETS, airlines short}, and **BRACE** for index short-premium.
3. Those symbols are injected into the universe → the scan prices them *now*.
4. Context tells the model: "RIDE energy/defense (Iran, 2m); BRACE index condor."
5. Model proposes a bull-call debit spread on XLE. Anti-chase would normally block it
   (XLE +4%, near HOD) — but `event=True` widens the bounds, so it passes. Defined-risk,
   under the cap, overnight-hold eligible (hard catalyst).
6. The index condor it might otherwise have sold is blocked by BRACE until vol digests;
   once `iv_rank` is high and the tape chops, FADE_VOL re-enables it to sell the rich IV.

## 12. Decisions for you

- **Econ calendar source:** live feed (more infra) vs hand-maintained list (simple,
  good enough for FOMC/CPI/NFP which are known far ahead)? Recommend hand-list MVP.
- **Mapping:** lean on the deterministic theme map (fast, predictable) or the model
  (flexible, costs a call)? Recommend theme map first, model fallback for unmapped events.
- **Aggressiveness of the anti-chase relaxation:** 8% VWAP ext is the proposed ceiling —
  comfortable, or tighter?
- **Scope to start:** Phase 1 only (the Iran-catching offensive half), then iterate?
