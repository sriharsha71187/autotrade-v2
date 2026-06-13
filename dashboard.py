#!/usr/bin/env python3
"""
dashboard.py — local, read-only "glass cockpit" for AutoTrade.

Serves a single auto-refreshing page at http://127.0.0.1:8787 rendering live state
from data the engine ALREADY writes each cycle (snapshots / state / outcomes /
learnings) plus a couple of live Alpaca reads. It NEVER trades, never mutates engine
state, and binds to localhost ONLY (it shows account data — keep it off the network).

Run:  ~/autotrade/venv/bin/python3 ~/autotrade/dashboard.py
"""
from __future__ import annotations

import glob
import json
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, send_file

import config as cfg
import autotrade as at
try:
    import regime as regime_mod
except Exception:
    regime_mod = None

HOST, PORT = "127.0.0.1", 8787
HERE = Path(__file__).resolve().parent
app = Flask(__name__)

# --- tiny TTL cache so a 5s page poll doesn't hammer the Alpaca API ----------
_cache: dict = {}


def _cached(key, ttl, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (now, val)
    return val


# intraday equity samples gathered while the dashboard is open (live curve)
_equity_samples: list = []
_eq_lock = threading.Lock()


def _tc():
    return _cached("tc", 3600, at.trading_client)


def _account():
    def f():
        a = _tc().get_account()
        eq, le = float(a.equity), float(a.last_equity)
        return {"equity": eq, "last_equity": le, "cash": float(a.cash),
                "day_pl": eq - le, "day_pl_pct": (eq - le) / le * 100 if le else 0.0}
    try:
        return _cached("account", 4, f)
    except Exception as e:
        return {"error": str(e)[:200]}


def _positions():
    try:
        return _cached("positions", 4, lambda: at.open_positions(_tc()))
    except Exception:
        return []


def _stats(state):
    try:
        return _cached("stats", 30, lambda: at.honest_trade_stats(_tc(), state))
    except Exception:
        return {}


def _today_records():
    files = sorted(glob.glob(str(cfg.SNAPSHOT_DIR / "*.jsonl")))
    if not files:
        return []
    out = []
    for ln in Path(files[-1]).read_text().splitlines():
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


def _decision_stream(records, n=30):
    rows = []
    for r in records[-n:]:
        d = r.get("decision") or {}
        res = r.get("result") or {}
        try:
            und = d.get("symbol") or at._decision_underlying(d) or ""
        except Exception:
            und = d.get("symbol") or ""
        rows.append({
            "t": (r.get("t") or "")[11:19],
            "action": d.get("action"),
            "symbol": und,
            "status": res.get("status"),
            "reason": res.get("reason") or "",
            "conviction": d.get("conviction"),
            "reasoning": (d.get("reasoning") or "")[:240],
        })
    rows.reverse()  # newest first
    return rows


def _regime(records, vix):
    if not regime_mod or not records:
        return None
    try:
        return regime_mod.summary(regime_mod.classify(records[-1].get("scan") or [], vix))
    except Exception:
        return None


def _equity_curve(live_equity):
    daily = []
    for fp in sorted(glob.glob(str(cfg.OUTCOMES_DIR / "*.json"))):
        try:
            o = json.loads(Path(fp).read_text())
            if o.get("equity_end") is not None:
                daily.append({"day": o.get("day"), "equity": round(float(o["equity_end"]), 2)})
        except Exception:
            pass
    intraday = []
    if live_equity is not None:
        with _eq_lock:
            _equity_samples.append((time.time(), round(float(live_equity), 2)))
            del _equity_samples[:-300]
            intraday = [{"t": time.strftime("%H:%M:%S", time.localtime(ts)), "equity": eq}
                        for ts, eq in _equity_samples]
    return {"daily": daily, "intraday": intraday}


@app.route("/api/state")
def api_state():
    state = at.load_state()
    records = _today_records()
    acct = _account()
    vix = records[-1].get("vix") if records else None
    counts: dict = {}
    for r in records:
        s = (r.get("result") or {}).get("status")
        counts[s] = counts.get(s, 0) + 1
    stats = _stats(state)
    in_use = state.get("last_model_used") or cfg.CLAUDE_MODEL
    eq = acct.get("equity") if isinstance(acct, dict) else None
    return jsonify({
        "now": at.et_now().strftime("%Y-%m-%d %H:%M:%S ET"),
        "account": acct,
        "model": {"preferred": cfg.CLAUDE_MODEL, "fallback": cfg.CLAUDE_FALLBACK_MODEL,
                  "in_use": in_use, "on_fallback": in_use != cfg.CLAUDE_MODEL},
        "flags": {"halted": bool(state.get("halted")),
                  "loss_override": bool(state.get("loss_override")),
                  "focus": state.get("focus")},
        "regime": _regime(records, vix),
        "vix": vix,
        "positions": _positions(),
        "tracked": {"spreads": state.get("active_multileg") or [],
                    "options": state.get("active_options") or []},
        "decisions": _decision_stream(records),
        "counts": counts,
        "by_strategy": stats.get("by_strategy", {}),
        "day_realized_pl": stats.get("day_realized_pl"),
        "scan": (records[-1].get("scan") if records else []) or [],
        "learnings": at.active_learnings_for_context(),
        "equity_curve": _equity_curve(eq),
        "last_cycle_at": state.get("last_cycle_at"),
    })


@app.route("/")
def index():
    return send_file(HERE / "dashboard.html")


if __name__ == "__main__":
    print(f"AutoTrade dashboard -> http://{HOST}:{PORT}   (localhost only; Ctrl-C to stop)")
    app.run(host=HOST, port=PORT, debug=False)
