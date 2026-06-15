# AutoTrade Audit Roadmap (2026-06-15)

Comprehensive proactive audit — 6 parallel auditors vs well-known best practice → ranked roadmap.
33 findings (entry 5 / exit 4 / sizing 6 / coverage 7 / instrument 4 / profit+reliability 7).

## EXECUTIVE SUMMARY
The through-line: the bot does the **textbook things wrong** in three places at once — it **buys the
worst instrument** (nearest-expiry/0DTE ATM premium), **manages winners and losers backwards** (no
breakeven/early trail, can leg out of a spread into an uncapped naked short), and **can't see its own
losses** (same-day-only P&L attribution reads ~$0 while equity fell -4.2% in 7 days). On top of that it
**sits out the cleanest trend on the board** waiting for a hard catalyst (the SPCX complaint, reproduced).
Sizing is a notional cap, not a risk budget, so per-trade risk swings 13x and one structure can blow 4
days of loss limit.

**Top 3 highest-leverage fixes:**
1. Add a min-DTE floor + kill 0DTE directional (config + selector) — caused the -$750 RKLB / 0DTE-Friday pattern.
2. Spread-atomicity + naked-short guard — the single -$2,382 SMCI naked short was ~60% of the period's decline; violates "defined-risk only."
3. Breakeven/early-trail on options — winners (COIN +35.8%, SPY +254%, ORCL +52%) round-trip to losses because nothing arms until +40%.

Measurement caveat: the cross-day P&L blind spot (#5) means **none of the bot's self-reported dollar
figures are trustworthy** — fix it early to verify everything else.

## TIER 1 — FIX NOW (high-impact, well-known, high-confidence)
1. **Naked-short from legging out of a spread — uncapped loss.** close path honors a subset of a tracked vertical → naked short. `autotrade.py:3319-3320`; 6/10 SMCI short 33500 leg $0.53→$4.50 = **-$2,382** (~60% of the decline). Fix: treat spreads as atomic on close; post-cycle assert no uncovered short → auto-flatten+alert. Effort M.
2. **Nearest-expiry default → 0-4 DTE, 0DTE on Fridays.** `option_chain_for` picks `expiries[0]`. RKLB 0DTE 6/12 = -$750. Fix: `MOMENTUM_OPTION_MIN_DTE`~5-7; never auto-buy 0DTE directional. Effort S.
3. **No breakeven / early trail — winners round-trip.** nothing between 0 and +40%. COIN +35.8%→-$90. Fix: at hw_pl≥+15-20% raise stop to breakeven; arm trail at +20-25%; scale out half at +25-30%. Apply to manage_options AND conviction path (no EOD backstop — most urgent). Effort M.
4. **Trailing give-back is multiplicative (% of peak).** +254% peak → no exit until +190%. Fix: fixed give-back band in profit-points / premium chandelier like GROWTH_TRAIL_PCT. Effort S. (merge with #3)
5. **Cross-day P&L blind spot — reports ~$0 while equity fell -4.2%.** `honest_trade_stats` matches same-day only; multi-day option book invisible; corrupts learning loop + dashboard. Fix: persistent open-lot ledger (FIFO) keyed by OCC across days; weekly per-strategy expectancy. Effort M. **Prerequisite to trusting all dollar figures.**
6. **Stock sizing is a notional cap, not a risk budget — per-trade risk swings 13x.** Fix: qty = RISK_PER_TRADE_$ / |entry-stop|, conviction-scaled; enforce OPTION_RISK_TARGET in code (currently advisory). Effort M.
7. **Sits out the cleanest trend waiting for a catalyst (SPCX +17% miss).** (a) prompt elevates "hard catalyst"; (b) conviction-ITM needs new-HOD while anti-chase blocks <1% off HOD → pullback entry qualifies for neither; (c) RSI None first ~75 min silently blocks trend entries. Fix: prompt nudge (strongest trend is valid w/o catalyst); decouple "clean trend" from "at the high" (qualify on structure, prefer 0.3-2% pullback); treat missing RSI as "don't block." Effort M (prompt S).
8. **Gap-fade fights the tape, ignores regime/VIX.** shorts the biggest gapper on risk-on days, fires before the regime gate, keeps fading in a VIX-40 tape. Fix: pass regime into every book; skip gap-up shorts when tape risk_on / fresh catalyst; apply VIX-HIGH veto to all books. Effort M.
9. **PER_OPTION cap ($1,200) is 4x the daily halt (-$300); comment is false.** Fix: PER_OPTION_NOTIONAL_CAP = abs(DAILY_LOSS_HALT)*0.5. Effort S.

## TIER 2 — NEXT
10. Correlation cap is bull/bear only — blind to a single-sector (semis/AI) book; add sector bucket + portfolio-heat cap. Pairs with #6.
11. IV-rank computed but never gates entry — buys premium at the top of its IV range; block/down-size single-name debit when iv_rank>70.
12. 0/1DTE spreads force-closed 15:45 — largely resolved by #2 (acceptance check).
13. No account-level drawdown floor — the stated -$1,500 ruin guardrail isn't implemented; add cumulative floor + halve size after 2 red days.
14. Daily halt failed to cap the -$2,382 bleed (wash-trade reject loop blocked exit); add hard per-position max-loss kill. Mostly closed by #1.
15. Market-order exits donate the bid-ask spread; use marketable limits on exits.
16. Single-leg longs give the model no peak/high-water context; surface hw_pl + give-back flag.
17. Overnight gaps bypass guardrails (Mac-sleep dependency); resting GTC broker stop + run cycles off-laptop.
18. Daily halt evaluated only at 2-5 min cadence; auto-satisfied once portfolio-heat cap (#10) lands.

## TIER 3 — LATER / RESEARCH (validate first)
19. ATM strike → target ~0.60-0.70 delta long leg for debit spreads.
20. Anti-chase has no established-trend carve-out (overlaps #7).
21. Four overlapping bullish vehicles, no deterministic selection rule — route one per setup.
22. Option-chain fetch caps at 3 underlyings — strong trends crowded out.
23. Earnings IV-crush book too narrow (10 names, 1/night) — broaden to a risk budget.
24. No opening-range-breakout book.
25. No single-name mean-reversion (debit) vehicle.
26. No pairs / relative-value / market-neutral book.

## CONFIDENCE / SEQUENCING
- Trust the measurement fix (#5) before believing dollar figures.
- High confidence (act now): #1 #2 #3 #4 #5 #6 #9.
- Medium: #7 (multi-factor; instrument submit-rate before/after), #11 #13 #17.
- Low / new R&D (validate with backtest): #22 #25 #26.
- Sequence: land #1/#2/#9 first (biggest downside protection, S/M). #5 unlocks honest eval. #6+#10 share the risk-budget refactor. #7 = prompt now + selector rework next.
