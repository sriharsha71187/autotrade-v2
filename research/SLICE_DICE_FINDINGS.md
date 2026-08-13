# Calm-day condor — slice-and-dice findings (4-agent parallel study, 2026-08-13)

Four independent research agents swept the strategy space around the deployed
calm-day condor (`calm_condor.py`), all on `research/data/spy_vix_daily.csv`
(4,926 real SPY+VIX days, 2007–2026) under a shared discipline: mine 2007–2018,
confirm 2019–2022, spend the 2023–2026 holdout only on final candidates.
Scripts referenced below live in the session scratchpad; the durable studies are
`backtest_gap_premium.py`, `backtest_pm_stops.py`, `backtest_structure_router.py`.

## 1. Entry conditions (294 cells examined)

**Finding: short-term vol DIRECTION beats every level-based gate.** All working
entry factors are one theme — *vol calming down yesterday*: VIX below its 5-day
mean (IS t=+3.8 alone), compressed prior-day range (<0.8× its 21d mean, t=+4.2),
prior up-day. Gap and VIX levels only matter as exclusions (down-gaps ≤−0.15%
and rising-VIX days are toxic).

Best candidate **C1: |gap| < 0.30% AND VIX < its 5d mean** — holdout
+6.5%/trade t=3.9 at 20% friction, ~70 trades/yr (vs deployed baseline +3.4%
t=1.2, ~36/yr). Survives multiplicity deflation (~300 cells → expect ~7 chance
survivors at t=2; C1 sits at t≈4–5 IS and 3.3–3.9 holdout).

**Decisive caveat (confirmed by red-team F1):** part of C1's edge is
convention-endogenous — synthetic credits use trailing-21d realized vol, which
mechanically overstates forward vol on exactly the VIX-falling days C1 selects.
The smoking gun: the VRP-bucket table inverts (cheap-VIX bins "win"). C1 is a
*candidate*, promotable only if live credit capture shows real calm-day credits
match the model.

## 2. Strike / structure geometry (158 configs examined)

- No unstopped geometry confidently beats any other OOS — tighter shorts scale
  credit and touch-rate together; all share the −100%-of-margin tail. Asymmetric
  condors, flies, broken wings: no material improvement.
- The interesting cell is **shorts 0.5σ + wings 2.0σ + touch-stop**: in-model it
  converts ~80% of days into a near-constant small cost and posts the study's
  best holdout stats (t=3.9 at 20% friction) with a benign sizing frontier.
  **But** the stopped-trade P&L is nearly deterministic in-model and flips sign
  between 10% and 20% friction — the entire advantage lives inside the stop-fill
  assumption. Candidate upgrade only after real stop-fill data.

## 3. Exit policies

- **Simple touch-stop at the short strike is the only defensible exit.** Real
  touch-timing (158 genuine intraday days; 8 touch events) implies f≈0.48 —
  supporting the f=0.5 modeling, and killing both the "earlier stop" idea
  (inner levels get crossed in the morning → negative expectancy at honest
  repricing: 0.5x-level −3.1%/trade IS) and profit-taking rules (give away the
  winners, keep the −105% tail).
- The touch-stop is **insurance, not edge**: costs ~1%/trade on holdout, sign
  guaranteed positive only if slippage ≤~10% and touches stay mid-day. No-stop
  is defensible as pure expectancy at ≤10% sizing, indefensible above.

## 4. Red-team audit (verdict: paper-trade yes, live no — not yet)

1. **FATAL-class caveat: the credit is assumed, not observed.** Break-even VRP
   multiplier is 0.97–1.05 vs the 1.10 used — the entire after-cost edge sits
   inside a ~13% vol-quote assumption. If the market prices 0DTE off recent (5d)
   vol, the strategy flips to −5.5%/trade. → **Kill criterion (now wired):
   `~/calm_condor_fills.jsonl` logs model vs filled credit; sustained ratio <1.0
   over the validation run kills the book.**
2. **The stop's "−11% worst day" is a fair-weather number.** A −3% midday shock
   with IV spike and 3× slip loses −63 to −78% of margin through the stop.
   → Size as if worst day = −100% of margin. The stop smooths expectancy;
   it does not delete the tail.
3. **The deployed 1PM book's direct evidence is N=14 days** (one calm regime,
   with a known upward pricing bias). The 20-year evidence is for open-entry.
   Live-vs-backtest divergences enumerated; the unbounded up-gap allowance was
   fixed (symmetric ±0.5% gap block), and quote-failure P&L booking was fixed
   (was: silently booked full credit as profit).
4. **Regime concentration:** 76% of 20-year P&L from 2013–2017; zero trades
   2007–09 and 2022 (gate closed — untested in bears, by construction); 2024
   (largest OOS year) was ~flat; OOS excluding two tiny 100%-win years: t=1.1.
   The "OOS" period is the same low-vol regime continuing.
5. **Multiplicity:** family-wise p ≈ 0.05–0.07 — real but marginal; the t=4.4
   headline overstates certainty.
6. **Path risk:** worst 10-trade window −28% of account at 10% sizing (inside
   the golden years); true maxDD ~−30%, triple the "worst day −11%" mental model.

## Bottom line

The slice-and-dice found two genuine candidate upgrades — the **VIX-falling
entry gate (C1)** and **0.5σ shorts with the stop** — and one confirmed
keep-as-is (simple touch-stop). None are promotable on synthetic evidence,
because every one of them concentrates its improvement exactly where the
pricing model is least trustworthy. The paper run now writes the audit file
that settles it: **judge `calm_condor_fills.jsonl` after ~2–3 months —
filled/model credit ratio ≥1.0 promotes the upgrades; <1.0 kills the book.**
Until then: deployed config unchanged, 10%/day sizing, and treat drawdown
expectations as −30%, not −11%.
