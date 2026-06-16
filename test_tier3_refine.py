"""Tier-3 refinement tests (#19, #21, #22, #23). Redirect config file paths to /tmp so the
suite never touches real state/overrides, then import the engine and exercise the new
deterministic helpers. Run: ./venv/bin/python3 /tmp/test_tier3_refine.py"""
import sys, os, types
from pathlib import Path

REPO = "/Users/nirvaan/autotrade"
sys.path.insert(0, REPO)

import config as cfg
# Redirect any writable paths so tests can't touch live files.
_tmp = Path("/tmp/tier3_test_state")
_tmp.mkdir(exist_ok=True)
for attr in ("OVERRIDES_FILE", "STATE_FILE", "LOG_FILE", "LEARNINGS_FILE"):
    if hasattr(cfg, attr):
        setattr(cfg, attr, _tmp / (attr.lower() + ".json"))

import autotrade as at
import earnings_crush as ec

PASS, FAIL = 0, 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {name}")
    else:
        FAIL += 1; print(f"  FAIL  {name}")


def synth_chain(spot, expiry="2099-01-15"):
    """A synthetic near-money chain (the shape option_chain_for returns): calls+puts every
    $1 from -10% to +10% of spot, with tight quotes."""
    rows = []
    lo, hi = int(spot * 0.90), int(spot * 1.10)
    for k in range(lo, hi + 1):
        for typ in ("call", "put"):
            rows.append({"symbol": f"X{k}{typ[0].upper()}", "type": typ,
                         "strike": float(k), "expiry": expiry,
                         "bid": 1.00, "ask": 1.05, "mid": 1.025})
    return rows


# ---- #19: debit-spread long leg lands ~ITM, not ATM ------------------------
print("#19 debit-spread long leg ~ITM")
spot = 100.0
rows = synth_chain(spot)
cfg.DEBIT_LONG_LEG_ITM_PCT = 0.04
# Bullish: long CALL should be ~4% BELOW spot (ITM call), i.e. ~96, NOT 100 (ATM).
long_call = at.debit_spread_long_leg(rows, spot, bullish=True)
check("bull long call is ITM (strike < spot)", long_call["type"] == "call" and long_call["strike"] < spot)
check("bull long call near 4% ITM (~96)", abs(long_call["strike"] - 96.0) <= 1.0)
check("bull long call is NOT ATM (not 100)", long_call["strike"] != 100.0)
# Bearish: long PUT should be ~4% ABOVE spot (ITM put), i.e. ~104.
long_put = at.debit_spread_long_leg(rows, spot, bullish=False)
check("bear long put is ITM (strike > spot)", long_put["type"] == "put" and long_put["strike"] > spot)
check("bear long put near 4% ITM (~104)", abs(long_put["strike"] - 104.0) <= 1.0)
# Knob respected: a larger ITM pct moves the strike deeper ITM.
cfg.DEBIT_LONG_LEG_ITM_PCT = 0.04
deeper = at.debit_spread_long_leg(rows, spot, bullish=True, itm_pct=0.08)
check("deeper itm_pct -> deeper strike", deeper["strike"] < long_call["strike"])


# ---- #21: router picks exactly one vehicle, the right one, and buckets the 4 ----
print("#21 deterministic vehicle router")
cfg.VEHICLE_ROUTER_ENABLED = True
cfg.VEHICLE_ROUTER_HIGH_IVR = 60.0
cfg.CONVICTION_ITM_ENABLED = True

# high IV-rank -> stock (don't buy rich premium)
r_rich = {"symbol": "AAA", "signal": "STRONG_BULL", "day_pct": 4.0}
oi_rich = {"AAA": {"iv_rank": 80.0, "atm_iv": 40.0}}
v = at.route_directional_vehicle(r_rich, options_intel=oi_rich, regime={"tape_bias": "risk_on"})
check("high IV-rank -> stock", v == "stock")

# low-IV intraday momentum (strong mover, no clean multi-day trend) -> debit_spread.
# Missing vwap_ext makes established_trend() False, so conviction doesn't qualify.
r_mom = {"symbol": "BBB", "signal": "STRONG_BULL", "day_pct": 3.5, "rsi": 60.0}
oi_low = {"BBB": {"iv_rank": 20.0, "atm_iv": 35.0}}
v = at.route_directional_vehicle(r_mom, options_intel=oi_low, regime={"tape_bias": "risk_on"})
check("low-IV momentum -> debit_spread", v == "debit_spread")

