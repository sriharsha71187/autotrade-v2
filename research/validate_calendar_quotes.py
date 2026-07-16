#!/usr/bin/env python3
"""Reality-check the double-calendar model against LIVE SPY quotes:
real mid debit + real bid/ask widths for the 4 legs vs the model's BS debit."""
import math, datetime as dt
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import config as cfg
from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest, OptionLatestQuoteRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest

sc = StockHistoricalDataClient(cfg.ALPACA_API_KEY, cfg.ALPACA_SECRET_KEY)
oc = OptionHistoricalDataClient(cfg.ALPACA_API_KEY, cfg.ALPACA_SECRET_KEY)
tc = TradingClient(cfg.ALPACA_API_KEY, cfg.ALPACA_SECRET_KEY, paper=True)

S = sc.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols="SPY"))["SPY"].price
print(f"SPY = {S:.2f}")

today = dt.date.today()
req = GetOptionContractsRequest(underlying_symbols=["SPY"], expiration_date_gte=today+dt.timedelta(days=11),
                                expiration_date_lte=today+dt.timedelta(days=24), limit=10000)
cons = tc.get_option_contracts(req).option_contracts
exps = sorted({c.expiration_date for c in cons})
if len(exps) < 2: raise SystemExit(f"not enough expiries: {exps}")
es, el = exps[0], exps[-1]
ds, dl = (es-today).days, (el-today).days
print(f"short exp {es} ({ds}d) / long exp {el} ({dl}d)")

# strikes at +/- 0.5x 14d expected move, IV~0.16 fallback
em = 0.5*S*0.16*math.sqrt(ds/365.0)
Kc = min((c.strike_price for c in cons), key=lambda k: abs(k-(S+em)))
Kp = min((c.strike_price for c in cons), key=lambda k: abs(k-(S-em)))
print(f"expected-move half-width {em:.2f} -> Kc={Kc} Kp={Kp}")

legs = {}
for c in cons:
    if c.expiration_date in (es, el) and c.strike_price in (Kc, Kp):
        cp = c.type.value[0]
        if (cp == "c" and c.strike_price == Kc) or (cp == "p" and c.strike_price == Kp):
            legs[(c.expiration_date, cp)] = c.symbol
syms = list(legs.values())
q = oc.get_option_latest_quote(OptionLatestQuoteRequest(symbol_or_symbols=syms))
debit, tot_spread = 0.0, 0.0
for (exp, cp), sym in sorted(legs.items()):
    bid, ask = q[sym].bid_price, q[sym].ask_price
    mid = (bid+ask)/2; sp = ask-bid
    sign = +1 if exp == el else -1
    debit += sign*mid; tot_spread += sp
    print(f"  {'LONG ' if sign>0 else 'SHORT'} {sym:22} bid {bid:6.2f} ask {ask:6.2f} mid {mid:6.2f} spread {sp:5.2f}")
print(f"\nreal mid net debit: {debit:.2f}/share (${debit*100:.0f}/spread)")
print(f"sum of 4 leg spreads: {tot_spread:.2f} -> avg half-spread/leg {tot_spread/8:.3f}")
print(f"round-trip cost (8 leg-sides at half-spread) as % of debit: {tot_spread/debit*100:.0f}%")
