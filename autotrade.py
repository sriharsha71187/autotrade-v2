#!/usr/bin/env python3
"""
autotrade.py — single-entry autonomous paper-trading bot.

Usage:
    python3 autotrade.py cycle          # one 5-min trading cycle (what launchd runs)
    python3 autotrade.py eod            # end-of-day LEARNING pass (LLM rules; run manually)
    python3 autotrade.py outcomes       # end-of-day OUTCOME capture (read-only, no LLM)
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
import traceback
from pathlib import Path
from datetime import datetime, timedelta, timezone

import warnings
warnings.filterwarnings("ignore", message=".*OpenSSL.*")

import requests

import config as cfg

# Third-party trading/data SDKs. Imported lazily-tolerant so `status` still works
# even if something isn't installed yet.
try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        MarketOrderRequest, LimitOrderRequest,
        TakeProfitRequest, StopLossRequest, OptionLegRequest,
        GetOrdersRequest,
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
        "last_entry_time": {},      # {symbol: iso-timestamp} for cooldown
        "active_multileg": [],      # [{legs,qty,entry_net,opened}] spreads/condors we manage
        "active_options": [],       # [{symbol, qty, entry, opened}] long options we manage
        "aborted_today": [],        # symbols deliberately aborted (1 abort/symbol/day)
        "loss_override": False,     # OVERRIDE command: keep trading past the daily loss halt (today only)
        "last_action": "",          # human-readable summary of last cycle action
        "focus": None,              # e.g. "TECH" set via Telegram FOCUS command
        "telegram_offset": 0,       # last processed Telegram update_id
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


def tg_poll_commands(state: dict) -> list[str]:
    """Return any new command words (STOP, RESUME, CLOSE ALL, STATUS, STATS,
    FOCUS X). Also reads the file fallback ~/autotrade_command.txt."""
    cmds = []
    # File fallback (works even with no Telegram)
    if cfg.COMMAND_FILE.exists():
        txt = cfg.COMMAND_FILE.read_text().strip()
        if txt:
            cmds.append(txt.upper())
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
                    cmds.append(msg.strip().upper())
        except Exception as e:
            log(f"telegram poll failed: {e}")
    return cmds


# ===========================================================================
# Market hours
# ===========================================================================
def is_market_open(tc) -> bool:
    """Authoritative check via Alpaca clock (handles holidays/half-days)."""
    try:
        return bool(tc.get_clock().is_open)
    except Exception as e:
        log(f"clock check failed, assuming closed: {e}")
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
    if held_min is None or held_min < cfg.TICKER_COOLDOWN_MIN:
        denied.append(f"held {held_min if held_min is None else round(held_min)}m < {cfg.TICKER_COOLDOWN_MIN}m")
    if sym in (state.get("aborted_today") or []):
        denied.append("already aborted today")
    if denied:
        log(f"abort {sym} denied: {denied}")
        return {"status": "abort_denied", "symbol": sym, "reasons": denied}
    if dry:
        log(f"[DRY] would ABORT {sym} (cancel bracket + market-close)")
        return {"status": "dry_run", "action": "abort", "symbol": sym}
    try:
        for o in tc.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100)):
            if o.symbol == sym and not parse_occ(o.symbol):
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
            if intraday is not None:
                try:
                    idf = intraday[s] if len(symbols) > 1 else intraday
                    iclose = idf["Close"].dropna()
                    if len(iclose):
                        last = float(iclose.iloc[-1])
                except Exception:
                    last = None
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
            out.append({"symbol": s, "last": round(last, 2),
                        "day_pct": round(pct, 2), "signal": sig})
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
    try:
        lo = round(spot * (1 - strike_pct), 2)
        hi = round(spot * (1 + strike_pct), 2)
        from alpaca.data.requests import OptionChainRequest
        chain = odc.get_option_chain(OptionChainRequest(
            underlying_symbol=underlying, strike_price_gte=lo, strike_price_lte=hi))
    except Exception as e:
        log(f"option chain fetch failed for {underlying}: {e}")
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
    # Momentum options: a strong mover during 10:00–14:00.
    if 10 <= h < 14:
        for r in scan:
            if abs(r["day_pct"]) >= 2.0 and r["symbol"] not in cfg.BLACKLIST:
                targets.append((r["symbol"], r["last"], False))
                break
    # Any option we already hold, so the model can choose to size a close.
    for p in positions:
        if "option" in (p.get("asset_class", "") or "").lower():
            meta = parse_occ(p["symbol"])
            if meta:
                targets.append((meta["underlying"],
                                spot_of.get(meta["underlying"]) or p.get("current"), False))

    chains, offered = {}, set()
    seen = set()
    for und, spot, today_exp in targets:
        if not und or und in seen or len(seen) >= max_underlyings:
            continue
        seen.add(und)
        rows = option_chain_for(odc, und, spot, want_today_expiry=today_exp)
        if rows:
            chains[und] = rows
            offered.update(r["symbol"] for r in rows)
    return chains, offered


def honest_trade_stats(tc) -> dict:
    """Neutral, no 'best ticker' label. Just realized P&L from today's fills,
    grouped by symbol, presented without ranking commentary."""
    try:
        req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=200)
        orders = tc.get_orders(filter=req)
    except Exception as e:
        log(f"stats fetch failed: {e}")
        return {}
    today = et_now().date()
    by_symbol = {}
    for o in orders:
        if not o.filled_at:
            continue
        if o.filled_at.astimezone(ET).date() != today:
            continue
        sym = o.symbol
        qty = float(o.filled_qty or 0)
        price = float(o.filled_avg_price or 0)
        signed = qty * price * (1 if str(o.side) == "OrderSide.SELL" else -1)
        by_symbol.setdefault(sym, {"net_cash_flow": 0.0, "fills": 0})
        by_symbol[sym]["net_cash_flow"] += signed
        by_symbol[sym]["fills"] += 1
    return {"note": "Today's fills only; cash-flow proxy, not full FIFO P&L. "
                    "Historical edge applies ONLY when today's signal scan agrees.",
            "by_symbol": by_symbol}


def load_learnings() -> list[dict]:
    if cfg.LEARNINGS_FILE.exists():
        try:
            return json.loads(cfg.LEARNINGS_FILE.read_text())
        except Exception:
            return []
    return []


# ===========================================================================
# Pre-filter — decide whether a cycle is worth calling Claude for
# ===========================================================================
def setup_qualifies(state, scan, positions, vix, now) -> tuple[bool, str]:
    # Always call Claude if we hold something that may need managing.
    if positions or state.get("active_multileg") or state.get("active_options"):
        return True, "open positions/condor to manage"
    h, m = now.hour, now.minute
    in_options_window = (h > 10 or (h == 10 and m >= 0)) and h < 14  # 10:00–14:00 ET
    in_condor_window = (h == 10 and 0 <= m <= 30)
    # Momentum: a strong mover during the options window.
    if in_options_window:
        for r in scan:
            if abs(r["day_pct"]) >= 2.0 and r["symbol"] not in cfg.BLACKLIST:
                return True, f"momentum setup: {r['symbol']} {r['day_pct']:+.1f}%"
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


def call_claude(context: dict) -> dict:
    system_prompt = Path(__file__).with_name("alpaca_system_prompt.txt").read_text()
    user_msg = (
        "Here is the current market + account context as JSON. Decide the single "
        "best action for this 5-minute cycle. Respond with ONLY a JSON object, no "
        "prose, no markdown fences.\n\n"
        f"{json.dumps(context, indent=2, default=str)}\n\n"
        "Schema: {\"action\": \"hold|buy_stock|buy_option|multi_leg|iron_condor|close|abort\", "
        "\"symbol\": str|null, \"direction\": \"long|short\", "
        "\"option_symbol\": str|null, \"qty\": int, "
        "\"stop_price\": float|null, \"target_price\": float|null, "
        "\"legs\": [{\"symbol\": str, \"side\": \"buy|sell\"}]|null, "
        "\"net_price\": float|null, "
        "\"condor_legs\": [..]|null, \"net_credit\": float|null, "
        "\"close_symbols\": [..], \"overnight_hold\": bool, "
        "\"conviction\": \"low|medium|high\", \"reasoning\": str}"
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": cfg.ANTHROPIC_API_KEY,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": cfg.CLAUDE_MODEL, "max_tokens": 1200,
                  "system": system_prompt,
                  "messages": [{"role": "user", "content": user_msg}]},
            timeout=60,
        ).json()
        text = "".join(b.get("text", "") for b in r.get("content", [])
                       if b.get("type") == "text")
        return _parse_model_json(text)
    except Exception as e:
        log(f"claude call failed: {e} | raw={r if 'r' in dir() else 'n/a'}")
        return {"action": "hold", "reasoning": f"claude error: {e}",
                "close_symbols": [], "conviction": "low"}


# ===========================================================================
# Order execution
# ===========================================================================
def place_stock_bracket(tc, symbol, qty, side, stop_price, target_price, dry):
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


def close_symbols(tc, symbols, dry):
    for sym in symbols:
        if dry:
            log(f"[DRY] would CLOSE {sym}")
            continue
        try:
            tc.close_position(sym)
            log(f"CLOSED {sym}")
            tg_send(f"✅ Closed {sym}.")
        except Exception as e:
            log(f"close {sym} failed: {e}")


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
        held = {p.symbol for p in tc.get_all_positions()}
    except Exception:
        held = None
    still = []
    for pos in items:
        try:
            legs = pos["legs"]
            qty = int(pos.get("qty", 1))
            entry_net = float(pos["entry_net"])     # $; + credit received, − debit paid
            symbols = [l["symbol"] for l in legs]
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
                if working:
                    age_min = 1e9
                    try:
                        age_min = (et_now() - datetime.fromisoformat(pos["opened"])).total_seconds() / 60
                    except Exception:
                        pass
                    if age_min > cfg.MULTILEG_FILL_TIMEOUT_MIN:
                        log(f"multileg entry {oid} unfilled {age_min:.0f}m — canceling")
                        try:
                            tc.cancel_order_by_id(oid)
                        except Exception as e:
                            log(f"multileg cancel failed: {e}")
                        continue  # drop tracking after cancel
                    still.append(pos)   # order still working; wait for the fill
                    continue
                log(f"multileg {symbols} not held and order not working — dropping")
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
            reason = None
            if entry_net >= 0:               # credit structure
                if pl <= -1.0 * entry_net:
                    reason = f"stop (P&L ${pl:.0f} on ${entry_net:.0f} credit)"
                elif pl >= 0.5 * entry_net:
                    reason = f"target (P&L ${pl:.0f} on ${entry_net:.0f} credit)"
            else:                            # debit structure
                debit = abs(entry_net)
                if pl <= -0.5 * debit:
                    reason = f"stop (P&L ${pl:.0f} on ${debit:.0f} debit)"
                elif pl >= 1.0 * debit:
                    reason = f"target (P&L ${pl:.0f} on ${debit:.0f} debit)"
            if reason:
                log(f"MULTILEG EXIT {symbols}: {reason}")
                tg_send(f"🧩 Closing spread: {reason}.")
                close_symbols(tc, symbols, dry)
                if dry:
                    still.append(pos)
            else:
                still.append(pos)
        except Exception as e:
            log(f"multileg management error: {e}")
            still.append(pos)
    state["active_multileg"] = still


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
            log(f"option {sym} no longer held — dropping from tracking")
            continue  # expired/exercised/closed already
        meta = parse_occ(sym)
        mid = _quote_mid(option_latest_quote(odc, sym))
        reason = None
        if hard_close:
            reason = "0DTE/EOD close" if (meta and meta["expiry"] <= today) else "EOD close"
        elif mid is not None and o.get("entry"):
            pl = (mid - o["entry"]) / o["entry"]
            if pl <= cfg.OPTION_STOP_PCT:
                reason = f"stop {pl:+.0%}"
            elif pl >= cfg.OPTION_TARGET_PCT:
                reason = f"target {pl:+.0%}"
        if reason:
            log(f"OPTION EXIT {sym}: {reason}")
            tg_send(f"📊 Exiting {sym} ({reason}).")
            close_symbols(tc, [sym], dry)
            if dry:
                still.append(o)  # dry-run didn't really close it
        else:
            still.append(o)
    state["active_options"] = still


# ===========================================================================
# Guardrails applied to a model decision before execution
# ===========================================================================
def passes_guardrails(decision, state, acct, now, ref_price=None,
                      offered_options=None, assets=None,
                      option_spread_pct=None) -> tuple[bool, str]:
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
    action = decision.get("action")
    today = now.strftime("%Y-%m-%d")
    if today in cfg.ECON_BLACKOUT_DATES and action not in ("hold", "close"):
        return False, "econ blackout day — no new entries"
    start_eq = state.get("start_equity")
    if start_eq is None:
        start_eq = acct["equity"]
    daily_pl = acct["equity"] - start_eq
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
        notional = qty * ref_price * 100  # 100 shares per contract
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
            try:
                if not dry:
                    tc.close_all_positions(cancel_orders=True)
                state["active_multileg"] = []
                state["active_options"] = []
                tg_send("✅ Closed all positions.")
            except Exception as e:
                tg_send(f"close all failed: {e}")
        elif c.startswith("STATUS"):
            tg_send(status_text(tc, state))
        elif c.startswith("STATS"):
            tg_send(json.dumps(honest_trade_stats(tc), indent=2)[:3500])
        elif c.startswith("FOCUS"):
            parts = c.split()
            state["focus"] = parts[1] if len(parts) > 1 else None
            tg_send(f"🎯 Focus set to {state['focus']}.")


def status_text(tc, state) -> str:
    try:
        a = account_snapshot(tc)
        pos = open_positions(tc)
        start_eq = state.get("start_equity")
        if start_eq is None:
            start_eq = a["equity"]
        daily = a["equity"] - start_eq
        lines = [f"Equity ${a['equity']:,.0f} | Day P&L ${daily:+,.0f}",
                 f"Halted: {state.get('halted')} | Focus: {state.get('focus')}",
                 f"Positions: {len(pos)}",
                 *[f"  {p['symbol']} {p['qty']:g} uPL ${p['unrealized_pl']:+.0f}" for p in pos],
                 f"Last action: {state.get('last_action','-')}"]
        return "\n".join(lines)
    except Exception as e:
        return f"status error: {e}"


# ===========================================================================
# CYCLE
# ===========================================================================
def run_cycle(dry: bool = False):
    if not _ALPACA_OK:
        log(f"alpaca-py not importable: {_ALPACA_ERR}")
        return
    state = load_state()
    tc = trading_client()
    odc = option_data_client() if _ALPACA_OK else None

    # 1. Commands first (so STOP/CLOSE ALL take effect before anything trades)
    cmds = tg_poll_commands(state)
    if cmds:
        handle_commands(tc, state, cmds, dry)

    # Market closed -> just persist state and leave.
    if not is_market_open(tc):
        log("market closed")
        save_state(state)
        return

    acct = account_snapshot(tc)
    if state.get("start_equity") is None:
        state["start_equity"] = acct["equity"]  # first cycle of the day

    # 2. Manage any open spreads + long options (code-enforced exits)
    manage_multileg(tc, odc, state, dry)
    manage_options(tc, odc, state, dry)

    # 2b. Daily loss halt — latches for the rest of the day and alerts once.
    # Skipped while the OVERRIDE day-flag is on (user chose to keep trading).
    daily_pl = acct["equity"] - state["start_equity"]
    if daily_pl <= cfg.DAILY_LOSS_HALT and not state.get("halted") and not state.get("loss_override"):
        state["halted"] = True
        log(f"DAILY LOSS HALT latched: day P&L {daily_pl:+.0f} <= {cfg.DAILY_LOSS_HALT}")
        tg_send(f"🛑 Daily loss halt: day P&L ${daily_pl:+,.0f}. New entries stopped for the day.")
    elif daily_pl <= cfg.DAILY_LOSS_HALT and state.get("loss_override"):
        log(f"loss override ON: day P&L {daily_pl:+.0f} past halt, but trading continues")

    if state.get("halted"):
        log("halted — skipping new decisions")
        state["last_action"] = "halted"
        save_state(state)
        return

    # 3. Build context
    positions = open_positions(tc)
    bracketed = bracketed_symbols(tc) if positions else set()
    universe, assets = build_universe(tc)
    # Quality floor: keep names priced over MIN_PRICE and drop extreme movers
    # (halted low-float runners like STI +513%) that aren't tradeable setups.
    scan = [r for r in signal_scan(universe)
            if r.get("last", 0) >= cfg.MIN_PRICE
            and abs(r.get("day_pct", 0)) <= cfg.MOMENTUM_MAX_DAY_PCT]
    vix = get_vix()
    now = et_now()

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

    context = {
        "now_et": now.isoformat(),
        "account": acct,
        "positions": positions,
        "bracket_managed": sorted(bracketed),
        "signal_scan": scan[:20],
        "option_chains": option_chains,
        "vix": vix,
        "fear_greed": fear_greed(),
        "news": breaking_news([r["symbol"] for r in scan[:10]]),
        "stats": honest_trade_stats(tc),
        "learnings": load_learnings(),
        "focus": state.get("focus"),
        "guardrails": {
            "daily_loss_halt": cfg.DAILY_LOSS_HALT,
            "per_trade_notional_cap": cfg.PER_TRADE_NOTIONAL_CAP,
            "per_option_notional_cap": cfg.PER_OPTION_NOTIONAL_CAP,
            "max_deployed_capital": cfg.MAX_DEPLOYED_CAPITAL,
            "vix_condor_ceiling": cfg.VIX_CONDOR_CEILING,
            "blacklist": cfg.BLACKLIST,
            "no_options_before": "10:00 ET",
            "no_0dte_after": "14:00 ET",
            "condor_window": "10:00–10:30 ET",
            "option_symbols_must_come_from": "option_chains",
        },
        "pre_filter_reason": why,
    }

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
        skip = [s for s in close_targets if s in bracketed]
        do = [s for s in close_targets if s not in bracketed]
        if skip:
            log(f"close skipped — exit handled by bracket: {skip}")
        if do:
            close_symbols(tc, do, dry)
        result = {"status": "close", "action": action,
                  "closed": do, "skipped_bracketed": skip}

    if action == "abort":
        result = abort_position(tc, state, decision, now, dry)

    if action in ("buy_stock", "buy_option", "multi_leg", "iron_condor"):
        ref_price = None
        opt_ask = None
        opt_spread = None
        if action == "buy_stock":
            ref_price = next((r["last"] for r in scan
                              if r["symbol"] == decision.get("symbol")), None)
        elif action == "buy_option":
            bid, opt_ask, ref_price = option_quote(odc, decision.get("option_symbol"))
            if bid and opt_ask and ref_price:
                opt_spread = (opt_ask - bid) / ref_price
        ok, reason = passes_guardrails(decision, state, acct, now, ref_price,
                                       offered_options, assets, opt_spread)
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
                                            decision["target_price"], dry)
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
                        state.setdefault("active_multileg", []).append(
                            {"legs": [{"symbol": l["symbol"], "side": l["side"]} for l in legs],
                             "qty": mlqty, "entry_net": net_price * 100 * mlqty,
                             "order_id": str(o.id), "opened": now.isoformat()})
                result = {"status": "dry_run" if dry else "submitted", "action": action,
                          "order_id": order_id, "ref_price": ref_price}
            except Exception as e:
                log(f"order placement failed for {action}: {e}")
                tg_send(f"⚠️ Order failed ({action}): {e}")
                result = {"status": "order_failed", "action": action, "error": str(e)}

    if decision.get("overnight_hold") and result.get("status") in ("submitted", "dry_run"):
        log(f"OVERNIGHT HOLD reasoning: {decision.get('reasoning','')}")
        tg_send(f"🌙 Holding overnight: {decision.get('reasoning','')[:200]}")

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
    for o in orders:
        if not o.filled_at or o.filled_at.astimezone(ET).date().isoformat() != day:
            continue
        qty = float(o.filled_qty or 0)
        price = float(o.filled_avg_price or 0)
        if qty <= 0 or price <= 0:
            continue
        side = "sell" if "SELL" in str(o.side).upper() else "buy"
        signed = qty * price * (1 if side == "sell" else -1)   # sell + / buy −
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
    rec = {"day": day, "equity_end": equity, "fills": fills, "by_symbol": by_symbol,
           "snapshots": n_snaps,
           "note": "realized_cashflow = sell+/buy− proxy; ≈ realized P&L only for "
                   "symbols fully closed today (open positions distort it)."}
    cfg.OUTCOMES_DIR.mkdir(exist_ok=True)
    (cfg.OUTCOMES_DIR / f"{day}.json").write_text(json.dumps(rec, indent=2, default=str))
    log(f"OUTCOMES {day}: {len(fills)} fills across {len(by_symbol)} symbols; equity={equity}")
    return rec


# ===========================================================================
# EOD learning
# ===========================================================================
def run_eod():
    if not _ALPACA_OK:
        log(f"alpaca-py not importable: {_ALPACA_ERR}")
        return
    tc = trading_client()
    day = et_now().strftime("%Y-%m-%d")
    stats = honest_trade_stats(tc)
    snap_file = cfg.SNAPSHOT_DIR / f"{day}.jsonl"
    snapshots = []
    if snap_file.exists():
        snapshots = [json.loads(l) for l in snap_file.read_text().splitlines() if l.strip()]

    existing = load_learnings()
    system = (
        "You review one trading day and produce LEARNINGS for an autonomous bot. "
        "Rules: (1) every rule MUST cite specific evidence (a fill, a snapshot time, "
        "or a stat). (2) flag any rule from a small sample as tentative. (3) NEVER "
        "produce coercive/quota rules ('must trade X times') or 'always trade <ticker>' "
        "rules — reject those. (4) historical edge applies only when the live signal "
        "scan agrees. Output ONLY a JSON array of "
        "{rule, evidence, tentative(bool), date} objects."
    )
    user = (
        f"Date: {day}\nMeasured stats: {json.dumps(stats, default=str)}\n\n"
        f"Intraday snapshots ({len(snapshots)}): {json.dumps(snapshots[-60:], default=str)}\n\n"
        f"Existing learnings: {json.dumps(existing, default=str)}\n\n"
        "For each 5-min snapshot, what was the optimal action vs what the bot did? "
        "Generate new evidenced rules. Return the FULL updated learnings array "
        "(keep still-relevant existing rules, drop tentative ones not reaffirmed in 14 days)."
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": cfg.ANTHROPIC_API_KEY,
                     "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": cfg.CLAUDE_MODEL, "max_tokens": 2000,
                  "system": system,
                  "messages": [{"role": "user", "content": user}]},
            timeout=120,
        ).json()
        text = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
        learnings = _parse_model_json(text)
        # Safety net: strip any rule that smells coercive even if the model slipped.
        banned = ("always trade", "must trade", "quota", "at least", "every cycle")
        learnings = [r for r in learnings
                     if not any(b in (r.get("rule", "").lower()) for b in banned)]
        cfg.LEARNINGS_FILE.write_text(json.dumps(learnings, indent=2, default=str))
        log(f"EOD: wrote {len(learnings)} learnings")
    except Exception as e:
        log(f"EOD learning failed: {e}")
        learnings = existing

    # EOD summary
    daily = "see Alpaca"
    try:
        a = account_snapshot(tc)
        daily = f"${a['equity']:,.0f} equity"
    except Exception:
        pass
    tg_send(f"📒 EOD {day}: {daily}. {len(learnings)} active learnings. "
            f"{len(snapshots)} snapshots reviewed.")


# ===========================================================================
# Entry point
# ===========================================================================
def main():
    args = sys.argv[1:]
    mode = args[0] if args else "cycle"
    dry = "--dry-run" in args
    try:
        if mode == "cycle":
            run_cycle(dry=dry)
        elif mode == "eod":
            run_eod()
        elif mode == "outcomes":
            compute_outcomes()
        elif mode == "status":
            print(status_text(trading_client(), load_state()))
        else:
            print(__doc__)
    except Exception as e:
        log(f"FATAL {mode}: {e}\n{traceback.format_exc()}")
        tg_send(f"⚠️ Bot error in {mode}: {e}")


if __name__ == "__main__":
    main()
