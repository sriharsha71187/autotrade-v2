#!/usr/bin/env python3
"""LLM risk-veto on mechanical momentum picks — the LLM's validated role (catches distressed/
parabolic/stale-ticker names price-momentum is blind to). Combines cheap MECHANICAL flags
(parabolic run, stretched RSI) with an LLM news screen (Alpaca headlines -> Claude) for distress/
going-concern/ticker-artifact risks. Returns {ticker: {verdict, reason}}; fails safe to
mechanical-only (never vetoes on LLM error -> never silently drops a name without a reason).

verdict in {KEEP, DOWNWEIGHT, VETO}. Run standalone: python research/llm_veto.py TICK1 TICK2 ...
"""
import sys, json, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as cfg
import warnings; warnings.filterwarnings("ignore")


def mechanical(tickers):
    """Cheap price-based flags + a default verdict. Catches the worst cases without an LLM:
    DATA-ARTIFACT (renamed/stale ticker, <10mo history -> can't verify, e.g. ECHO) and
    EXTREME PARABOLA (>8x or RSI>95 on a huge run, e.g. SNDK). Healthy momentum -> KEEP."""
    import yfinance as yf, numpy as np, pandas as pd
    px = yf.download(tickers, period="2y", auto_adjust=True, progress=False)["Close"]
    if isinstance(px, pd.Series): px = px.to_frame(tickers[0])
    out = {}
    for t in tickers:
        c = px[t].dropna() if t in px else pd.Series(dtype=float)
        if len(c) < 200:                     # <~10mo history = renamed/new/stale ticker; can't verify 12mo momentum
            out[t] = {"run_12mo": None, "rsi": None, "verdict": "VETO", "flag": "data-artifact (insufficient history)"}; continue
        run = float(c.iloc[-1]/c.iloc[-252]-1) if len(c) >= 252 else float(c.iloc[-1]/c.iloc[0]-1)
        d = c.diff(); up = d.clip(lower=0).rolling(14).mean(); dn = (-d.clip(upper=0)).rolling(14).mean()
        rsi = float((100-100/(1+up/dn.replace(0, np.nan))).iloc[-1])
        if run > 8.0 or (rsi > 95 and run > 4.0):
            v, f = "VETO", f"extreme parabola ({run*100:.0f}% / RSI {rsi:.0f})"
        elif run > 3.0 or rsi > 92:
            v, f = "DOWNWEIGHT", f"stretched ({run*100:.0f}% / RSI {rsi:.0f})"
        else:
            v, f = "KEEP", ""
        out[t] = {"run_12mo": run, "rsi": rsi, "verdict": v, "flag": f}
    return out


def recent_news(ticker, days=30, n=6):
    try:
        from alpaca.data.historical.news import NewsClient
        from alpaca.data.requests import NewsRequest
        nc = NewsClient(cfg.ALPACA_API_KEY, cfg.ALPACA_SECRET_KEY)
        start = dt.datetime.now() - dt.timedelta(days=days)
        r = nc.get_news(NewsRequest(symbols=ticker, start=start, limit=n))
        arts = r.data.get("news", []) if hasattr(r, "data") else r.news
        return [a.headline for a in arts][:n]
    except Exception:
        return []


def _llm_call(prompt):
    import anthropic
    client = anthropic.Anthropic(api_key=cfg.ANTHROPIC_API_KEY)
    for model in (cfg.CLAUDE_MODEL, cfg.CLAUDE_FALLBACK_MODEL):
        try:
            m = client.messages.create(model=model, max_tokens=1500,
                messages=[{"role": "user", "content": prompt}])
            return m.content[0].text
        except Exception:
            continue
    return None


def screen(tickers, use_llm=True):
    """Return {ticker: {verdict, reason, source}} — VETO/DOWNWEIGHT/KEEP. Mechanical always runs;
    LLM refines it when available (adds distress/going-concern detection price can't see)."""
    mech = mechanical(tickers)
    if not use_llm:
        return {t: {"verdict": m["verdict"], "reason": m["flag"] or "no mechanical flags", "source": "mechanical"} for t, m in mech.items()}
    blocks = []
    for t in tickers:
        m = mech[t]; news = recent_news(t)
        run = f"{m['run_12mo']*100:.0f}%" if m['run_12mo'] is not None else "?"
        rsi = f"{m['rsi']:.0f}" if m['rsi'] is not None else "?"
        blocks.append(f"{t}: 12mo run {run}, RSI {rsi}" + (f", MECH-FLAG: {m['flag']}" if m['flag'] else "")
                      + "\n  news: " + (" | ".join(news) if news else "(none found)"))
    prompt = (
        "You are a RISK-VETO on a mechanical momentum strategy that picked these stocks purely on "
        "12-month price momentum. You do NOT pick stocks. Flag ONLY names with risks price-momentum "
        "cannot see: distress/going-concern/default, accounting/fraud/litigation, dilution, a "
        "stale/renamed/wrong ticker, or a parabolic junk-spike (momentum-crash setup). If a name is a "
        "real liquid company with momentum backed by fundamentals, KEEP it — do NOT manufacture concerns.\n\n"
        f"Today: {dt.date.today()}. Candidates:\n\n" + "\n\n".join(blocks) +
        '\n\nReturn ONLY a JSON object: {"TICKER": {"verdict": "KEEP|DOWNWEIGHT|VETO", "reason": "one line"}, ...}')
    txt = _llm_call(prompt)
    verdicts = {}
    if txt:
        try:
            s = txt[txt.index("{"):txt.rindex("}")+1]
            verdicts = json.loads(s)
        except Exception:
            verdicts = {}
    # combine: LLM verdict if valid, else fall back to the mechanical verdict (never silently KEEP a flagged name)
    out = {}
    for t in tickers:
        v = verdicts.get(t) or verdicts.get(t.upper())
        if v and v.get("verdict") in ("KEEP", "DOWNWEIGHT", "VETO"):
            out[t] = {"verdict": v["verdict"], "reason": v.get("reason", ""), "source": "llm"}
        else:
            out[t] = {"verdict": mech[t]["verdict"], "reason": mech[t]["flag"] or "no flags (LLM n/a)", "source": "mechanical"}
    return out


if __name__ == "__main__":
    tks = sys.argv[1:] or ["WDC", "STX", "SNDK", "INTC", "TER", "TTMI", "VIAV", "VICR", "CIEN", "ECHO"]
    for t, v in screen(tks).items():
        print(f"  {t:6} {v['verdict']:11} {v['reason']}")
