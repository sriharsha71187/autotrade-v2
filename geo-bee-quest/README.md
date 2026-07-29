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
(`js/data.js`) — 660+ facts across 13 topics: maps & globes, continents &
oceans, all 50 US states & capitals, US rivers/mountains/landmarks, ~120 world
capitals and flags, world physical geography, world landmarks, India geography,
geography concepts, and **real map identification** (tap-the-state and
which-is-highlighted questions on accurate US & world maps, generated offline
from public-domain Natural Earth / US Census data — see `tools/generate-maps.js`).
Every fact is reviewable and editable in one file.

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
- **Bee simulations, format-faithful** — an NSF Mock (25 MCQs, +1 per
  correct, no negative marking — guessing encouraged, like the real
  Phase I), an IAC Mock (50 MCQs, +2/−1/0 with skip and a 30-minute
  timer, like the real qualifying exam), and an Oral Bee (spoken/typed
  answers on a 30-second clock with progressive-clue "mystery place"
  questions, like the real buzzer rounds).
- **Real bee question styles** — relational stems ("Which state lies
  directly south of Washington?", city-cluster questions), NatGeo-style
  two-option comparatives ("Which is farther north — X or Y?"), and
  odd-item-out questions, alongside recall and map identification.
- **Map challenges** — "Find Texas! Tap it on the map" (click questions,
  size-gated so tiny states get identify-only forms) and "which
  state/country is highlighted?", with a pulsing locator ring for small
  countries.
- **Voice answers** — oral-bee and spelling-mode questions accept spoken
  answers via the browser's built-in speech recognition (Chrome/Safari;
  falls back to typing). Read-aloud uses built-in speech synthesis. No AI
  services involved.
- **Countdown coach** — set the competition date in Settings and the home
  screen becomes a study plan: days remaining, today's review count, a
  new-facts-per-day pace that finishes the syllabus ~10 days early, and a
  weekly "mock bee day" reminder.
- **Parent dashboard** — per-topic coverage bars (mastered / solid / learning /
  not-yet-covered), current weak spots, accuracy, activity history, and a
  **printable review sheet** (🖨️ button) listing everything missed and not
  yet re-mastered — a coach's drill list.
- **Multi-kid profiles** — each explorer has their own name, avatar, progress,
  and Supabase sync row; switch from the avatar button on the home screen.
- **Fun layer** — XP, ranks, day-streaks, daily quests, a 40-sticker
  collection book, badges, confetti, rank-up celebrations, sounds.

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
