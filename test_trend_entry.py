import sys, os, tempfile
sys.path.insert(0, "/Users/nirvaan/autotrade")

import config as cfg
# redirect any file writes away from real state/logs
cfg.LOG_FILE = tempfile.NamedTemporaryFile(delete=False, suffix=".log").name
cfg.STATE_FILE = tempfile.NamedTemporaryFile(delete=False, suffix=".json").name
cfg.CONVICTION_ITM_ENABLED = True

import autotrade as at

RISK_ON = {"tape_bias": "risk_on"}
RISK_OFF = {"tape_bias": "risk_off"}
NEUTRAL = {"tape_bias": "neutral"}

def row(**kw):
    base = {"symbol": "X", "signal": "STRONG_BULL", "day_pct": 5.0,
            "vwap_ext": 0.015, "rsi": 60.0, "off_hod": 0.01, "off_lod": 0.05}
    base.update(kw)
    return base

passed = []
def check(name, cond):
    passed.append((name, cond))
    print(("PASS" if cond else "FAIL"), name)

# 1. SPCX case: STRONG_BULL above VWAP, RSI None, ~1% off HOD pullback.
spcx = row(symbol="SPCX", rsi=None, off_hod=0.01, vwap_ext=0.02, day_pct=17.0)
check("SPCX conviction_itm_qualifies TRUE",
      at.conviction_itm_qualifies(spcx, regime=RISK_ON) is True)
check("SPCX anti-chase does NOT block",
      at.anti_chase_reason(True, spcx, regime=RISK_ON) is None)

# SPCX even right at the high (new HOD) passes too.
spcx_hod = row(symbol="SPCX", rsi=None, off_hod=0.001, vwap_ext=0.02, day_pct=17.0)
check("SPCX at new HOD still qualifies",
      at.conviction_itm_qualifies(spcx_hod, regime=RISK_ON) is True)
check("SPCX at new HOD anti-chase OK",
      at.anti_chase_reason(True, spcx_hod, regime=RISK_ON) is None)

# 2a. Vertical first-bar spike: far above VWAP. Should be blocked + not qualify.
spike = row(symbol="VERT", off_hod=0.001, vwap_ext=0.09, rsi=None, day_pct=20.0)
check("vertical spike (vwap_ext 9%) NOT qualifies",
      at.conviction_itm_qualifies(spike, regime=RISK_ON) is False)
check("vertical spike anti-chase BLOCKS",
      at.anti_chase_reason(True, spike, regime=RISK_ON) is not None)

# 2b. Blown-off RSI (>80): should be blocked + not qualify.
blown = row(symbol="BLOW", off_hod=0.01, vwap_ext=0.02, rsi=85.0, day_pct=10.0)
check("blown-off RSI NOT qualifies",
      at.conviction_itm_qualifies(blown, regime=RISK_ON) is False)
check("blown-off RSI anti-chase BLOCKS",
      at.anti_chase_reason(True, blown, regime=RISK_ON) is not None)

# 3. Non-STRONG name ~1.5% off HOD: anti-chase still blocks if within stricter band;
#    carve-out requires trend structure. Use a BULL (not STRONG) 0.5% off the high.
weak = {"symbol": "WEAK", "signal": "BULL", "day_pct": 1.2,
        "vwap_ext": 0.01, "rsi": 60.0, "off_hod": 0.005, "off_lod": 0.05}
check("non-STRONG NOT established_trend",
      at.established_trend(weak, regime=RISK_ON) is False)
check("non-STRONG near high anti-chase BLOCKS (no carve-out)",
      at.anti_chase_reason(True, weak, regime=RISK_ON) is not None)
check("non-STRONG NOT conviction qualifies",
      at.conviction_itm_qualifies(weak, regime=RISK_ON) is False)

# 4. RSI None never fails closed: None vs healthy both pass the RSI sub-check.
r_none = row(symbol="N", rsi=None, off_hod=0.01)
r_ok = row(symbol="H", rsi=60.0, off_hod=0.01)
check("RSI None established_trend == healthy RSI established_trend",
      at.established_trend(r_none, regime=RISK_ON) == at.established_trend(r_ok, regime=RISK_ON) == True)
check("RSI None conviction == healthy RSI conviction (both TRUE)",
      at.conviction_itm_qualifies(r_none, regime=RISK_ON) ==
      at.conviction_itm_qualifies(r_ok, regime=RISK_ON) == True)
check("RSI None anti-chase == healthy RSI anti-chase (both not blocked)",
      (at.anti_chase_reason(True, r_none, regime=RISK_ON) is None) ==
      (at.anti_chase_reason(True, r_ok, regime=RISK_ON) is None) == True)

# 5. Hard-catalyst path still qualifies even without clean structure.
cat = {"symbol": "CAT", "signal": "BULL", "day_pct": 1.0, "vwap_ext": -0.01,
       "rsi": 50.0, "off_hod": 0.05, "off_lod": 0.05, "event_catalyst": True}
check("hard-catalyst (event_catalyst) qualifies",
      at.conviction_itm_qualifies(cat) is True)
cat2 = {"symbol": "CAT2", "signal": "BULL", "day_pct": 1.0, "vwap_ext": -0.01,
        "rsi": 50.0, "off_hod": 0.05, "off_lod": 0.05}
ev = {"ride": {"CAT2": {"dir": "long"}}}
check("hard-catalyst (event_state RIDE) qualifies",
      at.conviction_itm_qualifies(cat2, event_state=ev) is True)

# 6. Direction / tape sanity: a STRONG_BULL on a risk_off tape is NOT with-tape -> not trend.
against_tape = row(symbol="AGN", rsi=None)
check("STRONG_BULL on risk_off tape NOT established_trend",
      at.established_trend(against_tape, regime=RISK_OFF) is False)
check("neutral tape: STRONG_BULL IS established_trend",
      at.established_trend(against_tape, regime=NEUTRAL) is True)

# 7. SHORT side: STRONG_BEAR pullback (off_lod ~1%), RSI None, below VWAP, risk_off.
shrt = {"symbol": "S", "signal": "STRONG_BEAR", "day_pct": -6.0,
        "vwap_ext": -0.02, "rsi": None, "off_hod": 0.05, "off_lod": 0.01}
check("STRONG_BEAR pullback conviction qualifies",
      at.conviction_itm_qualifies(shrt, regime=RISK_OFF) is True)
check("STRONG_BEAR pullback anti-chase (short) OK",
      at.anti_chase_reason(False, shrt, regime=RISK_OFF) is None)
# short vertical spike: far below VWAP -> blocked
shrt_spike = {"symbol": "SS", "signal": "STRONG_BEAR", "day_pct": -10.0,
              "vwap_ext": -0.09, "rsi": None, "off_hod": 0.05, "off_lod": 0.001}
check("STRONG_BEAR vertical spike anti-chase BLOCKS",
      at.anti_chase_reason(False, shrt_spike, regime=RISK_OFF) is not None)

# 8. Deeper pullback beyond band (2.5% off high) still qualifies for anti-chase
#    (not a chase) but does NOT qualify conviction-ITM (outside the ~2% entry band).
deep = row(symbol="DEEP", rsi=None, off_hod=0.025)
check("deep pullback (2.5% off) conviction NOT qualifies",
      at.conviction_itm_qualifies(deep, regime=RISK_ON) is False)
check("deep pullback anti-chase not blocked (not at top)",
      at.anti_chase_reason(True, deep, regime=RISK_ON) is None)

ok = all(c for _, c in passed)
print("\nALL GREEN" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
