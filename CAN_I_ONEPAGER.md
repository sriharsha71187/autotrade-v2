# CAN I? — one-pager (draft v1, August 2026)

**The permissions layer for the agentic web.** One call tells any AI agent what a
website allows it to do — with evidence, timestamped.

```
REQUEST — MCP tool call or HTTPS
can_i(url="acme-shop.com/products", agent="claude-web", action="extract")

RESPONSE — 200 · 38 ms · $0.02 settled via x402
{
  "verdict":        "CONDITIONAL",
  "conditions":     ["rate ≤ 1 req/s", "no training use"],
  "evidence": {
    "robots.txt":   "Claude-Web: Crawl-delay: 1",
    "ai.txt":       "train: disallow",
    "tos":          "§4.2 — automated access permitted for personal use"
  },
  "observed_at":    "2026-08-30T09:15:02Z",
  "archived_since": "2026-08-31"
}
```

## The gap

Millions of agents act on websites; none of them can tell what they're allowed to
do. The web's permission signals have fragmented: per-vendor robots.txt rules for a
dozen crawlers, the emerging ai.txt convention, cryptographic agent cards, and
automation clauses buried in terms of service. No queryable source of truth exists.
Builders either hand-roll fragile parsers or ignore permissions and absorb the
liability — while AI-related litigation already spans hundreds of cases and "was
this access permitted?" becomes a question asked in court.

## The product

One endpoint, three verdicts — **ALLOWED / CONDITIONAL / DENIED** — receipts
included. CAN I continuously crawls the permission surface of the top 100,000
domains (robots.txt, ai.txt, agent cards, ToS automation clauses), normalizes the
competing standards into one schema, and serves a single answer: what *this* agent
may do on *this* site, right now, with the evidence quoted back. Delivered as an
MCP tool any agent installs in one line, and as plain HTTPS. Every check is logged
for the customer: exportable, timestamped proof that their agent asked before it
acted.

## Why now

- **Agent traffic is exploding** — every browsing, scraping, and shopping agent
  faces this question on every request.
- **The standards chaos is the moat** — robots.txt, ai.txt, agent cards, and ToS
  all disagree; normalizing them is the product nobody wants to build twice.
- **Machines can pay now** — x402 settled ~165M agent transactions in months; an
  agent can discover, call, and pay for CAN I with no human in the loop.
- **Nobody is archiving consent** — no one systematically records what sites
  permitted, when. That point-in-time record cannot be recreated later at any price.

## Business model

| Tier            | Price        | What it buys                                                      |
| --------------- | ------------ | ----------------------------------------------------------------- |
| Free            | $0           | 1,000 checks/mo — zero-friction install, top of funnel            |
| Metered         | $0.02/check  | Agent-paid via x402 or card on file; volume discounts             |
| Audit trail     | $99/mo       | Logged, exportable proof of every check — the retention product   |
| Archive (later) | License      | "What did site X permit on date T" — evidence for legal/compliance |

Buyers: builders of browsing and scraping agents, agent frameworks and platforms
(OEM), compliance teams — and, for the archive, litigators. The first three are
self-serve; no sales motion required.

## Runs itself

`CRAWL → DIFF → NORMALIZE → SERVE → ARCHIVE`

Scheduled, self-billing, self-healing: parser failures fall back to LLM
re-parsing; anything unresolved lands in an exception queue reviewed weekly
(~30 min of human time). CAN I reports what sites *declare*, not legal advice,
which keeps disputes out of the inbox. Distribution is agent-native: registry
listings, docs written for LLMs to read, starter-template integrations — agents
find and install it the moment their builder asks "how do I make my agent polite?"

## The compounding asset

Every day of operation is a day no competitor can buy back. Serving today's
snapshot funds collecting yesterday's history. The archive — a daily, attested,
point-in-time record of what the web permitted which agents to do — becomes the
evidentiary layer for the coming decade of AI-access disputes, and the trust
substrate for agent marketplaces. Late entrants can copy the crawler; they cannot
copy the calendar.

## First 90 days

| Weeks | Milestone                                                                          |
| ----- | ---------------------------------------------------------------------------------- |
| 1–2   | Crawler live on top 10k domains; daily snapshots archiving                          |
| 3–4   | `can_i` MCP server listed (Smithery, mcp.so, AgenticMarket); 10 design partners     |
| 5–8   | Coverage to 100k domains; publish "State of Agent Permissions" report               |
| 9–12  | Audit-trail subscriptions live; $1k MRR; first platform/OEM conversation            |

---

*CAN I? — ask before you act.*
