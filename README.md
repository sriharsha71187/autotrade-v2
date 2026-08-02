# AutoTrade

Autonomous **Alpaca paper** trading bot for US equities + defined-risk options.
Design principle: **Claude picks, code protects** — one model call per qualifying
cycle proposes a single action, wrapped in deterministic guardrails that can veto or
resize it but never invent a trade.

> **Paper only.** `PAPER=True` is hardcoded. Real keys live in `~/.autotrade.env`
> (chmod 600, outside the repo). Nothing here places live-money orders.

> **Status (2026-07): intraday bot PAUSED (`TRADING_PAUSED`), verdict reached.** Three
> independent checks (a leak-free archetype backtest, the literature, and the bot's own
> live decisions) converged: **no proven intraday LLM selection edge** — a random picker
> from the same candidate pool did as well. The engine now runs in **data-capture mode**
> (nightly perishable-signal capture: analyst/short-interest/estimates/insider/IV/social),
> and the project pivoted to a **validated momentum portfolio** deployed human-in-the-loop.
> See **[Where this landed](#where-this-landed-2026-07)**.

## Workflow at a glance

One model call (box ④) sits inside deterministic gates — **everything else is code**.
Gates *before* it decide whether and what it may propose; gates *after* it can veto or
resize but never invent a trade. Management, books, safety self-checks, and the nightly
learning loop all run as code.

```mermaid
flowchart TD
    subgraph CTX["① Build context — each cycle"]
        direction LR
        C1["Account &amp; open positions"]
        C2["Signal scan<br/>most-actives ∪ movers"]
        C3["VIX · Fear/Greed<br/>news · econ calendar"]
        C4["Options intel<br/>IV-rank · skew"]
    end

    CTX --> REG{"② Regime gate · regime.py<br/>trend × VIX → allowed books / FLAT<br/>+ sector-relative trend"}
    REG -->|FLAT| HOLD["Hold cash · manage only"]
    REG --> EVT["③ Event router · events.py<br/>RIDE catalyst · BRACE binary · FADE_VOL"]
    EVT --> QUAL{"setup_qualifies?"}
    QUAL -->|no| HOLD
    QUAL -->|yes| ROUTE["Vehicle router<br/>one vehicle per name"]

    ROUTE --> CLAUDE[["④ call_claude<br/>Fable-5 → Opus auto-fallback<br/>ONE structured action"]]

    CLAUDE --> GUARD{"⑤ passes_guardrails<br/>anti-chase · counter-tape · IV ceiling<br/>cooldown · per-name &amp; correlation caps · daily-loss halt"}
    GUARD -->|blocked| VETO["Log veto → behavioral-tripwire tally"]
    GUARD -->|clears| EXEC["Execute<br/>stock bracket · marketable-limit option · multi-leg"]

    EXEC --> MANAGE
    HOLD --> MANAGE
    VETO --> MANAGE
    subgraph MANAGE["⑥ Manage every cycle — code-enforced exits"]
        direction LR
        X1["Trail stop · breakeven lock"]
        X2["Sector-confluence<br/>fade / thesis-break stop"]
        X3["Naked-short guard<br/>max-loss kill"]
        X4["EOD flatten<br/>intraday book"]
    end

    MANAGE -.manages alongside.-> BOOKS
    subgraph BOOKS["Code-managed books — held overnight by design"]
        direction LR
        B1["growth sleeve"]
        B2["tail hedge"]
        B3["earnings crush"]
        B4["overnight drift"]
        B5["gap fade"]
        B6["sector pairs"]
        B7["conviction-ITM"]
    end

    MANAGE --> SAFE{"Per-cycle self-checks — ALERT-ONLY<br/>invariant layer · behavioral tripwire"}
    SAFE --> SNAP["Write snapshot + state"]

    SNAP --> EOD["EOD · outcomes → cross-day P&amp;L ledger<br/>→ evidence-weighted learnings → Telegram"]
    EOD -. "rulebook feeds tomorrow's context" .-> CTX
```

## How a cycle works
1. Build context — account, open positions, a live signal scan (most-actives ∪ movers),
   VIX, fear/greed, breaking news, an economic calendar, and per-name options intel.
2. **Regime gate** (`regime.py`, deterministic) — trend × VIX bands decide which
   strategies may open this cycle (or force FLAT). The model can only fill in strikes
   inside what the regime already authorized. A **sector-relative** overlay judges "with
   the tape" by the name's own sector (semis, megacap, software…), not just SPY — so a
   long into a sector that's quietly rolling over is treated as counter-tape.
3. **Event router** (`events.py`) — two-sided: RIDE a fresh catalyst (inject the name,
   relax anti-chase), BRACE before a scheduled binary (no short premium into it),
   FADE_VOL rich index IV after a spike.
4. **Decide** — `call_claude` sends the context and gets back one structured-JSON action
   (`buy_stock` / `buy_option` / `multi_leg` / `iron_condor` / `close` / `hold`).
5. **Guardrails** (`passes_guardrails`) — regime gate, anti-chase, counter-tape block,
   extreme-IV ceiling, stopped-out cooldown, per-name caps, correlation & notional caps,
   daily loss halt. A trade clears all of them or it doesn't go.
6. **Manage** — every open spread/option/stock is re-judged each cycle; stops trail to
   lock gains and a breakeven lock protects a winner; a **sector-confluence fade stop**
   cuts a position when the name goes adverse off entry *and* its sector is rolling over
   (backtested — a price-only version whipsawed and was rejected); a naked-short guard and
   per-position max-loss kill backstop the defined-risk rule; everything intraday is
   flattened before the close.

After the single exit each cycle, two **alert-only** self-checks run (never touch orders):
an **invariant layer** that catches state corruption (e.g. a phantom day-P&L from a book
collision), and a **behavioral tripwire** that flags the silent failure mode — seeing
setups but not acting, or the model stuck repeating one blocked reason.

## The model (and the Fable fallback)
- Preferred: `claude-fable-5`. Fallback: `claude-opus-4-8` (`config.CLAUDE_MODEL` /
  `CLAUDE_FALLBACK_MODEL`).
- Fable 5 + Mythos 5 were **government-suspended for all users on 2026-06-12** (no
  timeline). `_create_message` tries the preferred model and transparently falls back to
  Opus on a model-unavailable 404. The down-state is per-process, so each cycle re-probes
  Fable — the bot returns to it **automatically** the moment access is restored, and DMs
  you when that happens.

## Self-learning
After each close, `autotrade.py eod` reviews the day's snapshots + realized P&L and
maintains an evidence-weighted **rulebook** (`~/autotrade_learnings.json`), then DMs it to
you on Telegram. Each rule carries a status (`active` / `tentative` / `contested` /
`retired`), a regime `scope`, and `confirmations` / `refutations` counts.

**Contradiction policy (evidence-weighted + hysteresis):** when a new learning conflicts
with a standing one, it's first tested for being *regime-conditional* (scope both rules,
don't pick a winner); only a genuine reversal touches status, a single contradicting day
merely marks the incumbent `contested`, and a just-flipped rule is locked for a cooldown
so it can't thrash. Code enforces the mechanical invariants (hysteresis, clock
preservation, count clamping, silent-delete guard); the model does the semantic work. A
learning can never override the protected human priors in `config.LEARNING_PROTECTED_PRIORS`.

