import sys; sys.path.insert(0,"/Users/nirvaan/autotrade")
import config as cfg
cfg.DAILY_LOSS_HALT=-1500.0; cfg.DAILY_HALT_CONFIRM_CYCLES=2
def breach(daily_pl, broker_day_pl):
    return (daily_pl <= cfg.DAILY_LOSS_HALT) and (broker_day_pl <= cfg.DAILY_LOSS_HALT)
P=F=0
def chk(n,c):
    global P,F; print(("PASS " if c else "FAIL ")+n); P+=c; F+=(not c)
# the 6/16 case: phantom intraday -3585 but broker day P&L +18 -> NOT a breach
chk("phantom intraday loss + green broker day P&L = NO breach", breach(-3585,+18) is False)
chk("phantom intraday loss + flat broker = NO breach", breach(-3585,+2) is False)
# a REAL loss: both breach
chk("real loss (both below limit) = breach", breach(-1800,-1800) is True)
# intraday red but books offset so broker flat -> gated (no emergency flatten of a flat account)
chk("intraday red, account flat overall = NO breach (gated)", breach(-1600,-50) is False)
# broker red but intraday fine (book loss) -> not an intraday halt
chk("broker red from book loss, intraday fine = NO breach", breach(-100,-1700) is False)
print(f"\n{P} passed, {F} failed"); sys.exit(1 if F else 0)
