# Alpha-Hunt Review Packet — 2026-07-19

Purpose: complete, self-contained record of **every dataset we hold and every test run
against it**, so an independent reviewer (human or LLM) can audit the conclusions, poke
holes in the methodology, and propose tests we have NOT run. Nothing here is hidden:
every script named below is in `research/` in this repo, every dataset path and span is
listed, and §6 explicitly lists what we have *not* been able to test.

The headline claims under review:
1. **IBS mean-reversion on index ETFs is the only validated active-trading edge** in the
   data we hold (SPY OOS Sharpe 1.16, details §3.6).
2. Analyst-revision drift, classic PEAD, news-sentiment drift, calendar effects,
   overnight effect, correlation/dispersion timing, and the plain price/volume
   cross-section are **all dead** on this universe at daily frequency.
3. One watch-only candidate: momentum-neutral PEAD in high-coverage large caps (§3.5).

---

## 1. Data inventory (what exists, where, spans, caveats)

Price panels (research-grade, built 2026-07-19):
| Dataset | Path | Contents | Span | Caveats |
|---|---|---|---|---|
| Adjusted close panel | `research/.yf_adj_close.pkl` | 741 syms × 2,649 days, split/div-adjusted (yfinance) | 2016-01 → 2026-07 | survivorship-biased universe (current S&P500+400 members + ETFs) |
| Adjusted OHLCV panel | `research/.yf_ohlcv.pkl` | O/H/L/C/V, same universe | 2016-01 → 2026-07 | same |
| Alpaca close panel | `~/.trend_bars.pkl` | 742 syms × 2,383 days | 2017-01 → 2026-06 | **unadjusted-split glitches** (AMCR +367% artifact) — superseded by yf panels for event studies |
| Alpaca OHLCV cache | `research/.ohlcv_cache.pkl` (55 MB) | intraday/daily bars used by backtest_*.py | varies | same split caveat |

Event / feature histories:
| Dataset | Path | Contents | Span | Size |
|---|---|---|---|---|
| Earnings surprises | `~/autotrade_earnings_history/` | 734 names, per-event: date(BMO/AMC), est EPS, reported, surprise% (yfinance) | ~2001 → 2026 (~100 qtrs/name) | 6.5 MB |
| Analyst actions | `~/autotrade_ratings_history/` | 948 names, per-day: n_up, n_down, net, avg_target | 2016 → 2026 | 12 MB |
| News headlines | `~/autotrade_news_drift/` | 188 less-efficient names, 106k headlines, point-in-time (Alpaca NewsClient) | ~2016 → 2026 | 11 MB |
| News (57 liquid) | `~/autotrade_news_history/` | 57 names | ~10 yr | 416 KB |
| Options features | `~/autotrade_options_history/` | weekly ATM-IV/skew/term/put-call/OI/GEX (EODHD); **30 ETFs + only 4 stocks populated** (AAPL AMZN MSFT NVDA); single-name backfill resumed 7/19, in progress | Q4 2023 → 2026 (86 Fridays) | 680 KB |
| Insider | `~/autotrade_insider_history/` | 57 mega-liquid names, net_buy/n_buy/n_sell | 2021 → 2026, 2,199 rows | 244 KB |
| Institutional | `~/autotrade_institutional_history/` | 57 names, quarterly holder aggregates | short | 100 KB |
| Macro daily | `~/autotrade_macro_history.csv` | y10/y5/y3mo/y30, VIX, dollar, HYG/IEF, curve, credit, risk_off flag | 2016-07 → 2026-07 | 650 KB |
| Perishable capture | `~/autotrade_universe_capture/` + `_returns/` | 742 names/day × 21 layers (analyst, estimates, short interest, float, social, options snapshot, insider, news, valuation, dispersion) | **18 trading days** (2026-06-24 →) | 30 MB |
| Bot decision data | `~/autotrade_snapshots/` (14d), `~/autotrade_candidate_outcomes/` (11d), `~/autotrade_scorecard/` (4d) | per-cycle candidates+features+decision; forward returns of picked AND passed names | June 2026 | 19 MB |

Strategy ledger: `~/autotrade_strategies.json` — every strategy id, status
(queued/testing/candidate/validated/rejected/contested), metrics, n_trials, dated notes.