## Research & validation
The architecture above is sound; the honest open question is whether the **model's
selection adds value**. The current evidence — a leak-free archetype backtest, the
LLM-as-trader literature, and the bot's own live decisions — points the same way: **no
proven intraday selection edge.** The model's picks have not beaten a *random* pick from
the same candidate pool (an early, controlled test had a random picker beating the model
~93–100% of the time intraday); the one durable signature anywhere is long-only
cross-sectional momentum, which is beta-tilted, not alpha.

So the bot is run as an **instrumented experiment** — and, crucially, it captures
everything needed to *build* a deterministic strategy, not just grade the model. The goal
is **LLM-as-teacher**: use its decisions + outcomes as a labeled dataset, distill the part
(if any) that works into deterministic code, and remove the LLM wherever a rule reproduces
it.

The data pipeline (all read-only / non-fatal; no effect on the trading path):
- `~/autotrade_snapshots/` — per cycle: the **full candidate set + features**, the **news
  the model saw**, the active rulebook, the decision, and the result (`write_snapshot`).
- `~/autotrade_scorecard/` — per executed entry: how chased/extended it was (vwap_ext,
  off-HOD, RSI, mover-rank vs the alternatives) **and the trade kind** (stock vs option,
  structure, book, credit/debit) (`record_trade_scorecard`).
- `research/backfill_outcomes.py` → `~/autotrade_candidate_outcomes/` — the missing **Y**:
  forward returns of **every** candidate (picked *and* passed) at 30m/1h/2h/EOD/+1d/+3d.
  Without the outcomes of the names we *passed*, selection skill is unmeasurable.
