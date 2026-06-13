#!/usr/bin/env python3
"""
autotrade.py — single-entry autonomous paper-trading bot.

Usage:
    python3 autotrade.py cycle          # one 5-min trading cycle (what launchd runs)
    python3 autotrade.py eod            # end-of-day LEARNING pass (LLM rules; run manually)
    python3 autotrade.py outcomes       # end-of-day OUTCOME capture (read-only, no LLM)
    python3 autotrade.py growth         # run/inspect the long-term growth sleeve
    python3 autotrade.py overnight      # run/inspect the overnight-drift book
    python3 autotrade.py cycle --dry-run  # build context + decide, but place NO orders
    python3 autotrade.py status         # print current state to terminal

Design principles (from the rebuild spec):
  - Bracket orders are the primary stock exit. Once placed, we DO NOT touch them.
    There is no cancel_stale_orders here — that bug is gone by construction.
  - Today's signal scan drives ticker choice. Historical stats are shown neutrally
    (no "best ticker" label) so the model never treats NVDA as a standing order.
  - Claude is called at most once per cycle, and only when a rule-based pre-filter
    says a setup genuinely qualifies (saves cost + improves decision quality).
  - Every guardrail (loss halt, notional cap, time windows, cooldown) is enforced
    in code here, not delegated to the model.
"""

from __future__ import annotations  # lets 3.10-style type hints run on Python 3.9

import sys
import json
import time
import math
import fcntl
import traceback
from pathlib import Path
from datetime import datetime, timedelta, timezone

import warnings
warnings.filterwarnings("ignore", message=".*OpenSSL.*")

import requests
try:
    import anthropic
    _ANTHROPIC_OK = True
except Exception:
    _ANTHROPIC_OK = False

import config as cfg

# Third-party trading/data SDKs. Imported lazily-tolerant so `status` still works
# even if something isn't installed yet.
try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        MarketOrderRequest, LimitOrderRequest,
        TakeProfitRequest, StopLossRequest, OptionLegRequest,
        GetOrdersRequest, ReplaceOrderRequest,
    )
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, QueryOrderStatus
    from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest, OptionChainRequest
    from alpaca.data.timeframe import TimeFrame
    _ALPACA_OK = True
except Exception as _e:  # noqa
    _ALPACA_OK = False
    _ALPACA_ERR = str(_e)

try:
    import yfinance as yf
    _YF_OK = True
except Exception:
    _YF_OK = False

# US Eastern with automatic DST handling. The old hardcoded -4 offset was wrong
# from ~Nov to ~Mar (EST is -5), which silently shifted the options/condor time
# windows by an hour. zoneinfo is stdlib on 3.9+; fall back to fixed EST if absent.
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = timezone(timedelta(hours=-5))


# ===========================================================================
# Logging
# ===========================================================================
def log(msg: str):
    line = f"{datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S ET')} | {msg}"
    print(line, flush=True)
    try:
        with open(cfg.LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ===========================================================================
# State (single JSON file; survives between cycles)
# ===========================================================================
def default_state() -> dict:
    return {
        "trading_day": "",          # YYYY-MM-DD the state belongs to
        "halted": False,            # STOP command or loss-halt sets this True
        "start_equity": None,       # equity at first cycle of the day (None = not set yet)
        "start_hold_books_value": None,  # day-open mark of the hold-through books, to net their drift out of the intraday loss halt
        "last_entry_time": {},      # {symbol: iso-timestamp} for cooldown
        "active_multileg": [],      # [{legs,qty,entry_net,opened}] spreads/condors we manage
        "active_options": [],       # [{symbol, qty, entry, opened}] long options we manage
        "aborted_today": [],        # symbols deliberately aborted (1 abort/symbol/day)
        "entries_today": {},        # {underlying: spread/condor submit count} — per-name daily cap
        "stopped_today": {},        # {underlying: iso lock-time} stopped/cut at a loss — cooldown before re-entry
        "loss_override": False,     # OVERRIDE command: keep trading past the daily loss halt (today only)
        "last_cycle_at": None,      # iso-ts of the last real cycle (for cadence throttle)
        "last_action": "",          # human-readable summary of last cycle action
        "focus": None,              # e.g. "TECH" set via Telegram FOCUS command
        "telegram_offset": 0,       # last processed Telegram update_id
        "growth_sleeve": [],        # long-term growth holdings (managed by growth_sleeve.py)
        "growth": {},               # sleeve bookkeeping (open-equity, pending cash, rotation week)
        "overnight_reconciled": "", # YYYY-MM-DD we last swept stray overnight stocks (once/day)
        "overnight": {},            # overnight-drift book (holding + buy/sell day markers)
        "tail_hedge": {},           # tail-hedge book (long OTM-put holding, multi-day)
        "earnings": {},             # earnings IV-crush book (overnight condor holding)
        "gap_fade": {},             # gap-fade book (intraday; once-per-day latch)
        "realized_ledger": {},      # {YYYY-MM-DD: {strategy_label: realized_pl}} for code-closed books
    }


def load_state() -> dict:
    today = datetime.now(ET).strftime("%Y-%m-%d")
    if cfg.STATE_FILE.exists():
        try:
            s = json.loads(cfg.STATE_FILE.read_text())
        except Exception:
            s = default_state()
    else:
        s = default_state()
    # New trading day -> reset the per-day fields but keep telegram offset AND any
    # still-open positions we manage (an overnight option/condor must stay tracked).
    if s.get("trading_day") != today:
        fresh = default_state()
        fresh["telegram_offset"] = s.get("telegram_offset", 0)
        fresh["active_multileg"] = s.get("active_multileg", []) or []
        fresh["active_options"] = s.get("active_options", []) or []
        # The growth sleeve is a MULTI-DAY book — carry it (and its bookkeeping)
        # across the daily reset so overnight holdings stay tracked and managed.
        fresh["growth_sleeve"] = s.get("growth_sleeve", []) or []
        fresh["growth"] = s.get("growth", {}) or {}
        # The overnight-drift book holds a position across the daily reset (buy at the
        # prior close, sell at this open), so carry it and its bookkeeping. The
        # realized ledger is keyed by day — keep it so attribution has history.
        fresh["overnight"] = s.get("overnight", {}) or {}
        # Tail-hedge (long OTM puts) and earnings (overnight condor) hold option
        # positions across the daily reset — carry them so they stay tracked/managed.
        # gap_fade is intraday: let it reset fresh each day (new once-per-day latch).
        fresh["tail_hedge"] = s.get("tail_hedge", {}) or {}
        fresh["earnings"] = s.get("earnings", {}) or {}
        fresh["realized_ledger"] = s.get("realized_ledger", {}) or {}
        # Migrate any legacy single-condor field into the multileg list.
        legacy = s.get("active_condor")
        if legacy:
            fresh["active_multileg"].append(
                {"legs": legacy.get("legs", []), "qty": 1,
                 "entry_net": float(legacy.get("net_credit", 0)),
                 "opened": legacy.get("opened")})
        fresh["trading_day"] = today
        s = fresh
    return s


def save_state(s: dict):
    s["trading_day"] = s.get("trading_day") or datetime.now(ET).strftime("%Y-%m-%d")
    cfg.STATE_FILE.write_text(json.dumps(s, indent=2, default=str))


# ===========================================================================
# Clients
# ===========================================================================
def trading_client() -> "TradingClient":
    return TradingClient(cfg.ALPACA_API_KEY, cfg.ALPACA_SECRET_KEY, paper=cfg.PAPER)


def stock_data_client() -> "StockHistoricalDataClient":
    return StockHistoricalDataClient(cfg.ALPACA_API_KEY, cfg.ALPACA_SECRET_KEY)


def option_data_client() -> "OptionHistoricalDataClient":
    return OptionHistoricalDataClient(cfg.ALPACA_API_KEY, cfg.ALPACA_SECRET_KEY)


# ===========================================================================
# Telegram (dormant until TELEGRAM_TOKEN is set)
# ===========================================================================
def tg_enabled() -> bool:
    return bool(cfg.TELEGRAM_TOKEN and cfg.TELEGRAM_CHAT_ID)


def tg_send(text: str):
    if not tg_enabled():
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": cfg.TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as e:
        log(f"telegram send failed: {e}")


def tg_send_long(text: str, limit: int = 3500):
    """Send a long message as multiple Telegram messages (the API caps a single
    message at 4096 chars). Splits on line boundaries so a rule is never cut
    mid-sentence; a single over-long line is hard-split as a last resort."""
    lines, chunk = (text or "").split("\n"), ""
    for ln in lines:
        if len(chunk) + len(ln) + 1 > limit and chunk:
            tg_send(chunk)
            chunk = ""
        while len(ln) > limit:                 # one pathological line longer than a whole chunk
            tg_send(ln[:limit])
            ln = ln[limit:]
        chunk = ln if not chunk else f"{chunk}\n{ln}"
    if chunk:
        tg_send(chunk)


def _normalize_cmd(text: str) -> str:
    """Canonicalize a raw command. Telegram clients send slash-commands
    (`/status`) and may append the bot handle (`/status@my_bot`); strip both so
    `/status`, `/STATUS`, and a plain `STATUS` all map to the same command."""
    t = (text or "").strip()
    if t.startswith("/"):
        t = t[1:]
    head, sep, rest = t.partition(" ")
    if "@" in head:                      # drop a trailing @botname on the verb
        head = head.split("@", 1)[0]
    return (head + sep + rest).strip().upper()


def tg_poll_commands(state: dict) -> list[str]:
    """Return any new command words (STOP, RESUME, CLOSE ALL, STATUS, STATS,
    FOCUS X). Also reads the file fallback ~/autotrade_command.txt."""
    cmds = []
    # File fallback (works even with no Telegram)
    if cfg.COMMAND_FILE.exists():
        txt = cfg.COMMAND_FILE.read_text().strip()
        if txt:
            cmds.append(_normalize_cmd(txt))
            cfg.COMMAND_FILE.write_text("")  # consume it
    if tg_enabled():
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}/getUpdates",
                params={"offset": state.get("telegram_offset", 0) + 1, "timeout": 0},
                timeout=10,
            ).json()
            for upd in r.get("result", []):
                state["telegram_offset"] = upd["update_id"]
                msg = (upd.get("message") or {}).get("text", "")
                if msg:
                    cmds.append(_normalize_cmd(msg))
        except Exception as e:
            log(f"telegram poll failed: {e}")
    return cmds


# ===========================================================================
# Market hours
# ===========================================================================
def is_market_open(tc) -> bool:
    """Authoritative check via Alpaca clock (handles holidays/half-days). Retries a
    few times before assuming closed — a transient network blip (e.g. Wi-Fi still
    reconnecting right after wake) should not make the bot skip a live cycle."""
    last_err = None
    for attempt in range(3):
        try:
            return bool(tc.get_clock().is_open)
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(2 * (attempt + 1))  # 2s, then 4s backoff
    log(f"clock check failed after retries, assuming closed: {last_err}")
    return False


def et_now() -> datetime:
    return datetime.now(ET)


# ===========================================================================
# Data gathering
# ===========================================================================
def account_snapshot(tc) -> dict:
    a = tc.get_account()
    return {
        "equity": float(a.equity),
        "cash": float(a.cash),
        "buying_power": float(a.buying_power),
        "positions_value": float(a.long_market_value or 0) + float(a.short_market_value or 0),
        "daytrade_count": int(getattr(a, "daytrade_count", 0) or 0),
    }


def open_positions(tc) -> list[dict]:
    out = []
    for p in tc.get_all_positions():
        out.append({
            "symbol": p.symbol,
            "qty": float(p.qty),
            "side": str(p.side),
            "avg_entry": float(p.avg_entry_price),
            "current": float(p.current_price or 0),
            "unrealized_pl": float(p.unrealized_pl or 0),
            "asset_class": str(getattr(p, "asset_class", "")),
        })
    return out


def bracketed_symbols(tc) -> set:
    """Stock symbols whose shares are tied up in open bracket orders. Such a stock
    exits ONLY via its stop/target — a manual close is rejected ('insufficient qty,
    held_for_orders'), so we neither attempt it nor let the model request it.
    Detected as open orders on a plain (non-OCC) ticker; option/MLEG legs excluded."""
    try:
        oo = tc.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100))
        return {o.symbol for o in oo if o.symbol and not parse_occ(o.symbol)}
    except Exception as e:
        log(f"bracketed_symbols fetch failed: {e}")
        return set()


def directional_counts(positions) -> dict:
    """Open positions by market direction, for the correlation cap (don't put the
    whole book on one directional bet). Long stock / long call = bullish; short
    stock / long put = bearish. (A neutral condor's legs net out, so it only ever
    errs toward caution.)"""
    bull = bear = 0
    for p in positions:
        meta = parse_occ(p.get("symbol", ""))
        if meta:
            bull, bear = (bull + 1, bear) if meta["type"] == "call" else (bull, bear + 1)
        elif "SHORT" in str(p.get("side", "")).upper():
            bear += 1
        else:
            bull += 1
    return {"bull": bull, "bear": bear}


def abort_position(tc, state, decision, now, dry) -> dict:
    """Deliberate early exit of a bracketed stock (thesis invalidated). GATED to
    avoid the 5-min flip-flop: high conviction, held >= cooldown, once per symbol
    per day. On go: cancel the symbol's open (bracket) orders, then market-close.
    If the close fails AFTER cancelling, the position is unprotected -> alert loud."""
    sym = decision.get("symbol")
    conv = (decision.get("conviction") or "").lower()
    last = state.get("last_entry_time", {}).get(sym) if sym else None
    held_min = (now - datetime.fromisoformat(last)).total_seconds() / 60 if last else None
    denied = []
    if not sym:
        denied.append("no symbol")
    if conv != "high":
        denied.append("conviction not high")
    # Anti-flip-flop cooldown applies only when we KNOW the position is fresh. If no
    # entry-time was recorded (held_min is None — e.g. an option leg the engine never
    # registered in last_entry_time), an unknown-age position must NOT be permanently
    # un-abortable: a high-conviction, broken-thesis exit has to be able to fire. The
    # once-per-symbol-per-day cap below still prevents spamming. (This bug vetoed the
    # SMCI exit ~30× on 6/10 with "held None < 10m".)
    if held_min is not None and held_min < cfg.TICKER_COOLDOWN_MIN:
        denied.append(f"held {round(held_min)}m < {cfg.TICKER_COOLDOWN_MIN}m")
    if sym in (state.get("aborted_today") or []):
        denied.append("already aborted today")
    if denied:
        log(f"abort {sym} denied: {denied}")
        return {"status": "abort_denied", "symbol": sym, "reasons": denied}
    if dry:
        log(f"[DRY] would ABORT {sym} (cancel bracket + market-close)")
        return {"status": "dry_run", "action": "abort", "symbol": sym}
    try:
        # Cancel any resting order touching this symbol before closing. For a stock
        # that's its bracket (stop+target); for an option leg it's the working order
        # that would otherwise block the close as a wash trade. Either way the close
        # can't fire while an opposite-side order rests on the symbol.
        for o in tc.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=200)):
            osyms = {o.symbol} | {getattr(l, "symbol", None)
                                  for l in (getattr(o, "legs", None) or [])}
            if sym in osyms:
                tc.cancel_order_by_id(o.id)
        time.sleep(1.0)                       # let the cancels settle so shares free up
        tc.close_position(sym)
        log(f"ABORTED {sym}: bracket canceled + position closed")
        tg_send(f"⛔ Aborted {sym} (thesis invalidated).")
        state.setdefault("aborted_today", []).append(sym)
        return {"status": "aborted", "symbol": sym}
    except Exception as e:
        log(f"ABORT {sym} FAILED after cancel — POSITION MAY BE UNPROTECTED: {e}")
        tg_send(f"⚠️ ABORT {sym} close FAILED — check the position now! {e}")
        return {"status": "abort_failed", "symbol": sym, "error": str(e)}


def asset_meta(tc) -> dict:
    """{symbol: {"shortable": bool, "fractionable": bool}} for tradable US equities.
    Cached to a file once per ET day (the full asset list is ~14k rows)."""
    today = et_now().strftime("%Y-%m-%d")
    if cfg.ASSETS_CACHE.exists():
        try:
            c = json.loads(cfg.ASSETS_CACHE.read_text())
            if c.get("date") == today and c.get("assets"):
                return c["assets"]
        except Exception:
            pass
    out = {}
    try:
        from alpaca.trading.requests import GetAssetsRequest
        from alpaca.trading.enums import AssetClass, AssetStatus
        assets = tc.get_all_assets(GetAssetsRequest(
            asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE))
        for a in assets:
            if getattr(a, "tradable", False):
                out[a.symbol] = {"shortable": bool(getattr(a, "shortable", False)),
                                 "fractionable": bool(getattr(a, "fractionable", False))}
        cfg.ASSETS_CACHE.write_text(json.dumps({"date": today, "assets": out}, default=str))
    except Exception as e:
        log(f"asset metadata fetch failed: {e}")
    return out


def screener_symbols(tc) -> list[str]:
    """Live market screen: most-actives ∪ top gainers ∪ top losers, returned as a
    symbol list (tradable + price-filtered). Empty on failure (caller falls back)."""
    if not cfg.SCREENER_ENABLED:
        return []
    H = {"APCA-API-KEY-ID": cfg.ALPACA_API_KEY, "APCA-API-SECRET-KEY": cfg.ALPACA_SECRET_KEY}
    syms = []
    try:
        r = requests.get("https://data.alpaca.markets/v1beta1/screener/stocks/most-actives",
                         headers=H, params={"by": "volume", "top": cfg.SCREENER_TOP_ACTIVES},
                         timeout=10).json()
        syms += [a.get("symbol") for a in r.get("most_actives", []) if a.get("symbol")]
    except Exception as e:
        log(f"most-actives fetch failed: {e}")
    try:
        r = requests.get("https://data.alpaca.markets/v1beta1/screener/stocks/movers",
                         headers=H, params={"top": cfg.SCREENER_TOP_MOVERS}, timeout=10).json()
        for side in ("gainers", "losers"):
            for m in r.get(side, []):
                # movers include sub-$5 warrants/penny junk; drop by their own price field.
                if m.get("symbol") and float(m.get("price") or 0) >= cfg.MIN_PRICE:
                    syms.append(m["symbol"])
    except Exception as e:
        log(f"movers fetch failed: {e}")
    return syms


def build_universe(tc) -> tuple[list[str], dict]:
    """Assemble this cycle's scan universe: core indices + base watchlist + live
    screener, deduped, restricted to tradable assets, capped to UNIVERSE_MAX.
    Returns (symbols, asset_meta) — meta carries the shortable flags."""
    meta = asset_meta(tc)
    base = list(cfg.CORE_UNIVERSE) + list(cfg.MOMENTUM_UNIVERSE)
    universe, seen = [], set()
    for s in base + screener_symbols(tc):
        if not s or s in seen:
            continue
        if s in cfg.LEVERAGED_ETF_EXCLUDE:   # quality floor: no leveraged/inverse ETFs
            continue
        # Indices/ETFs in the base list may not appear in the equity asset map; keep
        # the base names regardless, but require screener names to be tradable.
        if s not in cfg.CORE_UNIVERSE and meta and s not in meta:
            continue
        seen.add(s)
        universe.append(s)
        if len(universe) >= cfg.UNIVERSE_MAX:
            break
    return universe, meta


def _intraday_indicators(idf, last) -> dict:
    """Anti-chase indicators from a 1-min OHLCV frame: VWAP extension, % off the
    day's high/low, intraday RSI(14) on 5-min closes, and move from the open.
    Each is None on any failure so callers never break / never falsely block."""
    out = {"vwap_ext": None, "off_hod": None, "off_lod": None,
           "rsi": None, "from_open": None}
    try:
        h = idf["High"].astype(float)
        l = idf["Low"].astype(float)
        c = idf["Close"].astype(float).dropna()
        v = idf["Volume"].astype(float).fillna(0)
        if len(c) < 5:
            return out
        typ = (h + l + c) / 3.0
        cumv = v.cumsum()
        vwap = (typ * v).cumsum() / cumv.replace(0, float("nan"))
        vwap_now = float(vwap.dropna().iloc[-1])
        if vwap_now > 0:
            out["vwap_ext"] = round((last - vwap_now) / vwap_now, 4)
        hod, lod = float(h.max()), float(l.min())
        if hod > 0:
            out["off_hod"] = round((hod - last) / hod, 4)
        if lod > 0:
            out["off_lod"] = round((last - lod) / lod, 4)
        op = float(idf["Open"].astype(float).dropna().iloc[0])
        if op > 0:
            out["from_open"] = round((last - op) / op * 100, 2)
        try:
            c5 = c.resample("5min").last().dropna()
        except Exception:
            c5 = c
        if len(c5) >= 15:
            delta = c5.diff().dropna()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, 1e-9)
            r = float((100 - 100 / (1 + rs)).iloc[-1])
            if r == r:  # not NaN
                out["rsi"] = round(r, 1)
    except Exception:
        pass
    return out


