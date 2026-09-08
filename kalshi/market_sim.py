"""
An honest synthetic market generator.

The single most important property of this file: BY DEFAULT THE MARKET IS
EFFICIENT. Each market's quoted price equals its true probability, so no
strategy can have a gross edge, and after fees every strategy LOSES. A backtest
that shows profit on the default settings would be a bug, not a discovery.

To model a *real* opportunity you must deliberately turn on a documented
inefficiency via `mispricing`. The one implemented here is the empirically
well-attested **favorite-longshot bias**: longshots (low-probability outcomes)
trade rich and favorites (high-probability outcomes) trade cheap. Its strength
is a number you choose, and the backtest's honesty is capped by how honest that
number is — which is exactly why the harness reports the assumption alongside the
result instead of hiding it.

Ground truth (`true_prob`) is hidden from the strategy; the strategy only ever
sees `yes_price` and forms its own estimate. Outcomes are drawn from `true_prob`,
so a strategy is ultimately graded by reality, not by its own guess.
"""

import random
from dataclasses import dataclass


@dataclass
class Market:
    ticker: str
    yes_price: float     # quoted YES price in dollars (0.01..0.99), what a trader sees
    true_prob: float     # HIDDEN ground-truth probability YES resolves true
    outcome: bool        # realized resolution, drawn ~ Bernoulli(true_prob)
    day: int


class MarketGenerator:
    """Yields resolved markets grouped by day.

    Parameters
    ----------
    seed : reproducibility.
    markets_per_day : how many distinct markets are available to bet each day.
    mispricing : strength of the favorite-longshot bias, k in [0, ~0.3].
        0.0  -> efficient market (price == true prob). The honest default.
        >0   -> longshots overpriced / favorites underpriced by up to k*|0.5-p|.
    prob_dist : "uniform" spreads true probs across (0,1); "realistic" clusters
        more mass toward the extremes (most real markets aren't coin flips).
    """

    def __init__(self, seed: int = 0, markets_per_day: int = 40,
                 mispricing: float = 0.0, prob_dist: str = "realistic"):
        self._rng = random.Random(seed)
        self.markets_per_day = markets_per_day
        self.mispricing = mispricing
        self.prob_dist = prob_dist
        self._counter = 0

    def _draw_true_prob(self) -> float:
        if self.prob_dist == "uniform":
            p = self._rng.random()
        else:
            # Beta(0.8, 0.8) is gently U-shaped: more markets near 0/1 than at
            # 0.5, which is closer to how event markets actually look.
            p = self._rng.betavariate(0.8, 0.8)
        return min(max(p, 0.02), 0.98)

    def _quoted_price(self, true_prob: float) -> float:
        # Favorite-longshot: push price toward 0.5 relative to truth. At k=0 this
        # is the identity (efficient). Longshots (p<0.5) get pushed UP
        # (overpriced); favorites (p>0.5) get pushed DOWN (underpriced).
        biased = true_prob + self.mispricing * (0.5 - true_prob)
        # Round to the nearest cent, as Kalshi prices are integer cents, and clip
        # into the tradable band.
        price = round(biased, 2)
        return min(max(price, 0.01), 0.99)

    def day(self, day_index: int) -> list[Market]:
        markets: list[Market] = []
        for _ in range(self.markets_per_day):
            true_prob = self._draw_true_prob()
            price = self._quoted_price(true_prob)
            outcome = self._rng.random() < true_prob
            self._counter += 1
            markets.append(Market(
                ticker=f"SIM-{day_index:04d}-{self._counter:06d}",
                yes_price=price, true_prob=true_prob, outcome=outcome,
                day=day_index))
        return markets

    def stream(self, days: int):
        """Yield (day_index, [Market, ...]) for `days` days."""
        for d in range(days):
            yield d, self.day(d)
