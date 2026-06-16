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
              "CLAUDE_MODEL", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "FMP_API_KEY"):
        if os.environ.get(k):
            data[k] = os.environ[k]
    return data


_env = _load_env()

ALPACA_API_KEY    = _env.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = _env.get("ALPACA_SECRET_KEY", "")
ANTHROPIC_API_KEY = _env.get("ANTHROPIC_API_KEY", "")
# PREFERRED model. fable-5 is the most capable here when available; the call path
# (autotrade._create_message) auto-falls back to CLAUDE_FALLBACK_MODEL when the preferred
# model is unavailable (fable-5 + mythos-5 were govt-suspended for ALL users 2026-06-12 —
# no timeline). Keeping fable preferred means the bot returns to it AUTOMATICALLY the
# instant Anthropic restores access, with zero intervention; until then every qualifying
# call gets one fast (unbilled) 404 then runs on the fallback.
CLAUDE_MODEL      = _env.get("CLAUDE_MODEL", "claude-fable-5")
CLAUDE_FALLBACK_MODEL = _env.get("CLAUDE_FALLBACK_MODEL", "claude-opus-4-8")
# fable-5 is an extended-THINKING model: it spends output tokens reasoning BEFORE it
# writes the JSON answer. With a big trading context that thinking can run ~800-1500
# tokens, so the budget must comfortably fit thinking + the ~600-token JSON or the
# answer is truncated (stop_reason=max_tokens, empty text -> "no JSON value found").
CLAUDE_MAX_TOKENS = int(_env.get("CLAUDE_MAX_TOKENS", "8000"))
TELEGRAM_TOKEN    = _env.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = _env.get("TELEGRAM_CHAT_ID", "")
# Financial Modeling Prep — free economic-calendar feed for the event router. Optional:
# with no key the scheduled/BRACE half is a no-op (the breaking-event RIDE half still
# works off the news feed). Add FMP_API_KEY=... to ~/.autotrade.env to enable it.
FMP_API_KEY       = _env.get("FMP_API_KEY", "")

# ---- hard guardrails (enforced in code, not left to the model) -------------
PAPER            = True
ACCOUNT_BASELINE = 100_000.0
DAILY_LOSS_HALT  = -300.0

# ---- account-level drawdown floor + red-day de-risking (AUDIT_ROADMAP #13) --
# The per-DAY loss halt (DAILY_LOSS_HALT) resets every morning, so it does nothing to
# stop a slow multi-day bleed — the stated −$1,500 ruin guardrail was never a CUMULATIVE
# floor. ACCOUNT_DRAWDOWN_HALT is a multi-day circuit breaker measured off the trailing
# equity HIGH-WATER (not the static baseline) so a recovery to new highs re-arms it: when
# equity falls this far below its rolling peak, NEW risk is blocked for the day (manage/
# exit still allowed), exactly like the daily halt. RED_DAY_DERISK halves risk-sizing
# after 2 consecutive red days (consecutive_red_days from the EOD ledger result).
ACCOUNT_DRAWDOWN_HALT = 3000.0     # halt NEW entries when equity <= high_water − this
RED_DAY_DERISK        = True       # after N consecutive red days, shrink risk-sizing
RED_DAY_RISK_MULT     = 0.5        # ...by this multiplier (applied to the #6 risk budgets)
RED_DAY_DERISK_AFTER  = 2          # consecutive red days that trigger the de-risk

# ---- hard per-position max-loss kill (AUDIT_ROADMAP #14) --------------------
# Independent of the daily/cumulative halt: force-close ANY single position whose
# unrealized loss blows past MAX_POSITION_LOSS_MULT × its INITIAL defined risk. The daily
# halt failed to cap the −$2,382 SMCI leg (the wash-reject loop blocked the exit); this is
# the backstop that kills a position the −50% option stop misses (a gapped long, or a
# debit spread whose loss ran well past its debit). Set well above the −50% stop so the two
# don't double-fire on the normal long-option case.
MAX_POSITION_LOSS_MULT = 2.0       # force-close at loss > this × the position's initial risk

# ---- daily profit target (a CEILING that banks gains, never a quota) -------
# Below TARGET: trade normally. TARGET..STRETCH: high-conviction entries only.
# At/above STRETCH: stop opening new risk for the day (still manage/exit).
DAILY_PROFIT_TARGET  = 200.0
DAILY_PROFIT_STRETCH = 400.0
PER_TRADE_NOTIONAL_CAP = 5_000.0   # cap on a single stock entry (qty * price)
PER_OPTION_NOTIONAL_CAP = 600.0    # HARD ceiling on ONE option structure's max-loss.
                                   # At the live −$1,500 daily floor this is ~0.4x, so a
                                   # single structure can't blow most of the day's loss
                                   # budget. (The conviction-ITM book is exempt — it uses
                                   #  its own CONVICTION_ITM_NOTIONAL_CAP.)
OPTION_RISK_TARGET     = 900.0     # TARGET max-loss to SIZE each defined-risk options
                                   # trade toward (wider wings / more contracts) — stops
                                   # the model trading teaspoon-sized $50-risk condors