def signal_scan(symbols: list[str]) -> list[dict]:
    """Rank tickers by today's % move using yfinance (batched). Returns list of
    {symbol, last, day_pct, signal} sorted by absolute move. 'signal' is a coarse
    label the model can use; it is NOT a trade order.

    'last' is the live intraday price (1-min bars) when available so the % move
    reflects the real move so far today, not a stale daily close. 'prev_close' is
    the prior *completed* daily close. Falls back to daily data if intraday is
    missing (pre/post market)."""
    if not _YF_OK:
        return []
    out = []
    try:
        # 5 daily bars -> reliable prior completed close at iloc[-2].
        daily = yf.download(symbols, period="5d", interval="1d",
                            progress=False, group_by="ticker", threads=True)
    except Exception as e:
        log(f"signal scan daily download failed: {e}")
        return []
    # Intraday 1-min bars for the live price. Best-effort; tolerate failure.
    intraday = None
    try:
        intraday = yf.download(symbols, period="1d", interval="1m",
                               progress=False, group_by="ticker", threads=True)
    except Exception as e:
        log(f"signal scan intraday download failed (using daily): {e}")
    for s in symbols:
        try:
            df = daily[s] if len(symbols) > 1 else daily
            if df is None or df.empty:
                continue
            dclose = df["Close"].dropna()
            if len(dclose) < 2:
                continue
            prev_close = float(dclose.iloc[-2])  # prior completed day
            if prev_close <= 0:
                continue
            last = None
            idf = None
            if intraday is not None:
                try:
                    idf = intraday[s] if len(symbols) > 1 else intraday
                    iclose = idf["Close"].dropna()
                    if len(iclose):
                        last = float(iclose.iloc[-1])
                except Exception:
                    last, idf = None, None
            if last is None:
                last = float(dclose.iloc[-1])  # fallback: latest daily
            pct = (last - prev_close) / prev_close * 100.0
            if pct >= 2:
                sig = "STRONG_BULL"
            elif pct >= 0.75:
                sig = "BULL"
            elif pct <= -2:
                sig = "STRONG_BEAR"
            elif pct <= -0.75:
                sig = "BEAR"
            else:
                sig = "NEUTRAL"
            row = {"symbol": s, "last": round(last, 2),
                   "day_pct": round(pct, 2), "signal": sig}
            if idf is not None:
                row.update(_intraday_indicators(idf, last))  # vwap_ext, off_hod, off_lod, rsi, from_open
            out.append(row)
        except Exception:
            continue
    out.sort(key=lambda r: abs(r["day_pct"]), reverse=True)
    return out


def rsi(symbol: str, period: int = 14) -> float | None:
    if not _YF_OK:
        return None
    try:
        df = yf.download(symbol, period="1mo", interval="1d", progress=False)
        if getattr(df.columns, "nlevels", 1) > 1:
            df.columns = df.columns.get_level_values(0)
        close = df["Close"].dropna()
        if len(close) < period + 1:
            return None
        delta = close.diff().dropna()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, 1e-9)
        return float((100 - 100 / (1 + rs)).iloc[-1])
    except Exception:
        return None


def breaking_news(symbols: list[str], limit: int = 8) -> list[dict]:
    """Alpaca v1beta1 news. Headlines only — no article bodies (copyright)."""
    try:
        r = requests.get(
            "https://data.alpaca.markets/v1beta1/news",
            headers={"APCA-API-KEY-ID": cfg.ALPACA_API_KEY,
                     "APCA-API-SECRET-KEY": cfg.ALPACA_SECRET_KEY},
            params={"symbols": ",".join(symbols[:20]), "limit": limit},
            timeout=10,
        ).json()
        return [{"symbol": ",".join(n.get("symbols", [])),
                 "headline": n.get("headline", ""),
                 "at": n.get("created_at", "")} for n in r.get("news", [])]
    except Exception as e:
        log(f"news fetch failed: {e}")
        return []


def fear_greed() -> str:
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=10).json()
        d = r["data"][0]
        return f"{d['value']} ({d['value_classification']})"
    except Exception:
        return "unknown"


def get_vix() -> float | None:
    if not _YF_OK:
        return None
    try:
        df = yf.download("^VIX", period="2d", interval="1d", progress=False)
        if getattr(df.columns, "nlevels", 1) > 1:
            df.columns = df.columns.get_level_values(0)
        return float(df["Close"].iloc[-1])
    except Exception:
        return None


def parse_occ(sym: str) -> dict | None:
    """Parse an OCC option symbol into {underlying, expiry 'YYYY-MM-DD', type,
    strike}. The last 15 chars are YYMMDD + C/P + 8-digit strike(×1000); the
    underlying is whatever precedes them. Returns None if it isn't OCC-shaped."""
    try:
        if not sym or len(sym) < 16:
            return None
        body = sym[-15:]
        und = sym[:-15]
        cp = body[6]
        if not und or cp not in ("C", "P") or not body[:6].isdigit() or not body[7:].isdigit():
            return None
        yy, mm, dd = body[0:2], body[2:4], body[4:6]
        return {"underlying": und, "expiry": f"20{yy}-{mm}-{dd}",
                "type": "call" if cp == "C" else "put", "strike": int(body[7:]) / 1000.0}
    except Exception:
        return None


def _quote_mid(quote, allow_one_sided: bool = False) -> float | None:
    """Mid of a quote, or None when there's no usable price. By default requires a
    two-sided quote (both bid and ask > 0); one-sided is allowed only for sizing."""
    if quote is None:
        return None
    bid = float(getattr(quote, "bid_price", 0) or 0)
    ask = float(getattr(quote, "ask_price", 0) or 0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    if allow_one_sided:
        return ask or bid or None
    return None


def option_latest_quote(odc, symbol):
    """Return the latest quote object for one option symbol, or None."""
    if not symbol or odc is None:
        return None
    try:
        from alpaca.data.requests import OptionLatestQuoteRequest
        q = odc.get_option_latest_quote(OptionLatestQuoteRequest(symbol_or_symbols=[symbol]))
        return q.get(symbol)
    except Exception as e:
        log(f"option quote fetch failed for {symbol}: {e}")
        return None


def option_price(odc, symbol) -> float | None:
    """Latest option price per share (mid, else one-sided) for notional sizing."""
    return _quote_mid(option_latest_quote(odc, symbol), allow_one_sided=True)


def option_quote(odc, symbol):
    """(bid, ask, mid) for an option, or (None, None, None). Used to size a
    marketable-limit entry and to reject wide-spread (illiquid) contracts."""
    q = option_latest_quote(odc, symbol)
    if q is None:
        return (None, None, None)
    bid = float(getattr(q, "bid_price", 0) or 0)
    ask = float(getattr(q, "ask_price", 0) or 0)
    if bid > 0 and ask > 0:
        return (bid, ask, (bid + ask) / 2)
    return (bid or None, ask or None, ask or bid or None)


def option_chain_for(odc, underlying, spot, want_today_expiry: bool = False,
                     strike_pct: float = 0.08, max_per_side: int = 10) -> list[dict]:
    """Compact near-the-money chain for ONE underlying so the model picks real
    contracts instead of inventing OCC symbols. Returns a list of
    {symbol, type, strike, expiry, bid, ask, mid} for a single target expiry
    (today's if want_today_expiry and available, else the nearest upcoming).
    Returns [] on any failure — callers treat 'no chain' as 'do not trade options'."""
    if odc is None or not spot or spot <= 0:
        return []
    lo = round(spot * (1 - strike_pct), 2)
    hi = round(spot * (1 + strike_pct), 2)
    from alpaca.data.requests import OptionChainRequest
    # Alpaca's option-chain endpoint intermittently returns empty mid-cycle; retry
    # a couple times before giving up so a transient blip isn't a blank "no options".
    chain = None
    for attempt in range(3):
        try:
            chain = odc.get_option_chain(OptionChainRequest(
                underlying_symbol=underlying, strike_price_gte=lo, strike_price_lte=hi))
            if chain:
                break
        except Exception as e:
            if attempt == 2:
                log(f"option chain fetch failed for {underlying}: {e}")
        if attempt < 2:
            time.sleep(0.6)
    if not chain:
        return []
    today = et_now().date().isoformat()
    rows = []
    for sym, snap in (chain or {}).items():
        meta = parse_occ(sym)
        if not meta:
            continue
        q = getattr(snap, "latest_quote", None)
        bid = float(getattr(q, "bid_price", 0) or 0) if q else 0.0
        ask = float(getattr(q, "ask_price", 0) or 0) if q else 0.0
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else (ask or bid or 0.0)
        rows.append({"symbol": sym, "type": meta["type"], "strike": meta["strike"],
                     "expiry": meta["expiry"], "bid": round(bid, 2),
                     "ask": round(ask, 2), "mid": round(mid, 2)})
    expiries = sorted({r["expiry"] for r in rows if r["expiry"] >= today})
    if not expiries:
        return []
    target = today if (want_today_expiry and today in expiries) else expiries[0]
    rows = [r for r in rows if r["expiry"] == target]
    calls = sorted([r for r in rows if r["type"] == "call"],
                   key=lambda r: abs(r["strike"] - spot))[:max_per_side]
    puts = sorted([r for r in rows if r["type"] == "put"],
                  key=lambda r: abs(r["strike"] - spot))[:max_per_side]
    return sorted(calls + puts, key=lambda r: (r["type"], r["strike"]))


def build_option_chains(odc, scan, positions, vix, now,
                        max_underlyings: int = 3) -> tuple[dict, set]:
    """Decide which underlyings could plausibly trade options THIS cycle and fetch
    a compact chain for each. Returns (chains_by_underlying, offered_symbol_set).
    Mirrors setup_qualifies' option windows so we don't fetch chains we can't use."""
    spot_of = {r["symbol"]: r["last"] for r in scan}
    targets = []  # (underlying, spot, want_today_expiry)
    h, m = now.hour, now.minute

    # Iron condor: calm core index in the 10:00–10:30 window, VIX under ceiling.
    if h == 10 and m <= 30 and (vix is None or vix < cfg.VIX_CONDOR_CEILING):
        for r in scan:
            if r["symbol"] in cfg.CORE_UNIVERSE and abs(r["day_pct"]) < 0.5:
                targets.append((r["symbol"], r["last"], True))
                break
    # Momentum options: strong movers during 10:00–14:00. PREFER movers that have
    # pulled back (so the anti-chase would actually ALLOW a directional option on
    # them — a put on a name at its lows, or a call at its highs, gets blocked), and
    # offer several candidates so a non-optionable name doesn't starve the rest. Fall
    # back to the biggest movers so the chain is never blank when a tape is moving.
    if 10 <= h < 14:
        movers = [r for r in scan
                  if abs(r["day_pct"]) >= 2.0 and r["symbol"] not in cfg.BLACKLIST]
        pulled = [r for r in movers
                  if anti_chase_reason(r["day_pct"] > 0, r) is None]   # entry would be allowed
        for r in (pulled or movers)[:5]:
            targets.append((r["symbol"], r["last"], False))
    # Any option we already hold, so the model can choose to size a close.
    for p in positions:
        if "option" in (p.get("asset_class", "") or "").lower():
            meta = parse_occ(p["symbol"])
            if meta:
                targets.append((meta["underlying"],
                                spot_of.get(meta["underlying"]) or p.get("current"), False))

    chains, offered = {}, set()
    seen, attempts = set(), 0
    for und, spot, today_exp in targets:
        # Stop at max_underlyings SUCCESSFUL chains (not attempts), but cap total
        # fetches so a run of non-optionable names can't blow up latency.
        if not und or und in seen or len(chains) >= max_underlyings or attempts >= 6:
            continue
        seen.add(und)
        attempts += 1
        rows = option_chain_for(odc, und, spot, want_today_expiry=today_exp)
        if rows:
            chains[und] = rows
            offered.update(r["symbol"] for r in rows)
    return chains, offered


def record_strategy_realized(state: dict, label: str, amount: float):
    """Add realized P&L to today's strategy ledger. Used by the code-closed books
    (growth sleeve, overnight drift) whose round-trips span multiple days and so
    can't be reconstructed from a single day's matched fills."""
    if not amount:
        return
    day = et_now().strftime("%Y-%m-%d")
    ledger = state.setdefault("realized_ledger", {}).setdefault(day, {})
    ledger[label] = round(ledger.get(label, 0.0) + float(amount), 2)


def _decision_strategy(d: dict) -> str:
    """Canonical strategy label for a model decision (the bucket its P&L belongs to)."""
    a = d.get("action")
    if a == "buy_stock":
        return "stock_short" if d.get("direction") == "short" else "stock_long"
    if a == "buy_option":
        return "long_option"
    if a == "iron_condor":
        return "iron_condor"
    if a == "multi_leg":
        net = d.get("net_price")
        if net is None:
            return "multi_leg"
        return "credit_spread" if float(net) >= 0 else "debit_spread"
    return a or "unknown"


def _decision_broker_symbols(d: dict) -> set:
    """The broker symbols (underlying or OCC option legs) an entry decision touches —
    the keys its fills will appear under, so realized P&L can be joined to a strategy."""
    a = d.get("action")
    syms = set()
    if a == "buy_stock" and d.get("symbol"):
        syms.add(d["symbol"])
    elif a == "buy_option" and d.get("option_symbol"):
        syms.add(d["option_symbol"])
    elif a == "multi_leg":
        for leg in (d.get("legs") or []):
            if leg.get("symbol"):
                syms.add(leg["symbol"])
    elif a == "iron_condor":
        for leg in (d.get("condor_legs") or []):
            if leg.get("symbol"):
                syms.add(leg["symbol"])
    return syms


def _strategy_map_for_day(day: str) -> dict:
    """broker_symbol -> strategy label, built from the day's SUBMITTED entry decisions
    in the snapshot log. Last submit wins (per-day attribution is best-effort if a
    symbol was traded by two different strategies the same day)."""
    f = cfg.SNAPSHOT_DIR / f"{day}.jsonl"
    m = {}
    if not f.exists():
        return m
    for line in f.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if (rec.get("result") or {}).get("status") != "submitted":
            continue
        d = rec.get("decision") or {}
        strat = _decision_strategy(d)
        for sym in _decision_broker_symbols(d):
            m[sym] = strat
    return m


def attribute_by_strategy(by_symbol: dict, day: str, state: dict | None) -> dict:
    """Group per-symbol matched realized P&L into strategy buckets, then fold in the
    code-closed books' realized from the ledger. Symbols with no mapped strategy and
    not owned by a book fall under 'unattributed'."""
    smap = _strategy_map_for_day(day)
    out = {}
    for sym, d in (by_symbol or {}).items():
        pl = d.get("realized_pl", 0.0)
        if not pl:
            continue
        label = smap.get(sym, "unattributed")
        out[label] = round(out.get(label, 0.0) + pl, 2)
    # Books record their own (often cross-day) realized into the ledger.
    for label, amt in ((state or {}).get("realized_ledger", {}).get(day, {}) or {}).items():
        out[label] = round(out.get(label, 0.0) + amt, 2)
    return out


def _fill_legs(o):
    """Yield the symbol-bearing fill objects of a closed order. A multi-leg/complex
    PARENT order carries NO symbol (o.symbol is None) — the OCC symbols live on its
    .legs — yet it still reports a filled_qty. Iterating raw orders therefore created
    a phantom None/'' symbol bucket with a real qty (the 6/12 'null symbol, 13 shares'
    data-integrity bug). Expand a symbol-less parent into its legs so every fill is
    attributed to a real symbol; drop a parent with neither symbol nor legs."""
    if getattr(o, "symbol", None):
        yield o
        return
    for leg in (getattr(o, "legs", None) or []):
        if getattr(leg, "symbol", None):
            yield leg


def honest_trade_stats(tc, state=None) -> dict:
    """Realized P&L from today's fills, grouped by symbol. Counts ONLY matched
    round-trips (min of buy vs sell qty) so an OPEN position contributes ~0
    realized — not its full notional. The old net-cash-flow proxy reported an
    open short's sell-to-open proceeds (e.g. +$35k) as if it were profit, which
    read as a phantom giant 'win'; matched realized P&L can't do that."""
    try:
        req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=200)
        orders = tc.get_orders(filter=req)
    except Exception as e:
        log(f"stats fetch failed: {e}")
        return {}
    today = et_now().date()
    # Accumulate buy/sell qty + notional per symbol from today's fills.
    agg = {}
    for parent in orders:
        if not parent.filled_at or parent.filled_at.astimezone(ET).date() != today:
            continue
        for o in _fill_legs(parent):        # expand a symbol-less multi-leg parent into its legs
            sym = o.symbol
            qty = float(o.filled_qty or 0)
            price = float(o.filled_avg_price or 0)
            if not sym or qty <= 0 or price <= 0:
                continue
            d = agg.setdefault(sym, {"buy_qty": 0.0, "buy_notional": 0.0,
                                     "sell_qty": 0.0, "sell_notional": 0.0, "fills": 0})
            if str(o.side) == "OrderSide.SELL":
                d["sell_qty"] += qty
                d["sell_notional"] += qty * price
            else:
                d["buy_qty"] += qty
                d["buy_notional"] += qty * price
            d["fills"] += 1
    by_symbol = {}
    total_realized = 0.0
    for sym, d in agg.items():
        mult = 100 if parse_occ(sym) else 1   # options trade in 100-share contracts
        matched = min(d["buy_qty"], d["sell_qty"])
        realized = 0.0
        if matched > 0:
            avg_buy = d["buy_notional"] / d["buy_qty"]
            avg_sell = d["sell_notional"] / d["sell_qty"]
            realized = matched * (avg_sell - avg_buy) * mult  # works long or short
        open_qty = (d["buy_qty"] - d["sell_qty"])  # >0 net long, <0 net short, 0 flat
        by_symbol[sym] = {
            "realized_pl": round(realized, 2),
            "open_qty": round(open_qty, 4),
            "fills": d["fills"],
        }
        total_realized += realized
    day = et_now().strftime("%Y-%m-%d")
    by_strategy = attribute_by_strategy(by_symbol, day, state)
    return {"note": "realized_pl = today's MATCHED round-trips only (open_qty!=0 "
                    "means the position is still open and NOT yet in realized_pl). "
                    "by_strategy attributes realized P&L to the strategy that opened "
                    "each name. Historical edge applies ONLY when the live scan agrees.",
            "day_realized_pl": round(total_realized, 2),
            "by_strategy": by_strategy,
            "by_symbol": by_symbol}


def _learn_slug(rule: str) -> str:
    """Stable id for a learning from its rule text (first words, kebab). Lets a rule be
    tracked across nightly rewrites even as wording drifts slightly."""
    import re
    words = re.sub(r"[^a-z0-9 ]", "", (rule or "").lower()).split()
    return "-".join(words[:6]) or "rule"


_ACTIVE_STATUSES = ("active", "tentative", "contested")


def _normalize_learning(d: dict) -> dict:
    """Coerce any learning (legacy {rule,evidence,tentative,date} or new) into the full
    evidence-weighted shape. Idempotent — safe to run on already-normalized rules."""
    rule = (d.get("rule") or "").strip()
    status = d.get("status")
    if status not in ("active", "tentative", "contested", "retired"):
        status = "tentative" if d.get("tentative") else "active"
    return {
        "id": d.get("id") or _learn_slug(rule),
        "rule": rule,
        "evidence": (d.get("evidence") or "").strip(),
        "scope": d.get("scope") or None,                 # regime/condition it applies in (None = always)
        "status": status,
        "confirmations": int(d.get("confirmations") or (0 if status == "retired" else 1)),
        "refutations": int(d.get("refutations") or 0),
        "since": d.get("since") or d.get("date") or "",
        "date": d.get("date") or "",
        "supersedes": d.get("supersedes") or None,
        "superseded_by": d.get("superseded_by") or None,
        "last_flip": d.get("last_flip") or None,         # code-managed; not in the model schema
    }


def load_learnings() -> list[dict]:
    if cfg.LEARNINGS_FILE.exists():
        try:
            raw = json.loads(cfg.LEARNINGS_FILE.read_text())
            return [_normalize_learning(d) for d in raw]
        except Exception:
            return []
    return []


def active_learnings_for_context() -> list[dict]:
    """The standing rulebook the live decision loop should see: retired rules excluded
    (they no longer apply), compacted to the fields that guide a decision. Scope and
    status let the model weight a contested or regime-conditional rule appropriately."""
    out = []
    for l in load_learnings():
        if l.get("status") == "retired":
            continue
        out.append({"rule": l["rule"], "scope": l.get("scope"),
                    "status": l.get("status"), "evidence": l.get("evidence"),
                    "confirmations": l.get("confirmations", 1)})
    return out


def _learn_days_between(d1: str, d2: str) -> int:
    """Whole days between two ISO dates; a large number if either can't be parsed (so a
    missing/garbled date never falsely triggers the hysteresis lock)."""
    try:
        a = datetime.fromisoformat(d1).date()
        b = datetime.fromisoformat(d2).date()
        return abs((b - a).days)
    except Exception:
        return 999


def _learn_clamp_step(old: int, new: int) -> int:
    """Limit how far a confirmation/refutation count can move in one session (no
    fabricated jumps), floored at 0."""
    step = cfg.LEARNING_MAX_COUNT_STEP
    if new > old:
        return max(0, min(new, old + step))
    if new < old:
        return max(0, max(new, old - step))
    return max(0, new)


