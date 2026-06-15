import sys, os, types
sys.path.insert(0, "/Users/nirvaan/autotrade")

# Redirect cfg log/state to /tmp BEFORE importing autotrade.
import config as cfg
from pathlib import Path
cfg.LOG_FILE = Path("/tmp/test_autotrade.log")
cfg.STATE_FILE = Path("/tmp/test_autotrade_state.json")

import autotrade as at


class Leg:
    def __init__(self, symbol, side, qty, price, oid="leg"):
        self.symbol = symbol
        self.side = side                 # "BUY"/"SELL"
        self.filled_qty = qty
        self.filled_avg_price = price
        self.id = oid


class Order:
    """A parent order. If symbol is set it's a single-leg fill; else legs are expanded."""
    _seq = 0
    def __init__(self, symbol, side, qty, price, filled_day, legs=None, oid=None):
        Order._seq += 1
        self.id = oid or f"ord{Order._seq}"
        self.symbol = symbol
        self.side = side
        self.filled_qty = qty
        self.filled_avg_price = price
        self.legs = legs
        import datetime as _dt
        d = _dt.datetime.strptime(filled_day, "%Y-%m-%d")
        self.filled_at = d.replace(tzinfo=at.ET, hour=14)
        self.submitted_at = self.filled_at


def fresh_state():
    return {"open_lots": {}, "processed_fills": [], "closed_trades": [],
            "realized_ledger": {}, "book_symbols": []}


def apply(state, orders, monkey_since="2000-01-01"):
    """Drive update_pnl_ledger with a fixed orders list (bypass broker)."""
    class TC:
        def get_orders(self, filter=None):
            # one page only; return everything once then empty (pagination stops)
            if getattr(self, "_done", False):
                return []
            self._done = True
            return orders
    return at.update_pnl_ledger(TC(), state, since_day=monkey_since)


