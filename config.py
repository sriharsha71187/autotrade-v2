"""
config.py — loads secrets and defines all file paths + guardrails.

Secrets live in ~/.autotrade.env (chmod 600), NEVER in source.
Expected fields in ~/.autotrade.env (one KEY=VALUE per line):
    ALPACA_API_KEY=...
    ALPACA_SECRET_KEY=...
    ANTHROPIC_API_KEY=...
    CLAUDE_MODEL=claude-sonnet-4-6
    TELEGRAM_TOKEN=...
    TELEGRAM_CHAT_ID=...
"""

import os
from pathlib import Path

HOME = Path.home()
ENV_FILE = HOME / ".autotrade.env"

# ---- file locations (everything in ~/) -------------------------------------
STATE_FILE      = HOME / "autotrade_state.json"
SNAPSHOT_DIR    = HOME / "autotrade_snapshots"      # per-cycle decision + result records
OUTCOMES_DIR    = HOME / "autotrade_outcomes"       # daily realized-P&L / fills capture
LEARNINGS_FILE  = HOME / "autotrade_learnings.json"
COMMAND_FILE    = HOME / "autotrade_command.txt"
LOG_FILE        = HOME / "autotrade.log"
LOCK_FILE       = HOME / "autotrade.lock"           # overlap guard (flock); see main()
ASSETS_CACHE    = HOME / "autotrade_assets.json"   # daily cache of tradable/shortable flags


def _load_env() -> dict:
    """Read KEY=VALUE pairs from ~/.autotrade.env. Missing file -> empty dict."""
    data = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            data[key.strip()] = val.strip().strip('"').strip("'")
    for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ANTHROPIC_API_KEY",
              "CLAUDE_MODEL", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
        if os.environ.get(k):
            data[k] = os.environ[k]
    return data


_env = _load_env()

ALPACA_API_KEY    = _env.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = _env.get("ALPACA_SECRET_KEY", "")
ANTHROPIC_API_KEY = _env.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL      = _env.get("CLAUDE_MODEL", "claude-sonnet-4-6")
TELEGRAM_TOKEN    = _env.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = _env.get("TELEGRAM_CHAT_ID", "")

# ---- hard guardrails (enforced in code, not left to the model) -------------
PAPER            = True
ACCOUNT_BASELINE = 100_000.0
DAILY_LOSS_HALT  = -300.0

# ---- daily profit target (a CEILING that banks gains, never a quota) -------
# Below TARGET: trade normally. TARGET..STRETCH: high-conviction entries only.
# At/above STRETCH: stop opening new risk for the day (still manage/exit).
DAILY_PROFIT_TARGET  = 200.0
DAILY_PROFIT_STRETCH = 400.0
PER_TRADE_NOTIONAL_CAP = 5_000.0   # cap on a single stock entry (qty * price)
PER_OPTION_NOTIONAL_CAP = 2_000.0  # cap on a single option entry (qty * premium * 100)
OPTION_MAX_SPREAD_PCT  = 0.15      # skip options whose bid-ask spread exceeds this (illiquid)
MAX_DEPLOYED_CAPITAL   = 40_000.0  # cap on total exposure across all open trades
MAX_SAME_DIRECTION_POSITIONS = 4   # correlation cap: don't put the whole book on one
                                   # directional bet (e.g. 7 tech shorts on a selloff)
CONDOR_WING_WIDTH      = 5.0       # $ width of condor wings, for max-loss sizing
TICKER_COOLDOWN_MIN    = 10
VIX_CONDOR_CEILING     = 25.0

# ---- cadence (bot-side; scheduler just ticks every minute) ------------------
# Faster during the opening hour (densest opportunity + the 10:00-10:30 condor
# window), normal the rest of the session. A flock prevents overlapping cycles.
CYCLE_FAST_INTERVAL_MIN   = 2      # 9:30-10:30 ET
CYCLE_NORMAL_INTERVAL_MIN = 5      # rest of the regular session