def _is_status_flip(old_status: str, new_status: str) -> bool:
    """A 'flip' subject to hysteresis = retiring an active rule, or reviving a retired
    one. contested<->active is NOT a flip — that's just evidence accumulating, which the
    design wants to flow freely (contested is the holding state before a real reversal)."""
    return ((old_status in _ACTIVE_STATUSES and new_status == "retired")
            or (old_status == "retired" and new_status in _ACTIVE_STATUSES))


def reconcile_learnings(existing: list[dict], proposed: list[dict], today: str):
    """Merge the model's proposed rulebook onto the standing one under the
    evidence-weighted + hysteresis policy. CODE enforces the mechanical invariants the
    model can't be trusted to (clock preservation, count clamping, hysteresis lock,
    silent-delete guard, retired-TTL pruning); the model already did the semantic work
    (regime-scope vs reversal vs noise). Returns (final_list, flip_events) where
    flip_events feeds the Telegram contradiction alert."""
    prior = {l["id"]: l for l in (existing or [])}
    seen, final, flips = set(), [], []
    for p in (proposed or []):
        p = _normalize_learning(p)
        pid = p["id"]
        if pid in seen:                      # dedupe a model that emitted the same id twice
            continue
        seen.add(pid)
        e = prior.get(pid)
        if e:
            p["since"] = e.get("since") or p.get("since") or today
            p["last_flip"] = e.get("last_flip")
            p["confirmations"] = _learn_clamp_step(e.get("confirmations", 0), p.get("confirmations", 0))
            p["refutations"] = _learn_clamp_step(e.get("refutations", 0), p.get("refutations", 0))
            if _is_status_flip(e["status"], p["status"]):
                locked = (e.get("last_flip")
                          and _learn_days_between(e["last_flip"], today) < cfg.LEARNING_HYSTERESIS_DAYS)
                if locked:
                    # Just flipped — hold the incumbent; one day can't thrash it back.
                    p["status"], p["rule"] = e["status"], e.get("rule", p["rule"])
                    p["superseded_by"] = e.get("superseded_by")
                    flips.append(("blocked", e, p))
                elif p["status"] == "retired":
                    # Retire only when the refutations actually out-evidence the
                    # confirmations by the required edge; otherwise it's merely CONTESTED.
                    if (p.get("refutations", 0) - p.get("confirmations", 0)) >= cfg.LEARNING_FLIP_MIN_EDGE:
                        p["last_flip"] = today
                        flips.append(("retired", e, p))
                    else:
                        p["status"] = "contested"
                        flips.append(("contested", e, p))
                else:                         # reviving a retired rule — allow, stamp the flip
                    p["last_flip"] = today
                    flips.append(("revived", e, p))
        else:                                 # brand-new rule — first seen today, by definition
            p["since"] = today
            p["last_flip"] = None
            p["confirmations"] = min(p.get("confirmations", 1) or 1, 1)
            p["refutations"] = 0
        if not p["date"]:
            p["date"] = today
        final.append(p)
    # Carry-forward guard: any incumbent the model simply omitted is kept — recency must
    # not erase accumulated evidence by omission. An omitted ACTIVE rule is flagged
    # (kept_omitted); an omitted RETIRED rule is carried silently so its last_flip history
    # survives for hysteresis (the TTL prune below still ages it out eventually).
    for e in (existing or []):
        if e["id"] not in seen:
            final.append(e)
            if e.get("status") in _ACTIVE_STATUSES:
                flips.append(("kept_omitted", e, e))
    # Prune long-retired rules so the file stays bounded (history served its purpose).
    final = [l for l in final
             if l.get("status") != "retired"
             or _learn_days_between(l.get("last_flip") or l.get("date") or "", today) <= 30]
    return final, flips


# ===========================================================================
# Pre-filter — decide whether a cycle is worth calling Claude for
# ===========================================================================
def setup_qualifies(state, scan, positions, vix, now) -> tuple[bool, str]:
    # Always call Claude if we hold something that may need managing.
    if positions or state.get("active_multileg") or state.get("active_options"):
        return True, "open positions/condor to manage"
    h, m = now.hour, now.minute
    mins = h * 60 + m
    # Momentum debit spreads run 10:00 until the (later) momentum cutoff, so an
    # afternoon breakout still triggers a model call even with no open position.
    in_momentum_window = 10 * 60 <= mins < (cfg.MOMENTUM_OPTION_CUTOFF_HOUR * 60
                                            + cfg.MOMENTUM_OPTION_CUTOFF_MIN)
    in_condor_window = (h == 10 and 0 <= m <= 30)
    # Momentum: a strong single-name mover, OR a trending broad tape, during the window.
    if in_momentum_window:
        for r in scan:
            if abs(r["day_pct"]) >= cfg.QUALIFY_MOMENTUM_PCT and r["symbol"] not in cfg.BLACKLIST:
                return True, f"momentum setup: {r['symbol']} {r['day_pct']:+.1f}%"
        # A trending tape is itself a participate-with-it setup — don't sit out a clean
        # orderly trend just because no single name has hit the momentum threshold.
        idx = [r["day_pct"] for r in scan if r["symbol"] in cfg.CORE_UNIVERSE
               and r.get("day_pct") is not None]
        if idx and abs(sum(idx) / len(idx)) >= cfg.QUALIFY_TAPE_PCT:
            return True, f"trending tape {sum(idx)/len(idx):+.1f}% — participate with it"
    # Condor: range-bound index, calm VIX, in the 10:00–10:30 window.
    if in_condor_window and (vix is None or vix < cfg.VIX_CONDOR_CEILING):
        calm = [r for r in scan if r["symbol"] in cfg.CORE_UNIVERSE
                and abs(r["day_pct"]) < 0.5]
        if calm:
            return True, "condor window, calm index"
    return False, "no qualifying setup — holding cash"


