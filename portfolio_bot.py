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


def rebalance(execute=False, lev=1.0, manual_veto=None, broker_name="alpaca", bk=None):
    import portfolio_growth as pg, brokers

    target, (c, s, dd) = pg.compute(verbose=False)
    target, vnote, verdicts = apply_veto(target, manual_veto)
    veto_unavailable = vnote.startswith("⚠")                  # LLM-veto couldn't run -> don't auto-buy unvetted momentum
    target = {t: w*lev for t, w in target.items()}            # leverage

    own = bk is None
    if own: bk = brokers.get_broker(broker_name)
    equity = bk.equity()
    positions = bk.positions()

    print(f"\n=== PORTFOLIO BOT · broker {broker_name} · equity ${equity:,.0f} · leverage {lev}x · "
          f"{'EXECUTE' if execute else 'DRY PLAN'} ===")
    print(f"  backtest(1.0x) CAGR {c*100:.1f}% Sharpe {s:.2f} maxDD {dd*100:.1f}% | ~lev-adj({lev}x): CAGR {c*lev*100:.0f}% maxDD {dd*lev*100:.0f}% (approx)")
    print(f"  veto: {vnote}\n")
    print(f"  {'symbol':7}{'target$':>11}{'current$':>11}{'delta$':>11}  action")
    orders = []; skipped = []
    for sym in sorted(set(list(target) + list(positions))):
        tgt = target.get(sym, 0.0)*equity; cur = positions.get(sym, 0.0); delta = tgt-cur
        act = ""
        if abs(delta) >= 25:
            side = "BUY" if delta > 0 else "SELL"
            # SAFETY: if veto unavailable, don't auto-BUY an unvetted momentum stock (only ETFs + sells/trims)
            if execute and veto_unavailable and side == "BUY" and sym not in ETF_SET:
                skipped.append(sym); act = "HOLD (needs veto)"
            else:
                act = f"{side} ${abs(delta):,.0f}"; orders.append((sym, side, abs(delta)))
        print(f"  {sym:7}{tgt:>11,.0f}{cur:>11,.0f}{delta:>11,.0f}  {act}")
    if skipped: print(f"  ⚠ skipped (unvetted momentum, veto unavailable): {', '.join(skipped)}")
    gross = sum(target.values())
    print(f"\n  gross exposure {gross*100:.0f}% · {len(orders)} orders")

    state = {"ts": str(dt.datetime.now()), "leverage": lev, "equity": equity,
             "target": target, "veto": vnote, "executed": False}
    if not execute:
        STATE.write_text(json.dumps(state, indent=2))
        print("  DRY PLAN — re-run with --execute to place orders.")
        if own: bk.disconnect()
        return
    placed = 0
    for sym, side, notional in orders:
        try:
            bk.place(sym, side, notional); placed += 1
        except Exception as e:
            print(f"    FAILED {side} {sym}: {str(e)[:60]}")
    state["executed"] = True; state["orders_placed"] = placed
    STATE.write_text(json.dumps(state, indent=2))
    if own: bk.disconnect()
    print(f"  {placed}/{len(orders)} orders placed on {broker_name}.")


def loop_mode(lev, manual_veto, broker_name):
    """KeepAlive daemon: wakes periodically, rebalances once per month when the market is open.
    Fresh broker connection each cycle (robust against IBKR Gateway disconnects/daily restart)."""
    import time, brokers
    print(f"portfolio_bot loop started · broker {broker_name} · leverage {lev}x · monthly rebalance")
    while True:
        try:
            bk = brokers.get_broker(broker_name)
            st = json.loads(STATE.read_text()) if STATE.exists() else {}
            ym = dt.datetime.now().strftime("%Y-%m")
            if st.get("last_rebalance_month") != ym and bk.is_open():
                print(f"[{dt.datetime.now()}] monthly rebalance for {ym}")
                rebalance(execute=True, lev=lev, manual_veto=manual_veto, broker_name=broker_name, bk=bk)
                st = json.loads(STATE.read_text()); st["last_rebalance_month"] = ym
                STATE.write_text(json.dumps(st, indent=2))
            bk.disconnect()
        except Exception as e:
            print(f"[{dt.datetime.now()}] loop error: {str(e)[:120]}")
        time.sleep(3 * 3600)


def main():
    lev = float(sys.argv[sys.argv.index("--leverage")+1]) if "--leverage" in sys.argv else 1.0
    mv = sys.argv[sys.argv.index("--veto")+1].split(",") if "--veto" in sys.argv else []
    broker = sys.argv[sys.argv.index("--broker")+1] if "--broker" in sys.argv else "alpaca"
    if "loop" in sys.argv:
        loop_mode(lev, mv, broker)
    else:
        rebalance(execute="--execute" in sys.argv, lev=lev, manual_veto=mv, broker_name=broker)


if __name__ == "__main__":
    main()