# ---- single-leg option exit management (code-enforced, like condors) --------
OPTION_STOP_PCT        = -0.50     # close a long option down 50% from entry
OPTION_TARGET_PCT      = 1.00      # take profit up 100% from entry
OPTION_EOD_CLOSE_HOUR  = 15        # force-close long options + spreads at/after this ET time
OPTION_EOD_CLOSE_MIN   = 45        # ...15:45 ET (and any 0DTE before expiry)
STOCK_EOD_CLOSE_MIN    = 50        # flatten stocks at 15:50 ET (DAY brackets die at the close,
                                   # so don't leave a stock unprotected overnight)
MULTILEG_FILL_TIMEOUT_MIN = 15     # cancel a multi-leg entry that hasn't filled in N min

# ---- dynamic universe (screener) -------------------------------------------
# Each cycle the universe = core indices + a base watchlist + live screener
# (most-actives ∪ top movers), filtered to tradable names priced over MIN_PRICE.
SCREENER_ENABLED   = True
SCREENER_TOP_ACTIVES = 40    # most-active equities by volume to pull
SCREENER_TOP_MOVERS  = 20    # top gainers + top losers to pull (each side)
MIN_PRICE          = 5.0     # drop sub-$5 names (penny/warrant junk)
UNIVERSE_MAX       = 60      # cap symbols sent to the per-cycle signal scan

# ---- quality floor (keep halted runners + leveraged ETFs out) --------------
# A +500% halted low-float runner (e.g. STI on 2026-06-04) is not a tradeable
# momentum setup. Drop anything whose absolute day move exceeds this — real
# gappers rarely clear it, circuit-breaker pumps always do.
MOMENTUM_MAX_DAY_PCT = 30.0

# ---- anti-chase: don't buy the top of an already-extended move ---------------
# A momentum entry should be a pullback toward VWAP, not a purchase at the high.
ANTI_CHASE_MAX_VWAP_EXT = 0.04   # block long if >4% above VWAP (short if >4% below)
ANTI_CHASE_MIN_OFF_EXTREME = 0.01  # block if within 1% of the day's high (long) / low (short)
RSI_OVERBOUGHT = 80.0            # block longs when intraday RSI above this
RSI_OVERSOLD   = 20.0            # block shorts when intraday RSI below this
# Leveraged / inverse ETFs decay and whipsaw; exclude them from the scan so the
# bot doesn't chase an inverse-ETF spike that's just the underlying selling off.
LEVERAGED_ETF_EXCLUDE = {
    "SOXL", "SOXS", "TQQQ", "SQQQ", "SPXL", "SPXU", "UPRO", "SPXS", "TNA", "TZA",
    "UVXY", "VIXY", "SVXY", "SVIX", "UVIX", "LABU", "LABD", "FAS", "FAZ",
    "YINN", "YANG", "NUGT", "DUST", "JNUG", "JDST", "GUSH", "DRIP", "ERX", "ERY",
    "BOIL", "KOLD", "UCO", "SCO", "TMF", "TMV", "WEBL", "WEBS", "BULZ",
    "FNGU", "FNGD", "NVDL", "NVDU", "NVDD", "TSLL", "TSLQ", "TSLS", "CONL", "MSTX",
    "MSTU", "MSTZ", "AGQ", "ZSL", "BITX", "ETHU",
}

# ---- trading universe ------------------------------------------------------
CORE_UNIVERSE = ["SPY", "QQQ", "IWM", "DIA"]
MOMENTUM_UNIVERSE = [
    "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "TSLA", "AMD", "AVGO",
    "PANW", "DDOG", "SNOW", "CRWD", "NET", "SMCI", "MU", "ARM", "TSM",
    "COIN", "MSTR", "PLTR", "UBER", "SHOP", "ABNB", "MRVL", "ASML", "LRCX",
    "KLAC", "ANET", "DELL", "ORCL", "ADBE", "NOW", "INTC",
]

BLACKLIST: list[str] = []

ECON_BLACKOUT_DATES: list[str] = [
    # "2026-06-17",  # example: FOMC decision day
]