# ===========================================================================
# Claude call
# ===========================================================================
def _parse_model_json(text: str):
    """Extract a JSON value from a model response.

    Claude sometimes ignores the "JSON only" instruction and wraps the value in
    prose and/or a ```json code fence. Stripping the fence markers alone leaves
    the prose, so a plain json.loads fails at 'line 1 column 1'. Here we try the
    whole string first (the happy path), then fall back to the first balanced
    {...} or [...] span, ignoring braces that appear inside strings.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Scan for every balanced {...} / [...] span (ignoring brackets inside
    # strings) and return the first one that actually parses. This skips stray
    # braces in the prose preamble, e.g. "{this}", that aren't valid JSON.
    start = None
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            if depth == 0:
                start = i
            depth += 1
        elif ch in "}]":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        start = None
    raise ValueError("no JSON value found in model response")


_LAST_API_ALERT_AT = None   # throttle for the "engine down" Telegram alert
_LAST_TAPE_ALERT_AT = None  # throttle for the "fighting the tape" self-diagnostic alert
_ANTHROPIC_CLIENT = None

# Strict JSON schema for structured outputs — the API CONSTRAINS the model to emit
# exactly this shape, so a malformed / prose / truncated-into-garbage response is
# impossible (that whole "no JSON value found" failure class is gone). All objects use
# additionalProperties:false; conditionally-unused fields are nullable, matching how the
# model already emits the full object with nulls.
_LEG_SCHEMA = {"type": "object", "additionalProperties": False,
               "properties": {"symbol": {"type": "string"}, "side": {"type": "string"}},
               "required": ["symbol", "side"]}
_DECISION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "action": {"type": "string",
                   "enum": ["hold", "buy_stock", "buy_option", "multi_leg",
                            "iron_condor", "close", "abort"]},
        "symbol": {"type": ["string", "null"]},
        "direction": {"type": ["string", "null"]},
        "option_symbol": {"type": ["string", "null"]},
        "qty": {"type": ["integer", "null"]},
        "stop_price": {"type": ["number", "null"]},
        "target_price": {"type": ["number", "null"]},
        "legs": {"type": ["array", "null"], "items": _LEG_SCHEMA},
        "net_price": {"type": ["number", "null"]},
        "condor_legs": {"type": ["array", "null"], "items": _LEG_SCHEMA},
        "net_credit": {"type": ["number", "null"]},
        "close_symbols": {"type": "array", "items": {"type": "string"}},
        "overnight_hold": {"type": "boolean"},
        "conviction": {"type": "string", "enum": ["low", "medium", "high"]},
        "reasoning": {"type": "string"},
    },
    "required": ["action", "symbol", "direction", "option_symbol", "qty", "stop_price",
                 "target_price", "legs", "net_price", "condor_legs", "net_credit",
                 "close_symbols", "overnight_hold", "conviction", "reasoning"],
}


def _anthropic_client():
    global _ANTHROPIC_CLIENT
    if _ANTHROPIC_CLIENT is None:
        _ANTHROPIC_CLIENT = anthropic.Anthropic(api_key=cfg.ANTHROPIC_API_KEY, timeout=90.0)
    return _ANTHROPIC_CLIENT


def call_claude(context: dict) -> dict:
    system_prompt = Path(__file__).with_name("alpaca_system_prompt.txt").read_text()
    user_msg = (
        "Here is the current market + account context as JSON. Decide the single "
        "best action for this 5-minute cycle.\n\n"
        f"{json.dumps(context, indent=2, default=str)}"
    )
    last_err = None
    for attempt in range(2):  # one retry — a wake-up timeout shouldn't force a hold
        try:
            r = _anthropic_client().messages.create(
                model=cfg.CLAUDE_MODEL, max_tokens=cfg.CLAUDE_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_msg}],
                # Structured outputs: the response text is GUARANTEED to be JSON
                # matching _DECISION_SCHEMA (or it's a refusal / max_tokens, handled
                # below) — no more malformed-output parse failures.
                output_config={"format": {"type": "json_schema", "schema": _DECISION_SCHEMA}},
            )
            # fable-5 can decline via a safety classifier (HTTP 200, refusal); and it
            # reasons before answering, so a too-small budget truncates the answer.
            # Name both explicitly so they surface (and alert) instead of masquerading
            # as a parse bug.
            if r.stop_reason == "refusal":
                cat = getattr(getattr(r, "stop_details", None), "category", None)
                raise RuntimeError(f"API model refusal (category={cat})")
            if r.stop_reason == "max_tokens":
                raise RuntimeError("model truncated at max_tokens "
                                   "(raise CLAUDE_MAX_TOKENS)")
            text = "".join(b.text for b in r.content if b.type == "text")
            return _parse_model_json(text)   # structured output -> this never fails now
        except anthropic.APIStatusError as e:
            # Billing/auth/rate-limit/bad-model — a real API failure (the 6/11 credit
            # outage). Carry the typed reason so the outage alert fires.
            last_err = RuntimeError(f"API {getattr(e, 'type', 'error')}: "
                                    f"{getattr(e, 'message', str(e))}")
            if attempt == 0:
                time.sleep(3)
        except Exception as e:
            last_err = e
            if attempt == 0:
                time.sleep(3)
    msg = f"claude call failed after retries: {last_err}"
    log(msg)
    # An API-level failure (billing, auth, rate-limit, bad model) is an OUTAGE: the
    # engine is blind until it's fixed. Alert loudly — but throttle so a multi-hour
    # outage doesn't spam Telegram every cycle.
    es = str(last_err)
    if any(k in es for k in ("API ", "credit", "authentication", "rate", "model:", "max_tokens", "truncat")):
        global _LAST_API_ALERT_AT
        _now = et_now()
        if (_LAST_API_ALERT_AT is None
                or (_now - _LAST_API_ALERT_AT).total_seconds() > 1800):
            tg_send(f"🚨 DECISION ENGINE DOWN — model calls failing: {es[:300]}")
            _LAST_API_ALERT_AT = _now
    return {"action": "hold", "reasoning": f"claude error: {last_err}",
            "close_symbols": [], "conviction": "low"}


# ===========================================================================
# Order execution
# ===========================================================================
def place_stock_bracket(tc, symbol, qty, side, stop_price, target_price, dry, ref=None):
    # Uncapped: widen the take-profit to a FAR level so the win isn't capped — the trailing
    # chandelier stop (manage_stops) becomes the real exit and a runner can run. The model's
    # target was only used for the bracket-orientation check in passes_guardrails.
    if cfg.STOCK_UNCAPPED and ref:
        f = cfg.STOCK_UNCAPPED_TARGET_PCT
        target_price = round(ref * (1 + f), 2) if side == "buy" else round(ref * (1 - f), 2)
    if dry:
        log(f"[DRY] would BRACKET {side} {qty} {symbol} stop={stop_price} tp={target_price}")
        return None
    req = MarketOrderRequest(
        symbol=symbol, qty=qty,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=round(float(target_price), 2)),
        stop_loss=StopLossRequest(stop_price=round(float(stop_price), 2)),
    )
    o = tc.submit_order(order_data=req)
    # Log the BRACKET's actual prices, never the model's raw stop/target.
    log(f"BRACKET {side} {qty} {symbol} | stop={stop_price} target={target_price} | id={o.id}")
    tg_send(f"📈 Entered {qty} {symbol} ({side}). Stop {stop_price}, target {target_price}.")
    return o


def place_multi_leg(tc, legs, net_price, qty, dry):
    """Generic 2–4 leg option order (verticals, straddles, condors, …).
    legs: [{symbol, side: 'buy'|'sell', ratio?}]. net_price is SIGNED per share:
    positive = net credit you collect, negative = net debit you pay. We submit a
    limit at -net_price (credit -> negative limit, debit -> positive limit)."""
    kind = "credit" if net_price >= 0 else "debit"
    if dry:
        log(f"[DRY] would MULTILEG {kind} qty={qty} net={net_price} "
            f"legs={[(l['symbol'], l['side']) for l in legs]}")
        return None
    order_legs = [
        OptionLegRequest(
            symbol=l["symbol"],
            side=OrderSide.SELL if l["side"] == "sell" else OrderSide.BUY,
            ratio_qty=int(l.get("ratio", 1)),
        ) for l in legs
    ]
    req = LimitOrderRequest(
        qty=int(qty), order_class=OrderClass.MLEG, time_in_force=TimeInForce.DAY,
        legs=order_legs, limit_price=round(-float(net_price), 2),
    )
    o = tc.submit_order(order_data=req)
    log(f"MULTILEG {kind} submitted | net≈{net_price} qty={qty} | "
        f"legs={[l['symbol'] for l in legs]} | id={o.id}")
    tg_send(f"🧩 {kind.title()} spread placed (~${abs(net_price)} {kind}).")
    return o


def place_iron_condor(tc, legs, net_credit, dry):
    """Thin wrapper: an iron condor is a 4-leg credit structure."""
    return place_multi_leg(tc, legs, abs(float(net_credit)), 1, dry)


def multileg_risk(legs, net_price, qty) -> float | None:
    """Max loss in $ for a DEFINED-RISK ratio-1 multi-leg, or None if the structure
    has a naked short (undefined risk). Debit: risk = debit paid. Credit: risk =
    (summed per-type strike width − credit). Per-type width is summed (conservative;
    only one side of a condor can breach, so this over-states risk = safe)."""
    width = 0.0
    for typ in ("call", "put"):
        grp = [l for l in legs if (parse_occ(l["symbol"]) or {}).get("type") == typ]
        if not grp:
            continue
        sells = sum(1 for l in grp if l["side"] == "sell")
        buys = sum(1 for l in grp if l["side"] == "buy")
        if sells > buys:
            return None  # naked short of this type -> undefined risk
        strikes = [parse_occ(l["symbol"])["strike"] for l in grp]
        width += (max(strikes) - min(strikes))
    qty = max(1, int(qty))
    if net_price < 0:                      # debit spread: can't lose more than debit
        return abs(net_price) * 100 * qty
    return max(0.0, width - net_price) * 100 * qty


def _lock_name_today(state, underlying, why):
    """Lock a single name out for STOPPED_COOLDOWN_MIN after a trade on it was closed at a
    LOSS (a code stop, or the model cutting a broken thesis). Stops re-losing the same idea
    on the same name (the 6/12 ADBE/RDW churn) without killing the name for the whole day —
    a clean later setup on a two-way name is fine once the cooldown passes. Index ETFs are
    exempt (the premium books legitimately re-use them)."""
    if not underlying or underlying in cfg.PREMIUM_INDEX_UNDERLYINGS:
        return
    locked = state.setdefault("stopped_today", {})
    if not isinstance(locked, dict):                  # migrate legacy list form
        locked = {n: et_now().isoformat() for n in locked}
        state["stopped_today"] = locked
    locked[underlying] = et_now().isoformat()
    log(f"NAME LOCKED {underlying} for {cfg.STOPPED_COOLDOWN_MIN}m ({why})")
    tg_send(f"🔒 {underlying} locked {cfg.STOPPED_COOLDOWN_MIN}m ({why}).")


def close_symbols(tc, symbols, dry):
    """Market-close each symbol/leg. Returns the SET of symbols confirmed FLAT (closed
    now, or already gone from the account). Callers MUST only drop tracking for symbols
    in the returned set — a leg that failed to close is still live and has to be
    retried, never silently abandoned. Abandoning it orphans the spread: still open at
    the broker with no code stop/target and invisible to the model."""
    if dry:
        for sym in symbols:
            log(f"[DRY] would CLOSE {sym}")
        return set(symbols)
    # Pass 1: cancel any resting order touching these symbols BEFORE closing. A
    # leftover opposite-side order (e.g. another spread's still-working leg sharing
    # this strike) makes close_position reject with "wash trade detected. use complex
    # orders" — and it never clears on its own, so the close loops every cycle
    # forever (the SMCI tangle on 6/10: 30 rejects, position never exited). Cancel
    # first, let it settle, then market-close.
    targets = set(symbols)
    try:
        for o in tc.get_orders(filter=GetOrdersRequest(
                status=QueryOrderStatus.OPEN, limit=200)):
            osyms = {o.symbol} | {getattr(l, "symbol", None)
                                  for l in (getattr(o, "legs", None) or [])}
            if targets & osyms:
                tc.cancel_order_by_id(o.id)
    except Exception as ce:
        log(f"pre-close cancel failed: {ce}")
    time.sleep(1.0)                       # let the cancels settle so the close isn't blocked
    # Pass 2: market-close each leg. Track which legs are confirmed flat.
    flat = set()
    for sym in symbols:
        try:
            tc.close_position(sym)
            log(f"CLOSED {sym}")
            tg_send(f"✅ Closed {sym}.")
            flat.add(sym)
        except Exception as e:
            es = str(e)
            # "position not found" (40410000) = the leg is already gone -> flat.
            if "position not found" in es or "40410000" in es:
                flat.add(sym)
            else:
                log(f"close {sym} failed: {e}")
    return flat


# ===========================================================================
# Multi-leg management (code-enforced stop/target for every spread/condor)
# ===========================================================================
def manage_multileg(tc, odc, state, dry):
    """Mark each open multi-leg to market and apply stop/target. P&L = entry_net −
    cost_to_close (both $). Credit structures: target +50% of credit, stop −100%
    of credit (i.e. cost-to-close 2× credit). Debit structures: target +100% of
    debit, stop −50%. Reconciles against the live account so closed/expired
    structures stop being tracked."""
    items = state.get("active_multileg") or []
    if not items:
        return
    from alpaca.data.requests import OptionLatestQuoteRequest
    try:
        held_pos = {p.symbol: p for p in tc.get_all_positions()}
        held = set(held_pos)
    except Exception:
        held_pos, held = {}, None
    still = []
    for pos in items:
        try:
            legs = pos["legs"]
            qty = int(pos.get("qty", 1))
            symbols = [l["symbol"] for l in legs]
            # Reconcile the cost basis to the ACTUAL fill once the legs are held. The
            # stored entry_net was the model's INTENDED net_price; a limit can fill at a
            # different net, which would make every stop/target and the displayed P&L
            # wrong from inception. Recompute from each leg's avg_entry_price (+ for a
            # short we collect, − for a long we pay), one time.
            if (held and not pos.get("basis_reconciled")
                    and all(s in held_pos for s in symbols)):
                try:
                    net = 0.0
                    for l in legs:
                        ap = abs(float(held_pos[l["symbol"]].avg_entry_price))
                        net += ap if l["side"] == "sell" else -ap
                    pos["entry_net"] = round(net * 100 * qty, 2)
                    pos["basis_reconciled"] = True
                    log(f"MULTILEG basis reconciled {symbols}: entry_net=${pos['entry_net']:.0f} (actual fill)")
                except Exception as be:
                    log(f"multileg basis reconcile failed {symbols}: {be}")
            entry_net = float(pos["entry_net"])     # $; + credit received, − debit paid
            if held is not None and not any(s in held for s in symbols):
                # Legs aren't positions yet. If the entry limit order is still
                # working, keep tracking it (and cancel if it's gone stale) so a
                # later fill is still managed. Only drop when the order is truly
                # gone (canceled/expired/rejected/filled-then-closed) or absent.
                oid = pos.get("order_id")
                status = ""
                if oid:
                    try:
                        status = str(getattr(tc.get_order_by_id(oid), "status", "")).lower()
                    except Exception:
                        status = ""
                working = any(k in status for k in
                              ("new", "accept", "pending", "partial", "held", "replaced"))
                gone = any(k in status for k in ("cancel", "expired", "reject"))
                age_min = 1e9
                try:
                    age_min = (et_now() - datetime.fromisoformat(pos["opened"])).total_seconds() / 60
                except Exception:
                    pass
                if gone:                                   # order died WITHOUT a fill -> drop
                    log(f"multileg {symbols} order {status or 'gone'} — dropping (no fill)")
                    continue
                if age_min > cfg.MULTILEG_FILL_TIMEOUT_MIN:
                    # Past the fill window, still not showing as held: a working order never
                    # filled (cancel it), or a filled spread has truly left the book
                    # (closed/expired) -> stop tracking.
                    if working:
                        log(f"multileg entry {oid} unfilled {age_min:.0f}m — canceling")
                        try:
                            tc.cancel_order_by_id(oid)
                        except Exception as e:
                            log(f"multileg cancel failed: {e}")
                    else:
                        log(f"multileg {symbols} not held past fill window — dropping")
                    continue
                # WITHIN the fill window: order working, OR filled-but-position-not-synced-
                # yet. KEEP — never orphan a filled spread on a transient position-sync lag
                # (the 6/12 ADBE #2 bug: filled, dropped on a sync cycle, rode to expiry
                # unmanaged while the model saw "open_spreads empty").
                still.append(pos)
                continue
            # EOD force-close: a 0DTE/short spread must not ride into expiration
            # (assignment / pin risk). Close at the same EOD time as single options —
            # UNLESS this is a catalyst-backed overnight momentum hold (pos['overnight'],
            # granted by the conviction gate at entry), which rides into tomorrow and is
            # still stop/target-managed each cycle and re-judged next day.
            _n = et_now()
            _is_eod = (_n.hour > cfg.OPTION_EOD_CLOSE_HOUR or
                       (_n.hour == cfg.OPTION_EOD_CLOSE_HOUR and _n.minute >= cfg.OPTION_EOD_CLOSE_MIN))
            if _is_eod and not pos.get("overnight"):
                log(f"MULTILEG EOD close {symbols}")
                tg_send("🧩 EOD-closing spread.")
                flat = close_symbols(tc, symbols, dry)
                # Only drop tracking when EVERY leg is confirmed flat. A partial/failed
                # close must stay tracked and retry — otherwise the spread is orphaned
                # (live at the broker, no stop/target, invisible to the model).
                if not all(s in flat for s in symbols):
                    log(f"MULTILEG EOD close INCOMPLETE {[s for s in symbols if s not in flat]} — keeping tracked")
                    still.append(pos)
                continue
            q = odc.get_option_latest_quote(OptionLatestQuoteRequest(symbol_or_symbols=symbols))
            cost_to_close = 0.0
            incomplete = False
            for l in legs:
                mid = _quote_mid(q.get(l["symbol"]))
                if mid is None:
                    incomplete = True
                    break
                cost_to_close += mid if l["side"] == "sell" else -mid
            if incomplete:
                still.append(pos)            # can't mark cleanly this cycle; retry next
                continue
            cost_to_close *= 100 * qty
            pl = entry_net - cost_to_close
            # Stash the live net mark + P&L on the spread so the context can show the
            # model the STRUCTURE's economics (not the isolated legs). Without this the
            # model reads a short leg going ITM as a catastrophe and panic-closes a
            # spread that's actually at max profit (the 6/10 SMCI fiasco).
            pos["pl"] = round(pl)
            pos["value_now"] = round(-cost_to_close)   # $ to liquidate the spread now
            reason = None
            if entry_net >= 0:               # credit structure (profit capped at credit)
                if pl <= -1.0 * entry_net:
                    reason = f"stop (P&L ${pl:.0f} on ${entry_net:.0f} credit)"
                elif pl >= 0.5 * entry_net:
                    reason = f"target (P&L ${pl:.0f} on ${entry_net:.0f} credit)"
            else:                            # DEBIT structure — let winners run, trail the peak
                debit = abs(entry_net)
                pf = pl / debit                          # profit as a fraction of the debit
                pos["hw_pf"] = max(pos.get("hw_pf", pf), pf)   # high-water profit fraction
                if pl <= -0.5 * debit:
                    reason = f"stop (P&L ${pl:.0f} on ${debit:.0f} debit)"
                elif pos["hw_pf"] >= cfg.OPTION_TRAIL_ACTIVATE:
                    # Trailing profit-lock (replaces the old hard +100%-of-debit cap):
                    # bank gains a give-back below the peak so a runner isn't sold at 2x.
                    if pf <= pos["hw_pf"] * (1 - cfg.OPTION_TRAIL_GIVEBACK):
                        reason = f"trail (peak {pos['hw_pf']:+.0%} → {pf:+.0%} of debit)"
            if reason:
                log(f"MULTILEG EXIT {symbols}: {reason}")
                tg_send(f"🧩 Closing spread: {reason}.")
                flat = close_symbols(tc, symbols, dry)
                # Stopped out -> lock the name for the day (no re-losing the same idea).
                if reason.startswith("stop") and all(s in flat for s in symbols):
                    _lock_name_today(state, _decision_underlying({"legs": legs}), "spread stopped out")
                # Keep tracking unless every leg confirmed flat (no silent orphan).
                if not all(s in flat for s in symbols):
                    log(f"MULTILEG EXIT INCOMPLETE {[s for s in symbols if s not in flat]} — keeping tracked")
                    still.append(pos)
            else:
                still.append(pos)
        except Exception as e:
            log(f"multileg management error: {e}")
            still.append(pos)
    state["active_multileg"] = still


def open_spreads_for_context(state) -> list:
    """Present the code-managed multi-leg spreads to the model as STRUCTURES — net
    entry, live liquidation value, net P&L, and the management plan — NOT as the
    isolated broker legs that show up in `positions`. A short leg going ITM is normal
    (often MAX PROFIT) for a debit spread; the model must judge the spread's net P&L,
    never one leg. Marks are stamped by manage_multileg each cycle (pl, value_now)."""
    out = []
    for pos in (state.get("active_multileg") or []):
        legs = pos.get("legs") or []
        entry_net = float(pos.get("entry_net", 0.0))   # $ signed: + credit, − debit
        kind = "credit" if entry_net >= 0 else "debit"
        calls = [l for l in legs if (parse_occ(l["symbol"]) or {}).get("type") == "call"]
        puts = [l for l in legs if (parse_occ(l["symbol"]) or {}).get("type") == "put"]
        if len(legs) >= 4 and calls and puts:
            label = "iron condor (credit)"
        elif puts and not calls:
            label = "bear-put DEBIT spread" if kind == "debit" else "bull-put CREDIT spread"
        elif calls and not puts:
            label = "bull-call DEBIT spread" if kind == "debit" else "bear-call CREDIT spread"
        else:
            label = f"{len(legs)}-leg {kind} spread"
        plan = ("target +50% credit / stop −100% credit" if kind == "credit"
                else f"trail peak after +{int(cfg.OPTION_TRAIL_ACTIVATE*100)}% / stop −50% debit")
        out.append({
            "underlying": _decision_underlying({"legs": legs}) or "?",
            "structure": label,
            "qty": int(pos.get("qty", 1)),
            "legs": [f"{l['side']} {(parse_occ(l['symbol']) or {}).get('strike','?')}"
                     f"{((parse_occ(l['symbol']) or {}).get('type','?') or '?')[0].upper()}"
                     for l in legs],
            "entry_net_$": round(entry_net),
            "value_now_$": pos.get("value_now"),
            "net_pl_$": pos.get("pl"),
            "peak_profit_pct_of_debit": (round(pos["hw_pf"] * 100) if kind == "debit"
                                         and pos.get("hw_pf") is not None else None),
            "overnight": bool(pos.get("overnight")),
            "managed_by_code": (f"stop/target auto-enforced ({plan}); "
                                + ("debit winners TRAIL the peak (give-back "
                                   f"{int(cfg.OPTION_TRAIL_GIVEBACK*100)}%); " if kind == "debit" else "")
                                + "EOD-closed 15:45 unless overnight"),
            "opened": pos.get("opened"),
        })
    return out


# ===========================================================================
# Single-leg option management (code-enforced stop/target/EOD, every cycle)
# ===========================================================================
def manage_options(tc, odc, state, dry):
    """Long single-leg options have no bracket, so manage them in code like the
    condor: stop at OPTION_STOP_PCT, target at OPTION_TARGET_PCT, and a hard
    EOD/0DTE close. Also reconciles tracking against the live account so expired
    or already-closed positions stop being tracked."""
    opts = state.get("active_options") or []
    if not opts:
        return
    now = et_now()
    today = now.date().isoformat()
    hard_close = (now.hour > cfg.OPTION_EOD_CLOSE_HOUR or
                  (now.hour == cfg.OPTION_EOD_CLOSE_HOUR and now.minute >= cfg.OPTION_EOD_CLOSE_MIN))
    # What does the broker actually still hold? (None = couldn't tell -> don't drop)
    try:
        held = {p.symbol for p in tc.get_all_positions()}
    except Exception:
        held = None

    still = []
    for o in opts:
        sym = o.get("symbol")
        if not sym:
            continue
        if held is not None and sym not in held:
            # Not a position yet. If the marketable-limit entry is still working,
            # keep tracking (cancel if stale); only drop when the order is gone.
            oid = o.get("order_id")
            status = ""
            if oid:
                try:
                    status = str(getattr(tc.get_order_by_id(oid), "status", "")).lower()
                except Exception:
                    status = ""
            if any(k in status for k in ("new", "accept", "pending", "partial", "held", "replaced")):
                age_min = 1e9
                try:
                    age_min = (now - datetime.fromisoformat(o["opened"])).total_seconds() / 60
                except Exception:
                    pass
                if age_min > cfg.MULTILEG_FILL_TIMEOUT_MIN:
                    log(f"option entry {oid} unfilled {age_min:.0f}m — canceling")
                    try:
                        tc.cancel_order_by_id(oid)
                    except Exception as e:
                        log(f"option cancel failed: {e}")
                    continue
                still.append(o)   # still working; wait for the fill
                continue
            log(f"option {sym} no longer held — dropping from tracking")
            continue  # expired/exercised/closed already
        meta = parse_occ(sym)
        mid = _quote_mid(option_latest_quote(odc, sym))
        reason = None
        if hard_close:
            reason = "0DTE/EOD close" if (meta and meta["expiry"] <= today) else "EOD close"
        elif mid is not None and o.get("entry"):
            pl = (mid - o["entry"]) / o["entry"]
            o["hw_pl"] = max(o.get("hw_pl", pl), pl)      # high-water profit
            if pl <= cfg.OPTION_STOP_PCT:
                reason = f"stop {pl:+.0%}"
            elif o["hw_pl"] >= cfg.OPTION_TRAIL_ACTIVATE:
                # Trailing profit-lock: once a winner, let it run and bank gains a set
                # give-back below the peak (replaces the old hard +100% cap so a runner
                # isn't force-sold at 2x). The model can still take profit earlier.
                trail = o["hw_pl"] * (1 - cfg.OPTION_TRAIL_GIVEBACK)
                if pl <= trail:
                    reason = f"trail (peak {o['hw_pl']:+.0%} → {pl:+.0%})"
        if reason:
            log(f"OPTION EXIT {sym}: {reason}")
            tg_send(f"📊 Exiting {sym} ({reason}).")
            flat = close_symbols(tc, [sym], dry)
            if sym in flat and reason.startswith("stop"):
                _lock_name_today(state, (meta or {}).get("underlying"), "long option stopped out")
            if sym not in flat:              # close failed -> keep tracking, retry (no orphan)
                log(f"OPTION EXIT INCOMPLETE {sym} — keeping tracked")
                still.append(o)
        else:
            still.append(o)
    state["active_options"] = still


def reconcile_overnight_stocks(tc, state, dry, skip=None):
    """Safety net for a MISSED EOD flatten. This is an intraday bot — stock DAY
    brackets die at the close, so no non-sleeve stock should ever survive into a
    new trading day. But flatten_stocks_eod only runs if the bot is actually awake
    at 15:50 ET; if the Mac sleeps through the close (as on 2026-06-04, which left
    an RDW long to ride overnight and gap down) a position carries over UNPROTECTED
    once its DAY bracket has expired. On the first cycle of each new day, flatten
    any non-sleeve stock left open from a prior session, then latch a per-day flag
    so this runs at most once daily. `skip`: growth-sleeve symbols (held overnight
    by design)."""
    today = et_now().strftime("%Y-%m-%d")
    if state.get("overnight_reconciled") == today:
        return
    skip = skip or set()
    try:
        positions = tc.get_all_positions()
    except Exception as e:
        log(f"overnight reconcile: positions fetch failed: {e}")
        return  # leave the flag unset -> retry next cycle
    # On the first cycle of the day nothing intraday has been opened yet, so any
    # open non-sleeve, non-option position is by definition a prior-day carryover.
    stray = [p for p in positions
             if "option" not in str(getattr(p, "asset_class", "")).lower()
             and p.symbol not in skip]
    failed = False
    for p in stray:
        sym = p.symbol
        if dry:
            log(f"[DRY] would reconcile (flatten) stray overnight stock {sym}")
            continue
        try:
            for o in tc.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=200)):
                if o.symbol == sym and not parse_occ(o.symbol):
                    tc.cancel_order_by_id(o.id)
            time.sleep(0.5)
            tc.close_position(sym)
            log(f"OVERNIGHT RECONCILE: flattened stray stock {sym} (carried from a prior day)")
            tg_send(f"🧹 Reconciled stray overnight stock {sym} (missed EOD flatten) — closed.")
        except Exception as e:
            log(f"overnight reconcile {sym} failed: {e}")
            failed = True  # don't latch -> retry the stragglers next cycle

    # Also force-close any TRACKED spread / long option carried from a PRIOR day that
    # is NOT a deliberate overnight hold. It should have been EOD-closed yesterday, but
    # if the close was missed (Mac asleep) it rides today — a 0DTE/short-DTE expiring
    # ITM means assignment. reconcile only sweeps STOCKS above; options need this.
    def _prior_day(ts):
        try:
            return datetime.fromisoformat(ts).date().isoformat() < today
        except Exception:
            return False
    ml_keep = []
    for pos in (state.get("active_multileg") or []):
        syms = [l["symbol"] for l in (pos.get("legs") or [])]
        if pos.get("overnight") or not _prior_day(pos.get("opened", "")) or not syms:
            ml_keep.append(pos)
            continue
        if dry:
            log(f"[DRY] would reconcile stray prior-day spread {syms}")
            ml_keep.append(pos)
            continue
        flat = close_symbols(tc, syms, dry)
        if all(s in flat for s in syms):
            log(f"OVERNIGHT RECONCILE: closed stray prior-day spread {syms} (missed EOD close)")
            tg_send(f"🧹 Reconciled stray overnight spread {syms} (missed EOD close).")
        else:
            ml_keep.append(pos)
            failed = True
    state["active_multileg"] = ml_keep
    opt_keep = []
    for o in (state.get("active_options") or []):
        sym = o.get("symbol")
        if o.get("overnight") or not _prior_day(o.get("opened", "")) or not sym:
            opt_keep.append(o)
            continue
        if dry:
            log(f"[DRY] would reconcile stray prior-day option {sym}")
            opt_keep.append(o)
            continue
        flat = close_symbols(tc, [sym], dry)
        if sym in flat:
            log(f"OVERNIGHT RECONCILE: closed stray prior-day option {sym} (missed EOD close)")
            tg_send(f"🧹 Reconciled stray overnight option {sym} (missed EOD close).")
        else:
            opt_keep.append(o)
            failed = True
    state["active_options"] = opt_keep

    # Latch only on a clean pass (dry runs never mutate state).
    if not dry and not failed:
        state["overnight_reconciled"] = today


def flatten_stocks_eod(tc, dry, skip=None):
    """At/after 15:50 ET, flatten any open STOCK position (cancel its bracket
    orders, then market-close). DAY bracket legs die at the close, so a stock left
    open overnight would be unprotected — and this is an intraday bot. (True
    overnight stock holds would need GTC brackets; not supported yet.)
    `skip`: symbols to leave alone (the growth sleeve holds these intentionally
    overnight and manages them itself)."""
    skip = skip or set()
    now = et_now()
    if not (now.hour > cfg.OPTION_EOD_CLOSE_HOUR or
            (now.hour == cfg.OPTION_EOD_CLOSE_HOUR and now.minute >= cfg.STOCK_EOD_CLOSE_MIN)):
        return
    try:
        positions = tc.get_all_positions()
    except Exception as e:
        log(f"eod flatten: positions fetch failed: {e}")
        return
    for p in positions:
        if "option" in str(getattr(p, "asset_class", "")).lower():
            continue  # options/spreads handled by their own EOD managers
        if p.symbol in skip:
            continue  # growth-sleeve holding — intentionally held overnight
        sym = p.symbol
        if dry:
            log(f"[DRY] would EOD-flatten stock {sym}")
            continue
        # Verify-and-retry: close_position submits a market order that can PARTIALLY
        # fill (6/12 MSTR: 40 -> 9 shares, the 9 then rode overnight unprotected once
        # its DAY bracket expired). Trusting one close() call leaves a remnant. Re-fetch
        # the position and retry until it's actually flat (or attempts exhausted).
        for attempt in range(cfg.EOD_FLATTEN_RETRIES):
            try:
                for o in tc.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100)):
                    if o.symbol == sym and not parse_occ(o.symbol):
                        tc.cancel_order_by_id(o.id)
                time.sleep(0.5)
                tc.close_position(sym)
                log(f"EOD FLATTEN stock {sym}" + (f" (retry {attempt})" if attempt else ""))
            except Exception as e:
                es = str(e)
                if "position not found" in es or "40410000" in es:
                    break                       # already flat
                log(f"eod flatten {sym} failed: {e}")
            time.sleep(1.0)
            try:
                still = next((q for q in tc.get_all_positions() if q.symbol == sym), None)
            except Exception:
                still = None
            if still is None or abs(float(getattr(still, "qty", 0) or 0)) < 1e-9:
                break                            # confirmed flat
            log(f"eod flatten {sym}: {still.qty} still open after attempt {attempt} — retrying")
        else:
            log(f"eod flatten {sym}: STILL OPEN after {cfg.EOD_FLATTEN_RETRIES} attempts")
            tg_send(f"⚠️ EOD flatten could not fully close {sym} — may ride overnight; "
                    f"morning reconcile will sweep it.")
            continue
        tg_send(f"🌆 EOD-flattened {sym}.")


def sweep_orphan_options(tc, state, dry, skip=None):
    """Belt-and-suspenders against an UNTRACKED option leg riding into expiry.

    manage_multileg / manage_options only close what's in active_multileg /
    active_options. If a filled spread ever falls out of tracking (the 6/12
    ADBE #2 desync: filled, dropped on a sync-lag cycle, then invisible to every
    decision cycle while open_spreads read 'empty'), nothing closes it and a
    0DTE leg rides into expiry → ITM assignment. This sweep is the last line of
    defense: at/after the EOD option-close window, force-close any broker-held
    OPTION position that is NOT in our tracking and NOT a shielded book (tail
    hedge / earnings condor hold their own options overnight by design).

    Independent of HOW the orphan happened — covers desync, a crashed mid-close,
    a manual broker fill — so no option can silently expire on us."""
    now = et_now()
    hard_close = (now.hour > cfg.OPTION_EOD_CLOSE_HOUR or
                  (now.hour == cfg.OPTION_EOD_CLOSE_HOUR and now.minute >= cfg.OPTION_EOD_CLOSE_MIN))
    if not hard_close:
        return
    skip = skip or set()
    # Every option symbol the engine legitimately tracks or a shielded book holds.
    tracked = set(skip)
    for m in (state.get("active_multileg") or []):
        for l in (m.get("legs") or []):
            if l.get("symbol"):
                tracked.add(l["symbol"])
    for o in (state.get("active_options") or []):
        if o.get("symbol"):
            tracked.add(o["symbol"])
    try:
        positions = tc.get_all_positions()
    except Exception as e:
        log(f"orphan sweep: positions fetch failed: {e}")
        return
    orphans = [p.symbol for p in positions
               if "option" in str(getattr(p, "asset_class", "")).lower()
               and p.symbol not in tracked]
    if not orphans:
        return
    log(f"ORPHAN SWEEP: untracked option legs at EOD -> force-closing {orphans}")
    if dry:
        for sym in orphans:
            log(f"[DRY] would orphan-sweep close {sym}")
        return
    flat = close_symbols(tc, orphans, dry)
    closed = [s for s in orphans if s in flat]
    if closed:
        tg_send(f"🧹 EOD orphan sweep: force-closed untracked option leg(s) {closed} "
                f"(were not in active tracking — caught before expiry).")


# ===========================================================================
# Active stock management — trail bracket stops to lock in gains (every cycle)
# ===========================================================================
def manage_stops(tc, dry, skip=None):
    """Scan stock positions every cycle and TRAIL each bracket's stop as the trade
    works (lock breakeven, then ratchet behind price). Modifies the held bracket
    stop leg IN PLACE (replace), so the OCO stays intact. Only ever tightens — the
    original stop remains the floor on protection, never loosened.
    `skip`: symbols to leave alone (growth-sleeve holds use their own wider
    chandelier stop, not these tight intraday trails)."""
    skip = skip or set()
    try:
        positions = [p for p in tc.get_all_positions()
                     if "option" not in str(getattr(p, "asset_class", "")).lower()
                     and p.symbol not in skip]
    except Exception as e:
        log(f"manage_stops: positions fetch failed: {e}")
        return
    if not positions:
        return
    try:
        orders = tc.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=200))
    except Exception as e:
        log(f"manage_stops: orders fetch failed: {e}")
        return
    stops = {}  # symbol -> live/held stop leg
    for o in orders:
        s, t = str(o.status).lower(), str(o.type).lower()
        if (o.symbol and not parse_occ(o.symbol) and "stop" in t and o.stop_price is not None
                and any(k in s for k in ("held", "new", "accept"))):
            stops[o.symbol] = o
    for p in positions:
        stop = stops.get(p.symbol)
        if not stop:
            continue
        entry, cur = float(p.avg_entry_price), float(p.current_price or 0)
        if entry <= 0 or cur <= 0:
            continue
        is_short = "short" in str(p.side).lower()
        fav = (entry - cur) / entry if is_short else (cur - entry) / entry
        if fav < cfg.STOP_TRAIL_ACTIVATE_PCT:
            continue
        cur_stop = float(stop.stop_price)
        if is_short:                                   # buy-stop above price -> ratchet DOWN
            new_stop = round(cur * (1 + cfg.STOP_TRAIL_DISTANCE_PCT), 2)
            tighter = new_stop < cur_stop * (1 - cfg.STOP_TRAIL_MIN_STEP_PCT)
        else:                                          # sell-stop below price -> ratchet UP
            new_stop = round(cur * (1 - cfg.STOP_TRAIL_DISTANCE_PCT), 2)
            tighter = new_stop > cur_stop * (1 + cfg.STOP_TRAIL_MIN_STEP_PCT)
        if not tighter:
            continue
        if dry:
            log(f"[DRY] would trail {p.symbol} stop {cur_stop} -> {new_stop} (fav {fav:+.1%})")
            continue
        try:
            tc.replace_order_by_id(stop.id, order_data=ReplaceOrderRequest(stop_price=new_stop))
            log(f"TRAIL {p.symbol} stop {cur_stop} -> {new_stop} (fav {fav:+.1%}, locking gains)")
            tg_send(f"🎯 Trailed {p.symbol} stop to {new_stop}.")
        except Exception as e:
            log(f"trail {p.symbol} stop failed: {e}")


# ===========================================================================
# Guardrails applied to a model decision before execution
# ===========================================================================
def anti_chase_reason(bullish, row, event=False):
    """A reason to BLOCK an extended momentum entry (buying the top / selling the
    bottom), or None. bullish=True for long/call, False for short/put. Indicators
    that are missing are skipped (can't assess -> don't block).

    event=True widens the bounds for a fresh-CATALYST name (the event router's RIDE
    posture): a hard catalyst drives a continuation, so we allow a more-extended
    entry WITH the move — still bounded (a continuation, not a blow-off chase), and
    still a defined-risk structure."""
    if not row:
        return None
    max_ext = cfg.ANTI_CHASE_MAX_VWAP_EXT_EVENT if event else cfg.ANTI_CHASE_MAX_VWAP_EXT
    min_off = cfg.ANTI_CHASE_MIN_OFF_EXTREME_EVENT if event else cfg.ANTI_CHASE_MIN_OFF_EXTREME
    ob = cfg.RSI_OVERBOUGHT_EVENT if event else cfg.RSI_OVERBOUGHT
    os_ = cfg.RSI_OVERSOLD_EVENT if event else cfg.RSI_OVERSOLD
    ext, rsi_v = row.get("vwap_ext"), row.get("rsi")
    if bullish:
        if ext is not None and ext > max_ext:
            return f"chasing: {ext:+.1%} above VWAP"
        off = row.get("off_hod")
        if off is not None and off < min_off:
            return f"chasing: {off:.1%} off high-of-day (at the top)"
        if rsi_v is not None and rsi_v > ob:
            return f"chasing: intraday RSI {rsi_v} overbought"
    else:
        if ext is not None and ext < -max_ext:
            return f"chasing: {ext:+.1%} below VWAP"
        off = row.get("off_lod")
        if off is not None and off < min_off:
            return f"chasing: {off:.1%} off low-of-day (at the bottom)"
        if rsi_v is not None and rsi_v < os_:
            return f"chasing: intraday RSI {rsi_v} oversold"
    return None


def _decision_underlying(decision) -> "str | None":
    """Underlying of an options decision — the explicit 'symbol', else parsed off a leg."""
    if decision.get("symbol"):
        return decision["symbol"]
    for l in (decision.get("legs") or decision.get("condor_legs") or []):
        m = parse_occ(l.get("symbol"))
        if m:
            return m["underlying"]
    return None


def _spread_directional_bias(legs) -> "str | None":
    """Directional lean of a simple 2-leg vertical from its legs: 'bullish' /
    'bearish', else None (condor / neutral / uninterpretable). Used to block a spread
    that FADES a strong single-name trend (selling a bearish spread into a breakout)."""
    parsed = [(l, parse_occ(l.get("symbol"))) for l in (legs or [])]
    parsed = [(l, m) for l, m in parsed if m]
    if len(parsed) != 2 or parsed[0][1]["type"] != parsed[1][1]["type"]:
        return None
    short = next((m for l, m in parsed if l.get("side") == "sell"), None)
    long_ = next((m for l, m in parsed if l.get("side") == "buy"), None)
    if not short or not long_:
        return None
    if short["type"] == "call":
        # short call below long call -> bear call (bearish); above -> bull call (bullish)
        return "bearish" if short["strike"] < long_["strike"] else "bullish"
    # puts: short above long -> bull put (bullish); below -> bear put (bearish)
    return "bullish" if short["strike"] > long_["strike"] else "bearish"


def _trade_bias(decision) -> "str | None":
    """Directional lean of any decision: 'bullish'/'bearish', else None (condor/hold).
    Used for the tape filter (don't fight a clearly directional market)."""
    a = decision.get("action")
    if a == "buy_stock":
        return "bearish" if decision.get("direction") == "short" else "bullish"
    if a == "buy_option":
        t = (parse_occ(decision.get("option_symbol")) or {}).get("type")
        return "bullish" if t == "call" else ("bearish" if t == "put" else None)
    if a == "multi_leg":
        return _spread_directional_bias(decision.get("legs") or [])
    return None


def passes_guardrails(decision, state, acct, now, ref_price=None,
                      offered_options=None, assets=None,
                      option_spread_pct=None, scan_row=None,
                      dir_counts=None, book_symbols=None,
                      regime=None, event_state=None, options_intel=None) -> tuple[bool, str]:
    """ref_price: current per-share price of the asset being traded — the stock
    last price for buy_stock, the option premium per share for buy_option. Used
    for the notional and total-deployed-capital caps.
    offered_options: the set of option symbols whose REAL contracts we put in the
    model's context this cycle. Any option order must reference only these, so the
    model can never make us submit a hallucinated OCC symbol.
    assets: {symbol: {shortable, fractionable}} so short entries can be vetoed on
    non-shortable names."""
    offered_options = offered_options or set()
    assets = assets or {}
    dir_counts = dir_counts or {"bull": 0, "bear": 0}
    book_symbols = book_symbols or set()
    action = decision.get("action")
    # The growth sleeve and overnight-drift book are separate, code-managed books —
    # the intraday engine may not open, short, or close their names (it would fight
    # the book's own management and tangle broker position netting).
    if action in ("buy_stock", "buy_option", "abort") and decision.get("symbol") in book_symbols:
        return False, f"{decision.get('symbol')} is a managed-book holding (off-limits to intraday)"
    # Deterministic regime gate (when the engine is on): the regime decides which
    # strategies may open this cycle. This is also where the momentum demotion is
    # enforced — naked directional stock trades map to a label the regime never
    # permits, so only defined-risk structures get through.
    if cfg.REGIME_ENGINE_ENABLED and regime is not None and action in (
            "buy_stock", "buy_option", "multi_leg", "iron_condor"):
        import regime as regime_mod
        ok_r, why_r = regime_mod.entry_allowed(regime, _decision_strategy(decision))
        if not ok_r:
            return False, f"regime gate: {why_r}"
    # Spread/condor entry cooldown: don't stack a new multi-leg while a recent one
    # (often a still-working, unfilled limit) is on the books — otherwise the model
    # re-submits the same condor every cycle (esp. on illiquid underlyings that don't
    # fill), piling up duplicate orders and wash-trade rejects.
    if action in ("iron_condor", "multi_leg"):
        recent = []
        for m in (state.get("active_multileg") or []):
            op = m.get("opened")
            if op:
                try:
                    recent.append((now - datetime.fromisoformat(op)).total_seconds() / 60)
                except Exception:
                    pass
        if recent and min(recent) < cfg.MULTILEG_COOLDOWN_MIN:
            return False, (f"multi-leg cooldown ({min(recent):.0f}<"
                           f"{cfg.MULTILEG_COOLDOWN_MIN}m since last spread)")
    # No shared short legs across spreads. Re-using the SAME short option as the short
    # leg of more than one spread (the 6/10 SMCI tangle: three bear-puts all short the
    # 33500) builds an unbalanced, oversized short that the close path can't unwind —
    # closing one spread's leg collides with the other's resting order ("wash trade
    # detected"), and the contract becomes un-exitable. One short strike = exactly one
    # open spread.
    if action == "multi_leg":
        open_shorts = set()
        for m in (state.get("active_multileg") or []):
            for l in (m.get("legs") or []):
                if l.get("side") == "sell":
                    open_shorts.add(l.get("symbol"))
        for l in (decision.get("legs") or []):
            if l.get("side") == "sell" and l.get("symbol") in open_shorts:
                return False, (f"short leg {l.get('symbol')} is already the short of an "
                               f"open spread — refusing to share a short strike (tangle risk)")
    # Per-name daily re-entry cap. Stops re-running the SAME thesis on one underlying
    # all day (6/9: 7 MRVL spreads, mostly unfilled) — each submit leaks spread/slippage.
    if action in ("iron_condor", "multi_leg"):
        und = _decision_underlying(decision) or decision.get("symbol")
        n_today = int((state.get("entries_today") or {}).get(und, 0))
        if und and n_today >= cfg.MAX_SPREADS_PER_NAME_PER_DAY:
            return False, (f"{und}: {n_today} spreads already today "
                           f"(cap {cfg.MAX_SPREADS_PER_NAME_PER_DAY}) — no more re-entries on this name")
    today = now.strftime("%Y-%m-%d")
    if today in cfg.ECON_BLACKOUT_DATES and action not in ("hold", "close"):
        return False, "econ blackout day — no new entries"
    start_eq = state.get("start_equity")
    if start_eq is None:
        start_eq = acct["equity"]
    # Prefer the INTRADAY P&L the cycle computed (shielded-book drift already netted
    # out); fall back to the raw equity delta only if it isn't present.
    daily_pl = acct["day_pl"] if "day_pl" in acct else (acct["equity"] - start_eq)
    if (daily_pl <= cfg.DAILY_LOSS_HALT and action not in ("hold", "close")
            and not state.get("loss_override")):
        return False, f"daily loss halt hit ({daily_pl:.0f})"
    # Daily profit target — a ceiling that banks gains, not a quota to chase.
    if action not in ("hold", "close"):
        conv = (decision.get("conviction") or "").lower()
        if daily_pl >= cfg.DAILY_PROFIT_STRETCH:
            return False, f"profit stretch ${daily_pl:.0f} reached — banking the day"
        if daily_pl >= cfg.DAILY_PROFIT_TARGET and conv != "high":
            return False, (f"above ${cfg.DAILY_PROFIT_TARGET:.0f} target (${daily_pl:.0f}) "
                           f"— high-conviction entries only")
    sym = decision.get("symbol")
    if sym and sym in cfg.BLACKLIST:
        return False, f"{sym} is blacklisted"
    # Anti-fade: never sell a spread AGAINST a strong single-name move. Selling a
    # bear-call into a STRONG_BULL breakout (or a bull-put into a STRONG_BEAR
    # breakdown) is fighting momentum on a name with a live catalyst — the exact
    # mistake that bled the book (INTC +10% / AVGO). Mean-reversion is for chop, not
    # for catching a freight train.
    if action == "multi_leg" and scan_row:
        bias = _spread_directional_bias(decision.get("legs") or [])
        sig = scan_row.get("signal") or ""
        dp = scan_row.get("day_pct") or 0.0
        if bias == "bearish" and sig == "STRONG_BULL":
            return False, (f"{sym} STRONG_BULL ({dp:+.1f}%) — refusing a bearish spread "
                           f"into a breakout (no fading momentum)")
        if bias == "bullish" and sig == "STRONG_BEAR":
            return False, (f"{sym} STRONG_BEAR ({dp:+.1f}%) — refusing a bullish spread "
                           f"into a breakdown (no fading momentum)")
    # Don't FIGHT THE TAPE. On a clearly risk-on day the bot was stacking weak bearish
    # single-name bets and bleeding (6/12: 5/5 bearish, market green). Block a directional
    # entry that opposes a clearly-directional broad market UNLESS the name itself is a
    # strong dislocation (|move| >= STRONG) — a real catalyst can fight the tape, a weak
    # signal can't.
    if regime is not None and regime.get("tape_bias") in ("risk_on", "risk_off") \
            and action in ("buy_stock", "buy_option", "multi_leg"):
        tb = _trade_bias(decision)
        dp = abs((scan_row or {}).get("day_pct") or 0.0)
        tape = regime.get("tape")
        if tb == "bearish" and regime["tape_bias"] == "risk_on" and dp < cfg.REGIME_STRONG_PCT:
            return False, (f"counter-tape: bearish bet while the market is risk-on "
                           f"(tape {tape:+.2f}%) and {sym or ''} only {dp:.1f}% — don't fade "
                           f"a green tape on a weak signal (trade WITH it)")
        if tb == "bullish" and regime["tape_bias"] == "risk_off" and dp < cfg.REGIME_STRONG_PCT:
            return False, (f"counter-tape: bullish bet while the market is risk-off "
                           f"(tape {tape:+.2f}%) and {sym or ''} only {dp:.1f}% — don't fight "
                           f"a red tape on a weak signal (trade WITH it)")
    # Index-only premium selling. The VRP edge a credit spread/condor harvests is
    # reliably negative only at the INDEX level (priced correlation risk); single-name
    # variance premia are ~zero and just bear idiosyncratic jump risk (what bled the
    # book on INTC/SHOP). So short-premium structures are restricted to index ETFs;
    # single names trade DIRECTIONALLY (debit spreads) / via the earnings book only.
    if cfg.CREDIT_SPREAD_INDEX_ONLY and action in ("iron_condor", "multi_leg") \
            and _decision_strategy(decision) in ("iron_condor", "credit_spread"):
        und = _decision_underlying(decision)
        if und and und not in cfg.PREMIUM_INDEX_UNDERLYINGS:
            return False, (f"{und}: credit spreads/condors are index-only (VRP edge is "
                           f"index-level) — single names trade directional/earnings only")
    # Event router — BRACE: no new SHORT-PREMIUM into a scheduled binary you can't
    # predict. A condor/credit spread sold right before a high-impact release is a
    # coin flip on the gap; defer it until the print is out (post-event the same
    # event may FLIP to FADE_VOL and the elevated IV makes the sale attractive).
    if event_state is not None and event_state.get("brace") \
            and action in ("iron_condor", "multi_leg") \
            and _decision_strategy(decision) in ("iron_condor", "credit_spread"):
        nxt = (event_state.get("next_event") or {})
        return False, (f"BRACE: {nxt.get('name','high-impact event')} in "
                       f"{nxt.get('mins_until','<')}m — no new short-premium into the release")
    # Extreme-IV gate. A directional DEBIT trade (debit spread / long option) on a name
    # with sky-high ATM IV is a lottery ticket: the premium is hugely overpriced for a
    # capped payoff, and the signal (incl. skew) is noise. The 6/12 RDW loss — 132% IV,
    # skew flipped call->put in 30 min — is exactly this. Block single-name directional
    # option trades above the IV ceiling; legit high-IV momentum (MU/MRVL ~105%) passes.
    # Also covers buy_stock: a 130%-IV name is a squeezy lottery ticket to trade
    # directionally as SHARES too (short-squeeze / gap risk), not just via options.
    if options_intel and (action in ("buy_option", "buy_stock")
                          or (action == "multi_leg" and _decision_strategy(decision) == "debit_spread")):
        und = _decision_underlying(decision)
        oi = options_intel.get(und) if und else None
        iv = oi.get("atm_iv") if oi else None
        if iv is not None and iv > cfg.OPTION_MAX_ATM_IV:
            return False, (f"{und}: ATM IV {iv:.0f}% > {cfg.OPTION_MAX_ATM_IV:.0f}% ceiling — "
                           f"extreme-IV lottery ticket (overpriced/noisy/squeezy), skip")
    # Stopped-out cooldown: once a single-name option trade is closed at a LOSS (code stop
    # or thesis cut), don't re-enter that name for STOPPED_COOLDOWN_MIN — stop re-losing the
    # same idea (the 6/12 ADBE/RDW churn) without killing a two-way name for the whole day.
    if action in ("buy_option", "multi_leg", "buy_stock"):
        und = _decision_underlying(decision)
        locked = state.get("stopped_today") or {}
        if isinstance(locked, list):                  # legacy form -> treat as locked
            locked = {n: now.isoformat() for n in locked}
        ts = locked.get(und) if und and und not in cfg.PREMIUM_INDEX_UNDERLYINGS else None
        if ts:
            try:
                elapsed = (now - datetime.fromisoformat(ts)).total_seconds() / 60
            except Exception:
                elapsed = 1e9
            if elapsed < cfg.STOPPED_COOLDOWN_MIN:
                return False, (f"{und}: stopped {elapsed:.0f}m ago — cooling down "
                               f"{cfg.STOPPED_COOLDOWN_MIN}m before re-entry (anti-churn)")
    # Cooldown
    if sym and action in ("buy_stock", "buy_option"):
        last = state.get("last_entry_time", {}).get(sym)
        if last:
            mins = (now - datetime.fromisoformat(last)).total_seconds() / 60
            if mins < cfg.TICKER_COOLDOWN_MIN:
                return False, f"{sym} on cooldown ({mins:.0f}<{cfg.TICKER_COOLDOWN_MIN}m)"
    # Time windows for options (enforced in code, not just advertised to the model)
    if action in ("buy_option", "iron_condor"):
        if now.hour < 10:
            return False, "no options before 10:00 ET"
        if now.hour >= 14:
            return False, "no new options after 14:00 ET (0DTE cutoff)"
    if action == "iron_condor":
        if not (now.hour == 10 and now.minute <= 30):
            return False, "condor only in the 10:00–10:30 ET window"
    # Momentum DEBIT spreads (multi-leg, net debit) get a LATER cutoff than the 14:00
    # 0DTE rule — they're directional, not pinned to a same-day expiry — so the bot
    # can catch an afternoon single-name breakout. Still bounded (force-closed 15:45).
    if action == "multi_leg" and _decision_strategy(decision) == "debit_spread":
        mins = now.hour * 60 + now.minute
        if mins < 10 * 60:
            return False, "no options before 10:00 ET"
        cutoff = cfg.MOMENTUM_OPTION_CUTOFF_HOUR * 60 + cfg.MOMENTUM_OPTION_CUTOFF_MIN
        if mins >= cutoff:
            return False, (f"momentum debit-spread cutoff "
                           f"{cfg.MOMENTUM_OPTION_CUTOFF_HOUR}:{cfg.MOMENTUM_OPTION_CUTOFF_MIN:02d} "
                           f"ET passed (spreads force-close 15:45)")

    # Sizing + total-exposure caps. positions_value is the current deployed amount;
    # MAX_DEPLOYED_CAPITAL caps deployed + this new trade.
    deployed = abs(acct.get("positions_value", 0.0) or 0.0)
    qty = int(decision.get("qty") or 0)

    if action == "buy_stock":
        if qty <= 0:
            return False, "qty must be > 0"
        direction = (decision.get("direction") or "long").lower()
        if direction not in ("long", "short"):
            return False, f"bad direction {direction}"
        stop, target = decision.get("stop_price"), decision.get("target_price")
        if stop is None or target is None:
            return False, "bracket needs stop_price and target_price"
        if ref_price is None or ref_price <= 0:
            return False, "no reference price for notional check"
        stop, target = float(stop), float(target)
        # Bracket orientation: long needs stop<entry<target; short flips it.
        if direction == "long" and not (stop < ref_price < target):
            return False, (f"long bracket needs stop<{ref_price}<target "
                           f"(got stop={stop}, target={target})")
        if direction == "short":
            if not (target < ref_price < stop):
                return False, (f"short bracket needs target<{ref_price}<stop "
                               f"(got stop={stop}, target={target})")
            if assets and not assets.get(sym, {}).get("shortable", False):
                return False, f"{sym} is not shortable"
        # Anti-chase: don't buy the top / short the bottom of an extended move — UNLESS
        # this is a fresh event-router catalyst (RIDE) OR a WITH-TAPE breakout (a long on
        # a green tape / short on a red tape), in which case the bounds widen so we stop
        # filtering out the bullish breakouts and only catching bearish pullback-shorts.
        _rd = ((event_state or {}).get("ride", {}) or {}).get(sym, {}).get("dir")
        _with_tape = (regime is not None and (
            (direction == "long" and regime.get("tape_bias") == "risk_on")
            or (direction == "short" and regime.get("tape_bias") == "risk_off")))
        cr = anti_chase_reason(direction == "long", scan_row, event=(_rd == direction or _with_tape))
        if cr:
            return False, f"{sym} {cr}"
        # Correlation cap: don't put the whole book on one directional bet.
        side_key = "bull" if direction == "long" else "bear"
        if dir_counts.get(side_key, 0) >= cfg.MAX_SAME_DIRECTION_POSITIONS:
            return False, (f"correlation cap: already {dir_counts[side_key]} "
                           f"{side_key} positions (max {cfg.MAX_SAME_DIRECTION_POSITIONS})")
        notional = qty * ref_price
        if notional > cfg.PER_TRADE_NOTIONAL_CAP:
            return False, f"notional {notional:.0f} > per-trade cap {cfg.PER_TRADE_NOTIONAL_CAP}"
        if deployed + notional > cfg.MAX_DEPLOYED_CAPITAL:
            return False, (f"deployed {deployed:.0f}+{notional:.0f} > "
                           f"max deployed {cfg.MAX_DEPLOYED_CAPITAL}")

    elif action == "buy_option":
        if qty <= 0:
            return False, "qty must be > 0"
        osym = decision.get("option_symbol")
        if not osym:
            return False, "buy_option needs option_symbol"
        if osym not in offered_options:
            return False, f"option_symbol {osym} not in this cycle's offered chain"
        if ref_price is None or ref_price <= 0:
            return False, "no option price for notional check"
        # Liquidity floor: a wide bid-ask means a market/marketable order donates
        # the spread on entry (e.g. RDW 1.00/1.20 = 18% -> instant ~-17%). Skip it.
        if option_spread_pct is None:
            return False, "no two-sided option quote (illiquid) — skipping"
        if option_spread_pct > cfg.OPTION_MAX_SPREAD_PCT:
            return False, (f"option spread {option_spread_pct:.0%} > "
                           f"{cfg.OPTION_MAX_SPREAD_PCT:.0%} cap (illiquid)")
        # Anti-chase on the underlying: a call into an extended up-move (or a put
        # into an extended down-move) is buying the top — block it.
        bullish = (parse_occ(osym) or {}).get("type") == "call"
        _und = _decision_underlying(decision)
        _rd = ((event_state or {}).get("ride", {}) or {}).get(_und, {}).get("dir")
        _dir = "long" if bullish else "short"
        _with_tape = (regime is not None and (
            (bullish and regime.get("tape_bias") == "risk_on")
            or (not bullish and regime.get("tape_bias") == "risk_off")))
        cr = anti_chase_reason(bullish, scan_row, event=(_rd == _dir or _with_tape))
        if cr:
            return False, f"{sym or osym} {cr}"
        side_key = "bull" if bullish else "bear"
        if dir_counts.get(side_key, 0) >= cfg.MAX_SAME_DIRECTION_POSITIONS:
            return False, (f"correlation cap: already {dir_counts[side_key]} "
                           f"{side_key} positions (max {cfg.MAX_SAME_DIRECTION_POSITIONS})")
        # Size on the ASK we will actually pay (mid * (1 + spread/2)), not mid.
        ask_est = ref_price * (1 + option_spread_pct / 2)
        notional = qty * ask_est * 100  # 100 shares per contract
        if notional > cfg.PER_OPTION_NOTIONAL_CAP:
            return False, f"option notional {notional:.0f} > cap {cfg.PER_OPTION_NOTIONAL_CAP}"
        if deployed + notional > cfg.MAX_DEPLOYED_CAPITAL:
            return False, (f"deployed {deployed:.0f}+{notional:.0f} > "
                           f"max deployed {cfg.MAX_DEPLOYED_CAPITAL}")

    elif action in ("multi_leg", "iron_condor"):
        # iron_condor is the legacy 4-leg form (positive net_credit); multi_leg is
        # the general 2–4 leg form with a SIGNED net_price.
        if action == "iron_condor":
            legs = decision.get("condor_legs") or []
            net_price = abs(float(decision.get("net_credit") or 0))
            if net_price <= 0:
                return False, "condor needs a positive net_credit"
            mlqty = 1
        else:
            legs = decision.get("legs") or []
            if decision.get("net_price") is None:
                return False, "multi_leg needs net_price (signed: + credit, − debit)"
            net_price = float(decision["net_price"])
            mlqty = qty if qty > 0 else 1
        if not (2 <= len(legs) <= 4):
            return False, f"multi-leg needs 2–4 legs (got {len(legs)})"
        leg_syms = [l.get("symbol") for l in legs]
        if any(not s for s in leg_syms):
            return False, "a leg is missing its symbol"
        if any(int(l.get("ratio", 1)) != 1 for l in legs):
            return False, "only ratio-1 legs are supported"
        missing = [s for s in leg_syms if s not in offered_options]
        if missing:
            return False, f"legs not in this cycle's offered chain: {missing}"
        # All legs must share ONE underlying and ONE expiry — Alpaca rejects a
        # mixed MLEG, and the risk math assumes a single-name single-expiry spread.
        metas = [parse_occ(s) for s in leg_syms]
        if any(m is None for m in metas):
            return False, "a leg is not a valid option symbol"
        if len({m["underlying"] for m in metas}) != 1:
            return False, "multi-leg legs span multiple underlyings"
        if len({m["expiry"] for m in metas}) != 1:
            return False, "multi-leg legs span multiple expiries"
        risk = multileg_risk(legs, net_price, mlqty)
        if risk is None:
            return False, "undefined-risk (naked short) multi-leg blocked"
        if risk > cfg.PER_OPTION_NOTIONAL_CAP:
            return False, f"multi-leg max-loss {risk:.0f} > cap {cfg.PER_OPTION_NOTIONAL_CAP}"
        if deployed + risk > cfg.MAX_DEPLOYED_CAPITAL:
            return False, (f"deployed {deployed:.0f}+risk {risk:.0f} > "
                           f"max deployed {cfg.MAX_DEPLOYED_CAPITAL}")

    return True, "ok"


# ===========================================================================
# Snapshot for EOD replay
# ===========================================================================
def write_snapshot(context: dict, decision: dict, result: dict = None):
    """One JSONL record per Claude cycle: market context, the FULL decision, and
    how it resolved (result: submitted/blocked/order_failed/hold + order_id/reason).
    This is the raw material for later outcome analysis — decisions are useless to
    learn from unless we also record what was actually done about them."""
    cfg.SNAPSHOT_DIR.mkdir(exist_ok=True)
    day = et_now().strftime("%Y-%m-%d")
    f = cfg.SNAPSHOT_DIR / f"{day}.jsonl"
    dkeys = ("action", "symbol", "direction", "qty", "stop_price", "target_price",
             "option_symbol", "legs", "net_price", "condor_legs", "net_credit",
             "close_symbols", "overnight_hold", "conviction", "reasoning")
    rec = {"t": et_now().isoformat(),
           "scan": context.get("signal_scan"),
           "vix": context.get("vix"),
           "positions": context.get("positions"),
           "decision": {k: decision.get(k) for k in dkeys},
           "result": result or {}}
    with open(f, "a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")


# ===========================================================================
# Command handling
# ===========================================================================
def _tail(path, n) -> str:
    """Last n lines of a log file, trimmed to fit one Telegram message."""
    try:
        lines = Path(path).read_text(errors="replace").splitlines()[-n:]
    except FileNotFoundError:
        return "(no log file yet)"
    except Exception as e:
        return f"(log read failed: {e})"
    body = "\n".join(lines)[-3800:]
    return body or "(empty)"


def apply_runtime_setting(key: str, raw: str) -> str:
    """Validate + persist a runtime setting override (Telegram SET). Writes to
    cfg.OVERRIDES_FILE which every fresh cycle re-applies, and updates this process
    so a subsequent GET reflects it immediately. Returns a status string."""
    typ = cfg.RUNTIME_SETTABLE.get(key)
    if typ is None:
        return f"{key} is not settable. Send SETTINGS to see the list."
    try:
        if typ is bool:
            val = str(raw).strip().lower() in ("1", "true", "on", "yes", "y")
        elif typ is int:
            val = int(float(raw))
        else:
            val = float(raw)
    except Exception:
        return f"bad value '{raw}' for {key} (expected {typ.__name__})"
    ov = {}
    if cfg.OVERRIDES_FILE.exists():
        try:
            ov = json.loads(cfg.OVERRIDES_FILE.read_text())
        except Exception:
            ov = {}
    ov[key] = val
    try:
        cfg.OVERRIDES_FILE.write_text(json.dumps(ov, indent=2))
    except Exception as e:
        return f"failed to persist {key}: {e}"
    setattr(cfg, key, val)   # reflect in this process immediately
    return f"✅ {key} = {val} (saved; effective next cycle)"


def handle_commands(tc, state, cmds, dry):
    for c in cmds:
        if c.startswith("STOP"):
            state["halted"] = True
            tg_send("🛑 Trading halted for the day.")
        elif c.startswith("RESUME"):
            state["halted"] = False
            tg_send("▶️ Trading resumed.")
        elif c.startswith("OVERRIDE"):
            # Day flag: keep trading the rest of TODAY even past the daily loss
            # halt. Resets automatically tomorrow. 'OVERRIDE OFF' turns it back on.
            if "OFF" in c:
                state["loss_override"] = False
                tg_send("🔒 Loss-halt override OFF — daily loss halt active again.")
            else:
                state["loss_override"] = True
                state["halted"] = False
                tg_send("⚠️ Loss-halt OVERRIDE ON for today — bot will keep trading "
                        "past the daily loss halt. (Resets tomorrow; STOP to halt.)")
        elif c.startswith("CLOSE ALL"):
            # Emergency flatten — includes the separate books (clear their tracking too).
            try:
                if not dry:
                    tc.close_all_positions(cancel_orders=True)
                state["active_multileg"] = []
                state["active_options"] = []
                state["growth_sleeve"] = []
                state["overnight"] = {}
                tg_send("✅ Closed all positions (incl. growth sleeve + overnight book).")
            except Exception as e:
                tg_send(f"close all failed: {e}")
        elif c.startswith("STATUS"):
            tg_send(status_text(tc, state))
        elif c.startswith("GROWTH"):
            import growth_sleeve as gs
            tg_send(json.dumps(gs.summary(state), indent=2)[:3500])
        elif c.startswith("OVERNIGHT"):
            import overnight_drift as od
            tg_send(json.dumps(od.summary(state), indent=2)[:3500])
        elif c.startswith("STATS"):
            tg_send(json.dumps(honest_trade_stats(tc, state), indent=2)[:3500])
        elif c.startswith("FOCUS"):
            parts = c.split()
            state["focus"] = parts[1] if len(parts) > 1 else None
            tg_send(f"🎯 Focus set to {state['focus']}.")
        elif c.startswith("LOG"):
            parts = c.split()
            n = min(int(parts[1]), 100) if len(parts) > 1 and parts[1].isdigit() else 30
            tg_send(_tail(cfg.LOG_FILE, n))
        elif c.startswith("ERRORS"):
            parts = c.split()
            n = min(int(parts[1]), 100) if len(parts) > 1 and parts[1].isdigit() else 30
            tg_send(_tail(cfg.LOG_FILE.parent / "autotrade_error.log", n))
        elif c.startswith("SETTINGS"):
            cur = {k: getattr(cfg, k, None) for k in cfg.RUNTIME_SETTABLE}
            tg_send("Settable (SET <KEY> <VALUE>):\n"
                    + json.dumps(cur, indent=2, default=str)[:3500])
        elif c.startswith("SET "):
            parts = c.split()
            if len(parts) < 3:
                tg_send("usage: SET <KEY> <VALUE>  (e.g. SET DAILY_LOSS_HALT -500)")
            else:
                tg_send(apply_runtime_setting(parts[1], parts[2]))
        elif c.startswith("GET"):
            parts = c.split()
            if len(parts) < 2:
                tg_send("usage: GET <KEY>  (e.g. GET DAILY_LOSS_HALT)")
            else:
                tg_send(f"{parts[1]} = {getattr(cfg, parts[1], '(unknown setting)')}")
        elif c.startswith("HELP") or c == "?":
            tg_send(
                "📋 Commands\n"
                "• STATUS — equity, positions, P&L by strategy\n"
                "• STATS — today's matched realized P&L\n"
                "• GROWTH / OVERNIGHT — book holdings\n"
                "• LOG [n] / ERRORS [n] — last n log lines (default 30)\n"
                "• SETTINGS — list changeable knobs\n"
                "• GET <KEY> / SET <KEY> <VALUE> — view / change a setting\n"
                "• STOP / RESUME — halt / resume trading today\n"
                "• OVERRIDE [OFF] — trade past the daily loss halt\n"
                "• CLOSE ALL — flatten everything (incl. books)\n"
                "• FOCUS <theme> — bias the model")


def status_text(tc, state) -> str:
    try:
        a = account_snapshot(tc)
        pos = open_positions(tc)
        start_eq = state.get("start_equity")
        if start_eq is None:
            start_eq = a["equity"]
        daily = a["equity"] - start_eq
        sleeve = state.get("growth_sleeve") or []
        g = state.get("growth", {})
        on = (state.get("overnight") or {}).get("holding")
        thh = (state.get("tail_hedge") or {}).get("holding")
        ech = (state.get("earnings") or {}).get("holding")
        reg = state.get("last_regime") or {}
        by_strat = honest_trade_stats(tc, state).get("by_strategy", {})
        strat_line = (" | ".join(f"{k} ${v:+.0f}" for k, v in
                                 sorted(by_strat.items(), key=lambda kv: -abs(kv[1])))
                      or "-")
        lines = [f"Equity ${a['equity']:,.0f} | Day P&L ${daily:+,.0f}",
                 f"Halted: {state.get('halted')} | Focus: {state.get('focus')}",
                 f"Positions: {len(pos)}",
                 *[f"  {p['symbol']} {p['qty']:g} uPL ${p['unrealized_pl']:+.0f}" for p in pos],
                 f"By strategy (realized): {strat_line}",
                 f"Growth sleeve: {len(sleeve)} holds "
                 f"({', '.join(h['symbol'] for h in sleeve) or '-'}) | "
                 f"pending ${g.get('pending_cash', 0):.0f}",
                 f"Overnight: {on['symbol']+' '+format(on.get('qty',0),'g') if on else 'flat'}",
                 f"Tail hedge: {thh['symbol']+' x'+str(thh.get('qty')) if thh else 'flat'}",
                 f"Earnings: {ech['underlying']+' condor (risk $'+format(ech.get('risk',0),'.0f')+')' if ech else 'flat'}",
                 (f"Regime: {reg.get('trend','-')}/{reg.get('vol','-')} — "
                  + ("FLAT (no new entries)" if reg.get('flat')
                     else "allowed: " + (", ".join(reg.get('allowed_strategies') or []) or "-"))
                  if reg else "Regime: engine off / not yet computed"),
                 (f"Events: router {'ON' if cfg.EVENT_ROUTER_ENABLED else 'OFF'}"
                  + (f" — econ feed: {cfg.EVENT_ECON_PROVIDER}" if cfg.EVENT_ROUTER_ENABLED else "")),
                 f"Last action: {state.get('last_action','-')}"]
        return "\n".join(lines)
    except Exception as e:
        return f"status error: {e}"


# ===========================================================================
# Cadence (bot-side) + overlap guard
# ===========================================================================
def _acquire_cycle_lock():
    """Non-blocking flock so two cycles never run at once (the scheduler may tick
    faster than a slow cycle finishes). Returns the open file (keep the reference
    to hold the lock) or None if another cycle holds it. Auto-released on exit."""
    try:
        f = open(cfg.LOCK_FILE, "w")
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except (BlockingIOError, OSError):
        return None


def _cycle_interval_min(now, state) -> float:
    """Conditional cadence. Opening hour (9:30–10:30 ET) is always FAST (2m). Otherwise
    run at the ACTIVE rate (3m) when there's something to stay on top of — an open
    intraday option position, a live event-router posture (RIDE/BRACE), or elevated vol
    — and the NORMAL rate (5m) on quiet, flat stretches to save the model call. Reads
    only cheap last-known state stashed by prior cycles (no extra API calls at the
    throttle gate)."""
    mins_since_open = (now.hour - 9) * 60 + now.minute - 30
    if 0 <= mins_since_open < 60:
        return cfg.CYCLE_FAST_INTERVAL_MIN
    active = (
        bool(state.get("active_multileg") or state.get("active_options"))
        or state.get("last_event_posture") in ("RIDE", "BRACE")
        or (state.get("last_regime") or {}).get("vol") in ("ELEVATED", "HIGH")
    )
    return cfg.CYCLE_ACTIVE_INTERVAL_MIN if active else cfg.CYCLE_NORMAL_INTERVAL_MIN


def _throttle_skip(state, now) -> bool:
    """True if this tick is too soon since the last real cycle (the scheduler
    ticks every minute; the bot decides the effective cadence)."""
    last = state.get("last_cycle_at")
    if not last:
        return False
    try:
        elapsed = (now - datetime.fromisoformat(last)).total_seconds() / 60
    except Exception:
        return False
    return elapsed < _cycle_interval_min(now, state) - 0.5   # 0.5m grace for tick jitter


# ===========================================================================
# CYCLE
# ===========================================================================
def run_cycle(dry: bool = False):
    if not _ALPACA_OK:
        log(f"alpaca-py not importable: {_ALPACA_ERR}")
        return
    now = et_now()
    state = load_state()
    tc = trading_client()

    # 1. Commands FIRST — processed every tick, 24/7 (including nights and weekends),
    #    so STATUS/LOG/SET/STOP/CLOSE ALL all respond whenever you send them from
    #    Telegram. Only the TRADING below is gated to weekday market hours.
    cmds = tg_poll_commands(state)
    if cmds:
        handle_commands(tc, state, cmds, dry)

    # Trading gate: skip nights/weekends with no further work (commands already
    # handled above). The Alpaca clock stays authoritative for the open/closed check.
    # Window is the regular cash session 9:30 ET (open) through 16:00 ET — gated by
    # minutes so nothing can attempt to act in the 9:00-9:30 pre-open half hour.
    _mins = now.hour * 60 + now.minute
    if not dry and (now.weekday() >= 5 or not (9 * 60 + 30 <= _mins < 16 * 60)):
        save_state(state)   # persist telegram offset + any command effects
        return

    odc = option_data_client() if _ALPACA_OK else None

    # 1b. Cadence throttle — every-minute ticks, but only do a real cycle every
    # CYCLE_FAST/NORMAL minutes. Commands above are still processed every tick.
    if not dry and _throttle_skip(state, now):
        save_state(state)   # persist any command/telegram-offset changes
        return
    state["last_cycle_at"] = now.isoformat()

    # Market closed -> just persist state and leave.
    if not is_market_open(tc):
        log("market closed")
        save_state(state)
        return

    acct = account_snapshot(tc)
    if state.get("start_equity") is None:
        state["start_equity"] = acct["equity"]  # first cycle of the day

    # Separate long-horizon / overnight books, each funded and managed on its own and
    # SHIELDED from the intraday machinery. The growth sleeve compounds prior-day gains
    # into screened long-term names; the overnight-drift book captures the close->open
    # index drift. `held_books` is the union the rest of the cycle must leave alone.
    import growth_sleeve as gs
    import overnight_drift as od
    import tail_hedge as th
    import earnings_crush as ec
    import gap_fade as gf
    gs.run(tc, state, dry)
    od.run(tc, state, dry)               # sell at open / buy near close (regime-gated)
    th.run(tc, state, dry)              # always-on crash hedge (flag-gated, OFF by default)
    ec.run(tc, state, dry)             # earnings IV-crush condor (flag-gated, OFF by default)
    gf.run(tc, state, dry)             # opening-gap fade 9:30-10:00 (flag-gated; intraday, not shielded)
    sleeve = gs.held_symbols(state)
    # Shielded books the intraday engine must leave alone. (gap_fade is intraday and
    # managed by the normal bracket machinery, so it is intentionally NOT here.)
    held_books = (sleeve | od.held_symbols(state)
                  | th.held_symbols(state) | ec.held_symbols(state))

    # Daily-loss-halt + profit gates must reflect INTRADAY P&L, not the mark drift of
    # the shielded HOLD-THROUGH books (the ~$25k growth sleeve, the tail-hedge puts,
    # the earnings condor). Those can swing the account hundreds of $ overnight or
    # intraday and would otherwise false-trip the floor on a flat intraday day — or
    # mask a real intraday blowout behind a green sleeve. Net out their value change
    # since day open. (The overnight-drift book liquidates daily and is tiny, so it's
    # intentionally left in; its mark doesn't persist to distort the day.)
    hold_books_value = (gs._sleeve_market_value(tc, state)
                        + th.held_value(tc, state) + ec.held_value(tc, state))
    if state.get("start_hold_books_value") is None:
        state["start_hold_books_value"] = hold_books_value
    book_drift = hold_books_value - state["start_hold_books_value"]

    # 1c. Overnight reconciliation (once/day): if the EOD flatten was missed (Mac
    #     asleep through the close), a non-book stock can survive to today with an
    #     expired DAY bracket — unprotected. Sweep any such carryover before trading.
    reconcile_overnight_stocks(tc, state, dry, skip=held_books)

    # 2. Manage any open spreads + long options (code-enforced exits), and flatten
    #    stocks at EOD so nothing rides overnight with an expiring DAY bracket.
    #    Book symbols are excluded — they ride overnight by design.
    manage_multileg(tc, odc, state, dry)
    manage_options(tc, odc, state, dry)
    manage_stops(tc, dry, skip=held_books)   # trail intraday bracket stops to lock in gains
    flatten_stocks_eod(tc, dry, skip=held_books)
    sweep_orphan_options(tc, state, dry, skip=held_books)   # last-line: no untracked option rides into expiry

    # 2b. Daily loss halt — latches for the rest of the day and alerts once.
    # Skipped while the OVERRIDE day-flag is on (user chose to keep trading).
    daily_pl = acct["equity"] - state["start_equity"] - book_drift   # INTRADAY P&L (book drift netted out)
    if daily_pl <= cfg.DAILY_LOSS_HALT and not state.get("halted") and not state.get("loss_override"):
        # Standard daily-loss-limit behavior: hard stop — FLATTEN the INTRADAY book
        # and halt, so the day's loss is actually capped. The growth sleeve is a
        # separate long-term book with its own stops and is NOT liquidated here.
        state["halted"] = True
        log(f"DAILY LOSS HALT latched: day P&L {daily_pl:+.0f} <= {cfg.DAILY_LOSS_HALT} — flattening intraday positions (sleeve kept)")
        try:
            if not dry:
                for p in tc.get_all_positions():
                    if p.symbol in held_books:
                        continue
                    try:
                        for o in tc.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=200)):
                            if o.symbol == p.symbol:
                                tc.cancel_order_by_id(o.id)
                    except Exception:
                        pass
                    tc.close_position(p.symbol)
            state["active_multileg"] = []
            state["active_options"] = []
        except Exception as e:
            log(f"loss-halt flatten failed: {e}")
        tg_send(f"🛑 Daily loss halt: day P&L ${daily_pl:+,.0f}. Flattened intraday book "
                f"(growth sleeve kept); trading stopped for the day.")
    elif daily_pl <= cfg.DAILY_LOSS_HALT and state.get("loss_override"):
        log(f"loss override ON: day P&L {daily_pl:+.0f} past halt, but trading continues")

    if state.get("halted"):
        log("halted — skipping new decisions")
        state["last_action"] = "halted"
        save_state(state)
        return

    # 3. Build context. Growth-sleeve holdings are excluded from the intraday
    # position list, directional counts, and deployed-capital cap — they are a
    # separate long-term book the model must not touch.
    positions = [p for p in open_positions(tc) if p["symbol"] not in held_books]
    bracketed = bracketed_symbols(tc) if positions else set()
    universe, assets = build_universe(tc)
    # Quality floor: keep names priced over MIN_PRICE and drop extreme movers
    # (halted low-float runners like STI +513%) that aren't tradeable setups.
    scan = [r for r in signal_scan(universe)
            if r.get("last", 0) >= cfg.MIN_PRICE
            and abs(r.get("day_pct", 0)) <= cfg.MOMENTUM_MAX_DAY_PCT]
    vix = get_vix()
    now = et_now()

    # Options intel (free IV/greeks-derived positioning): real ATM IV-rank + 25Δ skew
    # per underlying — for the index ETFs and the top single-name movers. Read-only.
    # Feeds the event router's FADE_VOL (real IV-rank, not the VIX proxy) and is
    # surfaced to the model so it can read where vol/skew actually sit.
    options_intel = {}
    if cfg.OPTIONS_INTEL_ENABLED:
        import options_intel as oi_mod
        movers = [r["symbol"] for r in scan if not r.get("event_catalyst")][:cfg.OPTIONS_INTEL_MAX_NAMES]
        for u in list(cfg.PREMIUM_INDEX_UNDERLYINGS) + movers:
            if u in options_intel:
                continue
            row = next((r for r in scan if r["symbol"] == u), None)
            spot = row.get("last") if row else None
            try:
                info = oi_mod.compute(odc, u, spot, now) if spot else None
            except Exception as e:
                log(f"options_intel {u} failed: {e}")
                info = None
            if info:
                options_intel[u] = info

    # Event router (two-sided macro/news: RISK + OPPORTUNITY). Detect live events, set
    # the cycle's posture, and INJECT the affected instruments into the scan NOW — so
    # the bot sees XLE/ITA/GLD the moment the event breaks, not after they climb the
    # movers list. Runs before regime/context so everything downstream sees them.
    # No-op when EVENT_ROUTER_ENABLED is off.
    event_state = None
    if cfg.EVENT_ROUTER_ENABLED:
        import events as ev
        try:
            event_state = ev.assess(state, scan, vix, now, dry,
                                     index_iv_rank=(options_intel.get("SPY") or {}).get("iv_rank"))
            inject = [s for s in (event_state or {}).get("inject", [])
                      if s not in {r["symbol"] for r in scan}]
            # Log the triggering headline(s) so a RIDE can be audited later (was it a
            # real event or a keyword false-positive?).
            _heads = "; ".join(f"[{e['theme']}|{e['age_min']}m] {e['headline'][:100]}"
                               for e in (event_state.get("breaking") or [])) if event_state else ""
            if inject:
                for r in signal_scan(inject):
                    if r.get("last", 0) >= cfg.MIN_PRICE:
                        r["event_catalyst"] = True
                        scan.append(r)
                log(f"event router: {event_state['posture']} — injected {inject} :: {_heads}")
            elif event_state and event_state["posture"] != "NEUTRAL":
                log(f"event router: {event_state['posture']} :: {_heads}")
        except Exception as e:
            log(f"event router failed (continuing without): {e}")
            event_state = None
    # Stash the posture so the NEXT tick's cadence throttle can read it cheaply
    # (a live RIDE/BRACE keeps the bot on the 3-min active cadence).
    state["last_event_posture"] = event_state["posture"] if event_state else None

    # Deterministic regime gate (OFF by default). When enabled, a pure-code
    # classifier decides which strategies are permitted this cycle — or forces the
    # book FLAT. If it's flat AND there is nothing open to manage, skip the model
    # call entirely (the "stay quiet on no-trade days" discipline). When something
    # is open, we still call the model to MANAGE it, but new entries are blocked
    # downstream by passes_guardrails(regime=...).
    regime = None
    if cfg.REGIME_ENGINE_ENABLED:
        import regime as regime_mod
        event_day = now.strftime("%Y-%m-%d") in cfg.ECON_BLACKOUT_DATES
        regime = regime_mod.classify(scan, vix, now, event_day=event_day)
        log(f"regime: {regime['trend']}/{regime['vol']} flat={regime['flat']} "
            f"allowed={regime['allowed']} :: {regime['reason']}")
        state["last_regime"] = regime_mod.summary(regime)   # for STATUS visibility
        # Self-diagnostic: if the open intraday book is net AGAINST a clearly directional
        # tape (e.g. mostly short while the market is risk-on), say so — proactively,
        # without being asked. This is the 6/12 failure made visible.
        _dc = directional_counts(positions)
        _tb = regime.get("tape_bias")
        _fighting = ((_tb == "risk_on" and _dc.get("bear", 0) - _dc.get("bull", 0) >= 2)
                     or (_tb == "risk_off" and _dc.get("bull", 0) - _dc.get("bear", 0) >= 2))
        if _fighting:
            global _LAST_TAPE_ALERT_AT
            if (_LAST_TAPE_ALERT_AT is None
                    or (now - _LAST_TAPE_ALERT_AT).total_seconds() > 1800):
                tg_send(f"⚠️ FIGHTING THE TAPE: book is {_dc.get('bull',0)} long / "
                        f"{_dc.get('bear',0)} short while the market is {_tb} "
                        f"(tape {regime.get('tape'):+.2f}%). Trades should lean WITH it.")
                log(f"self-diagnostic: fighting the tape ({_dc} vs {_tb})")
                _LAST_TAPE_ALERT_AT = now
        nothing_open = (not positions and not state.get("active_multileg")
                        and not state.get("active_options"))
        if regime["flat"] and nothing_open:
            state["last_action"] = f"flat regime ({regime['trend']}/{regime['vol']})"
            log("regime flat + nothing to manage — holding cash, no model call")
            save_state(state)
            return

    qualifies, why = setup_qualifies(state, scan, positions, vix, now)
    if not qualifies:
        log(f"pre-filter: {why}")
        state["last_action"] = f"hold ({why})"
        save_state(state)
        return
    log(f"pre-filter passed: {why}")

    # 3b. Option chains — only fetched when an options setup is plausible this cycle,
    # so the model selects REAL contracts. offered_options is the whitelist the
    # guardrail enforces; if a chain is empty the model simply can't trade it.
    option_chains, offered_options = build_option_chains(odc, scan, positions, vix, now)

    # Authoritative day P&L straight off the account (equity vs start-of-day),
    # plus open unrealized — the ground truth the model should trust over any
    # per-symbol fill math. Profit-target gating uses this same number. The
    # deployed-capital cap counts only the INTRADAY book, so exclude the sleeve.
    # Deployed = GROSS intraday exposure: sum |market value| of every non-book leg.
    # `positions` already excludes held_books, so this is the true intraday figure.
    # (The old abs(long_mv+short_mv)−books netted short option legs against longs and
    # double-abs'd, badly under-counting gross exposure whenever a credit spread/condor
    # was open — letting the deployed cap be silently overrun.)
    deployed_gross = round(sum(abs(p.get("market_value", 0.0) or 0.0) for p in positions), 2)
    acct = {**acct,
            "positions_value": deployed_gross,
            "day_pl": round(daily_pl, 2),                # INTRADAY P&L (book drift netted)
            "open_unrealized_pl": round(sum(p["unrealized_pl"] for p in positions), 2),
            "profit_target": cfg.DAILY_PROFIT_TARGET,
            "profit_stretch": cfg.DAILY_PROFIT_STRETCH}

    context = {
        "now_et": now.isoformat(),
        "account": acct,
        "positions": positions,
        "open_spreads": open_spreads_for_context(state),
        "growth_sleeve": gs.summary(state),
        "overnight_drift": od.summary(state),
        "tail_hedge": th.summary(state),
        "earnings": ec.summary(state),
        "gap_fade": gf.summary(state),
        "bracket_managed": sorted(bracketed),
        "signal_scan": scan[:20],
        "option_chains": option_chains,
        "vix": vix,
        "fear_greed": fear_greed(),
        "news": breaking_news([r["symbol"] for r in scan[:10]]),
        "events": (__import__("events").summary(event_state) if event_state else None),
        "options_intel": options_intel or None,
        "stats": honest_trade_stats(tc, state),
        "learnings": active_learnings_for_context(),   # standing rulebook, retired rules excluded
        "focus": state.get("focus"),
        "guardrails": {
            "daily_loss_halt": cfg.DAILY_LOSS_HALT,
            "per_trade_notional_cap": cfg.PER_TRADE_NOTIONAL_CAP,
            "per_option_notional_cap": cfg.PER_OPTION_NOTIONAL_CAP,
            "option_risk_target": cfg.OPTION_RISK_TARGET,
            "sizing": (f"SIZE every defined-risk options trade so its max-loss is "
                       f"~${cfg.OPTION_RISK_TARGET:.0f} (never over the "
                       f"${cfg.PER_OPTION_NOTIONAL_CAP:.0f} cap). Use ~$5 wings and "
                       f"ADD CONTRACTS to reach the target — do NOT trade minimum 1-lot, "
                       f"$1-wide, $50-risk condors; that wastes the range-day edge."),
            "max_deployed_capital": cfg.MAX_DEPLOYED_CAPITAL,
            "vix_condor_ceiling": cfg.VIX_CONDOR_CEILING,
            "blacklist": cfg.BLACKLIST,
            "no_options_before": "10:00 ET",
            "no_0dte_after": "14:00 ET (0DTE / index condors / buy_option ONLY)",
            "momentum_debit_spread_until": (f"{cfg.MOMENTUM_OPTION_CUTOFF_HOUR}:"
                                            f"{cfg.MOMENTUM_OPTION_CUTOFF_MIN:02d} ET — "
                                            f"single-name momentum debit spreads may be "
                                            f"opened in the afternoon (not bound by the "
                                            f"14:00 0DTE cutoff)"),
            "condor_window": "10:00–10:30 ET",
            "option_symbols_must_come_from": "option_chains",
        },
        "pre_filter_reason": why,
    }
    # Explicit clock so the model never does time math itself (it once misread 14:53
    # as past the 15:00 cutoff and skipped a valid momentum trade). Use these numbers.
    _mins_now = now.hour * 60 + now.minute
    _mom_cut = cfg.MOMENTUM_OPTION_CUTOFF_HOUR * 60 + cfg.MOMENTUM_OPTION_CUTOFF_MIN
    context["guardrails"]["clock"] = {
        "now_et": now.strftime("%H:%M ET"),
        "momentum_debit_cutoff": f"{cfg.MOMENTUM_OPTION_CUTOFF_HOUR}:{cfg.MOMENTUM_OPTION_CUTOFF_MIN:02d} ET",
        "minutes_until_momentum_cutoff": max(0, _mom_cut - _mins_now),
        "momentum_entries_open_now": 600 <= _mins_now < _mom_cut,
        "minutes_until_eod_spread_close": max(
            0, (cfg.OPTION_EOD_CLOSE_HOUR * 60 + cfg.OPTION_EOD_CLOSE_MIN) - _mins_now),
    }
    # Regime engine (when on): tell the model exactly what it may open this cycle,
    # so it produces a compliant decision instead of one the code will reject.
    if regime is not None:
        context["regime"] = regime_mod.summary(regime)
        context["guardrails"]["regime_gate"] = (
            "A deterministic regime classifier governs this cycle. Open ONLY the "
            "strategies in regime.allowed_strategies; if regime.flat is true, open "
            "nothing new (manage/exit existing only). For a momentum debit spread, "
            "trade in regime.direction. Entries outside this are rejected in code.")

    # 4. Decide
    decision = call_claude(context)
    log(f"decision: {decision.get('action')} {decision.get('symbol') or ''} "
        f"conv={decision.get('conviction')} :: {decision.get('reasoning','')[:140]}")

    # 5. Execute (guardrails first). `result` records how the decision resolved so
    # the snapshot can later be joined to outcomes. Single exit: write once at end.
    action = decision.get("action", "hold")
    result = {"status": "hold", "action": action}
    # Close targets: explicit close_symbols plus a bare action=="close" on `symbol`.
    close_targets = list(decision.get("close_symbols") or [])
    if action == "close" and decision.get("symbol") and decision["symbol"] not in close_targets:
        close_targets.append(decision["symbol"])
    if close_targets:
        # Bracketed stocks exit only via their stop/target — skip the futile close.
        # Book names (growth sleeve / overnight drift) are off-limits to the engine.
        skip = [s for s in close_targets if s in bracketed or s in held_books]
        do = [s for s in close_targets if s not in bracketed and s not in held_books]
        if skip:
            log(f"close skipped — exit handled by bracket: {skip}")
        if do:
            close_symbols(tc, do, dry)
        # If the model is cutting a tracked single-name spread that's at a LOSS, lock the
        # name for the day — don't re-lose the same idea (the 6/12 RDW thesis-cut churn).
        _targets = set(do) | set(close_targets)
        for _pos in (state.get("active_multileg") or []):
            _legs = [l.get("symbol") for l in (_pos.get("legs") or [])]
            _und = _decision_underlying({"legs": _pos.get("legs") or []})
            if (_und in _targets or any(s in _targets for s in _legs)) and (_pos.get("pl") or 0) < 0:
                _lock_name_today(state, _und, "spread cut at a loss")
        result = {"status": "close", "action": action,
                  "closed": do, "skipped_bracketed": skip}

    if action == "abort":
        result = abort_position(tc, state, decision, now, dry)

    if action in ("buy_stock", "buy_option", "multi_leg", "iron_condor"):
        ref_price = None
        opt_ask = None
        opt_spread = None
        scan_row = next((r for r in scan if r["symbol"] == decision.get("symbol")), None)
        if action == "buy_stock":
            ref_price = scan_row["last"] if scan_row else None
        elif action == "buy_option":
            bid, opt_ask, ref_price = option_quote(odc, decision.get("option_symbol"))
            if bid and opt_ask and ref_price:
                opt_spread = (opt_ask - bid) / ref_price
        # Make sure options intel exists for the name being traded (for the extreme-IV
        # gate) — compute on-demand if this underlying wasn't in the cycle's profiled set
        # (RDW-type screener movers usually aren't).
        if cfg.OPTIONS_INTEL_ENABLED and action in ("buy_option", "multi_leg", "buy_stock"):
            _u = _decision_underlying(decision)
            if _u and _u not in options_intel:
                _row = next((r for r in scan if r["symbol"] == _u), None)
                _spot = _row.get("last") if _row else None
                try:
                    import options_intel as _oimod
                    _info = _oimod.compute(odc, _u, _spot, now) if _spot else None
                    if _info:
                        options_intel[_u] = _info
                except Exception as _e:
                    log(f"options_intel on-demand {_u} failed: {_e}")
        ok, reason = passes_guardrails(decision, state, acct, now, ref_price,
                                       offered_options, assets, opt_spread, scan_row,
                                       directional_counts(positions), held_books,
                                       regime=regime, event_state=event_state,
                                       options_intel=options_intel)
        if not ok:
            log(f"guardrail blocked {action}: {reason}")
            result = {"status": "blocked", "action": action, "reason": reason}
        else:
            # Order submission wrapped so a rejected order is logged and the cycle
            # still finishes cleanly instead of crashing the whole run.
            try:
                order_id = None
                if action == "buy_stock":
                    direction = (decision.get("direction") or "long").lower()
                    side = "buy" if direction == "long" else "sell"
                    o = place_stock_bracket(tc, decision["symbol"], int(decision["qty"]),
                                            side, decision["stop_price"],
                                            decision["target_price"], dry, ref=ref_price)
                    order_id = str(o.id) if o else None
                    state.setdefault("last_entry_time", {})[decision["symbol"]] = now.isoformat()
                elif action == "buy_option":
                    # Marketable LIMIT (limit just above ask) instead of a naked
                    # market order — fills promptly but caps the price so a fast or
                    # wide-quoted option can't fill far through the ask.
                    osym, oqty = decision["option_symbol"], int(decision["qty"])
                    lim = round(opt_ask * 1.02, 2)
                    if not dry:
                        req = LimitOrderRequest(symbol=osym, qty=oqty, side=OrderSide.BUY,
                                                time_in_force=TimeInForce.DAY, limit_price=lim)
                        o = tc.submit_order(order_data=req)
                        order_id = str(o.id)
                        entry = float(o.filled_avg_price) if o.filled_avg_price else opt_ask
                        log(f"OPTION buy {oqty} {osym} @lim {lim} (ask {opt_ask}) entry≈{entry} id={o.id}")
                        tg_send(f"📊 Bought {oqty}x {osym} (limit {lim}).")
                        state.setdefault("active_options", []).append(
                            {"symbol": osym, "qty": oqty, "entry": entry,
                             "order_id": str(o.id), "opened": now.isoformat()})
                    else:
                        log(f"[DRY] would buy option {oqty} {osym} @lim {lim} (ask {opt_ask})")
                    if decision.get("symbol"):
                        state.setdefault("last_entry_time", {})[decision["symbol"]] = now.isoformat()
                elif action in ("multi_leg", "iron_condor"):
                    if action == "iron_condor":
                        legs = decision["condor_legs"]
                        net_price = abs(float(decision["net_credit"]))   # credit (+)
                        mlqty = 1
                    else:
                        legs = decision["legs"]
                        net_price = float(decision["net_price"])         # signed
                        mlqty = int(decision.get("qty") or 1) or 1
                    o = place_multi_leg(tc, legs, net_price, mlqty, dry)
                    if o and not dry:
                        order_id = str(o.id)
                        # entry_net in $ (signed): + credit collected, − debit paid.
                        ml_entry = {
                            "legs": [{"symbol": l["symbol"], "side": l["side"]} for l in legs],
                            "qty": mlqty, "entry_net": net_price * 100 * mlqty,
                            "order_id": str(o.id), "opened": now.isoformat()}
                        # Overnight hold: only if the model flagged a catalyst AND the
                        # deterministic conviction gate (RVOL/close-strength/breadth/RSI/
                        # no-earnings) passes; otherwise it stays intraday (15:45 close).
                        if decision.get("overnight_hold"):
                            import overnight_conviction as ocv
                            ok_on, why_on = ocv.evaluate(decision, scan_row, scan, now)
                            if ok_on:
                                ml_entry["overnight"] = True
                                log(f"OVERNIGHT HOLD granted {decision.get('symbol')}: {why_on}")
                                tg_send(f"🌙 Overnight hold {decision.get('symbol')}: {why_on}")
                            else:
                                log(f"overnight hold DENIED {decision.get('symbol')}: {why_on}")
                        state.setdefault("active_multileg", []).append(ml_entry)
                        # Per-name daily entry tally (counts submits, filled or not — an
                        # unfilled re-submit still costs and is exactly the churn we cap).
                        _und = _decision_underlying(decision) or decision.get("symbol")
                        if _und:
                            et = state.setdefault("entries_today", {})
                            et[_und] = int(et.get(_und, 0)) + 1
                result = {"status": "dry_run" if dry else "submitted", "action": action,
                          "order_id": order_id, "ref_price": ref_price}
            except Exception as e:
                log(f"order placement failed for {action}: {e}")
                tg_send(f"⚠️ Order failed ({action}): {e}")
                result = {"status": "order_failed", "action": action, "error": str(e)}

    # Single terminal write: full decision + how it resolved, then persist state.
    write_snapshot(context, decision, result)
    if result["status"] == "blocked":
        state["last_action"] = f"blocked: {result['reason']}"
    elif result["status"] == "order_failed":
        state["last_action"] = f"order failed: {result['error']}"
    else:
        state["last_action"] = f"{action} {decision.get('symbol') or ''}".strip()
    save_state(state)


