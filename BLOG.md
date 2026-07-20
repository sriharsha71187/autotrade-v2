# I Built an AI Trading Bot. It Demoted Itself to Compliance.

*A journey in twelve backtests, one fake stock, and $2,382 of imaginary money I lost while asleep.*

---

Like every person who has ever had both a Claude subscription and a brokerage account, I had The Idea: what if the AI just... traded for me? It reads faster than me, it doesn't panic, it doesn't get bored, and it has read every investing book ever written. I'd wake up to gains. The bot would send me a polite Telegram message: *"Good morning. We are rich."*

Reader, we were not rich.

## Act I: The Money Printer (that printed learnings instead)

Version one was glorious on paper. An LLM-powered engine that woke up every few minutes during market hours, looked at momentum, news, and sector flows, and decided — with the confidence of a man who has never been wrong — what to buy. It had a self-learning system. It journaled its lessons every night like a very expensive teenager.

The early days taught me things no course teaches:

- My bot once **legged out of a spread and left a naked short option open overnight**. I woke up down $2,382 (paper money, thank God) and up one deeply personal understanding of why brokers make you sign things.
- macOS `launchd` is a bigger threat to algorithmic trading than any market crash. My scheduler simply... wouldn't. The fix involved a KeepAlive daemon and the acceptance that my laptop naps more than my bot trades.
- A six-agent audit of my own codebase found **33 findings**, including the fact that my P&L math had been quietly wrong across days. The dashboard said we were fine. The dashboard lied.

Still — bugs are fixable. I fixed them. The engine got clean, the guardrails got real. Which is when the actual problem appeared.

## Act II: The Autopsy

With the plumbing finally trustworthy, I ran the test that everyone building an AI trader should run on day one and no one ever does: **I compared my brilliant LLM's intraday picks against random selection.**

Random did just as well.

I want to be precise here, because this cost me weeks: it's not that the AI was dumb. Its *reasoning* was gorgeous. Every trade came with a thesis worthy of a hedge fund memo. But gorgeous reasoning about a coin flip is still a coin flip, and academic literature backs this up — LLMs-as-stock-pickers largely deliver memorization plus market beta, dressed in confident prose.

So I did what any emotionally stable person would do. I declared a research mandate, paused trading, and backtested *everything*. The graveyard, in order of burial:

- **Intraday strategies** (opening range breakouts, VWAP reversion, first-half-hour momentum — including one effect from an actual peer-reviewed paper): the raw predictive signal today is under **1 basis point per day**. The transaction toll is 2–10bp per day. You cannot win a game where the entry fee exceeds the prize. The people who profit intraday are the ones *collecting* the toll.
- **0DTE options "income"** (the YouTube favorite — sell iron condors, win 90% of days): my backtest won 73–93% of days and still went to zero in every full-size variant, because the worst day loses **103% of the margin**. A strategy whose life expectancy is "until the first bad Tuesday."
- **Selling puts for "90% probability of profit"**: real win rate, fake edge. High win rate plus negative skew equals a slot machine running in reverse — it pays you in pennies until it charges you in years.
- **The tail hedge** (buy cheap crash insurance, look like a genius in 2020): the backtest boost turned out to require *selling the puts at the exact top of the panic*, an assumption I can only describe as astrology with Greeks.
- **LLM-as-stock-picker, one more time, with feeling**: I had the model build a point-in-time portfolio as of three months ago — with web access restricted to information before that date — and measured it forward. It made a sophisticated macro rotation call. It returned **+4%**. The dumb mechanical strategy returned **+53%**. The market does not award style points.

## Act III: The Survivor

One thing refused to die: **cross-sectional momentum**. Buy the top 10 stocks ranked by 6-month return, hold a month, repeat. Step to cash when the index is below its 200-day moving average. That's it. That's the strategy. It's been in the academic literature since 1993, it's about as secret as gravity, and it beat every clever thing I built by a margin that was frankly rude.

I tried so hard to improve it, and I want you to see the scoreboard, because it's the most instructive thing I own:

| My clever idea | Result vs. plain momentum |
|---|---|
| Volatility-scaled momentum | Worse |
| Residual (beta-stripped) momentum | Worse |
| 52-week-high proximity | Much worse |
| Diversified / decorrelated basket | Worse |
| 30 names instead of 10 | Worse |
| Inverse-volatility weighting | Much worse |
| Regime-switching between strategies | Worse out-of-sample |
| An ensemble of all of the above | Worst of all |

Ten attempts at intelligence, ten funerals. The only tweak that survived testing: reserving 3 of the 10 slots for *3-month* momentum — a fast lane for fresh runs (the stock that went up 7x last cycle entered the book two months into its run; the fast lane would've caught it a month earlier).

And one giant asterisk I will now tattoo on every backtest I ever publish: **survivorship bias**. My stock universe was "companies in the index *today*" — meaning every bankruptcy was helpfully deleted from history before my backtest bought it. One options backtest compounded to **$108 million** before I noticed it was trading in a world where nothing ever dies. The honest benchmark — a real momentum ETF that lived through every delisting — earns about 20% a year. Assume my numbers are optimistic and you'll be righter than my dashboard ever was.

## Act IV: The Demotion (or: my AI found its calling)

Here's the twist that makes the whole saga worth it. While the LLM was terrible at *picking* stocks, it turned out to be genuinely excellent at *vetoing* them.

The mechanical screen once ranked a ticker at the top of the momentum list — except the "stock" was a three-day-old ticker belonging to a company already in default. Price data can't see that. The AI, with thirty seconds of web search, could. Veto.

Then came my favorite day of the project: the screen served up a packaging company as the #2 momentum stock in America, up +367% in six months. The AI reviewed the list and refused to trade, on the grounds that the numbers "looked fabricated." They were. A corporate action had corrupted the price history; the real six-month return was **−6%**. My data pipeline had invented a monster stock, and the AI caught it in the act like a bouncer checking a fake ID.

So the org chart changed. The AI is no longer the portfolio manager. It's the **risk officer** — and honestly, it's a natural. The system now looks like this:

1. A dumb, cold script ranks the momentum stocks each month (and cross-checks every candidate against a second data source, because *fool me once*).
2. The AI investigates each name for fraud, distress, delisting, fake tickers, and junk-spikes — things price can't see but search can.
3. It shows me the full trade plan **and stops**.
4. Nothing happens until I type "confirm."

Human on the trigger, machine on the checklist, formula on the picks. Every layer earns its place by *checking facts*, not by having opinions.

## What I actually learned

- **There is no secret alpha at my scale.** The edge that survived is public, old, and boring. The work wasn't finding it — it was killing everything that looked shinier.
- **Costs are the apex predator.** Every fast strategy died of friction, not of being wrong.
- **Win rate is a sales metric, not an edge.** The 90%-win strategies all had the same tombstone.
- **Survivorship bias is everywhere** — in the backtest universe, and in the YouTube gurus whose blown-up predecessors don't make content anymore.
- **AI's best role in trading isn't oracle. It's skeptic.** Mine went from making predictions to auditing reality — from "buy this" to "this ticker is three days old and the company is in default" — and it has already paid for itself twice.
- **The hardest part is still ahead**, and it isn't code: it's not touching the dial during the first −30% year, which the backtest politely promises is coming.

The bot and I are live now — small, leveraged-but-capped, gated, vetoed, confirmed. I'll report back. If it works, remember this was all my idea. If it doesn't, the AI approved every trade.

---

*Built with Python, one very patient AI, a graveyard of backtests, and a dedicated brokerage account whose only job is to keep my experiment away from my actual money. Not investment advice — my own bot barely takes my advice.*