---

## 2. Statistical hygiene applied everywhere

- IS/OOS time split (2016-2021 / 2022-2026, or 2017-2022 / 2023-2026 for event studies).
- t-stats: Newey-West (lag = horizon/5) for overlapping weekly series; month-clustered
  for event studies (events in the same month are cross-correlated).
- Both-halves survivor rule: |t|>2.5 in IS **and** OOS with the same sign to count;
  |t|>3 to be believed. Trial counts tracked (multiple-testing).
- Costs: 1bp/side index ETFs; glitch guard drops |window return|>150%.
- Split-adjusted prices only for event studies (the Alpaca panel's split glitches
  previously manufactured fake momentum).
- Excess returns = vs equal-weight universe over the identical window (not vs SPY),
  removing market drift from event windows.

Known remaining biases (reviewer should weigh): **survivorship** (universe = current
index members; kills ~most small-cap effects' measurability but *inflates* long-side
results, so dead results are extra-dead); yfinance consensus EPS is the *final*
consensus (mild look-ahead vs point-in-time estimates — inflates, not deflates, PEAD).

---

## 3. Tests run (script → method → result → verdict)

### 3.1 Analyst rec-change / target-revision drift — `ratings_study.py`
85,533 events, 698 names, 2016-2026. Portfolios: net upgrades, net downgrades, strong
(|net|≥2), target raise/cut ≥10%, combos. Horizons 1/5/10/20/60d.
**Result:** NO positive post-upgrade drift at any horizon in any split. Only significant
cell: low-coverage upgrades **−2.4% excess @60d, t=−3.7** (contrarian: analysts upgrade
after run-ups that mean-revert). OOS the only t>2 cell is *fading* strong downgrades
(+1.3% @10d, t=2.5, n=298 — isolated, one of ~80 cells). **VERDICT: REJECTED.**

### 3.2 Classic PEAD (surprise quintiles) — `pead_study.py`
55,276 events, 693 names, 2016-2026, BMO/AMC entry timing, quarterly cross-sectional
surprise quintiles. **Result:** Q5−Q1 ≈ 0 in-sample at every horizon (t≤1.5); big beats
(≥+10%) mildly NEGATIVE after (−1.15% @20d); low-coverage half NEGATIVE (−1.27% @20d,
t=−2.1) — the *opposite* of the literature. **VERDICT: classic PEAD REJECTED.**

### 3.3 News-sentiment drift (Tier-0 ceiling test) — `news_drift_backfill.py` + lexicon study (run 7/18)
106k headlines, 188 less-efficient names, 10 yr, point-in-time. **Result:** no positive
drift (mild reversal), IC≈0, OOS active −10%/yr. A perfect sentiment classifier has no
drift to harvest → LLM (Tier-1) scoring of the same text cannot create one.
**VERDICT: REJECTED** (this is the ceiling argument — challenge it if you disagree).

### 3.4 Broad cross-sectional feature sweep — `feature_sweep.py`
25 features (momentum 21/63/126/252ex21, reversal 5/10, dist-200dma/52w-high, vol 20/60,
vol-of-vol, downside vol, MAX21 lottery, skew, ATR%, log dollar-volume, Amihud,
overnight-21, intraday-21, gap-frequency, IBS-5, beta-60, idio-vol, volume-trend)
× horizons {5,10,21,63}d × 703 stocks, weekly Spearman IC, NW t, VIX/regime splits.
**Result: 88 cells, ZERO both-halves survivors.** Best theme: illiquidity/vol/gap
features positive at 63d, concentrated in high-VIX/bear subsamples (ic_hivix ≈ 4-8×
ic_lovix) = risk premium, not signal. Short-term reversal exists but tiny (IC −0.016).
**VERDICT: plain price/volume cross-section is exhausted.**

### 3.5 PEAD, momentum-neutral, coverage-split — follow-up in `pead_study.py` session
The one surviving configuration: HIGH-coverage (large-cap) half, momentum-quintile-
neutralized Q5−Q1, month-clustered: +20d +1.18% (t 2.6) / +40d +2.71% (t 3.0) /
+60d +2.68% (t 2.4) full-period — but IS-half only t 1.1-1.4; effect is 2023-2026
concentrated; found after several cuts (selection!); and large-caps-drift-while-
small-caps-don't INVERTS the literature → likely the AI-era serial-mega-cap-beat
regime. Reverse control clean (momentum spread within surprise-neutral cells = 0.0,
t 0.1 — surprise, not momentum, drives it). **VERDICT: CANDIDATE, watch-only.**