# Stock sizing is a RISK budget, not a notional cap (AUDIT_ROADMAP #6). The model
# emits a qty + stop; we re-size in code so $-at-risk (qty * |entry-stop|) is the
# constant — not the dollars deployed. A wide-stop trade gets fewer shares, a tight-
# stop trade more, so both risk ~the same. PER_TRADE_NOTIONAL_CAP then CLAMPS it.
STOCK_RISK_PER_TRADE   = 250.0     # base $ risk per stock trade (medium conviction)
STOCK_RISK_CONV        = {"low": 0.6, "medium": 1.0, "high": 1.6}  # → ~$150/$250/$400
OPTION_MAX_SPREAD_PCT  = 0.15      # skip options whose bid-ask spread exceeds this (illiquid)
MAX_DEPLOYED_CAPITAL   = 40_000.0  # cap on total exposure across all open trades
MAX_SAME_DIRECTION_POSITIONS = 10  # correlation cap: don't put the whole book on one
                                   # directional bet (e.g. 7 tech shorts on a selloff)
# Sector/correlation heat cap (AUDIT_ROADMAP #10/#18). MAX_SAME_DIRECTION_POSITIONS only
# buckets long-vs-short, so 8 correlated semis longs read as 8 independent bets when they
# are really ONE concentrated AI/semis bet. These two add a finer correlation control:
#   - MAX_SECTOR_DIRECTIONAL: cap same-direction open positions WITHIN one sector (see
#     SECTOR_MAP below); a 5th semis-long is blocked even though the gross bull count is fine.
#   - PORTFOLIO_HEAT_MULT: total open DEFINED-RISK across all open positions (option/spread
#     max-loss + stock stop-distance risk), incl. the new trade, must stay <= this multiple
#     of abs(DAILY_LOSS_HALT). Bounds total intra-cycle open risk to a few days' loss budget.
MAX_SECTOR_DIRECTIONAL = 4         # max same-direction open positions within ONE sector
PORTFOLIO_HEAT_MULT    = 3.0       # total open defined-risk <= this × abs(DAILY_LOSS_HALT)
CONDOR_WING_WIDTH      = 5.0       # $ width of condor wings, for max-loss sizing
# The 14:00 ET options cutoff is for 0DTE / same-day index structures (condors,
# buy_option) which pin into the close. MOMENTUM DEBIT spreads are multi-day and
# don't have that risk, so they get a later entry cutoff — lets the bot catch an
# afternoon single-name breakout instead of going dark at 14:00.
MOMENTUM_OPTION_MIN_DTE = 5        # DIRECTIONAL momentum options must be at least this
                                   # many days to expiry. The default chain selector used
                                   # to grab the NEAREST expiry, which on a Friday is a pure
                                   # 0DTE and most days is 0-4 DTE — all theta, force-closed
                                   # same day before a multi-day thesis can play out (the
                                   # 0DTE-Friday RKLB loss). A name with no expiry >= this is
                                   # SKIPPED (never silently fall back to 0DTE). The 0DTE INDEX
                                   # CONDOR path is unaffected (it asks for today's expiry).
MOMENTUM_OPTION_CUTOFF_HOUR = 15
MOMENTUM_OPTION_CUTOFF_MIN  = 0    # momentum debit spreads enterable 10:00–15:00 ET
                                   # (spreads are force-closed 15:45, so this leaves
                                   #  ~45min of working time — 15:30 would be useless)
# Debit-spread long-leg selection (AUDIT_ROADMAP #19). A strict-ATM long leg carries
# max gamma/theta — it decays fastest and needs a big move to pay. For a MOMENTUM debit
# spread, target a SLIGHTLY-ITM long leg (~0.60-0.70 delta, more intrinsic / less theta)
# while the short leg stays OTM to define the width. DEBIT_LONG_LEG_ITM_PCT is the % the
# long strike sits in-the-money (proxy for ~0.65 delta): a call long ~spot*(1-pct), a put
# long ~spot*(1+pct). Surfaced to the model as a prior; the conviction-ITM book (already
# deep-ITM) and the 0DTE index condor path are unaffected.
DEBIT_LONG_LEG_ITM_PCT = 0.04
# Option-chain fetch breadth (AUDIT_ROADMAP #22). On a CALM day 3 underlyings is plenty;
# on a busy day (many names moving >=3%) a hard cap of 3 starves fresh strong trends —
# held positions and a couple of movers fill the slots and a new entry never gets a chain.
# Scale the cap up toward MAX_OPTION_UNDERLYINGS_BUSY by the count of >=3% movers, and
# fetch the top WITH-TAPE trend candidates BEFORE held-position underlyings so a new entry
# is never crowded out by names we're only re-managing.
MAX_OPTION_UNDERLYINGS_BUSY = 6    # ceiling on chains fetched on a busy (many-mover) day
MAX_OPTION_UNDERLYINGS_BASE = 3    # baseline on a calm day (the old hard cap)
# Deterministic directional-vehicle router (AUDIT_ROADMAP #21). Four bullish/bearish
# vehicles (stock, long option, debit spread, conviction-ITM) overlap with no rule for
# WHICH to use — so all four can fire on the same name and read as independent bets. The
# router picks ONE vehicle per directional signal by setup and surfaces it as a strong
# prior: high ATM IV-rank → stock (don't buy rich premium); low-IV clean multi-day trend
# → conviction-ITM (if enabled); low-IV intraday momentum → debit spread; default → stock.
VEHICLE_ROUTER_ENABLED   = True
VEHICLE_ROUTER_HIGH_IVR  = 60.0    # ATM IV-rank above this → prefer STOCK (premium is rich)

