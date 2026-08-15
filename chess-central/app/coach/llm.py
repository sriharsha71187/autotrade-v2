"""The AI coach: Claude turns Stockfish facts into coaching language.

Design rule: Stockfish and the database are the source of truth about chess
facts. Claude only ever sees structured data we computed (moves, evals,
motifs, stats) and writes the coaching narrative around it — it is
instructed never to invent moves or numbers.

Uses the official Anthropic SDK. Model default: claude-opus-5.
Server-side refusal fallbacks are enabled (fallbacks="default") so a rare
classifier decline transparently retries on the recommended fallback model.
"""
from __future__ import annotations

import json
from collections import defaultdict

from .. import config, db, util

SYSTEM_PROMPT = """You are the personal chess coach for Nirvaan, a young scholastic \
chess player in the Pacific Northwest (NWSRS rated, early in his journey, long-term \
goal: climb as far as his love of the game takes him — the family works backwards \
from Grandmaster as the north star).

You are given structured data computed by a chess engine (Stockfish) and the app's \
database: games, per-move evaluations, mistake classifications, tactical motif tags, \
puzzle performance, and rating history.

Hard rules:
- Ground every statement in the data provided. NEVER invent moves, evaluations, \
opponents, or statistics that are not in the data.
- If the data is insufficient to answer, say so plainly.
- Chess advice must be appropriate for a developing scholastic player: piece safety, \
checks/captures/threats, basic tactics and endgames — not GM-level abstractions.
- Be encouraging but honest. Effort and process over results. Losses are learning \
material, never shameful.
- When writing for Nirvaan directly, use short sentences, a warm tone, and concrete \
tips he can use in his very next game. When writing for his parent/coach, be candid \
and analytical.
"""


# ------------------------------------------------------------------ plumbing

def is_configured() -> bool:
    return bool(_api_key())


def _api_key() -> str:
    import os
    return (config.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")).strip()


def _model() -> str:
    return config.get("llm_model") or "claude-opus-5"


def _call(user_content: str, effort: str = "high", max_tokens: int = 8000) -> str:
    """One grounded coaching call. Raises LLMError with a friendly message."""
    try:
        import anthropic
    except ImportError as e:
        raise LLMError("The 'anthropic' package is not installed — run "
                       "./.venv/bin/pip install anthropic") from e
    if not is_configured():
        raise LLMError("No Anthropic API key configured. Add it in Settings "
                       "(stored only in local config.json).")

    client = anthropic.Anthropic(api_key=_api_key())
    try:
        response = client.beta.messages.create(
            model=_model(),
            max_tokens=max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": effort},
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
        )
    except anthropic.AuthenticationError as e:
        raise LLMError("Anthropic API key was rejected — check it in Settings.") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Rate limited by the Anthropic API — try again in a minute.") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Anthropic API error ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Could not reach the Anthropic API — check your connection.") from e

    if response.stop_reason == "refusal":
        raise LLMError("The model declined this request (safety classifier). "
                       "This is rare for chess content — try rephrasing.")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise LLMError("Empty response from the model — try again.")
    return text


class LLMError(Exception):
    """User-presentable LLM failure."""


SCAN_SCHEMA = {
    "type": "object",
    "properties": {
        "moves_san": {
            "type": "array", "items": {"type": "string"},
            "description": "Every move in standard algebraic notation, in order, "
                           "white and black alternating, no move numbers",
        },
        "white_name": {"type": ["string", "null"]},
        "black_name": {"type": ["string", "null"]},
        "date": {"type": ["string", "null"], "description": "YYYY-MM-DD if visible"},
        "result": {"type": ["string", "null"],
                   "description": "Exactly '1-0', '0-1' or '1/2-1/2' if written"},
        "event": {"type": ["string", "null"]},
        "notes": {"type": "string",
                  "description": "Anything uncertain: illegible moves, guesses made, "
                                 "ambiguous handwriting — so a human can double-check"},
    },
    "required": ["moves_san", "white_name", "black_name", "date", "result",
                 "event", "notes"],
    "additionalProperties": False,
}


