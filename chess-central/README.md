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
| **Insights** | Engine-derived strengths, weaknesses, and opportunities with the evidence behind each — phase leaks, motif patterns (hanging pieces, missed forks), conversion problems, time trouble, tilt sessions, color gaps |
| **Games** | Every synced game with per-move analysis; click one for its key moments and what should have been played |
| **Openings** | Score by opening family for each color — spot the repertoire gaps |
| **Puzzles** | Generated from his own mistakes and missed tactics, with spaced repetition weighted toward current weaknesses |
| **Rival prep** | Add a regular opponent → head-to-head record, their opening repertoire, their habitual mistakes, and prep puzzles that drill the punishments |
| **Tournaments** | NWSRS/NW Chess + US Chess events near Seattle/Eastside, national scholastic championships, rating-capped online arenas; track interested → registered → played |
| **GM Roadmap** | Seven-stage ladder from today's rating to the title chase, with a study plan and weekly rhythm for the current stage |
| **Journal** | Coach's notes that persist — tournament observations, goals, lessons |
| **Kid mode** | Puzzle-first, streaks, badges, rating rocket — encouraging, zero jargon |

## Configuration

`app/config.py` holds defaults (player names, usernames, NWSRS ID `TBKBF05U`,
USCF ID `33201208`, Seattle/Eastside city list). Anything you change in
**Settings** is saved to `config.json`. The database lives at `data/chess.db`
(SQLite) — copy that one file to back up all history, notes, and puzzle
progress.

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

19 offline tests cover the annotation pipeline, motif detection, puzzle
generation/uniqueness, spaced repetition, badges, sync record mapping, and
the API surface. No network or Stockfish needed (a built-in 2-ply engine
stands in).

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