# ---- overnight momentum hold (catalyst-backed conviction; see overnight_conviction.py)
# A momentum DEBIT spread is normally force-closed at 15:45. It may instead RIDE
# overnight ONLY if the model flags a hard catalyst (overnight_hold) AND the
# deterministic data gate passes. Judged late in the day so close-strength is real.
OVERNIGHT_MOMENTUM_ENABLED = True
OVERNIGHT_DECISION_HOUR = 14
OVERNIGHT_DECISION_MIN  = 30      # only judge overnight holds from 14:30 ET on
OVERNIGHT_RVOL_MIN      = 1.5     # today's volume vs 20-day avg (real participation)
OVERNIGHT_OFF_HOD_MAX   = 1.5     # % from HOD/LOD to count as "closing strong"
OVERNIGHT_BREADTH_MIN   = 3       # same-direction strong movers (a theme, not a one-off)
OVERNIGHT_RSI_MAX       = 85.0    # above this (bull) = blow-off; mirror for bear
OVERNIGHT_MIN_DATA_CONFIRMS = 3   # need >=3 of the 4 data checks (RVOL/close/breadth/RSI)
# Index-only premium selling. Evidence (Carr-Wu, Driessen-Maenhout-Vilkov): the
# variance/vol risk premium a credit spread harvests is reliably negative ONLY at the
# index level — single-name variance premia are ~zero/positive and just bear
# idiosyncratic jump risk. So short-premium structures (credit spreads / condors) are
# restricted to these index ETFs. Single names trade DIRECTIONALLY (debit spreads);
# single-name premium is reserved for the earnings IV-crush book.
CREDIT_SPREAD_INDEX_ONLY  = True
PREMIUM_INDEX_UNDERLYINGS = ["SPY", "QQQ", "IWM", "DIA"]
TICKER_COOLDOWN_MIN    = 10
MULTILEG_COOLDOWN_MIN  = 12        # min gap between spread/condor ENTRIES, so a
                                   # working (unfilled) condor isn't re-submitted every cycle
MAX_SPREADS_PER_NAME_PER_DAY = 3   # cap re-entries on ONE underlying/day — stops the
                                   # churn (6/9: 7 MRVL spreads, mostly unfilled) that
                                   # just bleeds spread/slippage on the same thesis
STOPPED_COOLDOWN_MIN = 75          # after a name's trade is stopped/cut at a loss, wait this
                                   # long before re-entering it (was a full-day lock)
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
CYCLE_FAST_INTERVAL_MIN   = 2      # 9:30-10:30 ET (opening hour — densest opportunity)
CYCLE_ACTIVE_INTERVAL_MIN = 3      # conditional: open position / live event / elevated vol
CYCLE_NORMAL_INTERVAL_MIN = 5      # quiet stretches (flat, nothing open) — save the call

# ---- single-leg option exit management (code-enforced, like condors) --------
OPTION_STOP_PCT        = -0.50     # hard stop: close a long option / debit spread down 50%
OPTION_TARGET_PCT      = 1.00      # (legacy fixed target — superseded by the trailing lock)
# Trailing profit-lock for long options + DEBIT spreads: once a winner, let it run and
# bank gains a give-back below the peak (replaces the old hard +100% cap so runners
# aren't force-sold at 2x). Credit spreads/condors are capped at their credit -> no trail.
OPTION_TRAIL_ACTIVATE  = 0.25      # arm the chandelier trail once profit reaches +25%
OPTION_BREAKEVEN_AT    = 0.15      # once peak >= +15%, the stop floor becomes breakeven —
                                   # a winner that good is never allowed back to red
OPTION_TRAIL_GIVEBACK_BAND = 0.20  # FIXED give-back in profit-POINTS below the peak once
                                   # the trail is armed (chandelier). Replaces the old
                                   # multiplicative hw*(1-giveback), whose band widened with
                                   # the peak and let big winners round-trip too far.
OPTION_TRAIL_GIVEBACK  = 0.25      # (legacy multiplicative give-back — kept for back-compat;
                                   # no longer used by the exit ladder)
# DISCRETIONARY exits (profit-take / trailing / breakeven / thesis / normal stop) use a
# MARKETABLE LIMIT instead of a market order so they don't donate the (up to 15%-wide)
# bid/ask spread on every close (AUDIT_ROADMAP #15): sell-to-close at bid×(1−slip),
# buy-to-close at ask×(1+slip), then FALL BACK to market after a short fill timeout. The
# SAFETY closes (EOD/forced flatten, max-loss kill, naked-short force-close, orphan sweep,
# loss-halt flatten) stay PURE MARKET — certainty of exit over a few cents of slippage.
OPTION_EXIT_SLIP       = 0.02      # marketable-limit slip off bid/ask on discretionary exits
OPTION_EXIT_FILL_WAIT_SEC = 8      # wait this long for the marketable limit, then market-fallback
# IV-rank ceiling for BUYING single-name premium (AUDIT_ROADMAP #11). A long/debit
# single-name option whose ATM IV-rank is above this is buying RICH vol on a capped
# payoff — block it. Premium SELLING (index condors) and unknown iv_rank (None) are exempt.
OPTION_MAX_IV_RANK     = 70.0      # block single-name DEBIT/long entry when ATM iv_rank > this
OPTION_EOD_CLOSE_HOUR  = 15        # force-close long options + spreads at/after this ET time
OPTION_EOD_CLOSE_MIN   = 45        # ...15:45 ET (and any 0DTE before expiry)
STOCK_EOD_CLOSE_MIN    = 50        # flatten stocks at 15:50 ET (DAY brackets die at the close,
                                   # so don't leave a stock unprotected overnight)
