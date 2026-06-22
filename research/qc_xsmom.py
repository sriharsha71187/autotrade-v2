# QuantConnect LEAN — Cross-sectional 6-month momentum (long-only), monthly rebalance.
# Re-validates the one base archetype that showed a real signature in our /tmp sweep,
# but on QC's POINT-IN-TIME, SURVIVORSHIP-BIAS-FREE universe (the rigor gap our test had).
#
# HOW TO RUN (no local install): QuantConnect.com -> free account -> "Create New Algorithm"
# (Python) -> paste this file -> Backtest. Read CAGR / Sharpe / Drawdown / and the SPY
# benchmark line. Compare to our finding (XS-6mo-mom long-only: ~beats random, t_exSPY~3.9,
# +ve 9/11 yrs, beta ~1.2 "smart beta"). If it holds here, the foundation is real.
#
# Design (matches the validated base):
#   Universe : top liquid US equities by dollar volume, price > $5 (QC handles delistings,
#              so NO survivorship bias — the key upgrade over the /tmp run).
#   Signal   : trailing 6-month total return, SKIP the most recent month (the standard
#              Jegadeesh-Titman 12-1 / here 6-1 momentum skip, avoids short-term reversal).
#   Hold     : long the TOP QUINTILE, equal-weight, rebalance MONTHLY.
#   Costs    : default IB fee + slippage models (realistic, not zero).
from AlgorithmImports import *


class CrossSectionalMomentum(QCAlgorithm):

    def Initialize(self):
        self.SetStartDate(2015, 1, 1)
        self.SetEndDate(2025, 12, 31)
        self.SetCash(100_000)
        self.SetBenchmark("SPY")
        # Realistic frictions (QC defaults already model fees+slippage; IB is conservative).
        self.SetBrokerageModel(BrokerageName.InteractiveBrokersBrokerage, AccountType.Margin)
        self.Settings.FreePortfolioValuePercentage = 0.05

        # Parameters (change in QC's "Parameters" panel without editing code).
        self.lookback   = int(self.GetParameter("lookback") or 126)   # 6mo ~ 126 trading days
        self.skip       = int(self.GetParameter("skip") or 21)        # skip last ~1 month
        self.n_universe = int(self.GetParameter("n_universe") or 100) # liquid universe size
        self.top_pct    = float(self.GetParameter("top_pct") or 0.20) # long top quintile

        self.UniverseSettings.Resolution = Resolution.Daily
        self.AddUniverse(self._coarse_filter)

        self._rebalance = False
        # Rebalance monthly: flag on the first trading day of each month, act in OnData.
        self.Schedule.On(self.DateRules.MonthStart("SPY"),
                         self.TimeRules.AfterMarketOpen("SPY", 30),
                         lambda: setattr(self, "_rebalance", True))

        self.SetWarmUp(self.lookback + self.skip + 5, Resolution.Daily)

    def _coarse_filter(self, coarse):
        # Liquid, real-priced names with fundamental/price data; top-N by dollar volume.
        liquid = [c for c in coarse if c.HasFundamentalData and c.Price > 5 and c.DollarVolume > 5e6]
        liquid.sort(key=lambda c: c.DollarVolume, reverse=True)
        return [c.Symbol for c in liquid[: self.n_universe]]

    def OnData(self, data):
        if self.IsWarmingUp or not self._rebalance:
            return
        self._rebalance = False

        # Score each universe name by 6-month-skip-1-month total return (point-in-time history).
        scores = {}
        for sym in self.ActiveSecurities.Keys:
            hist = self.History(sym, self.lookback + self.skip, Resolution.Daily)
            if hist.empty or "close" not in hist.columns:
                continue
            closes = hist["close"].dropna()
            if len(closes) < self.lookback + self.skip:
                continue
            past  = closes.iloc[0]                       # ~ (lookback+skip) days ago
            recent = closes.iloc[-(self.skip + 1)]       # ~ skip days ago (the "skip month")
            if past > 0:
                scores[sym] = (recent - past) / past     # momentum, excluding last month

        if not scores:
            return

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        n_long = max(1, int(len(ranked) * self.top_pct))
        winners = [sym for sym, _ in ranked[:n_long]]

        # Exit anything no longer a winner; equal-weight the winners (long-only).
        for sym in [s for s in self.Portfolio.Keys if self.Portfolio[s].Invested and s not in winners]:
            self.Liquidate(sym)
        w = 1.0 / len(winners)
        for sym in winners:
            self.SetHoldings(sym, w)
