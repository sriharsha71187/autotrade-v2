"""Badges for kid mode — earned once, kept forever."""
from __future__ import annotations

from .. import db, util

BADGES = [
    ("first_win", "First Victory", "Win your first synced game", "🏆"),
    ("ten_wins", "Ten Wins", "Win 10 games", "🎖️"),
    ("fifty_wins", "Half-Century", "Win 50 games", "🏅"),
    ("first_puzzle", "Puzzle Starter", "Solve your first puzzle", "🧩"),
    ("puzzle_50", "Puzzle Hunter", "Solve 50 puzzles", "🔍"),
    ("puzzle_200", "Puzzle Master", "Solve 200 puzzles", "🧠"),
    ("streak_3", "On Fire", "3-day puzzle streak", "🔥"),
    ("streak_7", "Unstoppable", "7-day puzzle streak", "⚡"),
    ("streak_30", "Iron Will", "30-day puzzle streak", "💎"),
    ("clean_game", "Clean Sheet", "Play a game with zero mistakes or blunders", "✨"),
    ("giant_slayer", "Giant Slayer", "Beat someone rated 150+ above you", "⚔️"),
    ("mate_delivered", "Checkmate!", "Win by checkmate", "👑"),
    ("fork_master", "Fork Master", "Play 10 winning forks", "🍴"),
    ("endgame_win", "Endgame Grinder", "Win 10 games that reached an endgame", "🐢"),
    ("hundred_games", "Century Club", "Play 100 games", "💯"),
]


def recompute() -> list[str]:
    """Evaluate all badge conditions; award anything newly earned."""
    earned = {b["key"] for b in db.rows("SELECT key FROM badges")}
    newly = []

    def award(key):
        if key not in earned:
            with db.tx() as conn:
                conn.execute("INSERT OR IGNORE INTO badges(key, earned_at) VALUES (?,?)",
                             (key, util.now_iso()))
            earned.add(key)
            newly.append(key)

    wins = db.scalar("SELECT COUNT(*) FROM games WHERE result='win'") or 0
    games = db.scalar("SELECT COUNT(*) FROM games") or 0
    if wins >= 1: award("first_win")
    if wins >= 10: award("ten_wins")
    if wins >= 50: award("fifty_wins")
    if games >= 100: award("hundred_games")

    solved = db.scalar(
        "SELECT COUNT(DISTINCT puzzle_id) FROM puzzle_attempts WHERE correct=1") or 0
    if solved >= 1: award("first_puzzle")
    if solved >= 50: award("puzzle_50")
    if solved >= 200: award("puzzle_200")

    from ..puzzles.scheduler import stats
    streak = stats()["streak_days"]
    if streak >= 3: award("streak_3")
    if streak >= 7: award("streak_7")
    if streak >= 30: award("streak_30")

    clean = db.scalar(
        """SELECT COUNT(*) FROM games g WHERE g.analyzed_at IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM moves m WHERE m.game_id=g.id
               AND m.mover='player' AND m.classification IN ('mistake','blunder'))
           AND (SELECT COUNT(*) FROM moves m2 WHERE m2.game_id=g.id) >= 20""") or 0
    if clean >= 1: award("clean_game")

    giant = db.scalar(
        """SELECT COUNT(*) FROM games WHERE result='win'
           AND opponent_rating - player_rating >= 150""") or 0
    if giant >= 1: award("giant_slayer")

    mates = db.scalar(
        "SELECT COUNT(*) FROM games WHERE result='win' AND termination IN ('mate','checkmated','won')") or 0
    mates2 = db.scalar(
        """SELECT COUNT(DISTINCT game_id) FROM moves
           WHERE mover='player' AND motifs LIKE '%delivered_mate%'""") or 0
    if mates >= 1 or mates2 >= 1: award("mate_delivered")

    forks = db.scalar(
        "SELECT COUNT(*) FROM moves WHERE mover='player' AND motifs LIKE '%played_fork%'") or 0
    if forks >= 10: award("fork_master")

    endgame_wins = db.scalar(
        """SELECT COUNT(DISTINCT g.id) FROM games g JOIN moves m ON m.game_id=g.id
           WHERE g.result='win' AND m.phase='endgame'""") or 0
    if endgame_wins >= 10: award("endgame_win")

    return newly


def all_badges() -> list[dict]:
    earned = {b["key"]: b["earned_at"] for b in db.rows("SELECT * FROM badges")}
    return [
        {"key": k, "name": n, "how": how, "emoji": e,
         "earned_at": earned.get(k)}
        for k, n, how, e in BADGES
    ]