MULTILEG_FILL_TIMEOUT_MIN = 15     # cancel a multi-leg entry that hasn't filled in N min
EOD_FLATTEN_RETRIES    = 3         # verify-and-retry the EOD stock close (a partial fill must
                                   # not leave a remnant riding overnight unprotected)

# ---- self-learning: contradiction handling (evidence-weighted + hysteresis) ----
# When a new EOD learning contradicts a standing one, we DON'T let the newcomer win
# by recency. The incumbent flips only when the challenger out-evidences it, a single
# contradicting day merely marks the incumbent 'contested', and a rule that just
# flipped is locked for a cooldown so it can't thrash. Code enforces the mechanical
# invariants (hysteresis, since-preservation, count clamping); the model does the
# semantic work (is this regime-conditional, a real reversal, or noise).
LEARNING_HYSTERESIS_DAYS      = 3   # a rule that just flipped direction/status is locked this long
LEARNING_FLIP_MIN_EDGE        = 2   # challenger needs >= this many more confirmations to retire incumbent
LEARNING_MAX_COUNT_STEP       = 1   # confirmations/refutations may move at most this much per session
# Inviolable human priors a learning may NEVER override. These are advisory-context
# anchors for the EOD model; the REAL protection is that the deterministic guardrails
# (regime gate, loss floor, defined-risk-only) are separate code a learning can't touch.
LEARNING_PROTECTED_PRIORS = [
    "never fade a strong single-name move (ride momentum, don't counter a live catalyst)",
    "defined-risk structures only — every trade has a known max loss",
    "respect the daily loss floor; ruin-prevention guardrails are not optional",
    "re-judge every open position each run on current metrics; exit a broken thesis early",
]

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
# Established-trend carve-out (AUDIT_ROADMAP #7/#20). A confirmed trend — STRONG_BULL/BEAR,
# on the trend side of VWAP, RSI in-band (or unknown), with the tape — is a VALID entry on a
# SHALLOW pullback, not a chase. For such a name we widen the off-extreme tolerance to this
# (a ~2% dip in a real uptrend is the BEST entry, not "at the top"). The vwap_ext / RSI
# ceilings are UNCHANGED, so a vertical first-bar spike (far above VWAP) or a blown-off RSI
# is still blocked — this only stops treating a healthy pullback like an opening spike.
CONVICTION_TREND_MAX_OFF_HOD = 0.02  # allow entry up to ~2% off the high (long) / low (short)
                                     #  ONLY for a structurally-confirmed trend (see above)

# ---- event router (two-sided: macro/news is RISK and OPPORTUNITY) -----------
# A fresh hard catalyst that creates a directional move is exactly what the momentum
# books want — so the same event datum routes to RIDE (trade WITH the move, relaxed
# anti-chase, defined-risk) or BRACE (no new short-premium into a binary you can't
# predict) or FADE_VOL (after the spike, sell the now-rich index IV). See
# docs/EVENT_ROUTER_SPEC.md. Ships OFF; enable + watch like every other book.
EVENT_ROUTER_ENABLED  = False
EVENT_FRESH_MIN       = 90       # a headline older than this is priced in (skip)
EVENT_PRE_MIN         = 30       # BRACE this many minutes before a HIGH-impact release
EVENT_MAX_RIDE_TRADES = 2        # cap new catalyst trades per RIDE theme/day (anti-churn)
EVENT_IV_RANK_HIGH    = 70.0     # iv_rank above this => FADE_VOL (premium-selling favored)
EVENT_NEWS_LIMIT      = 30       # market-wide headlines pulled per cycle for detection
EVENT_STATE_FILE      = HOME / "autotrade_events.json"  # econ-calendar + iv-baseline cache
# Options intel (free IV/greeks-derived signals: real ATM IV-rank + 25Δ skew). Powers
# a real-IV FADE_VOL and surfaces options positioning to the model. Read-only, no risk.
OPTIONS_INTEL_ENABLED = True
OPTIONS_INTEL_FILE    = HOME / "autotrade_options_intel.json"  # rolling per-name ATM-IV history
OPTIONS_INTEL_MAX_NAMES = 3      # single-name movers to profile per cycle (+ the index ETFs)
OPTIONS_SKEW_THRESHOLD = 0.02    # |25Δ call IV − put IV| above this => a directional skew
# Extreme-IV ceiling for DIRECTIONAL option trades (debit spreads / long options). A name
# with sky-high ATM IV is a lottery ticket — the debit is hugely overpriced for a capped
# payoff and the signal (incl. skew) is noise (the 6/12 RDW lesson: 132% IV, skew flipped
# call->put in 30 min). Above this, single-name directional option trades are rejected.
# Legit high-IV momentum names (MU/MRVL ~105%) still pass; only the >ceiling junk is cut.
OPTION_MAX_ATM_IV = 120.0        # ATM IV % ceiling for single-name directional option trades
                                 # (120 gives legit high-IV semis headroom; still cuts RDW-type
                                 #  lottery tickets ~130%+)