# ===========================================================================
# Outcome capture (read-only; NO model call, NO rule generation)
# ===========================================================================
def compute_outcomes(tc=None, day=None) -> dict:
    """Record what actually happened today so a real sample accumulates for later
    analysis: realized cash-flow P&L per symbol, every fill (with type/class so
    target-vs-stop exits are inferable), and end-of-day equity. Writes
    OUTCOMES_DIR/<day>.json. Read-only — does not trade, call the model, or write
    learnings; that stays off until the sample is big enough to mean something."""
    tc = tc or trading_client()
    day = day or et_now().strftime("%Y-%m-%d")
    fills, by_symbol = [], {}
    try:
        orders = tc.get_orders(filter=GetOrdersRequest(
            status=QueryOrderStatus.CLOSED, limit=500))
    except Exception as e:
        log(f"outcomes order fetch failed: {e}")
        orders = []
    for parent in orders:
        if not parent.filled_at or parent.filled_at.astimezone(ET).date().isoformat() != day:
            continue
        for o in _fill_legs(parent):       # expand a symbol-less multi-leg parent into its legs
            qty = float(o.filled_qty or 0)
            price = float(o.filled_avg_price or 0)
            if not o.symbol or qty <= 0 or price <= 0:
                continue
            side = "sell" if "SELL" in str(o.side).upper() else "buy"
            mult = 100 if parse_occ(o.symbol) else 1               # options are ×100/contract
            signed = qty * price * mult * (1 if side == "sell" else -1)   # sell + / buy −
            fills.append({"symbol": o.symbol, "side": side, "qty": qty, "price": price,
                          "type": str(getattr(o, "type", "")).lower(),
                          "class": str(getattr(o, "order_class", "")).lower(),
                          "filled_at": str(o.filled_at), "order_id": str(o.id)})
            d = by_symbol.setdefault(o.symbol, {"realized_cashflow": 0.0, "fills": 0,
                                                "bought_qty": 0.0, "sold_qty": 0.0})
            d["realized_cashflow"] += signed
            d["fills"] += 1
        d["sold_qty" if side == "sell" else "bought_qty"] += qty
    equity = None
    try:
        equity = account_snapshot(tc)["equity"]
    except Exception:
        pass
    snap_file = cfg.SNAPSHOT_DIR / f"{day}.jsonl"
    n_snaps = len(snap_file.read_text().splitlines()) if snap_file.exists() else 0
    # Honest matched realized P&L + per-strategy attribution (which strategy made/lost
    # the day), so "how was the day" can be answered by strategy, not just by symbol.
    try:
        hstats = honest_trade_stats(tc, load_state())
    except Exception as e:
        log(f"outcomes attribution failed: {e}")
        hstats = {}
    rec = {"day": day, "equity_end": equity,
           "day_realized_pl": hstats.get("day_realized_pl"),
           "realized_by_strategy": hstats.get("by_strategy", {}),
           "fills": fills, "by_symbol": by_symbol,
           "snapshots": n_snaps,
           "note": "day_realized_pl/realized_by_strategy = MATCHED round-trips + book "
                   "ledger (the honest numbers). realized_cashflow below = sell+/buy− "
                   "proxy; ≈ realized P&L only for symbols fully closed today."}
    cfg.OUTCOMES_DIR.mkdir(exist_ok=True)
    (cfg.OUTCOMES_DIR / f"{day}.json").write_text(json.dumps(rec, indent=2, default=str))
    log(f"OUTCOMES {day}: {len(fills)} fills across {len(by_symbol)} symbols; equity={equity}")
    return rec