def scan_scoresheet(image_b64: str, media_type: str) -> dict:
    """Read a photographed handwritten scoresheet into moves + headers.

    Returns the model's raw transcription — the caller validates legality
    move by move; nothing is trusted until python-chess replays it.
    """
    try:
        import anthropic
    except ImportError as e:
        raise LLMError("The 'anthropic' package is not installed — run "
                       "./.venv/bin/pip install anthropic") from e
    if not is_configured():
        raise LLMError("Scoresheet scanning uses the AI coach — add your "
                       "Anthropic API key in Settings first.")

    client = anthropic.Anthropic(api_key=_api_key())
    prompt = (
        "This is a photo of a handwritten chess scoresheet from a scholastic "
        "tournament. Transcribe it.\n\n"
        "- Read the moves in order (columns are usually White | Black per row, "
        "rows numbered).\n"
        "- Output standard algebraic notation (SAN): e4, Nf3, O-O, exd5, Qxf7+, "
        "e8=Q. Normalize sloppy notation (0-0 -> O-O, NF3 -> Nf3, PxP needs "
        "the real squares if you can infer them from context).\n"
        "- Kids' scoresheets have errors: skipped numbers, moves in the wrong "
        "column, illegible scribbles. Transcribe what is actually written; when "
        "you must guess between readings, pick the chess-plausible one and "
        "mention it in notes.\n"
        "- If a move is truly illegible, stop the move list there and say so in "
        "notes — a shorter correct list beats a longer corrupted one.\n"
        "- Also read the header fields (players, date, result) if present."
    )
    try:
        response = client.beta.messages.create(
            model=_model(),
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={
                "effort": "high",
                "format": {"type": "json_schema", "schema": SCAN_SCHEMA},
            },
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": media_type,
                                "data": image_b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
    except anthropic.AuthenticationError as e:
        raise LLMError("Anthropic API key was rejected — check it in Settings.") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Rate limited by the Anthropic API — try again in a minute.") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Anthropic API error ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Could not reach the Anthropic API — check your connection.") from e

    if response.stop_reason == "refusal":
        raise LLMError("The model declined to read this image — try a clearer photo.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError("Could not parse the transcription — try a clearer photo.") from e


def _note_get(kind: str, ref_id: str) -> dict | None:
    return db.row("SELECT * FROM llm_notes WHERE kind=? AND ref_id=?", (kind, str(ref_id)))


def _note_put(kind: str, ref_id: str, content: str) -> None:
    with db.tx() as conn:
        conn.execute(
            """INSERT INTO llm_notes (kind, ref_id, content, model, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(kind, ref_id) DO UPDATE SET
                 content=excluded.content, model=excluded.model,
                 created_at=excluded.created_at""",
            (kind, str(ref_id), content, _model(), util.now_iso()))


# ------------------------------------------------------- data assembly (facts)

def _game_facts(game_id: int) -> dict:
    g = db.row("SELECT * FROM games WHERE id=?", (game_id,))
    if not g:
        raise LLMError("Game not found.")
    if not g["analyzed_at"]:
        raise LLMError("Game is not analyzed yet — run analysis first.")
    moves = db.rows("SELECT * FROM moves WHERE game_id=? ORDER BY ply", (game_id,))
    key_moments = [
        {
            "move_number": (m["ply"] + 1) // 2,
            "side": "Nirvaan" if m["mover"] == "player" else "opponent",
            "played": m["san"],
            "classification": m["classification"],
            "better_was": m["best_san"] or m["best_uci"],
            "win_chance_before_pct": m["winprob_before"],
            "win_chance_after_pct": m["winprob_after"],
            "motifs": json.loads(m["motifs"] or "[]"),
            "phase": m["phase"],
            "seconds_spent": m["move_time"],
        }
        for m in moves
        if m["classification"] in ("inaccuracy", "mistake", "blunder")
    ]
    good_moves = [
        {"move_number": (m["ply"] + 1) // 2, "played": m["san"],
         "motifs": json.loads(m["motifs"] or "[]")}
        for m in moves
        if m["mover"] == "player" and m["classification"] == "best" and m["motifs"] not in (None, "[]")
    ]
    from ..analysis.annotate import game_accuracy
    return {
        "game": {k: g[k] for k in ("platform", "color", "opponent_name",
                                   "opponent_rating", "player_rating", "result",
                                   "termination", "time_class", "opening_name",
                                   "eco", "played_at", "moves_count")},
        "accuracy": game_accuracy(game_id),
        "key_moments": key_moments,
        "his_best_moments": good_moves[:8],
        "full_movelist_san": " ".join(m["san"] for m in moves),
    }


def _overview_facts(days: int = 30) -> dict:
    """Aggregate facts for reports and chat grounding."""
    from ..puzzles.scheduler import stats as puzzle_stats
    games = db.rows(
        """SELECT played_at, color, opponent_name, opponent_rating, player_rating,
                  result, termination, time_class, opening_name
           FROM games WHERE played_at >= date('now', ?) ORDER BY played_at DESC""",
        (f"-{days} days",))
    insights = db.rows("SELECT kind, title, detail FROM insights ORDER BY score DESC LIMIT 12")
    ratings = db.rows(
        """SELECT source, date, rating FROM ratings_history
           WHERE date >= date('now', ?) ORDER BY date""", (f"-{days + 60} days",))
    phase = db.rows(
        """SELECT phase, COUNT(*) n,
                  ROUND(AVG(MAX(winprob_before - winprob_after, 0)), 2) avg_winprob_loss,
                  SUM(classification='blunder') blunders
           FROM moves WHERE mover='player' GROUP BY phase""")
    motifs: dict[str, int] = defaultdict(int)
    for r in db.rows("""SELECT motifs FROM moves
                        WHERE mover='player' AND classification IN ('mistake','blunder')"""):
        for t in json.loads(r["motifs"] or "[]"):
            motifs[t] += 1
    record = db.row(
        "SELECT COUNT(*) n, SUM(result='win') w, SUM(result='loss') l FROM games")
    return {
        "all_time_record": record,
        f"games_last_{days}_days": games[:60],
        "current_insights": insights,
        "rating_history_recent": ratings,
        "mistakes_by_phase": phase,
        "mistake_motif_counts": dict(sorted(motifs.items(), key=lambda kv: -kv[1])),
        "puzzle_training": puzzle_stats(),
    }


# ------------------------------------------------------------------ features

def game_commentary(game_id: int, force: bool = False) -> dict:
    """Coach's written commentary for one game: a parent section + a kid section."""
    if not force:
        cached = _note_get("game_commentary", game_id)
        if cached:
            return {"content": cached["content"], "cached": True,
                    "model": cached["model"], "created_at": cached["created_at"]}
    facts = _game_facts(game_id)
    prompt = f"""Here is the engine-analyzed data for one of Nirvaan's games:

```json
{json.dumps(facts, indent=1)}
```

Write commentary on this game in exactly two markdown sections:

## For the coach
Candid analysis for the parent/coach (4-8 sentences): the story of the game, the \
decisive moments (reference specific moves from key_moments), what pattern this game \
fits in his development, and ONE specific thing to practice this week because of \
this game.

## For Nirvaan
A short note to Nirvaan himself (3-5 short sentences, warm, age-appropriate): one \
thing he genuinely did well (use his_best_moments or solid phases), one moment to \
learn from described simply, and one concrete tip for the next game. End with \
encouragement that emphasizes effort."""
    content = _call(prompt, effort="medium", max_tokens=4000)
    _note_put("game_commentary", str(game_id), content)
    return {"content": content, "cached": False, "model": _model(),
            "created_at": util.now_iso()}


def weekly_report(force: bool = False) -> dict:
    """One-page coach report covering the recent period."""
    week_key = util.now_iso()[:10]
    if not force:
        cached = _note_get("weekly_report", week_key)
        if cached:
            return {"content": cached["content"], "cached": True,
                    "model": cached["model"], "created_at": cached["created_at"]}
    facts = _overview_facts(days=14)
    prompt = f"""Here is Nirvaan's chess data for roughly the last two weeks, plus \
all-time context:

```json
{json.dumps(facts, indent=1, default=str)}
```

Write the coach's report for the parent as markdown with these sections:
1. **The headline** — one sentence: the single most important thing this period.
2. **What happened** — games played, results, rating movement, notable games.
3. **What's working** — grounded in the strengths/insights data.
4. **The one thing to fix** — the highest-impact weakness right now, with the \
evidence, and exactly how to train it this week (be concrete: which kind of \
puzzles, what time control, what habit).
5. **Tournament readiness** — given current form, what kind of event makes sense soon.
6. **A note to read to Nirvaan** — 2-3 warm sentences to read aloud to him.

Keep the whole report under 500 words. If there were few or no games this period, \
say so and focus on the training data instead."""
    content = _call(prompt, effort="high", max_tokens=8000)
    _note_put("weekly_report", week_key, content)
    return {"content": content, "cached": False, "model": _model(),
            "created_at": util.now_iso()}


def rival_brief(rival_id: int, force: bool = False) -> dict:
    """Kid-readable pre-game pep talk built from the scout report."""
    rival = db.row("SELECT * FROM rivals WHERE id=?", (rival_id,))
    if not rival:
        raise LLMError("Rival not found.")
    if not rival["scout_report"]:
        raise LLMError("Scout this rival first — the brief is built from the scout report.")
    if not force:
        cached = _note_get("rival_brief", rival_id)
        if cached:
            return {"content": cached["content"], "cached": True,
                    "model": cached["model"], "created_at": cached["created_at"]}
    report = json.loads(rival["scout_report"])
    prompt = f"""Nirvaan has an upcoming game against a familiar opponent: {rival['name']}. \
Here is the scouting data (head-to-head record, their opening repertoire, their \
typical mistakes from engine analysis of their public games):

```json
{json.dumps(report, indent=1)}
```

Write a pre-game brief in two markdown sections:

## Game plan (for the coach)
The practical prep: what they play as White and Black and what Nirvaan should answer \
with, the mistakes they tend to make and how to be ready to punish them, and how to \
handle the head-to-head history psychologically.

## Pep talk (read to Nirvaan)
4-5 short sentences he can absorb in the minute before the game: what to watch for, \
one simple plan, and confidence. Never trash-talk the opponent — respect + readiness."""
    content = _call(prompt, effort="medium", max_tokens=4000)
    _note_put("rival_brief", str(rival_id), content)
    return {"content": content, "cached": False, "model": _model(),
            "created_at": util.now_iso()}


# ------------------------------------------------------------------ chat

MAX_CHAT_TURNS = 30


def chat(message: str) -> dict:
    """Ask-the-coach: multi-turn chat grounded in the live database."""
    message = (message or "").strip()
    if not message:
        raise LLMError("Empty message.")
    history = db.rows(
        "SELECT role, text FROM coach_chat ORDER BY id DESC LIMIT ?", (MAX_CHAT_TURNS,))
    history.reverse()
    # the window must start on a user turn or the API rejects the role order
    while history and history[0]["role"] != "user":
        history.pop(0)

    facts = _overview_facts(days=60)
    grounding = (
        "Current data snapshot for grounding (regenerated each conversation turn):\n"
        f"```json\n{json.dumps(facts, indent=1, default=str)}\n```"
    )
    messages = [{"role": "user", "content": grounding},
                {"role": "assistant",
                 "content": "Understood — I'll ground my coaching answers in this data."}]
    for h in history:
        messages.append({"role": h["role"], "content": h["text"]})
    messages.append({"role": "user", "content": message})

    try:
        import anthropic
    except ImportError as e:
        raise LLMError("The 'anthropic' package is not installed.") from e
    if not is_configured():
        raise LLMError("No Anthropic API key configured — add it in Settings.")
    client = anthropic.Anthropic(api_key=_api_key())
    try:
        response = client.beta.messages.create(
            model=_model(),
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium"},
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=messages,
        )
    except anthropic.AuthenticationError as e:
        raise LLMError("Anthropic API key was rejected — check it in Settings.") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Rate limited — try again in a minute.") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Anthropic API error ({e.status_code}).") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Could not reach the Anthropic API.") from e

    if response.stop_reason == "refusal":
        raise LLMError("The model declined this request — try rephrasing.")
    reply = "".join(b.text for b in response.content if b.type == "text").strip()

    with db.tx() as conn:
        conn.execute("INSERT INTO coach_chat (created_at, role, text) VALUES (?,?,?)",
                     (util.now_iso(), "user", message))
        conn.execute("INSERT INTO coach_chat (created_at, role, text) VALUES (?,?,?)",
                     (util.now_iso(), "assistant", reply))
    return {"reply": reply, "model": _model()}


def chat_history() -> list[dict]:
    return db.rows("SELECT id, created_at, role, text FROM coach_chat ORDER BY id")


def chat_clear() -> None:
    with db.tx() as conn:
        conn.execute("DELETE FROM coach_chat")


def kid_note_for_latest_game() -> str | None:
    """The '## For Nirvaan' section of the most recent game commentary, if any."""
    g = db.row("SELECT id FROM games ORDER BY played_at DESC LIMIT 1")
    if not g:
        return None
    note = _note_get("game_commentary", g["id"])
    if not note:
        return None
    content = note["content"]
    marker = "## For Nirvaan"
    if marker in content:
        return content.split(marker, 1)[1].strip()
    return None
