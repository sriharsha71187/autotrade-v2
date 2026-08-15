"""The GM roadmap: milestone ladder + a study plan for each stage.

Framing for parents: reaching GM is a ~10-year project that a tiny fraction
of players complete; the ladder below is the *route*, and every rung is a
worthy destination on its own. Reference ages come from strong-but-realistic
trajectories of US scholastic players who reached master level or beyond.
The plan the app shows is always for the CURRENT stage, adjusted by the
weakness data — climb the rung in front of you.
"""
from __future__ import annotations

from .. import db

# Each stage: rating band (best available scale noted), what actually matters
# to learn there, weekly plan, and how you know it's time to move on.
STAGES = [
    {
        "key": "spark",
        "name": "The Spark",
        "band": "NWSRS < 600 (new scholastic player)",
        "goal": "Love the game, see the whole board, stop hanging pieces.",
        "skills": [
            "Piece safety: is my piece attacked? Is it defended?",
            "Checks, captures, threats — scan them EVERY move",
            "Basic checkmates: two rooks (ladder), King+Queen",
            "Opening principles only: center pawn, knights/bishops out, castle",
            "Complete games without leaving pieces en prise",
        ],
        "weekly": [
            "5-6 puzzles a day from his own games (the app's daily set)",
            "3-4 rapid games (10+5 or 15+10) — NOT bullet/blitz yet",
            "Review every loss for 5 minutes: find the one moment it went wrong",
            "1 fun session: chess960, hand-and-brain with a parent, or a chess video",
        ],
        "graduate_when": "NWSRS/online rapid ~600 and hanging-piece blunders are rare",
    },
    {
        "key": "builder",
        "name": "The Builder",
        "band": "NWSRS 600-900 · online rapid 600-1000",
        "goal": "Tactics become automatic; endgames stop being scary.",
        "skills": [
            "Core tactics: forks, pins, skewers, discovered attacks, one-movers on sight",
            "K+P vs K endgames: opposition, the square rule, promotion technique",
            "A simple repertoire: one White opening system, one answer to e4, one to d4",
            "Don't trade when behind; do trade when ahead",
            "Longer thinks at critical moments (the app tracks fast-move blunders)",
        ],
        "weekly": [
            "8-10 puzzles a day; repeat missed ones until instant (Woodpecker style)",
            "4-5 rapid games with the SAME openings every time",
            "1 endgame session: drill K+P positions against the engine",
            "1 OTB event a month: NWSRS quads or scholastic swiss",
        ],
        "graduate_when": "NWSRS ~900, tactics accuracy >80% on the daily set",
    },
    {
        "key": "competitor",
        "name": "The Competitor",
        "band": "NWSRS 900-1200 · USCF 700-1000",
        "goal": "Real tournament habits: preparation, focus, resilience.",
        "skills": [
            "Tactical combinations (2-3 movers), removing the defender, deflection",
            "Rook endgames: Lucena and Philidor positions",
            "Middlegame plans: weak squares, open files, when to attack the king",
            "Time management for G/60+ OTB games",
            "Rival prep: know your regular opponents' openings (the app scouts them)",
        ],
        "weekly": [
            "10 puzzles a day, mixed themes, plus the review queue",
            "3 rapid + 1 longer game (30+0 or slower) with full self-review",
            "Annotate one of his own games a week in the journal — in his own words",
            "Monthly rated OTB tournament; state scholastic events every year",
        ],
        "graduate_when": "USCF ~1000 and top-10 finishes in state scholastic sections",
    },
    {
        "key": "student",
        "name": "The Student of the Game",
        "band": "USCF 1000-1400",
        "goal": "Positional understanding joins the tactics.",
        "skills": [
            "Pawn structure basics: isolated, doubled, passed pawns, pawn breaks",
            "Piece quality: good vs bad bishops, knight outposts",
            "Opening understanding — the IDEAS behind his lines, not memorized moves",
            "Minor-piece and queen endgames; converting +2 without drama",
            "Calculation discipline: candidate moves, then check the reply",
        ],
        "weekly": [
            "30-45 min tactics daily; start simple calculation training",
            "2 long games (G/30+ minimum) + review with engine AFTER guessing first",
            "Study one classic game a week (Morphy, Capablanca first)",
            "Consider a real coach now if not already — this is the stage it pays most",
        ],
        "graduate_when": "USCF ~1400 sustained across 4+ tournaments",
    },
    {
        "key": "warrior",
        "name": "The Tournament Warrior",
        "band": "USCF 1400-1800",
        "goal": "Compete seriously: nationals, norms culture, deep repertoire.",
        "skills": [
            "A real repertoire with files in the app: prep vs main tries",
            "Advanced endgames: fortress ideas, R+P technique, opposite bishops",
            "Attack and defense: king safety judgment, sacrifice evaluation",
            "Psychology: playing up, must-win games, recovering from losses",
        ],
        "weekly": [
            "1 hour daily split: tactics / endgames / openings 30-20-10",
            "Weekly long OTB or online classical game, seriously reviewed",
            "National scholastic events (Elementary/Junior High Nationals)",
            "Coach-led study plan; the app's data goes to the coach",
        ],
        "graduate_when": "USCF ~1800; competitive in open sections",
    },
    {
        "key": "candidate",
        "name": "The Candidate",
        "band": "USCF 1800-2200 → National Master",
        "goal": "Master-level pattern bank and professional work habits.",
        "skills": [
            "Deep calculation training, complex middlegame studies",
            "Full opening repertoire with novelties and model games",
            "Endgame precision (Dvoretsky-level material)",
            "FIDE-rated events for an international rating",
        ],
        "weekly": [
            "2+ hours daily structured study with a strong coach",
            "FIDE-rated norm-eligible events; travel tournaments",
            "Database work on his own games: recurring error taxonomy",
        ],
        "graduate_when": "USCF 2200 = National Master title (life title!)",
    },
    {
        "key": "titled",
        "name": "The Title Chase",
        "band": "FIDE 2300 CM/FM → 2400 IM → 2500 GM + norms",
        "goal": "CM → FM → IM norms → GM norms. The long, worthy road.",
        "skills": [
            "International norm tournaments (3 GM norms + FIDE 2500 for GM)",
            "Professional preparation: engine + database workflow, seconds",
            "Physical and mental conditioning — five-hour games are sport",
        ],
        "weekly": [
            "Full-time-adjacent training schedule around school",
            "International round-robins and strong opens",
        ],
        "graduate_when": "Grandmaster. Then: stay hungry.",
    },
]