# Anti-chase RELAXATION for a fresh-catalyst RIDE name (wider bounds, never removed —
# a continuation entry, not a blow-off-top chase; still a defined-risk debit spread).
ANTI_CHASE_MAX_VWAP_EXT_EVENT    = 0.08
ANTI_CHASE_MIN_OFF_EXTREME_EVENT = 0.003
RSI_OVERBOUGHT_EVENT = 90.0
RSI_OVERSOLD_EVENT   = 10.0
# Live economic-calendar feed. Default = the free Forex Factory / faireconomy weekly
# JSON (no key, includes ISO times WITH tz offset + impact). FMP's calendar is now
# paywalled, so it's only used if you set EVENT_ECON_PROVIDER="fmp" with a paid key.
EVENT_ECON_PROVIDER   = "faireconomy"   # "faireconomy" (free) | "fmp" (needs paid key)
EVENT_ECON_URL        = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
EVENT_ECON_COUNTRY    = "USD"           # faireconomy uses "USD"; FMP uses "US"
EVENT_ECON_MIN_IMPACT = "High"
# Headline keyword signatures -> theme. First match wins.
EVENT_SIGNATURES = {
    "oil_geopolitical": ["iran", "israel", "missile", "air strike", "airstrike",
                         "sanction", "opec", "strait of hormuz", "invasion",
                         "attack on", "ceasefire", "oil embargo", "tanker"],
    "rate_dovish":      ["rate cut", "cuts rates", "dovish", "signals easing",
                         "pauses hikes"],
    "rate_hawkish":     ["rate hike", "raises rates", "hawkish", "higher for longer"],
    "macro_shock":      ["new tariff", "tariffs on", "credit downgrade",
                         "downgrades u.s.", "sovereign default", "debt default"],
}
# theme -> affected liquid instruments + risk direction. The deterministic fast path;
# unmapped events fall back to a (cached) model call.
EVENT_THEME_MAP = {
    "oil_geopolitical": {"long": ["XLE", "USO", "XOM", "CVX", "ITA", "LMT", "RTX", "GLD"],
                         "short": ["JETS", "AAL", "DAL", "UAL", "CCL"], "risk": "off"},
    "rate_dovish":      {"long": ["SPY", "QQQ", "XLK", "IWM", "GLD"], "short": [], "risk": "on"},
    "rate_hawkish":     {"long": [], "short": ["QQQ", "IWM", "XLK"], "risk": "off"},
    "macro_shock":      {"long": ["GLD"], "short": ["SPY", "QQQ", "IWM"], "risk": "off"},
}
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

# ---- sector/theme map for the correlation-heat cap (AUDIT_ROADMAP #10) -------
# Coarse correlation buckets — names that tend to move together get ONE bucket, so the
# heat cap (MAX_SECTOR_DIRECTIONAL) treats 8 semis longs as one concentrated bet, not 8
# independent ones. Deliberately broad (AI/semis is one bucket, not split by sub-industry)
# because the failure mode is correlated drawdown, not GICS precision. Symbol→sector is
# built off this in code (sector_for); anything unmapped falls back to "other".
SECTOR_MAP = {
    "semis_ai": ["NVDA", "AMD", "AVGO", "MU", "ARM", "TSM", "ASML", "LRCX", "KLAC",
                 "ANET", "SMCI", "INTC", "AMAT", "MRVL", "DELL", "VRT"],
    "megacap_tech": ["AAPL", "MSFT", "AMZN", "META", "GOOGL", "NFLX"],
    "software": ["PANW", "DDOG", "SNOW", "CRWD", "NET", "ORCL", "ADBE", "NOW", "CRM",
                 "PLTR", "SHOP", "INTU", "AXON"],
    "crypto": ["COIN", "MSTR", "MSTU", "MSTX", "BITX", "ETHU"],
    "ev_mobility": ["TSLA", "UBER", "ABNB", "MELI"],
    "energy": ["XLE", "USO", "XOM", "CVX", "UCO", "ERX", "OIH", "SLB"],
    "defense": ["ITA", "LMT", "RTX", "NOC", "GD"],
    "index_etf": ["SPY", "QQQ", "IWM", "DIA", "VOO", "VTI"],
    "metals": ["GLD", "GDX", "NUGT", "NEM"],
}

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

# ---- overnight-drift sleeve (close-to-open index capture) -------------------
# A small, SEPARATE book that exploits the well-documented overnight (close->open)
# equity drift: buy a broad-index ETF near the close, sell it at the next open.
# Directional and uncorrelated to the intraday momentum book and to the short-
# premium options. Defined size, regime-gated (only when the index is in an
# uptrend and VIX is calm), and SHIELDED from the intraday machinery exactly like
# the growth sleeve (excluded from EOD flatten, trailing stops, loss-halt flatten,
# the overnight reconcile, and off-limits to the decision model).
OVERNIGHT_DRIFT_ENABLED = True
OVERNIGHT_SYMBOL        = "SPY"     # deepest liquidity + strongest documented drift
OVERNIGHT_NOTIONAL      = 5_000.0   # $ deployed each night (separate from intraday caps)
OVERNIGHT_BUY_HOUR      = 15        # buy near the close...
OVERNIGHT_BUY_MIN       = 55        # ...at/after 15:55 ET
OVERNIGHT_SELL_HOUR     = 9         # sell after the open...
OVERNIGHT_SELL_MIN      = 35        # ...at/after 9:35 ET (let the open settle)
OVERNIGHT_TREND_MA      = 200       # only buy when the index is above its 200-DMA (risk-on)
OVERNIGHT_VIX_CEILING   = 28.0      # skip the hold if VIX is elevated (gap risk)
OVERNIGHT_SKIP_WEEKEND  = True      # don't buy Fridays (weekend hold is weaker/riskier)

