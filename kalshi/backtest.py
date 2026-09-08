"""
The backtest engine: run the strategy over a stream of resolved markets and
report honest, fee-inclusive results.

The Forecaster is the seam where "how good is your model" is made explicit. In
the simulation it is built from the market's hidden true probability plus a
configurable amount of estimation error — so `skill_error=0.30` means "your
forecasts are off by ~30 cents RMS," i.e. nearly useless, while `skill_error=0.0`
means "you are omniscient" (unrealistic, an upper bound). In a real deployment
you delete the Forecaster and feed the strategy your actual model's probability.

This lets the harness answer the real question — *is 5%/yr possible?* — as a
function of two honest inputs: how mispriced the market is (market_sim.mispricing)
and how good your forecasts are (skill_error). If the answer is "only when the
market is very inefficient AND your model is very sharp," that is the finding,
and it costs nothing to learn here.
"""

import random

from . import config, strategy
from .market_sim import Market, MarketGenerator
from .metrics import BacktestResult
from .paper_broker import KalshiPaperBroker, KalshiTradeError


class Forecaster:
    """Turns a market's hidden truth into the estimate a trader would act on.

    skill_error is the RMS error (in probability units) of the forecast. Real
    forecasting of binary events is hard; even good models rarely beat ~0.10.
    """

    def __init__(self, skill_error: float = 0.10, seed: int = 1):
        self.skill_error = skill_error
        self._rng = random.Random(seed)

    def estimate(self, market: Market) -> float:
        if self.skill_error <= 0:
            return market.true_prob
        est = market.true_prob + self._rng.gauss(0.0, self.skill_error)
        return min(max(est, 0.01), 0.99)


def simulate(days_stream, model_fn, starting_cash: float | None = None,
             verbose: bool = False, total_days_hint: int | None = None
             ) -> BacktestResult:
    """The one true engine, shared by synthetic and real backtests.

    days_stream : an iterable of (day_index, [Market, ...]).
    model_fn    : Market -> estimated P(YES). This is the ONLY source of a view;
                  the strategy and broker are identical whatever it is. Swap in a
                  Forecaster (synthetic) or a CalibrationModel (real, out-of-
                  sample) — the fee gate treats them the same.
    """
    broker = KalshiPaperBroker(starting_cash=starting_cash)
    start = broker.bankroll
    equity_curve = [start]
    bets_placed = 0
    days = 0

    for day_index, markets in days_stream:
        days += 1
        broker.start_new_day()
        # Rank the day's opportunities by estimated edge and take best-first, so
        # the stake cap is spent on the strongest bets, not the first ones seen.
        intents = []
        for m in markets:
            model_prob = model_fn(m)
            intent = strategy.decide(m.ticker, m.yes_price, model_prob,
                                     broker.bankroll)
            if intent is not None:
                intents.append((intent, m))
        intents.sort(key=lambda pair: pair[0].edge_cents, reverse=True)

        opened: list[Market] = []
        for intent, m in intents:
            if broker._breaker_blocking_new_bets():
                break
            try:
                broker.buy(intent.ticker, intent.side, intent.contracts,
                           intent.price, intent.reason)
                bets_placed += 1
                opened.append(m)
            except KalshiTradeError:
                # This one bet didn't fit; skip it and keep trying lower-edge
                # ones. A single unplaceable bet must not cancel the whole day.
                continue

        for m in opened:
            broker.settle(m.ticker, yes_won=m.outcome)

        equity_curve.append(broker.bankroll)
        if verbose:
            step = max(1, (total_days_hint or 1) // 10)
            if day_index % step == 0:
                print(f"  day {day_index:4d}: bankroll ${broker.bankroll:,.2f} "
                      f"({len(opened)} bets)")

    wins = sum(1 for t in broker.settled if t.won)
    return BacktestResult(
        days=days, bets_placed=bets_placed, bets_settled=len(broker.settled),
        wins=wins, starting_bankroll=start, ending_bankroll=broker.bankroll,
        total_fees=broker.total_fees_paid, max_drawdown=_max_drawdown(equity_curve),
        equity_curve=equity_curve)


def run_backtest(days: int = 252, markets_per_day: int = 40,
                 mispricing: float = 0.0, skill_error: float = 0.10,
                 prob_dist: str = "realistic",
                 starting_cash: float | None = None,
                 seed: int = 0, verbose: bool = False) -> BacktestResult:
    """Synthetic backtest.

    mispricing : how inefficient the market is (0 = efficient; see market_sim).
    skill_error : how noisy your forecasts are (0 = omniscient; ~0.10 = good).
    """
    gen = MarketGenerator(seed=seed, markets_per_day=markets_per_day,
                          mispricing=mispricing, prob_dist=prob_dist)
    forecaster = Forecaster(skill_error=skill_error, seed=seed + 1)
    return simulate(gen.stream(days), forecaster.estimate,
                    starting_cash=starting_cash, verbose=verbose,
                    total_days_hint=days)


def run_on_markets(markets: list[Market], model_fn, days: int = 252,
                   starting_cash: float | None = None,
                   verbose: bool = False) -> BacktestResult:
    """Backtest over a fixed list of resolved markets (e.g. real Kalshi data),
    spreading them across `days` equal chunks so sizing/compounding is paced like
    the synthetic run rather than betting the whole history in one step."""
    days = max(1, min(days, len(markets)))
    chunk = max(1, len(markets) // days)
    stream = ((i, markets[i * chunk:(i + 1) * chunk]) for i in range(days))
    return simulate(stream, model_fn, starting_cash=starting_cash,
                    verbose=verbose, total_days_hint=days)


def _max_drawdown(curve: list[float]) -> float:
    peak = curve[0] if curve else 0.0
    worst = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, 1.0 - v / peak)
    return worst