# Ambitious-but-seen-in-the-wild reference points for a GM-track scholastic
# player, shown as a dotted line on the trajectory chart, never as a demand.
REFERENCE_TRAJECTORY = [
    {"age": 7, "uscf": 900}, {"age": 8, "uscf": 1200}, {"age": 9, "uscf": 1500},
    {"age": 10, "uscf": 1800}, {"age": 11, "uscf": 2000}, {"age": 12, "uscf": 2200},
    {"age": 14, "fide": 2400}, {"age": 16, "fide": 2500},
]


def _best_current_ratings() -> dict:
    out = {}
    for source in ("nwsrs", "uscf_regular", "lichess_rapid", "chesscom_rapid",
                   "lichess_blitz", "chesscom_blitz"):
        r = db.row(
            "SELECT rating, date FROM ratings_history WHERE source=? ORDER BY date DESC LIMIT 1",
            (source,))
        if r:
            out[source] = r
    return out


def current_stage(ratings: dict | None = None) -> dict:
    """Pick the stage from the best available rating signal."""
    ratings = ratings if ratings is not None else _best_current_ratings()
    uscf = (ratings.get("uscf_regular") or {}).get("rating")
    nwsrs = (ratings.get("nwsrs") or {}).get("rating")
    online = max(
        (ratings.get(s, {}).get("rating") or 0)
        for s in ("lichess_rapid", "chesscom_rapid")
    ) if ratings else 0

    if uscf:
        if uscf < 700: idx = 1 if nwsrs and nwsrs >= 600 else 0
        elif uscf < 1000: idx = 2
        elif uscf < 1400: idx = 3
        elif uscf < 1800: idx = 4
        elif uscf < 2200: idx = 5
        else: idx = 6
    elif nwsrs:
        if nwsrs < 600: idx = 0
        elif nwsrs < 900: idx = 1
        else: idx = 2
    elif online:
        if online < 600: idx = 0
        elif online < 1000: idx = 1
        else: idx = 2
    else:
        idx = 0
    return {"index": idx, "stage": STAGES[idx]}


def full() -> dict:
    ratings = _best_current_ratings()
    cur = current_stage(ratings)
    next_stage = STAGES[cur["index"] + 1] if cur["index"] + 1 < len(STAGES) else None
    return {
        "ratings": ratings,
        "current": cur["stage"],
        "current_index": cur["index"],
        "next": next_stage,
        "stages": STAGES,
        "reference_trajectory": REFERENCE_TRAJECTORY,
        "honest_note": (
            "Becoming a GM takes roughly a decade of sustained, joyful work — "
            "and fewer than 2,000 people alive have done it. The plan here climbs "
            "one rung at a time; every rung is a real achievement. The best "
            "predictor at Nirvaan's stage isn't talent, it's games played, "
            "mistakes reviewed, and fun protected."
        ),
    }
