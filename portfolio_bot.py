#!/usr/bin/env python3
"""PORTFOLIO BOT — the application. Runs the growth-tilted multi-sleeve algorithm:
  risk-parity+target-vol / vol-target-QQQ / stock-momentum(+LLM-veto) / leveraged-QQQ+regime / thematic-trend
applies leverage, and rebalances the Alpaca paper account. Idempotent monthly rebalance + weekly
tactical check. SAFE: dry-plan by default; --execute places orders.

  python portfolio_bot.py                 # show the plan (no orders)
  python portfolio_bot.py --execute       # rebalance the paper account
  python portfolio_bot.py --leverage 1.5  # apply 1.5x (50% margin)

LLM-veto: auto when ANTHROPIC API credits exist; otherwise holds all momentum names and flags for
manual review (never false-vetoes a healthy name). State -> ~/.portfolio_bot_state.json
"""
import sys, json, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "research"))
import config as cfg
import warnings; warnings.filterwarnings("ignore")

ETF_SET = {"SPY","TLT","GLD","QQQ","QLD","IEF","SMH","SOXX","IGV","XBI","XOP","GDX","KRE","ITB",
           "XRT","XME","TAN","IYT","ARKK","JETS","SPMO"}
STATE = Path.home() / ".portfolio_bot_state.json"


def apply_veto(target, manual_veto=None):
    """Veto the momentum-sleeve STOCKS (not ETFs). manual_veto = tickers to force-VETO (e.g. from
    an in-session LLM review). Returns (new_target, note, verdicts)."""
    import llm_veto
    manual_veto = set(manual_veto or [])
    stocks = [t for t in target if t not in ETF_SET]
    if not stocks:
        return target, "no momentum stocks held", {}
    verdicts = llm_veto.screen(stocks, use_llm=True)
    real_llm = any(v["source"] == "llm" for v in verdicts.values())
    if not real_llm:
        # mechanical-only is unreliable for distress (misses ECHO, false-vetoes WDC) -> trust ONLY manual vetoes
        for t in stocks:
            verdicts[t] = ({"verdict": "VETO", "reason": "manual/in-session LLM review", "source": "manual"}
                           if t in manual_veto else {"verdict": "KEEP", "reason": "held (no auto-veto)", "source": "held"})
        if not manual_veto:
            return target, f"⚠ LLM-veto UNAVAILABLE (no API credits) — held all {len(stocks)} momentum names; MANUAL review recommended: {', '.join(stocks)}", verdicts
    else:
        for t in manual_veto:                      # in-session override on top of real LLM
            verdicts[t] = {"verdict": "VETO", "reason": "manual/in-session LLM review", "source": "manual"}
    new = dict(target); freed = 0.0; kept = []
    for t in stocks:
        v = verdicts[t]["verdict"]
        if v == "VETO": freed += new.pop(t, 0.0)
        elif v == "DOWNWEIGHT": freed += new[t]*0.5; new[t] *= 0.5; kept.append(t)
        else: kept.append(t)
    if kept and freed > 0:
        for t in kept: new[t] += freed/len(kept)
    vetoed = [t for t in stocks if verdicts[t]["verdict"] == "VETO"]
    return new, f"veto applied: dropped {vetoed or 'none'}; redistributed to {len(kept)} survivors", verdicts


def main():
    execute = "--execute" in sys.argv
    lev = 1.0
    if "--leverage" in sys.argv: lev = float(sys.argv[sys.argv.index("--leverage")+1])
    import portfolio_growth as pg
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    manual_veto = []
    if "--veto" in sys.argv: manual_veto = sys.argv[sys.argv.index("--veto")+1].split(",")
    target, (c, s, dd) = pg.compute(verbose=False)
    target, vnote, verdicts = apply_veto(target, manual_veto)
    target = {t: w*lev for t, w in target.items()}            # leverage

    tc = TradingClient(cfg.ALPACA_API_KEY, cfg.ALPACA_SECRET_KEY, paper=True)
    acct = tc.get_account(); equity = float(acct.equity)
    positions = {p.symbol: float(p.market_value) for p in tc.get_all_positions()}

    print(f"\n=== PORTFOLIO BOT · equity ${equity:,.0f} · leverage {lev}x · "
          f"{'EXECUTE' if execute else 'DRY PLAN'} ===")
    print(f"  backtest CAGR {c*100:.1f}% · Sharpe {s:.2f} · maxDD {dd*100:.1f}%")
    print(f"  veto: {vnote}\n")
    print(f"  {'symbol':7}{'target$':>11}{'current$':>11}{'delta$':>11}  action")
    orders = []
    for sym in sorted(set(list(target) + list(positions))):
        tgt = target.get(sym, 0.0)*equity; cur = positions.get(sym, 0.0); delta = tgt-cur
        act = ""
        if abs(delta) >= 25:
            side = "BUY" if delta > 0 else "SELL"; act = f"{side} ${abs(delta):,.0f}"; orders.append((sym, side, abs(delta)))
        print(f"  {sym:7}{tgt:>11,.0f}{cur:>11,.0f}{delta:>11,.0f}  {act}")
    gross = sum(target.values())
    print(f"\n  gross exposure {gross*100:.0f}% · {len(orders)} orders")

    state = {"ts": str(dt.datetime.now()), "leverage": lev, "equity": equity,
             "target": target, "veto": vnote, "executed": False}
    if not execute:
        STATE.write_text(json.dumps(state, indent=2))
        print("  DRY PLAN — re-run with --execute to place orders."); return
    placed = 0
    for sym, side, notional in orders:
        try:
            tc.submit_order(MarketOrderRequest(symbol=sym, notional=round(notional, 2),
                side=OrderSide.BUY if side == "BUY" else OrderSide.SELL, time_in_force=TimeInForce.DAY))
            placed += 1
        except Exception as e:
            print(f"    FAILED {side} {sym}: {str(e)[:60]}")
    state["executed"] = True; state["orders_placed"] = placed
    STATE.write_text(json.dumps(state, indent=2))
    print(f"  {placed}/{len(orders)} orders placed on the PAPER account.")


if __name__ == "__main__":
    main()