# ===========================================================================
# DETERMINISTIC REGIME ENGINE + new strategy books  (ALL default OFF)
# ===========================================================================
# Evidence-driven redesign (research 2026-06-07). A pure-code regime classifier
# decides WHICH strategy is allowed each cycle — or forces FLAT — instead of the
# model picking freely from a menu; the model only fills strikes within the
# pre-authorized structure. Everything here is gated OFF by default so the proven
# engine is byte-for-byte unchanged until each piece is explicitly enabled+tested.
REGIME_ENGINE_ENABLED = False

# VIX regime bands (level). LOW: premium too thin to sell. ELEVATED: richest
# short-premium (trade smaller). HIGH: short-vol is lethal -> STAY FLAT intraday.
REGIME_VIX_LOW      = 14.0
REGIME_VIX_ELEVATED = 20.0
REGIME_VIX_HIGH     = 28.0
# Index (SPY) trend classification from its day move vs prior close.
REGIME_TREND_PCT  = 1.0   # |move| >= this -> trending; else range-bound
REGIME_STRONG_PCT = 2.0   # |move| >= this -> STRONG trend (momentum debit spreads)
REGIME_TAPE_PCT   = 0.30  # mean index-ETF move >= this -> risk_on/risk_off tape (don't fight it)

# Uncapped stock trend trades: place the bracket's take-profit FAR away so the win isn't
# capped — the trailing chandelier stop (manage_stops) becomes the real exit and a runner
# can run. (Alpaca brackets require a TP leg; "far" = effectively no cap intraday.)
STOCK_UNCAPPED            = True
STOCK_UNCAPPED_TARGET_PCT = 0.30   # far target = entry ± 30% (rarely hit; trail governs)

# Pre-filter: don't sit out clean orderly trends. A single name moving this much, OR a
# trending broad tape, is enough to call the model (was a hard 2.0% single-name only).
QUALIFY_MOMENTUM_PCT = 1.3   # single-name |day move| that qualifies a momentum setup
QUALIFY_TAPE_PCT     = 0.6   # mean index-ETF |move| that qualifies a "trade with the tape" setup

# ---- tail-hedge convexity book ---------------------------------------------
# Small always-on long-vol overlay: cheap OTM SPY puts that pay on a crash, sized
# as a fixed small premium drag, scaled up in the HIGH-VIX regime. Separate book,
# off-limits to intraday, like the growth/overnight sleeves.
TAIL_HEDGE_ENABLED  = False
TAIL_HEDGE_SYMBOL   = "SPY"
TAIL_HEDGE_BUDGET   = 300.0   # $ premium per roll (the insurance "drag")
TAIL_HEDGE_OTM_PCT  = 0.07    # buy puts ~7% out of the money
TAIL_HEDGE_DTE_MIN  = 30
TAIL_HEDGE_DTE_MAX  = 60
TAIL_HEDGE_ROLL_DTE = 21      # roll/replace when under this many days to expiry
TAIL_HEDGE_TAKE_PROFIT_MULT = 3.0   # monetize a hedge that triples on a vol spike

# ---- earnings IV-crush book -------------------------------------------------
# Defined-risk short premium (iron condor) into a single name's earnings, opened
# the afternoon before, closed the day after on the IV crush. Best when VIX 16-22.
EARNINGS_CRUSH_ENABLED = False
# VIX band (AUDIT_ROADMAP #23). The 16-22 band left the book DARK most of the month (VIX
# sits 12-16 in calm regimes). Widen the band so the structural IV-crush edge is harvested
# across more of the calendar; the upper bound still stands the book down in a panic tape
# where a single name can gap clean through both wings.
EARNINGS_VIX_MIN  = 12.0
EARNINGS_VIX_MAX  = 28.0
EARNINGS_MAX_RISK = 1_000.0   # max defined loss per SINGLE earnings condor
EARNINGS_WING_WIDTH = 5.0
EARNINGS_MIN_DTE  = 2         # prefer an expiry >= this many days out, so a single
                              # missed post-earnings close can't let the condor expire
                              # ITM and assign (falls back to nearest if none listed)
# Concurrency + shared risk budget (AUDIT_ROADMAP #23). The book used to open ONE condor
# a night on the first universe name reporting. Allow up to EARNINGS_MAX_CONCURRENT condors
# the same night (on DIFFERENT names) as long as their combined defined risk stays within
# EARNINGS_NIGHT_RISK_BUDGET — spreading the crush edge across several names instead of
# betting the whole night on one print. Each individual condor still respects EARNINGS_MAX_RISK.
EARNINGS_MAX_CONCURRENT  = 3       # max open earnings condors held over one night
EARNINGS_NIGHT_RISK_BUDGET = 2_500.0   # shared defined-risk ceiling across tonight's condors
# Eligibility filter (AUDIT_ROADMAP #23). Only sell into a name whose front-expiry option
# market is TIGHT — a wide condor donates the spread on all four legs at entry AND exit, and
# a thin two-sided market makes the post-crush buy-back expensive. Reject a name whose ATM
# straddle legs quote wider than this fraction of their mid.
EARNINGS_MAX_LEG_SPREAD_PCT = 0.12
EARNINGS_UNIVERSE = [         # liquid optionable names with clean, well-priced earnings moves.
                              # Broadened (#23) so a night rarely has zero eligible names; the
                              # tight-spread + VIX-band runtime filters keep quality high.
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "AMD", "NFLX", "CRM",
    "AVGO", "ORCL", "ADBE", "CRWD", "PANW", "NOW", "INTC", "QCOM", "MU", "TXN",
    "JPM", "BAC", "GS", "V", "MA", "DIS", "NKE", "COST", "WMT", "HD",
    "PLTR", "SHOP", "UBER", "ABNB", "COIN", "SMCI", "MRVL", "DELL", "LRCX", "KLAC",
]

