#!/usr/bin/env python3
"""Broker abstraction so portfolio_bot can target Alpaca OR Interactive Brokers behind one
interface: equity(), positions(), is_open(), cancel_all(), place(symbol, side, notional).

IBKR needs a running IB Gateway / TWS with the API enabled:
  - paper Gateway port 4002, live Gateway 4001 (TWS paper 7497, live 7496)
  - enable: Gateway/TWS -> Configure -> Settings -> API -> Enable ActiveX/Socket clients,
    add 127.0.0.1 to trusted IPs, set the socket port.
IBKR has no dollar-notional order, so we convert notional->shares via free yfinance prices
(no IBKR market-data subscription required).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg


class AlpacaBroker:
    name = "alpaca"
    def __init__(self):
        from alpaca.trading.client import TradingClient
        self.tc = TradingClient(cfg.ALPACA_API_KEY, cfg.ALPACA_SECRET_KEY, paper=True)
    def equity(self): return float(self.tc.get_account().equity)
    def positions(self): return {p.symbol: float(p.market_value) for p in self.tc.get_all_positions()}
    def is_open(self): return self.tc.get_clock().is_open
    def cancel_all(self): self.tc.cancel_orders()
    def place(self, symbol, side, notional):
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        self.tc.submit_order(MarketOrderRequest(symbol=symbol, notional=round(notional, 2),
            side=OrderSide.BUY if side == "BUY" else OrderSide.SELL, time_in_force=TimeInForce.DAY))
    def disconnect(self): pass


class IBKRBroker:
    name = "ibkr"
    def __init__(self, host="127.0.0.1", port=None, client_id=7):
        from ib_insync import IB
        # IBKR_PORT in env, else default to paper Gateway 4002
        port = port or int(getattr(cfg, "IBKR_PORT", 0) or 4002)
        self.ib = IB(); self.ib.connect(host, port, clientId=client_id, timeout=20)
        self._px = {}
    def equity(self):
        for v in self.ib.accountValues():
            if v.tag == "NetLiquidation" and v.currency in ("USD", "BASE"):
                return float(v.value)
        return 0.0
    def positions(self):
        return {p.contract.symbol: float(p.marketValue) for p in self.ib.portfolio()}
    def is_open(self):
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
            n = datetime.now(ZoneInfo("America/New_York"))
            mins = n.hour*60 + n.minute
            return n.weekday() < 5 and 570 <= mins < 960   # Mon-Fri 09:30-16:00 ET (holidays not handled)
        except Exception:
            return True   # fail-open; IBKR queues/rejects appropriately
    def cancel_all(self): self.ib.reqGlobalCancel()
    def _price(self, symbol):
        if symbol not in self._px:
            import yfinance as yf, warnings; warnings.filterwarnings("ignore")
            h = yf.download(symbol, period="5d", auto_adjust=True, progress=False)["Close"].dropna()
            self._px[symbol] = float(h.iloc[-1]) if len(h) else None
        return self._px[symbol]
    def place(self, symbol, side, notional):
        from ib_insync import Stock, MarketOrder
        px = self._price(symbol)
        if not px: print(f"    no price for {symbol}, skip"); return
        qty = round(notional / px)
        if qty < 1: return
        c = Stock(symbol, "SMART", "USD"); self.ib.qualifyContracts(c)
        self.ib.placeOrder(c, MarketOrder("BUY" if side == "BUY" else "SELL", qty))
    def disconnect(self):
        try: self.ib.disconnect()
        except Exception: pass


def get_broker(name="alpaca"):
    return IBKRBroker() if name == "ibkr" else AlpacaBroker()
