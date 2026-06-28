#!/usr/bin/env python3
"""Paper-trade the growth-tilted portfolio on Alpaca. Computes target weights (portfolio_growth),
reads the paper account, and rebalances via notional orders. SAFE BY DEFAULT: prints the plan and
does NOTHING unless run with --execute. Run monthly (+ weekly for the tactical/leverage sleeves).

  python research/paper_trade.py            # dry plan (no orders)
  python research/paper_trade.py --execute  # actually place the orders on the PAPER account
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as cfg
import portfolio_growth as pg

MIN_TRADE = 25.0   # skip dust trades under $25


def main():
    execute = "--execute" in sys.argv
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    target, (c, s, dd) = pg.compute(verbose=False)
    tc = TradingClient(cfg.ALPACA_API_KEY, cfg.ALPACA_SECRET_KEY, paper=True)
    acct = tc.get_account(); equity = float(acct.equity)
    positions = {p.symbol: float(p.market_value) for p in tc.get_all_positions()}

    print(f"\n=== PAPER-TRADE growth portfolio · equity ${equity:,.0f} · "
          f"{'EXECUTE' if execute else 'DRY PLAN (no orders)'} ===")
    print(f"  backtested CAGR {c*100:.1f}% · Sharpe {s:.2f} · maxDD {dd*100:.1f}%\n")
    print(f"  {'symbol':7}{'target$':>11}{'current$':>11}{'delta$':>11}  action")
    orders = []
    syms = sorted(set(list(target.keys()) + list(positions.keys())))
    for sym in syms:
        tgt = target.get(sym, 0.0) * equity
        cur = positions.get(sym, 0.0)
        delta = tgt - cur
        act = ""
        if abs(delta) >= MIN_TRADE:
            side = "BUY" if delta > 0 else "SELL"
            act = f"{side} ${abs(delta):,.0f}"
            orders.append((sym, side, abs(delta)))
        print(f"  {sym:7}{tgt:>11,.0f}{cur:>11,.0f}{delta:>11,.0f}  {act}")
    print(f"\n  {len(orders)} orders to rebalance.")

    if not execute:
        print("  DRY RUN — re-run with --execute to place these on the paper account."); return
    placed = 0
    for sym, side, notional in orders:
        try:
            tc.submit_order(MarketOrderRequest(symbol=sym, notional=round(notional, 2),
                side=OrderSide.BUY if side == "BUY" else OrderSide.SELL, time_in_force=TimeInForce.DAY))
            placed += 1; print(f"    placed {side} {sym} ${notional:,.0f}")
        except Exception as e:
            print(f"    FAILED {side} {sym}: {str(e)[:70]}")
    print(f"\n  {placed}/{len(orders)} orders placed on the PAPER account.")


if __name__ == "__main__":
    main()