# low-IV clean multi-day trend -> conviction_itm (book enabled). vwap_ext/off_hod are
# FRACTIONS (ANTI_CHASE_MAX_VWAP_EXT~0.04, CONVICTION_TREND_MAX_OFF_HOD~0.02).
r_trend = {"symbol": "CCC", "signal": "STRONG_BULL", "day_pct": 5.0, "rsi": 60.0,
           "vwap_ext": 0.02, "off_hod": 0.01}
v = at.route_directional_vehicle(r_trend, options_intel=oi_low, regime={"tape_bias": "risk_on"})
check("low-IV clean trend -> conviction_itm", v == "conviction_itm")

# same setup, conviction book OFF -> debit_spread fallback.
cfg.CONVICTION_ITM_ENABLED = False
v = at.route_directional_vehicle(r_trend, options_intel=oi_low, regime={"tape_bias": "risk_on"})
check("clean trend, book off -> debit_spread", v == "debit_spread")
cfg.CONVICTION_ITM_ENABLED = True

# default: weak/no-signal -> None (not forced into a vehicle).
r_flat = {"symbol": "DDD", "signal": "NEUTRAL", "day_pct": 0.2}
v = at.route_directional_vehicle(r_flat, options_intel=oi_low)
check("no signal -> None (not routed)", v is None)

# default weak-but-directional (small move, no trend) -> stock.
r_small = {"symbol": "EEE", "signal": "WEAK_BULL", "day_pct": 1.0}
v = at.route_directional_vehicle(r_small, options_intel={"EEE": {"iv_rank": 30.0}})
check("weak directional small move -> stock", v == "stock")

# router returns exactly ONE vehicle per name from a scan.
scan = [r_rich, r_mom, r_trend]
oi_all = {**oi_rich, **oi_low}
router = at.build_directional_router(scan, options_intel=oi_all, regime={"tape_bias": "risk_on"})
check("router maps each name to exactly one vehicle", set(router) == {"AAA", "BBB", "CCC"}
      and all(isinstance(x, str) for x in router.values()))

# guardrail: routed-to-stock name rejects a debit spread on the same name (one vehicle).
# Build a debit_spread decision on AAA (routed to 'stock') and confirm it's blocked.
acct = {"equity": 100000.0, "day_pl": 0.0}
import datetime
now = at.et_now()
debit_dec = {"action": "multi_leg", "net_price": -1.0,
             "legs": [{"symbol": "AAA250101C00090000", "side": "buy"},
                      {"symbol": "AAA250101C00095000", "side": "sell"}]}
# _decision_vehicle should read this as debit_spread.
check("_decision_vehicle reads debit spread", at._decision_vehicle(debit_dec) == "debit_spread")
# router says AAA -> stock; a debit on AAA must be rejected.
ok, why = at.passes_guardrails(debit_dec, {"start_equity": 100000.0}, acct, now,
                               ref_price=1.0,
                               offered_options={"AAA250101C00090000", "AAA250101C00095000"},
                               vehicle_router={"AAA": "stock"}, positions=[])
check("guardrail blocks wrong vehicle (debit on stock-routed name)", (not ok) and "vehicle router" in why)

# bucket aggregation: a name already held bullish via a LONG CALL blocks a 2nd bullish
# vehicle (a stock long) on the same name. Uses buy_stock so the option time-cutoff guard
# (earlier in passes_guardrails) doesn't pre-empt the router block we're testing.
held_pos = [{"symbol": "AAA250101C00100000", "side": "long", "qty": 1}]
stock_dec = {"action": "buy_stock", "symbol": "AAA", "direction": "long", "qty": 10,
             "stop_price": 95.0, "target_price": 110.0, "conviction": "medium"}
ok2, why2 = at.passes_guardrails(stock_dec, {"start_equity": 100000.0}, acct, now,
                                 ref_price=100.0, vehicle_router={}, positions=held_pos,
                                 scan_row={"symbol": "AAA", "day_pct": 1.0})
check("bucket: 2nd bullish vehicle on held-bullish name blocked",
      (not ok2) and "one vehicle per name" in why2)