- `research/analyze_picks.py` — rigorous selection-skill test: each pick vs the
  alternatives it had **that same cycle**, plus a Monte-Carlo random baseline.
- `research/distill.py` — mines the candidate dataset for a deterministic **feature→return
  signal** (LLM-agnostic), out-of-sample day-split gated, and tests whether the model's
  pick adds anything *beyond* the features. If a deterministic rule reproduces the good
  entries, the LLM is removable from selection.
- `research/qc_xsmom.py` — a QuantConnect/LEAN cross-sectional-momentum algo to re-validate
  that one signature on a survivorship-free engine.

Run the pipeline (after the bot has logged a few days of snapshots):
```bash
PY=~/autotrade/venv/bin/python3
$PY ~/autotrade/research/backfill_outcomes.py   # snapshots -> candidate-outcomes (the Y)
$PY ~/autotrade/research/analyze_picks.py        # picks vs same-cycle alternatives + random baseline
$PY ~/autotrade/research/distill.py              # feature->return signal + LLM incremental value
```

The self-learning loop is held **on trial, not on trust**: it's firewalled from the
deterministic signal mining (which only uses market data), and the snapshots now log the
rulebook state, so the checkpoint can *measure* whether it improved entry quality or just
churned the policy — and cut it if it's noise.

## Where this landed (2026-07)

The experiment answered its question. The distilled learnings, each backed by a backtest
in `research/`:

**What was rejected (with evidence):**
- **Intraday LLM selection** — no edge vs a random pick from the same pool; the
  LLM-as-trader literature says the same (memorization + beta). ORB, VWAP fades, 0DTE,
  and true-intraday time-series momentum (Gao-Han-Li-Zhou first-HH→last-HH) all die at
  realistic costs.
- **Options for the equity book** — conclusively out. Double calendars are a cost-razor
  (dead at real per-leg spreads), QQQ LEAPS dip-buying is dominated by plain QLD, the
  monetized tail hedge's CAGR boost was a perfect-timing artifact (break-even-to-negative
  under realistic assumptions, and it *worsens* drawdown), and premium-selling stacks the
  crash factor. Cut drawdown with **less leverage**, not hedges.
- **Short-side participation** — negative-EV (moderate down-days V-bounce); long bias is
  correct.
- **News-drift (Tier0)** — 106k headlines over 188 less-efficient names, 10yr: no positive
  drift (mild reversal), IC≈0, OOS active return −10%/yr. LLM news scoring not warranted.

**What survived:**
- **Long-only cross-sectional momentum** (beta-tilted, but real): the deployed portfolio
  is 10 names = 7 core (6-month lookback) + 3 fast-track (3-month), equal-weight, monthly
  rotation, 1.5x margin cap, regime-gated, with **dispersion-gated leverage** (1.5x when
  momentum spread is wide, 0.9x when compressed). Generated by `research/monthly_picks.py`.
- **The LLM's real job: risk-veto, not picking.** A monthly LLM pass vetoes mechanical
  picks with broken theses — it caught an in-default name surfacing through a ticker
  change and a 40x parabola, and honestly passed on the clean ones. Data artifacts
  (unadjusted splits faking +367% momentum) are cross-checked against a second source.
- **Human-in-the-loop execution** beats full autonomy: picks are rebalanced via a
  claude.ai skill + broker connector on a dedicated account, with a hard confirm gate.

**Open thread:** PEAD (post-earnings-announcement drift) + estimate revisions — the one
literature-backed signal not yet exhausted; the nightly perishable capture exists to test
it point-in-time.

## Files
- `autotrade.py` — the engine (cycle, management, guardrails, learning).
- `config.py` — keys, paths, and all hard guardrails (edit guardrails here).
- `regime.py` / `events.py` / `options_intel.py` — regime gate, event router, IV intel.
- Book modules (separate, code-managed, held overnight by design):
  `growth_sleeve.py`, `overnight_drift.py`, `tail_hedge.py`, `earnings_crush.py`,
  `gap_fade.py`, `sector_pairs.py` (market-neutral stat-arb). Conviction-ITM (deep-ITM
  multi-day longs) lives in `autotrade.py`. `orb.py` / `mean_reversion.py` ship dark
  (`*_ENABLED=False`) — both were backtested and rejected as single-name artifacts.
  `option_scalp.py` (momentum-burst SPY/QQQ option scalps) also ships dark: prior
  evidence is adverse; `research/backtest_option_scalp.py` must pass its friction +
  exit-lag verdict bar before the flag is ever flipped.