# ===========================================================================
# EOD learning
# ===========================================================================
# Strict structured-output schema for the enriched rule shape. nullable strings for the
# optional link/scope fields (the model emits the full object, with nulls where unused).
_LEARNINGS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"learnings": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "id": {"type": "string"}, "rule": {"type": "string"},
            "evidence": {"type": "string"}, "scope": {"type": ["string", "null"]},
            "status": {"type": "string",
                       "enum": ["active", "tentative", "contested", "retired"]},
            "confirmations": {"type": "integer"}, "refutations": {"type": "integer"},
            "since": {"type": "string"}, "date": {"type": "string"},
            "supersedes": {"type": ["string", "null"]},
            "superseded_by": {"type": ["string", "null"]}},
        "required": ["id", "rule", "evidence", "scope", "status", "confirmations",
                     "refutations", "since", "date", "supersedes", "superseded_by"]}}},
    "required": ["learnings"],
}


def _learnings_system_prompt() -> str:
    return (
        "You maintain the LEARNINGS rulebook for an autonomous trading bot. You are given "
        "the standing rulebook (each rule has id, status, scope, confirmations, refutations, "
        "since) and one day of evidence. Update it under a strict EVIDENCE-WEIGHTED policy. "
        "Hard rules:\n"
        "(1) Every rule MUST cite specific evidence (a fill, a snapshot time, or a stat).\n"
        "(2) PRESERVE each existing rule's id, since, and scope unless you are deliberately "
        "changing scope; carry rules forward — do NOT silently drop a still-relevant rule.\n"
        "(3) CONTRADICTIONS: when today's evidence conflicts with a standing rule, FIRST ask "
        "if it's actually REGIME-CONDITIONAL — if so, set a 'scope' on BOTH rules (e.g. "
        "'RANGE/high-VIX' vs 'trend day') instead of picking a winner. Only if it's a genuine "
        "REVERSAL: do NOT delete the incumbent — set its status to 'contested' and bump its "
        "refutations by 1. Mark it 'retired' (with superseded_by = the winning rule's id) ONLY "
        "when refutations exceed confirmations by a clear margin over MULTIPLE days. A single "
        "contradicting day never retires a rule.\n"
        "(4) Reaffirmed a rule today? bump its confirmations by 1 (status stays/returns to "
        "'active'). Move confirmations/refutations by at most 1 per day.\n"
        "(5) status is one of: active, tentative (small sample), contested (evidence is "
        "currently split), retired (superseded). New low-sample rules start 'tentative'.\n"
        "(6) NEVER produce coercive/quota ('must trade X times') or 'always trade <ticker>' "
        "rules. NEVER emit a rule that contradicts these PROTECTED PRIORS:\n    - "
        + "\n    - ".join(cfg.LEARNING_PROTECTED_PRIORS) + "\n"
        "Return a JSON object {\"learnings\": [ {id, rule, evidence, scope, status, "
        "confirmations, refutations, since, date, supersedes, superseded_by}, ... ]} — the "
        "FULL updated rulebook (active + contested + any newly-retired)."
    )