### 3.6 Index-timing sweep — `timing_sweep.py`, `corr_timing.py`, `ibs_anatomy.py`
SPY/QQQ/MDY/IWM, IS/OOS, 1bp/side:
| Effect | IS | OOS | Verdict |
|---|---|---|---|
| Turn-of-month | Sh 0.78 | Sh 0.33 | decayed |
| Overnight-only | Sh 0.61 | Sh 0.09 | decayed |
| Day-of-week | — | ≤3bp/day | noise |
| VIX +20%/5d → long | Sh 0.33 | Sh 0.21 | nothing |
| **IBS <0.1→>0.9 (SPY)** | **Sh 1.31, dd −13.8%** | **Sh 1.16, dd −9.6%** | **validated** |
IBS robustness: ALL 5 entry/exit grid cells OOS Sh 0.88-1.16 on SPY; QQQ similar;
RSI-2 (independent formulation) confirms; trade anatomy: 15 trades/yr, 74% win,
+0.96%/trade, med hold 4d, 1 losing year in 11 (2018 −9.5%), worst trades −4 to −7%.
**VERDICT: VALIDATED.** (Reviewer: main risks = regime break in MR, execution slippage
on panic closes, single-market concentration.)

### 3.7 Correlation-structure timing — `corr_timing.py`
Avg pairwise correlation (top-200 liquid) and 21d cross-sectional dispersion, weekly,
vs forward 5d SPY and forward momentum-decile excess. **Result:** rank-corr ±0.03,
no quintile monotonicity. The live dispersion gate's value is drawdown-shaping (position
sizing), NOT return prediction. **VERDICT: no timing signal.**

### 3.8 Options-IV cross-section — `ivrv_study.py`
The nightly discovery's two "candidates" (opt_atm_iv IC +0.144 t 3.6; iv×rv IC −0.198
t −4.76) re-tested cleanly. **Finding: the panel was 30 ETFs + only 4 stocks** (EODHD
single-name backfill had silently stalled). On the ETF cross-section, high-IV vs low-IV
spread = +2.1%/4wk (t 5.2) — but that is ARKK/GDX/XBI vs TLT/UUP/LQD = **asset-class
beta in a bull tape**, not stock selection. IV−RV spread proper: t 1.2.
**VERDICT: prior candidates DEMOTED (contested); retest when single-name coverage
exists (backfill resumed 7/19, in progress).**

### 3.9 Insider — data-insufficiency check
2,199 rows, 57 mega-caps, 2021-2026, dominated by routine selling. The literature
effect (cluster buys, small caps) is invisible in this panel. **VERDICT: not testable
with current data** (needs small-cap insider history, e.g. SEC Form 4 bulk).

### Prior studies (pre-7/19, all in git history / strategy board)
- Intraday: LLM selection vs random (random wins), ORB, VWAP-fade, 0DTE condors,
  true-intraday TSMOM (first-HH→last-HH, `backtest_intraday_tsmom.py`) — all rejected.
- Options for the equity book: double calendars (cost-razor), QQQ LEAPS dip-buy
  (QLD-dominated), monetized tail hedge (timing artifact, worsens DD) — rejected.
- Portfolio sleeves (validated, deployed or board-listed): 10-name momentum 7+3
  (deploy_growth Sh 1.34), risk-parity+TV (1.01-1.04), vol-target QQQ (1.03),
  capstone blend (1.27), permanent portfolio (0.95).

---

## 4. Where the LLM stands after all this
No selection edge (random-picker test); no text edge at Tier-0 ceiling (news study);
validated role = monthly risk-veto on mechanical picks (caught an in-default ticker
rename + a 40x parabola) + data-artifact catching. See `~/autotrade_strategies.json`.

