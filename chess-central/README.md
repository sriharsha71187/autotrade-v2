# ♞ Nirvaan Chess Central

A complete chess coaching hub for Nirvaan — game memory, engine analysis,
personalized puzzles, rival preparation, tournament finder, and a road map
that works backwards from Grandmaster.

Everything runs locally on your Mac. His games are pulled from public APIs;
nothing is uploaded anywhere.

## Quick start (Mac)

```bash
brew install stockfish        # the analysis engine (one time)
cd chess-central
./run.sh                      # installs deps on first run, then serves
```

Then open:

- **http://localhost:8425** — parent/coach dashboard
- **http://localhost:8425/kid** — Nirvaan's view (bookmark on his device)

First session: hit **Sync games** on the Overview tab. Games download from
Lichess and Chess.com (`nirvaan0421`), analysis starts automatically, and the
dashboard fills in as the engine works through his games. Analysis speed is
~15–30 seconds per game at default settings; the backlog only has to happen
once, after which each new game is analyzed within moments of syncing.

## What's inside

| Area | What it does |
|---|---|
| **Overview** | Ratings across NWSRS / USCF / Lichess / Chess.com, rating-progress chart, top insights |
| **Insights** | Engine-derived strengths, weaknesses, and opportunities with the evidence behind each — phase leaks, motif patterns (hanging pieces, missed forks), conversion problems, time trouble, tilt sessions, color gaps. Windowed to recent form by default (last 90 days, selectable) so last year's habits don't dilute this month's coaching |
| **Games** | Every synced game with per-move analysis; click one for its key moments and what should have been played |
| **Openings** | Score by opening family for each color — plus his real repertoire mined from his own games: the lines he actually plays, plain-English "left book" findings (where he runs out of known moves and what it costs), and a repertoire trainer that drills the repeated positions where his habitual move leaks |
| **Puzzles** | Generated from his own mistakes and missed tactics, with spaced repetition weighted toward current weaknesses |
| **Academy** 🎓 | A complete curriculum — 8 tracks, 44 lessons, 119 drills covering board vision, checkmates, tactics, defense, openings, endgames, strategy, and tournament habits. Every position is engine-verified (the test suite re-checks all of them); lessons pair concept text with animated example boards and interactive drills, track progress, and link to matching chess.com Premium practice areas |
| **Rival prep** | Add a regular opponent → head-to-head record, their opening repertoire, their habitual mistakes, and prep puzzles that drill the punishments |
| **Tournaments** | NWSRS/NW Chess + US Chess events near Seattle/Eastside, national scholastic championships, rating-capped online arenas; track interested → registered → played |
| **AI Coach** ✨ | Claude-powered coaching layer (see below): per-game commentary, weekly reports, ask-the-coach chat, rival pep talks |
| **GM Roadmap** | Seven-stage ladder from today's rating to the title chase, with a study plan and weekly rhythm for the current stage |
| **Journal** | Coach's notes that persist — tournament observations, goals, lessons |
| **OTB games** ♟ | Type or paste any over-the-board game (full PGN or just the moves) on the Games tab — it flows through the exact same engine analysis, puzzles, and insights as online games |
| **Weekly rhythm** | The roadmap's weekly plan is now a live checklist on Overview — several items check themselves off from real data (puzzle days, losses reviewed, games played, OTB play) |
| **Game detective** 🕵️ | Guided loss review in kid mode: after each analyzed loss, he's taken to the exact moment the game turned and asked to find the better move — completing it marks the game reviewed |
| **Kid mode** | Puzzle-first, effort-based praise, days-practiced tracking, a "your skills are growing" chart (safe-move %, not rating), badges — encouraging, zero jargon |
| **Coach packet** 🖨 | One button on Overview → a clean printable page (save as PDF) with ratings, recent form, the engine's read, his repertoire and where it leaks, rivals, and journal notes — everything a human coach wants before a lesson |
| **Backups** | A dated copy of the database lands in `data/backups/` automatically each day the app starts (2 weeks kept, manual "Back up now" in Settings) |

## The AI Coach (Claude integration)

The deterministic layer (Stockfish + the database) is the source of truth about
chess facts; Claude turns those facts into coaching language. It only ever sees
structured data the app computed and is instructed never to invent moves or
numbers.

