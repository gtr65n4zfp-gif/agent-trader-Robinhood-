"""
Calibration: measure whether real markets are mispriced, and turn that into an
out-of-sample "model" the strategy can trade — using ONLY observable prices and
realized outcomes, never a hidden true probability.

The idea, and why it's honest:
  - Bin resolved markets by their quoted price.
  - In each bin, the realized fraction of YES resolutions is an *empirical*
    estimate of the true probability at that price. If markets were efficient,
    that fraction would equal the price in every bin. Any systematic gap IS the
    mispricing (e.g. the favorite-longshot bias: longshot bins resolve YES less
    often than their price implies; favorite bins more often).
  - Fit those bins on a TRAIN split; predict on a disjoint TEST split. The model
    never sees the outcome of a market it trades, so there's no lookahead — the
    classic way a mispricing backtest fools itself.

If real Kalshi data shows a big, stable gap, this model will find and (via the
existing fee gate) trade it. If markets are efficient, the gaps are noise, the
gate mostly closes, and fees do the rest. Either way the answer is earned, not
assumed.
"""

from dataclasses import dataclass, field

from .market_sim import Market


@dataclass
class CalibrationModel:
    band_cents: int = 5          # width of each price bin, in cents
    min_count: int = 30          # a bin needs this many samples to be trusted
    shrink_pseudocount: float = 20.0  # pull thin bins toward the price (anti-overfit)

    _rate: dict[int, float] = field(default_factory=dict)   # band -> empirical YES rate
    _count: dict[int, int] = field(default_factory=dict)
    _mean_price: dict[int, float] = field(default_factory=dict)

    def _band(self, price: float) -> int:
        cents = round(price * 100)
        return int(round(cents / self.band_cents) * self.band_cents)

    def fit(self, markets: list[Market]) -> "CalibrationModel":
        wins: dict[int, int] = {}
        n: dict[int, int] = {}
        psum: dict[int, float] = {}
        for m in markets:
            b = self._band(m.yes_price)
            n[b] = n.get(b, 0) + 1
            wins[b] = wins.get(b, 0) + (1 if m.outcome else 0)
            psum[b] = psum.get(b, 0.0) + m.yes_price
        for b in n:
            price_hat = psum[b] / n[b]
            # Shrink the raw win rate toward the bin's own price: thin bins stay
            # near the (efficient) prior, thick bins move to their evidence.
            k = self.shrink_pseudocount
            rate = (wins[b] + k * price_hat) / (n[b] + k)
            self._rate[b] = rate
            self._count[b] = n[b]
            self._mean_price[b] = price_hat
        return self

    def predict(self, market: Market) -> float:
        """Estimated P(YES) for a market, from its price bin. Falls back to the
        price itself when the bin is too thin to trust (i.e. assume efficiency)."""
        b = self._band(market.yes_price)
        if self._count.get(b, 0) >= self.min_count:
            return min(max(self._rate[b], 0.01), 0.99)
        return market.yes_price

    def report(self) -> str:
        lines = [f"  {'price band':>10} {'n':>7} {'mean price':>11} "
                 f"{'YES rate':>9} {'gap':>8}"]
        for b in sorted(self._rate):
            mp = self._mean_price[b]
            rate = self._rate[b]
            gap = rate - mp
            trust = "" if self._count[b] >= self.min_count else "  (thin)"
            lines.append(f"  {b/100:>10.2f} {self._count[b]:>7} {mp:>11.3f} "
                         f"{rate:>9.3f} {gap:>+8.3f}{trust}")
        lines.append("  gap>0: resolves YES more often than priced (favorite "
                     "underpriced)")
        lines.append("  gap<0: resolves YES less often than priced (longshot "
                     "overpriced)")
        return "\n".join(lines)

    def mean_abs_gap(self) -> float:
        """Average |gap| over trusted bins — a one-number summary of how
        mispriced this market set is. Near 0 = efficient."""
        gaps = [abs(self._rate[b] - self._mean_price[b])
                for b in self._rate if self._count[b] >= self.min_count]
        return sum(gaps) / len(gaps) if gaps else 0.0


def train_test_split(markets: list[Market], train_frac: float = 0.6,
                     seed: int = 0) -> tuple[list[Market], list[Market]]:
    import random
    rng = random.Random(seed)
    shuffled = markets[:]
    rng.shuffle(shuffled)
    cut = int(len(shuffled) * train_frac)
    return shuffled[:cut], shuffled[cut:]
