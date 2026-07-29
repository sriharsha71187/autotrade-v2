# 🌍 GeoBee Quest

A fun, **adaptive** geography-bee training app for kids — built for a rising
2nd grader preparing for geography bee competitions. No AI at runtime, no ads,
no accounts for the kid, works offline, free to host.

Question styles are modeled on the two main elementary geography bees:

| Competition | Format trained |
|---|---|
| **NSF Junior Geography Bee** (grades 1–3) | Phase I written test (25 MCQs) + oral rounds; includes a dedicated India-geography section |
| **IAC / International Geography Bee** (elementary) | 50-MCQ qualifying exam with **+2 / −1 / 0** scoring + buzzer-style recall rounds |

All questions are original, generated from a **hand-curated fact bank**
(`js/data.js`) — ~450 facts across 11 topics: maps & globes, continents &
oceans, all 50 US states & capitals, US rivers/mountains/landmarks, ~120 world
capitals and flags, world physical geography, world landmarks, India geography,
and geography concepts. Every fact is reviewable and editable in one file.

## How it teaches (not just quizzes)

- **Placement first** — a 12-question adaptive diagnostic finds the right
  starting tier instead of starting from zero.
- **Teach → test** — new facts are introduced with a "New fact!" card, then
  quizzed immediately.
- **Per-fact memory** — every fact has a Leitner-style strength (box 0–5).
  Correct answers push review further out (4h → 1d → 3d → 7d → 21d);
  misses come back within the same session, then again the next day.
- **Mastered = retired** — solid facts stop repeating except for rare
  long-interval retention checks. No random repetition.
- **Difficulty tiers 1–5 per topic** — the app promotes a topic to the next
  tier when ~80% of the current tier is known.
- **Varied forms** — the same fact appears as capital→state, state→capital,
  flag→country, nickname→state, and typed-recall, so there's no pattern
  memorization.
- **Bee simulations** — a Written Bee (25 MCQs, +2/−1/0 scoring) and an Oral
  Bee (typed recall, 3 strikes), mirroring real formats.
- **Parent dashboard** — per-topic coverage bars (mastered / solid / learning /
  not-yet-covered), current weak spots, accuracy, and activity history.
- **Fun layer** — XP, ranks, streaks, badges, confetti, sounds, and optional
  read-aloud (browser speech, no AI service).

## Running it

It's a static site — no build step.

- **Locally:** just open `index.html`, or `python3 -m http.server` and visit
  `http://localhost:8000`.
- **GitHub Pages:** push this folder as the root of a public repo; the included
  workflow (`.github/workflows/pages.yml`) auto-enables Pages and deploys on
  every push to `main`. Your app appears at
  `https://<username>.github.io/<repo>/`.

## Progress storage & multi-device sync

Progress always saves to the browser (`localStorage`) — the app is fully
usable offline with zero setup. To share one memory across devices
(iPad + laptop, etc.):

1. Create a free [Supabase](https://supabase.com) project.
2. In the Supabase **SQL Editor**, run the contents of
   [`supabase/schema.sql`](supabase/schema.sql) (creates one table with
   row-level security).
3. Copy **Project Settings → API → Project URL** and the **anon public** key
   into [`js/config.js`](js/config.js).
4. In the app: **Settings → Family Sync** → create the parent account once,
   then sign in on each device. Progress merges automatically (per-fact,
   latest-wins) and syncs after every round; offline play syncs when back
   online.

The anon key is designed to be public in frontend code — data is protected by
row-level security, so each account can only read/write its own rows.

There's also a manual **backup code** (Settings → Backup) that works with no
account at all.

## Editing content

Everything lives in `js/data.js`:

- `COUNTRIES` — name, capital, continent, flag emoji, difficulty tier
  (`ct` overrides the tier for tricky capitals like Canberra/Bern).
- `STATES` — the 50 states with capitals, regions, nicknames, tiers.
- Topic arrays (`MAPS`, `CONTINENTS`, `US_PHYSICAL`, `WORLD_PHYSICAL`,
  `US_LANDMARKS`, `WORLD_LANDMARKS`, `INDIA`, `CONCEPTS`) — each item is
  `{ q, a, d: [3 distractors], t: tier, x: fun fact, alt: [accepted typed answers] }`.

Add a fact, refresh the page, and it enters the adaptive rotation
automatically. Topics can be toggled on/off in Settings.

## Privacy

No analytics, no tracking, no third-party scripts. Flag images load from
flagcdn.com (with emoji fallback offline); the optional sync talks only to
*your own* Supabase project.

## License

MIT