# DIRECTIONAL_STRATEGIES groups the four into one bucket label set.
check("debit_spread in directional bucket", "debit_spread" in at.DIRECTIONAL_STRATEGIES)
check("stock_long + long_option in directional bucket",
      {"stock_long", "long_option"} <= at.DIRECTIONAL_STRATEGIES)


# ---- #22: max_underlyings scales with >=3% movers; trend candidates sort first ----
print("#22 option-chain breadth scaling + trend-first ordering")
cfg.MAX_OPTION_UNDERLYINGS_BASE = 3
cfg.MAX_OPTION_UNDERLYINGS_BUSY = 6

# Monkeypatch the chain fetch + anti_chase so build_option_chains runs offline and every
# candidate "succeeds", recording the ORDER it was asked for.
_asked = []
def _fake_chain(odc, und, spot, want_today_expiry=False):
    _asked.append(und)
    return [{"symbol": f"{und}_C", "type": "call", "strike": spot, "expiry": "2099-01-15",
             "bid": 1.0, "ask": 1.05, "mid": 1.025}]
at.option_chain_for = _fake_chain
at.anti_chase_reason = lambda *a, **k: None      # never block a candidate

class _Now:
    hour, minute = 11, 0
now2 = _Now()

# Calm tape: only 2 names moving >=3% -> cap stays low (base + 2 = 5, but few movers).
calm_scan = [
    {"symbol": "M1", "last": 100.0, "day_pct": 5.0, "signal": "STRONG_BULL"},
    {"symbol": "M2", "last": 50.0, "day_pct": 3.5, "signal": "STRONG_BULL"},
    {"symbol": "S1", "last": 20.0, "day_pct": 1.0, "signal": "WEAK_BULL"},
]
_asked.clear()
chains, offered, conv = at.build_option_chains(None, calm_scan, [], 15.0, now2,
                                               regime={"tape_bias": "risk_on"})
# 2 movers >=3% -> cap = base(3) + 2 = 5; only 2 movers qualify so 2 chains fetched.
check("calm: 2 movers -> 2 chains fetched", len(chains) == 2)

# Busy tape: 8 names moving >=3% -> cap rises to BUSY ceiling (6), so 6 chains fetched.
busy_scan = [{"symbol": f"B{i}", "last": 100.0 + i, "day_pct": 3.0 + i,
              "signal": "STRONG_BULL"} for i in range(8)]
_asked.clear()
chains_b, offered_b, conv_b = at.build_option_chains(None, busy_scan, [], 15.0, now2,
                                                     regime={"tape_bias": "risk_on"})
check("busy: many movers -> cap scales to BUSY (6 chains)", len(chains_b) == 6)
check("busy fetch never exceeds BUSY ceiling", len(chains_b) <= cfg.MAX_OPTION_UNDERLYINGS_BUSY)

# Trend candidates fetched BEFORE held positions: a held option underlying must NOT crowd
# out fresh movers. Give 1 mover + a held position on HELDU; the mover is asked first.
_asked.clear()
mixed_scan = [{"symbol": "TREND", "last": 100.0, "day_pct": 6.0, "signal": "STRONG_BULL"}]
held = [{"symbol": "HELDU250101C00100000", "asset_class": "us_option", "current": 100.0}]
_orig_parse = at.parse_occ
at.parse_occ = (lambda s: {"underlying": "HELDU", "type": "call", "strike": 100.0,
                           "expiry": "2025-01-01"} if s.startswith("HELDU") else None)
cfg.MAX_OPTION_UNDERLYINGS_BASE = 3
chains_m, _, _ = at.build_option_chains(None, mixed_scan, held, 15.0, now2,
                                        regime={"tape_bias": "risk_on"})
at.parse_occ = _orig_parse                # restore — don't leak into other tests
check("trend candidate asked BEFORE held underlying", _asked and _asked[0] == "TREND")
check("both trend + held fetched", "TREND" in chains_m and "HELDU" in chains_m)

# WITH-TAPE trend sorts ahead of a bigger AGAINST-tape mover.
_asked.clear()
tape_scan = [
    {"symbol": "AGAINST", "last": 100.0, "day_pct": -8.0, "signal": "STRONG_BEAR"},  # down on risk_on
    {"symbol": "WITH", "last": 100.0, "day_pct": 4.0, "signal": "STRONG_BULL"},       # up on risk_on
]
cfg.MAX_OPTION_UNDERLYINGS_BASE = 3
at.build_option_chains(None, tape_scan, [], 15.0, now2, regime={"tape_bias": "risk_on"})
check("WITH-tape trend sorts before bigger AGAINST-tape mover", _asked[0] == "WITH")


