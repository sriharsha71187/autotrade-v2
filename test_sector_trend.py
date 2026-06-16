import sys, tempfile
sys.path.insert(0, "/Users/nirvaan/autotrade")
import config as cfg
cfg.LOG_FILE = tempfile.NamedTemporaryFile(delete=False, suffix=".log").name
cfg.STATE_FILE = tempfile.NamedTemporaryFile(delete=False, suffix=".json").name
cfg.OVERRIDES_FILE = tempfile.NamedTemporaryFile(delete=False, suffix=".json").name

import regime as rg
import autotrade as at

# Synthetic scan mirroring 6/16: broad indices mixed (Dow green masks Nasdaq red),
# SEMIS crashing 5-10%, software flat.
def row(sym, dp, sig=None, vwap_ext=None, rsi=None):
    return {"symbol": sym, "day_pct": dp, "last": 100.0,
            "signal": sig, "vwap_ext": vwap_ext, "rsi": rsi}

SCAN = [
    row("SPY", -0.6), row("QQQ", -1.9), row("DIA", +0.6), row("IWM", -0.9),
    # semis crash — 4 members in scan (>= MIN_MEMBERS)
    row("AMD", -7.0, "STRONG_BEAR", vwap_ext=-0.02, rsi=25),
    row("MRVL", -10.0, "STRONG_BEAR", vwap_ext=-0.02, rsi=25),
    row("MU", -6.0, "STRONG_BEAR", vwap_ext=-0.02, rsi=25),
    row("KLAC", -7.0, "STRONG_BEAR", vwap_ext=-0.02, rsi=25),
    # software flat (2 members -> has a reading, ~neutral)
    row("PANW", 0.1), row("DDOG", -0.1),
    # crypto: only ONE member in scan -> < MIN_MEMBERS, no sector reading (fallback test)
    row("COIN", -3.0),
]
VIX = 18.0

# ---- 1. classify exposes sector_trends; semis ~ -7.5, broad tape ~ -0.7 ----
reg = rg.classify(SCAN, VIX)
st = reg["sector_trends"]
assert "semis_ai" in st, st
assert abs(st["semis_ai"] - (-7.5)) < 0.01, st["semis_ai"]
assert "software" in st, st
assert abs(reg["tape"] - (-0.7)) < 0.01, reg["tape"]          # mean(-0.6,-1.9,0.6,-0.9)
# crypto has <2 in-scan members -> excluded; index_etf excluded
assert "crypto" not in st, st
assert "index_etf" not in st, st
# broad tape_bias is ~neutral (|-0.7| within... actually -0.7 <= -0.30 -> risk_off!)
print("broad tape_bias =", reg["tape_bias"], "tape =", reg["tape"])
# summary surfaces sector_trends
assert "sector_trends" in rg.summary(reg)

# sector_trend_bias helper
assert rg.sector_trend_bias(st, "semis_ai", cfg.REGIME_TAPE_PCT) == "risk_off"
assert rg.sector_trend_bias(st, "software", cfg.REGIME_TAPE_PCT) == "neutral"
assert rg.sector_trend_bias(st, "crypto", cfg.REGIME_TAPE_PCT) is None   # absent
print("PASS 1: classify/sector_trends/helper")

# ---- 2. Flag ON: effective_tape_bias is SECTOR-relative ----
cfg.SECTOR_TREND_ENABLED = True
assert at.effective_tape_bias("AMD", reg) == "risk_off"        # semis sector
assert at.effective_tape_bias("MU", reg) == "risk_off"
# software member -> neutral sector
assert at.effective_tape_bias("PANW", reg) == "neutral"

# A STRONG_BULL semis name (a long counter to the falling semis sector) must NOT pass.
bull_semi = row("NVDA", +3.0, "STRONG_BULL", vwap_ext=+0.02, rsi=60)
assert at.established_trend(bull_semi, reg) is False, "counter-sector long should fail"
# A STRONG_BEAR semis name (short, WITH the falling sector) IS favored (passes).
bear_semi = row("AMD", -7.0, "STRONG_BEAR", vwap_ext=-0.02, rsi=25)
assert at.established_trend(bear_semi, reg) is True, "with-sector short should pass"
print("PASS 2: flag ON sector-relative established_trend")

# ---- 3. Fallback: name in a sector with <2 in-scan members -> broad bias ----
# crypto has only COIN in scan -> no sector reading -> falls back to broad tape_bias.
assert at.effective_tape_bias("COIN", reg) == reg["tape_bias"], "fallback to broad"
# an 'other' (unmapped) name also falls back
assert at.effective_tape_bias("ZZZZ", reg) == reg["tape_bias"]
print("PASS 3: fallback to broad bias for thin/unmapped sectors")

# ---- 4. Flag OFF: byte-identical to broad behavior at every site ----
cfg.SECTOR_TREND_ENABLED = False
# effective_tape_bias == broad tape_bias for EVERY symbol (semis included)
for s in ("AMD", "MU", "PANW", "COIN", "NVDA", "SPY", "ZZZZ"):
    assert at.effective_tape_bias(s, reg) == reg["tape_bias"], s
print("PASS 4a: flag-OFF effective_tape_bias == broad everywhere")

# established_trend: prove flag-ON-broad == flag-OFF for a name whose sector == broad.
# Build a regime whose broad tape_bias == a sector's bias so on/off agree.
# Use a scan where semis sector bias == broad bias (both risk_off here).
# A STRONG_BEAR semis short: with flag OFF it checks broad (risk_off) -> pass.
cfg.SECTOR_TREND_ENABLED = False
off_ans = at.established_trend(bear_semi, reg)
cfg.SECTOR_TREND_ENABLED = True
on_ans = at.established_trend(bear_semi, reg)
cfg.SECTOR_TREND_ENABLED = False
# semis bias (risk_off) == broad bias (risk_off) so the two agree for this name
assert off_ans == on_ans == True, (off_ans, on_ans)
print("PASS 4b: same answer flag-on-broad vs flag-off when sector==broad")

# established_trend with flag OFF uses broad bias exactly: a STRONG_BULL on a risk_off
# broad tape must FAIL whether or not it's a semi (broad-only judgment).
cfg.SECTOR_TREND_ENABLED = False
assert at.established_trend(bull_semi, reg) is False   # broad risk_off blocks the long
print("PASS 4c: flag-OFF established_trend uses broad bias")

# ---- 5. Defensive: bad rows can't crash the sector_trends computation ----
# (_index_move/_tape already tolerate well-formed rows; sector_trends must survive
# junk values — bad day_pct types, missing keys — without raising.)
junk = [row("SPY", -0.5), {"symbol": "AMD", "day_pct": "nope"},
        {"symbol": "MU"}, {"day_pct": 1.0}, {}]
assert rg._sector_trends(junk) == {}, rg._sector_trends(junk)
assert rg._sector_trends(None) == {}
assert rg._sector_trends([]) == {}
# classify still returns a dict for sector_trends on a clean scan
assert isinstance(rg.classify(SCAN, 18.0)["sector_trends"], dict)
print("PASS 5: defensive sector_trends")

print("\nALL TESTS PASSED")
print("sector_trends =", {k: round(v, 2) for k, v in st.items()})
