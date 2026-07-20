# AutoTrade — an autonomous AI trading bot, built to test itself

*A self-running trading system for US equities & defined-risk options — engineered around one
unusual idea: don't assume the AI has an edge, **measure** whether it does, and distill the part
that works into code.*

---

### What it is
A bot that trades on its own. Each qualifying cycle, an AI model (Claude) proposes **one** trade;
a deterministic code layer can veto, resize, or override it — but can never invent a trade. It runs
unattended on a schedule, manages its own positions, enforces its own risk limits, and reports to
the operator by phone (Telegram). **Paper-traded only** — no live-money risk — while its edge is
validated.

### Design philosophy — *the model picks, the code protects*
Most AI-trading projects hand the model the keys. This one inverts the trust: the LLM is wrapped in
layers of deterministic guardrails it cannot override.
- **Regime gate** — market trend × volatility decide which strategies may even open (or force cash).
- **Event router** — rides fresh catalysts; braces before scheduled binary events.
- **Guardrails** — anti-chase, counter-trend blocks, per-name & correlation caps, a hard daily-loss halt.
- **Code-enforced exits** — trailing stops, breakeven locks, sector-confluence stops, **defined-risk only**.

The model only fills in a decision inside a box the code has already drawn.

### What makes it different — *it measures itself*
The hard question most projects skip: *does the AI's stock-picking actually add value, or is it
expensive noise?* This bot is built to answer that with data:
- Every decision logs the **full choice set** — every candidate it saw, the features, the news it
  read, the pick, and the result.
- A research pipeline computes the **forward return of every candidate it passed on**, not just what
  it traded — the only honest way to measure selection skill.
- A **distillation** layer mines that data for a deterministic signal and tests whether the AI adds
  anything *beyond* the observable features.

The endgame: **distill the AI's useful behavior into deterministic code, and remove the AI wherever
a rule reproduces it.** Model as *teacher*, not trader.

### Honest status
- ✅ It runs autonomously, safely, and exactly as designed — the engineering works.
- ⚠️ **Edge: unproven.** Three independent checks — a leak-free backtest, the academic literature,
  and the bot's *own* live decisions — so far show **no proven intraday selection edge**: the model's
  picks haven't beaten a random pick from the same candidate pool. The one durable signal found
  anywhere is long-only cross-sectional momentum — market *beta*, not alpha.
- 🔬 It is therefore run as an **instrumented experiment** — to find and distill an edge, not as a
  proven money-maker. Knowing *whether* it works is the entire point.

### Under the hood
| | |
|---|---|
| **Stack** | Python · Claude (LLM) · Alpaca (broker + data) · scheduler · Telegram control |
| **Self-learning** | a nightly evidence-weighted rulebook — held *on trial*, measured not trusted |
| **Risk** | defined-risk structures only · hard daily-loss halt · paper account hardcoded · let winners run |
| **Observability** | localhost dashboard (live equity, decisions, vetoes, learnings) + full per-cycle logs |

---

*Research / validation phase. Paper-trading only. Built for rigor over hype — the goal is to know
the truth about whether an LLM can trade, and to keep only the part that demonstrably works.*