## 5. Reproduction
```bash
PY=~/autotrade/venv/bin/python3
$PY research/yf_panel.py && $PY research/yf_ohlcv.py       # rebuild clean panels
$PY research/earnings_backfill.py                          # ~40 min, resumable
$PY research/ratings_study.py                              # §3.1
$PY research/pead_study.py                                 # §3.2/3.5
$PY research/feature_sweep.py                              # §3.4
$PY research/timing_sweep.py                               # §3.6
$PY research/corr_timing.py                                # §3.6/3.7
$PY research/ibs_anatomy.py                                # §3.6
$PY research/ivrv_study.py                                 # §3.8
```

## 6. What we have NOT tested (the honest gap list — start your review here)
1. **Intraday microstructure on the broad universe** — Alpaca minute bars exist for
   everything; only SPY 30-min TSMOM was tested. Untested: cross-sectional intraday
   reversal, close-auction imbalance effects, first-hour range breakout per-name.
2. **Point-in-time fundamentals** — no PIT fundamental DB (value/quality/accruals/
   buybacks untestable without lookahead). Would need Compustat-style or EODHD
   fundamentals with as-reported dates.
3. **Short interest** — only 18 days captured (bi-weekly exchange data has years of
   history at FINRA; squeeze/days-to-cover signals untested).
4. **Social/retail attention** — 18 days (StockTwits). No history API.
5. **Single-name options flow** — IV/skew/OI history exists for only 4 stocks so far;
   backfill in progress. Options-flow anomalies (skew shifts pre-event, put-call OI)
   remain open.
6. **13F / institutional flows** — quarterly holdings history not backfilled.
7. **Index reconstitution, M&A arb, buyback announcements** — no event feeds.
8. **Nonlinear/ML interactions** — only pairwise feature products swept
   (`discover.py`, 323 combos); no gradient-boosted / neural cross-sectional model
   (deliberately: n≈500k obs with heavy cross-correlation invites overfit, but a
   reviewer could argue for a properly purged/embargoed GBM pass).
9. **Cross-asset momentum/carry** (futures, FX, rates) — out of scope of equity data.
10. **Survivorship-free universe** — all cross-sectional results use current index
    members. A CRSP-style delisted-inclusive rerun could revive small-cap effects
    (the QuantConnect re-validation path exists: `qc_xsmom.py`).

---

## 7. Addendum — response to the independent audit (2026-07-19, same day)

An independent LLM audit (`AUTOTRADE_ALPHA_AUDIT.md`, Codex) reviewed this packet at
commit `0cebd5c`. Point-by-point disposition, with what was verified and changed:

**Conceded and FIXED:**
- **beta60/idio60 were silently all-NaN** (DataFrame.rolling().cov(Series) broadcasting
  failure) — verified: 0 non-null values. Fixed with an explicit rolling covariance +
  a coverage manifest that ABORTS on <30% coverage. Re-run: 96 cells, beta/idio-vol
  land mid-pack, still ZERO both-halves survivors — the fix widens the null, does not
  overturn it. The packet's "25 features" is corrected to 24 defined / 24 now tested.
- **Same-close IBS fill is not executable** — correct. Added `ibs_next_open.py`
  (next-open fills both legs + 10-session time stop): SPY IS Sh 0.92 / OOS Sh 0.99 @
  1bp/side, and 0.81 / 0.87 @ 5bp/side (~+10-11%/yr, OOS maxDD −8.5%). This matches the
  auditor's independent 1993-2026 harness (their 2022+ Sharpe 0.94) — the effect
  cross-validates across two implementations and two datasets. THE DEPLOYABLE STATISTIC
  IS ~Sharpe 0.9-1.0, not 1.16.
- **The 2022-2026 segment was used during selection** (grid + variants inspected) —
  correct in principle; it is validation, not virgin holdout. IBS status accordingly
  demoted from "validated" to **frozen paper-trade candidate** (`spy_ibs_next_open_v1`,
  enter IBS<0.10 / exit IBS>0.90 / 10-session stop / next-open fills — frozen 7/19;
  any change = new version, forward clock resets).
- **Momentum-neutral PEAD was not reproducible from committed code** — correct. Now
  committed as `pead_momneutral.py` with an explicit post-selection disclosure. Status
  remains watch-only candidate.
