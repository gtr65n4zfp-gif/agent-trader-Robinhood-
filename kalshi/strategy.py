"""
The strategy: a fee-aware, EV-gated, fractional-Kelly value bettor.

It is deliberately narrow and does exactly one thing: given a market's quoted
YES price and an *external* probability estimate (from a Forecaster — see
backtest.py), it bets only when the estimated edge clears the fee hurdle by a
safety margin, and sizes the bet by a fraction of Kelly capped hard by the stake
limit.

Everything it refuses to do is on purpose:
  - It does not bet on every market ("bet every day" is the losing instinct);
    most markets fail the edge gate and are skipped.
  - It holds to settlement (one fee), never churns.
  - It never sizes above MAX_STAKE_FRACTION, whatever Kelly says.

The strategy has NO opinion about where the probability estimate comes from or
whether it's any good. That honesty is the point: swap in a real model and the
same gate applies; if the model has no edge, the gate mostly closes and fees do
the rest.
"""

from dataclasses import dataclass

from . import config, fees


@dataclass
class OrderIntent:
    ticker: str
    side: str            # "YES" or "NO"
    contracts: int
    price: float         # YES price to trade at (broker handles NO cost internally)
    model_prob: float
    edge_cents: float    # estimated edge over price, in cents, for the chosen side
    hurdle_cents: float  # break-even fee edge + margin that had to be cleared
    reason: str


def _kelly_stake_fraction(win_prob: float, cost: float) -> float:
    """Full-Kelly stake fraction for a binary contract bought at `cost` with
    estimated win probability `win_prob`. f* = (win_prob - cost) / (1 - cost).
    Returns 0 for non-favorable bets."""
    if cost <= 0 or cost >= 1:
        return 0.0
    f = (win_prob - cost) / (1.0 - cost)
    return max(f, 0.0)


def decide(ticker: str, yes_price: float, model_prob: float,
           bankroll: float) -> OrderIntent | None:
    """Return an OrderIntent, or None to skip. `model_prob` is P(YES) from the
    forecaster; `bankroll` is used only for sizing."""
    price = round(yes_price, 2)
    if not (0.02 <= price <= 0.98):
        # Extreme prices: the cent-rounded fee swamps the thin remaining upside.
        return None

    hurdle_cents = fees.break_even_edge_cents(price) + config.MIN_EDGE_MARGIN_CENTS

    yes_edge_cents = (model_prob - price) * 100.0        # buying YES at `price`
    no_edge_cents = (price - model_prob) * 100.0         # buying NO at (1 - price)

    if yes_edge_cents >= hurdle_cents:
        side, win_prob, cost = "YES", model_prob, price
        edge_cents = yes_edge_cents
    elif no_edge_cents >= hurdle_cents:
        side, win_prob, cost = "NO", 1.0 - model_prob, 1.0 - price
        edge_cents = no_edge_cents
    else:
        return None

    kelly_frac = _kelly_stake_fraction(win_prob, cost) * config.KELLY_FRACTION
    stake_frac = min(kelly_frac, config.MAX_STAKE_FRACTION)
    stake_dollars = stake_frac * bankroll
    contracts = int(stake_dollars // cost)
    if contracts < 1:
        return None

    return OrderIntent(
        ticker=ticker, side=side, contracts=contracts, price=price,
        model_prob=model_prob, edge_cents=edge_cents, hurdle_cents=hurdle_cents,
        reason=(f"{side} edge {edge_cents:.1f}c > hurdle {hurdle_cents:.1f}c; "
                f"stake {stake_frac*100:.2f}% of bankroll"))