def _load_day_snapshots(day: str) -> list:
    f = cfg.SNAPSHOT_DIR / f"{day}.jsonl"
    if not f.exists():
        return []
    return [json.loads(l) for l in f.read_text().splitlines() if l.strip()]


def _stats_from_outcomes(day: str) -> dict:
    """The persisted realized-P&L summary for a PAST day (honest_trade_stats only works
    for 'today', so a historical replay must read the outcomes file instead)."""
    f = cfg.OUTCOMES_DIR / f"{day}.json"
    if not f.exists():
        return {"note": "no outcomes file for this day"}
    try:
        o = json.loads(f.read_text())
        return {"day_realized_pl": o.get("day_realized_pl"),
                "by_strategy": o.get("realized_by_strategy", {}),
                "by_symbol": o.get("by_symbol", {})}
    except Exception:
        return {"note": "outcomes file unreadable"}


def _learning_pass(day, stats, snapshots, existing, snap_cap=60):
    """One day's learning generation + reconcile. Returns (learnings, flips, ok). Pure of
    side effects (no file write / Telegram) so both the nightly EOD run and the multi-day
    backfill can drive it. On any model/parse failure returns (existing, [], False) so the
    caller keeps the prior rulebook intact."""
    user = (
        f"Date: {day}\nMeasured stats: {json.dumps(stats, default=str)}\n\n"
        f"Intraday snapshots ({len(snapshots)}): {json.dumps(snapshots[-snap_cap:], default=str)}\n\n"
        f"Standing rulebook: {json.dumps(existing, default=str)}\n\n"
        "For each 5-min snapshot, what was the optimal action vs what the bot did? Apply the "
        "contradiction policy above and return the full updated rulebook."
    )
    try:
        r = _anthropic_client().messages.create(
            model=cfg.CLAUDE_MODEL, max_tokens=cfg.CLAUDE_MAX_TOKENS,
            system=_learnings_system_prompt(),
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": _LEARNINGS_SCHEMA}},
        )
        if r.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"learnings call ended on {r.stop_reason}")
        text = "".join(b.text for b in r.content if b.type == "text")
        proposed = _parse_model_json(text).get("learnings", [])
        # Safety net: strip any rule that smells coercive even if the model slipped.
        banned = ("always trade", "must trade", "quota", "at least", "every cycle")
        proposed = [p for p in proposed
                    if not any(b in (p.get("rule", "").lower()) for b in banned)]
        # CODE enforces the mechanical invariants the model can't be trusted with
        # (hysteresis lock, clock preservation, count clamping, silent-delete guard).
        learnings, flips = reconcile_learnings(existing, proposed, day)
        return learnings, flips, True
    except Exception as e:
        log(f"learning pass {day} failed: {e}")
        return existing, [], False