- **Ratings data are week-ending-Friday aggregates, not daily** — verified (100% of
  sampled event dates are Fridays; `ratings_backfill.py` groups on `W-FRI`). The
  rejection is downgraded from "rec-change drift is dead" to "no drift measurable at
  weekly granularity with composition-confounded targets"; a clean per-action test
  needs broker-level timestamped grades. Directionally the null stands (a <=4-session
  entry delay does not plausibly flip a 20-60d drift's sign), but the strong wording
  was not earned.
- **"Month-clustered" t was a t on monthly means, not a cluster-robust SE on the
  event-weighted mean; no HAC across overlapping 40/60d windows** — correct; noted as
  a known inference weakness. Sign-flips between the event-weighted mean and the
  monthly-mean t occur in sparse cells.
- **The news "Tier-0 ceiling" argument was too strong** — conceded. The lexicon null
  rejects polarity-drift strategies on this universe; it does not bound event-type /
  novelty / expectation-surprise extraction. A Tier-1 test would need a frozen,
  pre-registered protocol; it remains unjustified on priors, not "proven impossible."

**Did NOT reproduce (checked on the canonical environment):**
- "Four current failures in `test_trend_entry.py`" — the file passes ALL GREEN here,
  and the full suite (16 test files) is green. Most likely their sandbox lacked
  `~/.autotrade.env` / data files. CI + import-safe pytest conversion remains a fair
  ask and is on the roadmap.

**Where we agree with the auditor's bottom line:** paper-trade `spy_ibs_next_open_v1`
at reduced notional, no live capital until an independent-vendor signal reconciliation
and an executable-cutoff (3:50pm minute-bar) comparison are done; keep the LLM out of
selection; next research = executable close mean-reversion, point-in-time PEAD,
broker-level analyst actions, Form 4 cluster buys, index reconstitution.

---

## 8. Addendum 2 — counter-audit of the Codex greenfield candidates (2026-07-20)

Codex delivered two further documents: an intraday pass (7 more families tested, ALL
rejected — earnings continuation/reversal/fade, opening residual momentum, month-end
rebalance, pre-FOMC, anti-FOMC; consistent with our nulls) proposing `closeflow_v1`
(closing-auction imbalance continuation — a frozen SPEC, blocked on NYSE-imbalance/
NOII/NBBO history we do not own), and a greenfield pass proposing
`form4_cluster_reversal_v1` (SEC Form 4 officer/director cluster buys, 20-session
hold, SPY-hedged; prototype dev 29.0%/Sh 0.96, test 18.7%/Sh 0.85, HAC t 2.42).

Counter-audit of form4 (their artifacts, our clean panel):
- **Their numbers REPRODUCE exactly** from `work/sec_cluster_daily_returns.csv`
  (CAGR/Sharpe/DD match to the decimal; positive every year but 2024; max single day
  <= 17% of period log-P&L).
- **The drawdown-matched placebo — which their own doc calls the most important
  unresolved test — FAILS on the liquid subset.** 623 of 2,975 events overlap our
  split-adjusted panel. Cluster events: +0.48%/20d SPY-hedged. Placebo names matched
  on prior-20-session excess return (+/-2pp, same month, no cluster): +0.77%. Paired
  monthly difference: **-0.30%/20d (t -0.5)**, flat in both 2020-22 and 2023-26.
  In large/mid caps, insider clusters add NOTHING beyond generic dip-buying.
- **Therefore the prototype's entire P&L concentrates in the 79% of events outside
  the liquid panel** — small caps, where their own caveats are sharpest: 46.6%
  non-random price coverage, ticker-reuse/delisting risk, no 10b5-1 exclusion
  pre-2023(?), and a 10bp cost model that is optimistic for $5M-ADV names.
  This matches the literature (insider alpha is a small-firm effect) but means the
  strategy is untestable-to-buildable on our current free data.

Disposition: `form4_cluster_reversal` on the board as **contested** (not candidate) —
promotion requires their own gates (CRSP/Norgate delisting-clean rerun, small-cap
placebo, acceptance-timestamp reconstruction). `closeflow_v1` on the board as
**queued/blocked-on-data**. Neither changes current deployment (momentum book live;
`spy_ibs_next_open_v1` to paper).