# ---- conviction-ITM book (deep-ITM, multi-day directional options) ----------
# See CONVICTION_ITM_SPEC.md. Near-ATM/nearest-expiry directional options bleed
# theta and get force-closed same-day so a multi-day catalyst can't play out.
# This book buys DEEP-ITM (~0.75 delta, mostly intrinsic) contracts at a 2-5 week
# expiry, sizes them on RISK (stop distance) not premium, and holds them across the
# 15:45 flatten for several days with a daily thesis re-judge. Its symbols are
# SHIELDED from the EOD option-close + orphan sweep exactly like the tail-hedge /
# earnings books. Routing is DETERMINISTIC (build_option_chains auto-selects ITM-vs-
# ATM by setup type); the model never reasons about option structure. Ships OFF.
CONVICTION_ITM_ENABLED       = False   # master flag — when False the engine is byte-for-byte unchanged
CONVICTION_ITM_DEPTH         = 0.07    # strike ~7% ITM (~0.75 delta proxy via moneyness)
CONVICTION_ITM_DTE_MIN       = 14      # target expiry window (days to expiry)...
CONVICTION_ITM_DTE_MAX       = 35      # ...pick the listed expiry nearest the middle of this band
CONVICTION_ITM_STOP_PCT      = -0.30   # option stop (basis for risk-based sizing)
CONVICTION_ITM_RISK_TARGET   = 900.0   # max $ risk per position (stop-distance based)
CONVICTION_ITM_NOTIONAL_CAP  = 3000.0  # max premium outlay per position (one name can't eat the book)
CONVICTION_ITM_MAX_HOLD_DAYS = 5       # re-judge daily; hard exit after this many trading days
CONVICTION_ITM_MIN_DTE_EXIT  = 5       # close/roll under this DTE (before the gamma-theta cliff)

# ---- gap-fade strategy (9:30-10:00 ET) --------------------------------------
# Fade an opening gap back toward the prior close on a liquid name. Stocks revert
# gaps ~60-70% (evidence). Fills the otherwise-dead first half hour; defined stop
# beyond the gap extreme. Runs as its own code book (not via the model).
GAP_FADE_ENABLED  = False
GAP_FADE_MIN_PCT  = 1.0    # only fade gaps at least this large
GAP_FADE_MAX_PCT  = 4.0    # skip monster gaps (news-driven -> gap-and-go, not fade)
GAP_FADE_NOTIONAL = 3_000.0
GAP_FADE_STOP_PCT = 0.010  # stop ~1% beyond entry (past the gap extreme)
GAP_FADE_WINDOW_END_MIN = 0   # no new gap-fade once it's 10:00 ET (h==10, m>=this)
GAP_FADE_UNIVERSE = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "AMD"]


BLACKLIST: list[str] = []

ECON_BLACKOUT_DATES: list[str] = [
    # Hard blackout: whole-day FLAT, no new entries at all. For catalysts you'd rather
    # RIDE (sell no premium, but stay free to trade direction), use EVENT_CATALYST_DATES.
    # "2026-06-17",  # example: FOMC decision day
]

# Catalyst days: a known scheduled binary is today (FOMC, CPI, jobs). NOT a full
# blackout — short premium (iron condor / credit spread) is disabled so the bot never
# sells INTO the event, but the directional books (debit spread / long option / stock)
# stay open so a real pre- or post-event move can still be ridden.
EVENT_CATALYST_DATES: list[str] = [
    "2026-06-17",  # FOMC decision (2pm ET) + May retail sales (8:30am) — Warsh's first
                   # meeting as chair. Don't sell premium into it; ride a move if it comes.
]

