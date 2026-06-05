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
MAX_SAME_DIRECTION_POSITIONS = 6   # correlation cap: don't put the whole book on one
                                   # directional bet (e.g. 7 tech shorts on a selloff)
CONDOR_WING_WIDTH      = 5.0       # $ width of condor wings, for max-loss sizing
TICKER_COOLDOWN_MIN    = 10
VIX_CONDOR_CEILING     = 25.0

# ---- active stock management: trail the bracket stop to lock in gains ---------
# Once a stock trade is up this much, start trailing its stop behind price (only
# ever tightens — never loosens — so the bracket stays the floor on protection).
STOP_TRAIL_ACTIVATE_PCT = 0.008   # begin trailing once ~0.8% in profit
STOP_TRAIL_DISTANCE_PCT = 0.006   # keep the stop ~0.6% behind the current price
STOP_TRAIL_MIN_STEP_PCT = 0.002   # only move the stop if it tightens ≥0.2% (anti-churn)

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

# ---- growth sleeve (long-horizon compounding book, funded by prior-day gains) ---
# A SEPARATE capital pool from the intraday engine. Each new trading day it deploys
# yesterday's profit (plus a small floor on flat days) into a screened basket of
# long-term growth names, held overnight/for weeks with active management (a wide
# chandelier trailing stop + trend-break exit + weekly rotation). These holdings are
# SHIELDED from the intraday machinery (EOD flatten, tight trailing stops, the daily
# loss-halt flatten) and the intraday model is told not to touch them.
GROWTH_SLEEVE_ENABLED   = True
GROWTH_STATE_FILE       = HOME / "autotrade_growth_screen.json"  # daily screen cache
GROWTH_DAILY_FLOOR      = 100.0     # deploy at least this much even on a flat/red day
GROWTH_MAX_SLEEVE_CAPITAL = 25_000.0  # cap total long-term exposure
GROWTH_MAX_PER_NAME     = 6_000.0   # cap a single growth holding
GROWTH_TOP_N            = 5         # hold up to this many names (equal-weight target)
GROWTH_DEPLOY_HOUR      = 9         # deploy at/after 9:40 ET (let the open settle)
GROWTH_DEPLOY_MIN       = 40
GROWTH_TRAIL_PCT        = 0.12      # chandelier stop: exit 12% below the high-water mark
GROWTH_TREND_MA         = 50        # trend-break exit: sell if close falls below the 50-DMA
GROWTH_ROTATE_WEEKLY    = True      # once a week, swap the weakest holding for the top screen leader
GROWTH_ROTATE_MARGIN    = 0.05      # only rotate if the leader's score beats the laggard's by this
GROWTH_LEV_PENALTY      = 0.85      # de-rate leveraged-ETF scores so they win only when clearly stronger

# Long-term growth universe: secular large-cap growers + broad index ETFs, plus a
# curated set of BROAD-INDEX leveraged ETFs (NOT narrow single-stock leverage). The
# leveraged names are eligible ONLY on a confirmed uptrend (price>200DMA & +6mo).
GROWTH_UNIVERSE = [
    "NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "AVGO", "AMD", "TSM", "ASML",
    "LLY", "COST", "V", "MA", "NFLX", "CRM", "NOW", "ORCL", "PLTR", "INTU",
    "AMAT", "LRCX", "ANET", "VRT", "CRWD", "PANW", "ADBE", "UBER", "MELI", "AXON",
    "SPY", "QQQ", "VOO", "VTI",
]
GROWTH_LEVERAGED = ["TQQQ", "QLD", "UPRO", "SSO", "SOXL", "SPXL"]  # broad-index leverage only

BLACKLIST: list[str] = []

ECON_BLACKOUT_DATES: list[str] = [
    # "2026-06-17",  # example: FOMC decision day
]