def backfill_learnings(days: int = 14):
    """Rebuild the rulebook from the last `days` of logged evidence by REPLAYING each
    trading day in chronological order through the same evidence-weighted pass run_eod
    uses. Confirmations accumulate as a rule recurs; hysteresis uses the real dates.
    Starts from an EMPTY rulebook (the current file is backed up first) so the result is
    a clean chronological build — not a double-count of the most recent day. The file is
    rewritten after each day, so a mid-run failure is resumable and never loses progress."""
    import datetime as _dt
    cutoff = (et_now().date() - _dt.timedelta(days=days)).isoformat()
    day_list = sorted(f.stem for f in cfg.SNAPSHOT_DIR.glob("*.jsonl") if f.stem >= cutoff)
    if not day_list:
        log(f"backfill: no snapshot days within {days}d (since {cutoff})")
        tg_send(f"📚 Backfill: no logged days in the last {days}d — nothing to rebuild.")
        return
    if cfg.LEARNINGS_FILE.exists():
        bak = cfg.LEARNINGS_FILE.with_suffix(".json.bak")
        bak.write_text(cfg.LEARNINGS_FILE.read_text())
        log(f"backfill: backed up current learnings -> {bak.name}")
    log(f"backfill: replaying {len(day_list)} days {day_list[0]}..{day_list[-1]}")
    tg_send(f"📚 Rebuilding learnings from {len(day_list)} days "
            f"({day_list[0]} → {day_list[-1]})…")
    existing, all_flips, failed = [], [], []
    for d in day_list:
        snaps = _load_day_snapshots(d)
        stats = _stats_from_outcomes(d)
        learnings, flips, ok = _learning_pass(d, stats, snaps, existing, snap_cap=40)
        if ok:
            existing = learnings
            cfg.LEARNINGS_FILE.write_text(json.dumps(existing, indent=2, default=str))
            all_flips += flips
            log(f"backfill {d}: {len(snaps)} cycles -> {len(existing)} rules ({len(flips)} events)")
        else:
            failed.append(d)
            log(f"backfill {d}: pass failed — keeping {len(existing)} rules, continuing")
    active = [l for l in existing if l.get("status") != "retired"]
    retired = [l for l in existing if l.get("status") == "retired"]

    def _fmt(l):
        icon = {"active": "• ", "tentative": "🧪 ", "contested": "⚖️ "}.get(l.get("status"), "• ")
        scope = f" [{l['scope']}]" if l.get("scope") else ""
        ev = (l.get("evidence") or "").strip()
        return (f"{icon}{(l.get('rule') or '').strip()}{scope} "
                f"(×{l.get('confirmations', 1)})" + (f"\n   ↳ {ev}" if ev else ""))

    fail_note = f" ({len(failed)} day(s) skipped: {', '.join(failed)})" if failed else ""
    tg_send(f"📚 Learnings rebuilt from {len(day_list) - len(failed)}/{len(day_list)} days{fail_note}. "
            f"{len(active)} active, {len(retired)} retired. {len(all_flips)} contradiction events.")
    if active:
        tg_send_long("📚 Rebuilt rulebook:\n\n" + "\n\n".join(_fmt(l) for l in
                     sorted(active, key=lambda x: -x.get("confirmations", 0))))
    log(f"backfill complete: {len(active)} active, {len(retired)} retired rules")


def run_eod():
    if not _ALPACA_OK:
        log(f"alpaca-py not importable: {_ALPACA_ERR}")
        return
    tc = trading_client()
    day = et_now().strftime("%Y-%m-%d")
    stats = honest_trade_stats(tc)
    snapshots = _load_day_snapshots(day)
    existing = load_learnings()
    learnings, flips, ok = _learning_pass(day, stats, snapshots, existing)
    if ok:
        cfg.LEARNINGS_FILE.write_text(json.dumps(learnings, indent=2, default=str))
        log(f"EOD: wrote {len(learnings)} learnings ({len(flips)} contradiction events)")

    # EOD summary + the LEARNINGS themselves, pushed to Telegram. The whole point of
    # the self-learning loop is that the rules reach the user — not just a count.
    daily = "see Alpaca"
    try:
        a = account_snapshot(tc)
        daily = f"${a['equity']:,.0f} equity"
    except Exception:
        pass
    realized = stats.get("day_realized_pl")
    pl_line = f" | realized P&L ${realized:+,.0f}" if isinstance(realized, (int, float)) else ""
    # What's NEW vs the set we walked in with (match on id), so the message leads with
    # today's deltas rather than re-sending the whole standing rulebook.
    prior_ids = {l.get("id") for l in existing}
    active = [l for l in learnings if l.get("status") != "retired"]
    new_rules = [l for l in active if l.get("id") not in prior_ids]

    _STATUS_ICON = {"active": "• ", "tentative": "🧪 ", "contested": "⚖️ ", "retired": "🗑️ "}

    def _fmt(l):
        flag = _STATUS_ICON.get(l.get("status"), "• ")
        scope = f" [{l['scope']}]" if l.get("scope") else ""
        n = l.get("confirmations", 1)
        ev = (l.get("evidence") or "").strip()
        return (f"{flag}{(l.get('rule') or '').strip()}{scope} (×{n})"
                + (f"\n   ↳ {ev}" if ev else ""))

    header = (f"📒 EOD {day}: {daily}{pl_line}. "
              f"{len(active)} active learnings ({len(new_rules)} new today). "
              f"{len(snapshots)} snapshots reviewed.")
    tg_send(header)
    if new_rules:
        tg_send_long("🆕 New / updated learnings today:\n\n"
                     + "\n\n".join(_fmt(l) for l in new_rules))
    elif active:
        # Nothing new — still surface the current standing rulebook so it's visible.
        tg_send_long("📚 Standing learnings (no new rules today):\n\n"
                     + "\n\n".join(_fmt(l) for l in active))
    # Contradiction events — the whole point of the policy is that flips/contests are
    # visible and auditable, not silent. Summarize what today's evidence did to the book.
    if flips:
        _VERB = {"contested": "⚖️ CONTESTED (held, gathering evidence)",
                 "retired": "🗑️ RETIRED (superseded by stronger evidence)",
                 "blocked": "🔒 FLIP BLOCKED by hysteresis (just changed; held incumbent)",
                 "revived": "♻️ REVIVED from retired",
                 "kept_omitted": "📌 KEPT (model omitted it; no silent delete)"}
        lines = []
        for kind, e, _p in flips:
            if kind == "kept_omitted":
                continue                      # routine guard; don't spam these
            lines.append(f"{_VERB.get(kind, kind)}: {(e.get('rule') or '').strip()}")
        if lines:
            tg_send_long("⚠️ Contradiction events today:\n\n" + "\n\n".join(lines))


# ===========================================================================
# Entry point
# ===========================================================================
def main():
    args = sys.argv[1:]
    mode = args[0] if args else "cycle"
    dry = "--dry-run" in args
    try:
        if mode == "cycle":
            lock = _acquire_cycle_lock() if not dry else True
            if lock is None:
                log("prior cycle still running — skipping this tick")
                return
            try:
                run_cycle(dry=dry)
            finally:
                if lock is not True:
                    lock.close()  # release the flock
        elif mode == "eod":
            run_eod()
        elif mode == "backfill":
            # Rebuild the learnings rulebook from the last N days of logs (default 14)
            # by chronological replay. Usage: autotrade.py backfill [days]
            n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 14
            backfill_learnings(days=n)
        elif mode == "outcomes":
            compute_outcomes()
        elif mode == "growth":
            # Manually run / inspect the growth sleeve (deploy + manage + rotate).
            import growth_sleeve as gs
            st = load_state()
            gs.run(trading_client(), st, dry)
            save_state(st)
            print(json.dumps(gs.summary(st), indent=2))
        elif mode == "overnight":
            # Manually run / inspect the overnight-drift book (sell at open / buy near close).
            import overnight_drift as od
            st = load_state()
            od.run(trading_client(), st, dry)
            save_state(st)
            print(json.dumps(od.summary(st), indent=2))
        elif mode == "status":
            print(status_text(trading_client(), load_state()))
        elif mode == "pnl":
            # Account P&L + open positions (allowlisted; no heredoc needed).
            a = trading_client().get_account()
            eq, le = float(a.equity), float(a.last_equity)
            print(f"equity ${eq:,.2f} | day P&L ${eq-le:+,.2f} ({(eq-le)/le*100:+.2f}%) "
                  f"| cash ${float(a.cash):,.0f}")
            ps = trading_client().get_all_positions()
            print(f"open positions: {len(ps)}")
            for p in sorted(ps, key=lambda x: -abs(float(x.unrealized_pl or 0))):
                print(f"  {p.symbol:24s} qty={float(p.qty):>8.2f}  "
                      f"uPL ${float(p.unrealized_pl or 0):+9.2f}  "
                      f"mv ${float(p.market_value or 0):+10.0f}")
        elif mode == "intel":
            # Options intel (IV-rank + skew) for a symbol, or the index ETFs by default.
            import options_intel as oi
            import yfinance as yf
            syms = [args[1].upper()] if len(args) > 1 else list(cfg.PREMIUM_INDEX_UNDERLYINGS)
            odc, now = option_data_client(), et_now()
            for u in syms:
                try:
                    spot = float(yf.Ticker(u).fast_info.last_price)
                except Exception:
                    spot = None
                info = oi.compute(odc, u, spot, now) if spot else None
                print(json.dumps(info, indent=2) if info else f"{u}: no data")
        elif mode == "warm-iv":
            # Seed/refresh IV-rank for the names we trade options on (no prompts).
            import options_intel as oi
            import yfinance as yf
            names = sorted(set(cfg.MOMENTUM_UNIVERSE) | set(cfg.EARNINGS_UNIVERSE))
            odc, now = option_data_client(), et_now()
            px = yf.download(names, period="1d", interval="1d", progress=False,
                             group_by="ticker", threads=True)
            ok = 0
            for u in names:
                try:
                    df = px[u] if len(names) > 1 else px
                    spot = float(df["Close"].dropna().iloc[-1])
                except Exception:
                    spot = None
                info = None
                try:
                    info = oi.compute(odc, u, spot, now) if spot else None
                except Exception:
                    info = None
                if info and info.get("iv_rank") is not None:
                    ok += 1
                    print(f"  {u:6s} ATM IV {info['atm_iv']:5.1f}%  IV-rank {info['iv_rank']:5.1f}"
                          f"  skew {info['skew']:+6.1f}  ({info['iv_rank_basis']})")
                else:
                    print(f"  {u:6s} -- skipped")
            print(f"warmed {ok}/{len(names)} names")
        else:
            print(__doc__)
    except Exception as e:
        log(f"FATAL {mode}: {e}\n{traceback.format_exc()}")
        tg_send(f"⚠️ Bot error in {mode}: {e}")


if __name__ == "__main__":
    main()