# ---- runtime settings changeable from Telegram (SET <KEY> <VALUE>) ----------
# A whitelist of safe numeric/bool knobs the user can change remotely without a
# shell or code edit. SET writes the new value to OVERRIDES_FILE; every cycle is a
# fresh process that re-applies the overrides at import (_apply_overrides below),
# so a change takes effect on the next cycle. Only these keys can be set — never
# secrets, file paths, or universes.
OVERRIDES_FILE = HOME / "autotrade_overrides.json"
RUNTIME_SETTABLE = {
    "DAILY_LOSS_HALT": float,
    "DAILY_PROFIT_TARGET": float,
    "DAILY_PROFIT_STRETCH": float,
    "MAX_DEPLOYED_CAPITAL": float,
    "PER_TRADE_NOTIONAL_CAP": float,
    "PER_OPTION_NOTIONAL_CAP": float,
    "OPTION_RISK_TARGET": float,
    "STOCK_RISK_PER_TRADE": float,     # STOCK_RISK_CONV is a dict — not cast-mappable (scalars only)
    "MAX_SAME_DIRECTION_POSITIONS": int,
    # correlation-heat cap (#10) + account drawdown floor / red-day de-risk (#13)
    # + per-position max-loss kill (#14). All scalars; SECTOR_MAP (a dict) is not settable.
    "MAX_SECTOR_DIRECTIONAL": int,
    "PORTFOLIO_HEAT_MULT": float,
    "ACCOUNT_DRAWDOWN_HALT": float,
    "RED_DAY_DERISK": bool,
    "RED_DAY_RISK_MULT": float,
    "RED_DAY_DERISK_AFTER": int,
    "MAX_POSITION_LOSS_MULT": float,
    "MAX_SPREADS_PER_NAME_PER_DAY": int,
    "MOMENTUM_OPTION_MIN_DTE": int,
    "CREDIT_SPREAD_INDEX_ONLY": bool,
    "EVENT_ROUTER_ENABLED": bool,
    "EVENT_PRE_MIN": int,
    "EVENT_FRESH_MIN": int,
    "EVENT_MAX_RIDE_TRADES": int,
    "EVENT_IV_RANK_HIGH": float,
    "OPTIONS_INTEL_ENABLED": bool,
    "OPTIONS_INTEL_MAX_NAMES": int,
    "OPTIONS_SKEW_THRESHOLD": float,
    "OPTION_MAX_ATM_IV": float,
    "STOPPED_COOLDOWN_MIN": int,
    "OPTION_TRAIL_ACTIVATE": float,
    "OPTION_TRAIL_GIVEBACK": float,
    "OPTION_BREAKEVEN_AT": float,
    "OPTION_TRAIL_GIVEBACK_BAND": float,
    "OPTION_EXIT_SLIP": float,             # #15 marketable-limit exit slip
    "OPTION_EXIT_FILL_WAIT_SEC": float,    # #15 fill wait before market fallback
    "OPTION_MAX_IV_RANK": float,           # #11 single-name premium-buying IV-rank ceiling
    "CYCLE_ACTIVE_INTERVAL_MIN": float,
    "CYCLE_NORMAL_INTERVAL_MIN": float,
    "OVERNIGHT_MOMENTUM_ENABLED": bool,
    "VIX_CONDOR_CEILING": float,
    "MOMENTUM_MAX_DAY_PCT": float,
    "OVERNIGHT_DRIFT_ENABLED": bool,
    "OVERNIGHT_NOTIONAL": float,
    "GROWTH_SLEEVE_ENABLED": bool,
    "GROWTH_DAILY_FLOOR": float,
    "GROWTH_MAX_SLEEVE_CAPITAL": float,
    # regime engine + new books (flip on remotely once tested)
    "REGIME_ENGINE_ENABLED": bool,
    "TAIL_HEDGE_ENABLED": bool,
    "TAIL_HEDGE_BUDGET": float,
    "EARNINGS_CRUSH_ENABLED": bool,
    "EARNINGS_MAX_RISK": float,
    # earnings book broadening (#23): VIX band, concurrency + shared budget, tight-spread filter
    "EARNINGS_VIX_MIN": float,
    "EARNINGS_VIX_MAX": float,
    "EARNINGS_MAX_CONCURRENT": int,
    "EARNINGS_NIGHT_RISK_BUDGET": float,
    "EARNINGS_MAX_LEG_SPREAD_PCT": float,
    # debit-spread long-leg ITM target (#19) + option-chain breadth scaling (#22)
    # + deterministic directional-vehicle router (#21)
    "DEBIT_LONG_LEG_ITM_PCT": float,
    "MAX_OPTION_UNDERLYINGS_BUSY": int,
    "MAX_OPTION_UNDERLYINGS_BASE": int,
    "VEHICLE_ROUTER_ENABLED": bool,
    "VEHICLE_ROUTER_HIGH_IVR": float,
    "GAP_FADE_ENABLED": bool,
    "GAP_FADE_NOTIONAL": float,
    # conviction-ITM book (deep-ITM, multi-day directional options)
    "CONVICTION_ITM_ENABLED": bool,
    "CONVICTION_ITM_DEPTH": float,
    "CONVICTION_ITM_DTE_MIN": int,
    "CONVICTION_ITM_DTE_MAX": int,
    "CONVICTION_ITM_STOP_PCT": float,
    "CONVICTION_ITM_RISK_TARGET": float,
    "CONVICTION_ITM_NOTIONAL_CAP": float,
    "CONVICTION_ITM_MAX_HOLD_DAYS": int,
    "CONVICTION_ITM_MIN_DTE_EXIT": int,
    # established-trend pullback tolerance (anti-chase carve-out + conviction-ITM trend gate)
    "CONVICTION_TREND_MAX_OFF_HOD": float,
}


def _apply_overrides():
    """Apply user overrides from OVERRIDES_FILE over the defaults above. Only keys in
    RUNTIME_SETTABLE are honored, coerced to their declared type. Bad file/values are
    ignored so a typo can never break startup."""
    if not OVERRIDES_FILE.exists():
        return
    try:
        import json
        ov = json.loads(OVERRIDES_FILE.read_text())
    except Exception:
        return
    g = globals()
    for k, v in (ov or {}).items():
        typ = RUNTIME_SETTABLE.get(k)
        if typ is None:
            continue
        try:
            g[k] = typ(v) if typ is not bool else bool(v)
        except Exception:
            continue


_apply_overrides()