**Setup:** put your Anthropic API key in **Settings → Anthropic API key** (it is
stored only in local `config.json`, which is gitignored) or export
`ANTHROPIC_API_KEY`. Get a key at [platform.claude.com](https://platform.claude.com).

**What it does:**

- **Game commentary** — on any analyzed game: a candid section for the coach and
  a warm, simple section for Nirvaan (his section also appears in kid mode).
  Turn on `llm_auto_commentary` in config to write it automatically after each
  analysis run.
- **Weekly report** (AI Coach tab) — a one-page readout: headline, what
  happened, what's working, the one thing to fix and how to train it, tournament
  readiness, and a note to read aloud.
- **Ask the coach** (AI Coach tab) — chat grounded in a live snapshot of his
  games, insights, motif counts, ratings, and puzzle stats.
- **Rival pep talk** — turns a scout report into a game plan + a pre-game pep
  talk (never trash-talk).

**Model & cost:** defaults to `claude-opus-5` (changeable in Settings). Server-side
refusal fallbacks are enabled, so rare safety-classifier declines transparently
retry on Anthropic's recommended fallback model. Rough cost at Opus pricing: a
few cents per game commentary, ~5-10¢ per weekly report or chat exchange.
Results are cached in the database (regenerate with the ⟳ links / `force=true`).

## Configuration

`app/config.py` holds defaults (player names, usernames, NWSRS ID `TBKBF05U`,
USCF ID `33201208`, Seattle/Eastside city list). Anything you change in
**Settings** is saved to `config.json`. The database lives at `data/chess.db`
(SQLite) — copy that one file to back up all history, notes, and puzzle
progress.

## Kid mode on his iPad (and the Parent PIN)

By default the server binds to this Mac only. To let Nirvaan use kid mode from
an iPad on your home wifi:

1. **Set a Parent PIN first** in Settings — with a PIN set, the parent
   dashboard shows a lock screen and the kid view stays open. Secrets (the API
   key, the PIN itself) are never sent back to the browser after saving.
2. Start with `HOST=0.0.0.0 ./run.sh` — the startup banner prints the LAN URL.
3. On the iPad, open `http://<your-mac's-ip>:8425/kid` and add it to the Home
   Screen.

The PIN is a courtesy gate for a shared family network, not real security —
don't expose the port beyond your home wifi.

## Keeping it fresh automatically (optional)

Sync on a schedule with launchd, same pattern as your other agents — or just
press Sync when he's played. A minimal cron alternative:

```bash
# crontab -e — sync every evening at 8pm (server must be running)
0 20 * * * curl -s -X POST http://localhost:8425/api/sync > /dev/null
```

## Tests

```bash
cd chess-central && ./.venv/bin/python -m pytest tests/ -q
```

58 offline tests cover the annotation pipeline, motif detection, puzzle
generation/uniqueness, spaced repetition, badges, sync record mapping, the
API surface, engine provenance and re-analysis, OTB game entry, the weekly
rhythm, loss review, secrets redaction, the PIN gate, and the full Academy
curriculum (every drill position is re-verified with an engine check). No
network or Stockfish needed (a built-in 2-ply engine stands in).

## Moving this to its own repository

The app is fully self-contained in this directory:

```bash
# on github.com: create an empty private repo "nirvaan-chess-central", then
git clone --no-checkout https://github.com/sriharsha71187/autotrade-v2.git tmp
cd tmp && git checkout claude/nirvaan-chess-central-gvfbbg -- chess-central
cd chess-central && git init && git add -A && git commit -m "Nirvaan Chess Central"
git remote add origin git@github.com:sriharsha71187/nirvaan-chess-central.git
git push -u origin main
```

(or simply copy the `chess-central/` folder into a fresh clone of the new repo)

## Notes & limitations

- **OTB rating scrapers** (NWSRS, US Chess) and the tournament scrapers are
  best-effort HTML parsing — sites change. When a scrape fails the app keeps
  the last-known values and the seeded PNW tournament calendar, and you can
  add events manually.
- **Engine settings**: `engine_movetime_ms` (default 350ms/position) trades
  speed for depth. For a scholastic player's games it is already far stronger
  than needed to find every real mistake.
- **Rival scouting** analyzes up to ~12 of the rival's recent games with a
  quick engine pass; re-scout before a big event for fresh data.