PASS = 0
FAIL = 0
def chk(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  {extra}")


def led(state, day, label):
    return state.get("realized_ledger", {}).get(day, {}).get(label, 0.0)


# ---- 1. LONG: open -> add -> partial close -> full close ----
print("[1] long open/add/partial/full close")
s = fresh_state()
# stock AAA, not in any snapshot map -> stock_long
apply(s, [
    Order("AAA", "BUY", 10, 100.0, "2026-06-08"),   # open 10 @100
    Order("AAA", "BUY", 10, 110.0, "2026-06-08"),   # add 10 @110 -> avg 105, qty 20
    Order("AAA", "SELL", 5, 120.0, "2026-06-09"),   # close 5 @120 -> (120-105)*5 = +75
    Order("AAA", "SELL", 15, 90.0, "2026-06-10"),   # close 15 @90 -> (90-105)*15 = -225
])
chk("partial close realized +75 on 6/09", abs(led(s, "2026-06-09", "stock_long") - 75.0) < 1e-6, led(s,"2026-06-09","stock_long"))
chk("full close realized -225 on 6/10", abs(led(s, "2026-06-10", "stock_long") - (-225.0)) < 1e-6, led(s,"2026-06-10","stock_long"))
chk("lot removed after full close", "AAA" not in s["open_lots"])

# ---- 2. SHORT: open short -> cover ----
print("[2] short open -> cover")
s = fresh_state()
apply(s, [
    Order("BBB", "SELL", 10, 50.0, "2026-06-08"),   # short 10 @50
    Order("BBB", "BUY", 10, 40.0, "2026-06-09"),    # cover @40 -> short profit (50-40)*10 = +100
])
chk("short cover realized +100", abs(led(s, "2026-06-09", "stock_short") - 100.0) < 1e-6, led(s,"2026-06-09","stock_short"))
chk("short lot removed", "BBB" not in s["open_lots"])

# ---- 3. FLIP: long 10, sell 25 -> close 10 long, open 15 short ----
print("[3] flip long->short")
s = fresh_state()
apply(s, [
    Order("CCC", "BUY", 10, 100.0, "2026-06-08"),   # long 10 @100
    Order("CCC", "SELL", 25, 120.0, "2026-06-09"),  # close 10 (+200), open short 15 @120
])
chk("flip closes long for +200", abs(led(s, "2026-06-09", "stock_long") - 200.0) < 1e-6, led(s,"2026-06-09","stock_long"))
chk("flip leaves short 15 @120", s["open_lots"].get("CCC", {}).get("qty") == -15 and abs(s["open_lots"]["CCC"]["avg_cost"]-120.0)<1e-6, s["open_lots"].get("CCC"))
chk("flipped lot strategy stock_short", s["open_lots"]["CCC"]["strategy"] == "stock_short")

# ---- 4. OPTIONS mult=100 ----
print("[4] option mult=100")
s = fresh_state()
osym = "SPY260815C00500000"   # OCC-shaped
chk("parse_occ recognizes option", at.parse_occ(osym) is not None)
apply(s, [
    Order(osym, "BUY", 2, 1.00, "2026-06-08"),      # buy 2 contracts @1.00
    Order(osym, "SELL", 2, 1.50, "2026-06-09"),     # sell @1.50 -> (1.5-1.0)*2*100 = +100
])
chk("option realized +100 (x100 mult)", abs(led(s, "2026-06-09", "long_option") - 100.0) < 1e-6, led(s,"2026-06-09","long_option"))

# ---- 5. CROSS-DAY: open day1, close day3, booked on day3 under opening strategy ----
print("[5] cross-day attribution")
s = fresh_state()
apply(s, [
    Order("DDD", "BUY", 4, 200.0, "2026-06-08"),    # open day1
    Order("DDD", "SELL", 4, 250.0, "2026-06-10"),   # close day3 -> +200
])
chk("cross-day booked on close day 6/10", abs(led(s, "2026-06-10", "stock_long") - 200.0) < 1e-6, led(s,"2026-06-10","stock_long"))
chk("nothing booked on open day 6/08", led(s, "2026-06-08", "stock_long") == 0.0)

# ---- 6. IDEMPOTENCY: apply same fills twice -> identical, no double-count ----
print("[6] idempotency")
s = fresh_state()
orders = [
    Order("EEE", "BUY", 10, 10.0, "2026-06-08", oid="oE1"),
    Order("EEE", "SELL", 10, 12.0, "2026-06-09", oid="oE2"),
]
apply(s, orders)
first = led(s, "2026-06-09", "stock_long")
n_proc = len(s["processed_fills"])
apply(s, orders)   # re-apply identical
second = led(s, "2026-06-09", "stock_long")
chk("realized unchanged on 2nd apply", abs(first - second) < 1e-6 and abs(first-20.0)<1e-6, f"{first} vs {second}")
chk("processed_fills did not grow", len(s["processed_fills"]) == n_proc, len(s["processed_fills"]))

# ---- 7. DOUBLE-COUNT GUARD: a book symbol is NOT added to the ledger ----
print("[7] double-count guard")
s = fresh_state()
s["book_symbols"] = ["NVDA"]   # owned by growth sleeve, say
apply(s, [
    Order("NVDA", "BUY", 5, 100.0, "2026-06-08"),
    Order("NVDA", "SELL", 5, 130.0, "2026-06-09"),
])
chk("book symbol produces NO ledger entry", led(s, "2026-06-09", "stock_long") == 0.0 and not s["realized_ledger"].get("2026-06-09"))
chk("book symbol produces NO open lot", "NVDA" not in s["open_lots"])
chk("book symbol fills NOT in processed", len(s["processed_fills"]) == 0)

# ---- 8. multi-leg parent (symbol=None) expands into legs ----
print("[8] multi-leg parent expansion + spread strategy via map")
s = fresh_state()
c1 = "XYZ260815C00100000"
c2 = "XYZ260815C00110000"
# Pre-seed snapshot map by monkeypatching _strategy_map_for_day
orig_map = at._strategy_map_for_day
at._strategy_map_for_day = lambda day: {c1: "debit_spread", c2: "debit_spread"}
try:
    parent_open = Order(None, "BUY", 1, 0.0, "2026-06-08",
                        legs=[Leg(c1, "BUY", 1, 2.0), Leg(c2, "SELL", 1, 1.0)])
    parent_close = Order(None, "SELL", 1, 0.0, "2026-06-09",
                         legs=[Leg(c1, "SELL", 1, 3.0), Leg(c2, "BUY", 1, 1.5)])
    apply(s, [parent_open, parent_close])
finally:
    at._strategy_map_for_day = orig_map
# long c1: (3-2)*1*100=+100 ; short c2: (1-1.5)*1*100=-50 ; net debit_spread = +50
chk("debit_spread net +50 from legs", abs(led(s, "2026-06-09", "debit_spread") - 50.0) < 1e-6, led(s,"2026-06-09","debit_spread"))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