# ---- #23: multiple earnings names eligible; N concurrent within budget ----------
print("#23 earnings book broadening")
cfg.EARNINGS_MAX_CONCURRENT = 3
cfg.EARNINGS_NIGHT_RISK_BUDGET = 2500.0
cfg.EARNINGS_MAX_RISK = 1000.0
cfg.EARNINGS_MAX_LEG_SPREAD_PCT = 0.12

# Universe broadened well beyond the old 10 names.
check("earnings universe broadened (>10 names)", len(cfg.EARNINGS_UNIVERSE) > 10)
# VIX band widened so it isn't dark most of the month.
check("VIX band widened (min<=12, max>=28)", cfg.EARNINGS_VIX_MIN <= 12 and cfg.EARNINGS_VIX_MAX >= 28)

# Stub the night's reporting names + condor builder so eligibility runs offline.
NIGHT = ["AAPL", "MSFT", "NVDA", "AMD"]   # 4 names report; concurrency cap is 3
ec._earnings_names_tonight = lambda today, exclude=None: [s for s in NIGHT if s not in (exclude or set())]
def _fake_build(odc, sym, spot, today):
    legs = [{"symbol": f"{sym}_SC", "side": "sell"}, {"symbol": f"{sym}_LC", "side": "buy"},
            {"symbol": f"{sym}_SP", "side": "sell"}, {"symbol": f"{sym}_LP", "side": "buy"}]
    return legs, 1.50, 800.0          # each condor: $800 risk
ec._build_condor = _fake_build

import datetime as _dt
today = _dt.date(2099, 1, 15)
state = {"earnings": {}}
picks = ec.eligible_tonight(None, state, today, vix=18.0)
# 4 names report but: concurrency cap 3 AND budget 2500 / 800 = 3 fit -> exactly 3.
check("multiple earnings names pass eligibility", len(picks) >= 2)
check("N concurrent capped at EARNINGS_MAX_CONCURRENT (3)", len(picks) == 3)
check("chosen condors within night budget",
      sum(p["risk"] for p in picks) <= cfg.EARNINGS_NIGHT_RISK_BUDGET)
check("each condor within per-trade max risk",
      all(p["risk"] <= cfg.EARNINGS_MAX_RISK for p in picks))

# Tighter budget squeezes concurrency below the cap (budget binds, not the count).
cfg.EARNINGS_NIGHT_RISK_BUDGET = 1500.0   # only 1 x $800 fits (2 would be 1600 > 1500)
picks2 = ec.eligible_tonight(None, {"earnings": {}}, today, vix=18.0)
check("tighter budget limits concurrency", len(picks2) == 1)
cfg.EARNINGS_NIGHT_RISK_BUDGET = 2500.0

# Already-holding 2 condors -> only 1 more slot (concurrency), and budget accounts for held risk.
state3 = {"earnings": {"holdings": [
    {"underlying": "META", "legs": [], "risk": 800.0, "entry_date": today.isoformat()},
    {"underlying": "GOOGL", "legs": [], "risk": 800.0, "entry_date": today.isoformat()},
]}}
picks3 = ec.eligible_tonight(None, state3, today, vix=18.0)
check("held condors count toward concurrency cap", len(picks3) <= 1)
check("held condors count toward shared budget",
      all(800.0 * len(state3["earnings"]["holdings"]) + p["risk"] <= cfg.EARNINGS_NIGHT_RISK_BUDGET
          for p in picks3))

# tight-spread filter rejects a wide-quote name.
wide_rows_by_sym = {"W_SC": {"bid": 1.0, "ask": 1.5, "mid": 1.25}}  # 40% wide
check("tight-spread filter rejects wide legs",
      not ec._legs_tight([{"symbol": "W_SC", "side": "sell"}], wide_rows_by_sym))
tight_rows = {"T_SC": {"bid": 1.00, "ask": 1.05, "mid": 1.025}}     # ~5% wide
check("tight-spread filter passes tight legs",
      ec._legs_tight([{"symbol": "T_SC", "side": "sell"}], tight_rows))


print(f"\n{'='*50}\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