- `alpaca_system_prompt.txt` — the model's trading instructions.
- `backtest.py` — offline backtest harness.
- `research/` — validation & distillation (see **Research & validation**):
  `backfill_outcomes.py`, `analyze_picks.py`, `distill.py`, `qc_xsmom.py`.
- `~/.autotrade.env` — secrets (copy from `autotrade.env.template`, chmod 600).

## CLI
```bash
PY=~/autotrade/venv/bin/python3; A=~/autotrade/autotrade.py
$PY $A cycle [--dry-run]   # one decision cycle (dry-run places NO orders)
$PY $A status              # what the engine currently sees
$PY $A pnl                 # account P&L + open positions
$PY $A eod                 # end-of-day: regenerate learnings + DM them
$PY $A backfill [days]     # rebuild learnings from the last N days of logs (default 14)
$PY $A outcomes            # capture the day's realized-P&L / fills to ~/autotrade_outcomes
$PY $A intel SYM           # options intel (ATM IV, IV-rank, skew) for a symbol
$PY $A warm-iv             # seed/refresh IV-rank for the names we trade options on
$PY $A growth | overnight  # run/inspect a specific code-managed book
```

## Local dashboard
A read-only "glass cockpit" — renders live state from the data the engine already
writes each cycle (snapshots / state / outcomes / learnings) plus a couple of live
Alpaca reads. It never trades and binds to **localhost only**.
```bash
~/autotrade/venv/bin/python3 ~/autotrade/dashboard.py   # -> http://127.0.0.1:8787
```
Shows: live equity / day P&L / realized-by-strategy, regime & tape, open positions and
tracked spreads, the **decision & veto stream** (every cycle's action + why a guardrail
blocked it), the signal scan, the learnings rulebook, and an equity curve. Polls every 5s.

## Scheduling (launchd, macOS)
- `com.autotrade.loop` — **KeepAlive daemon** running `autotrade.py loop 60` (the engine
  self-cycles; conditional cadence: ~3 min when active, ~5 min when quiet). This replaced
  the old `com.autotrade.cycle` StartInterval job, which stalled unpredictably on this
  host even while awake. An EOD self-heal (marker + `eod_catchup`) covers missed
  end-of-day passes.
- `com.autotrade.eod` — weekdays 13:20 PT (16:20 ET): the self-learning pass.
- `com.autotrade.outcomes` — captures realized P&L after the close.
- `com.autotrade.caffeinate` — keeps the Mac awake through market hours.

Install/refresh the jobs:
```bash
bash ~/autotrade/install.sh
```
Pause the main loop anytime:
```bash
launchctl bootout gui/$(id -u)/com.autotrade.loop
```
(Trading itself is currently paused via the `TRADING_PAUSED` env flag in
`~/.autotrade.env` — the loop keeps running for liveness + nightly data capture, but
places no orders.)

## Risk philosophy
Let winners run (no upside caps — long options and stock use a far take-profit so the
trailing stop governs), but keep ruin-prevention guardrails: a daily loss floor, a hard
daily loss halt that flattens the intraday book, defined-risk structures only, and a
hardcoded paper account. The growth sleeve and other hold-through books are separate
long-term books the intraday engine never touches.

## Remote control
- File fallback: `echo "STOP" > ~/autotrade_command.txt`
  Commands: `STOP`, `RESUME`, `CLOSE ALL`, `STATUS`, `STATS`, `FOCUS TECH`,
  `OVERRIDE` (keep trading past the daily loss halt; `OVERRIDE OFF` to undo; resets next day).
- Telegram: add `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID` to `~/.autotrade.env`. The same
  commands work from your phone, and the bot DMs you on entries/exits, the EOD learnings,
  contradiction events, and model-restore.

## First-time setup
```bash
cp ~/autotrade/autotrade.env.template ~/.autotrade.env
nano ~/.autotrade.env            # paste keys, save
chmod 600 ~/.autotrade.env
bash ~/autotrade/install.sh      # deps + scheduler

# sanity-check before it trades on its own (places NO orders):
~/autotrade/venv/bin/python3 ~/autotrade/autotrade.py cycle --dry-run
```
Read `~/autotrade.log`. When the dry-run decisions look sane, it's already live on the
schedule the installer loaded.
