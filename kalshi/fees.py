"""
Kalshi's real trading-fee formula, isolated and testable.

    fee = ceil( FEE_COEFF * contracts * price * (1 - price) )   [dollars]

Everything about whether this whole idea can work runs through this function,
so it lives alone, is exact (cent-rounding included), and carries its own
sanity checks. If this is wrong, every P&L number downstream is a lie.
"""

import math

from . import config


def trade_fee(contracts: int, price: float, coeff: float | None = None) -> float:
    """Dollar fee for a single order of `contracts` at `price` (0..1 dollars).

    Rounded UP to the next whole cent, exactly as Kalshi charges it. A tiny
    order still pays at least a penny once the raw fee exceeds zero, which is
    itself a reason small binary bets bleed.
    """
    if contracts <= 0:
        return 0.0
    price = _clamp_price(price)
    c = config.FEE_COEFF if coeff is None else coeff
    raw = c * contracts * price * (1.0 - price)
    # Round away float noise (e.g. 1.75 stored as 1.7500000000000002) BEFORE the
    # ceil, or the cent-rounding would over-charge by a penny on exact values.
    cents = round(raw * 100.0, 6)
    return math.ceil(cents) / 100.0


def fee_as_fraction_of_stake(price: float, coeff: float | None = None) -> float:
    """Entry fee as a fraction of the dollars you put at risk, for ONE contract.

    Stake per YES contract is `price` dollars. This is the number that has to be
    beaten by edge before a trade makes money — and it's why 50c markets
    (fee ~= 3.5% of stake) are the worst place to trade frequently.
    """
    price = _clamp_price(price)
    return trade_fee(1, price, coeff) / price


def break_even_edge_cents(price: float, coeff: float | None = None,
                          round_trip: bool = False) -> float:
    """Minimum mispricing, in CENTS of price, needed just to cover fees on one
    contract — i.e. how far your true probability must sit from the quoted price
    before the trade's expected value crosses zero.

    A YES contract bought at `price` when the true probability is `p` has gross
    expected value (p - price) dollars = 100*(p - price) cents per contract.
    Fees per contract are `trade_fee(1, price)` dollars = that *100 cents. So the
    edge (in cents) must exceed the fee (in cents); round_trip doubles the fee
    for strategies that exit early instead of holding to settlement.
    """
    fee_dollars = trade_fee(1, price, coeff)
    if round_trip:
        fee_dollars *= 2
    return fee_dollars * 100.0


def _clamp_price(price: float) -> float:
    # Kalshi prices trade in 1c increments within (0, 1). Clamp defensively so a
    # bad feed can't produce a negative or >1 "fee".
    return min(max(price, 0.01), 0.99)


def _self_check() -> None:
    """Assertions that pin the formula to Kalshi's published numbers. Run via
    `python -m kalshi.fees`."""
    # At 50c the raw fee is 0.07 * 1 * 0.25 = 0.0175 -> rounds up to 0.02.
    assert trade_fee(1, 0.50) == 0.02, trade_fee(1, 0.50)
    # 100 contracts at 50c: 0.07 * 100 * 0.25 = 1.75 exactly -> 1.75.
    assert trade_fee(100, 0.50) == 1.75, trade_fee(100, 0.50)
    # At 95c: 0.07 * 0.95 * 0.05 = 0.003325 -> rounds up to 0.01 (a full cent on
    # a nickel of upside).
    assert trade_fee(1, 0.95) == 0.01, trade_fee(1, 0.95)
    # Break-even edge at 50c is ~4 cents (2c fee, expressed against a 50c stake
    # it's a 2c move needed... in *price* terms the fee is 2c/contract, so the
    # true prob must be >=2c away). Confirm it's 2 cents of price.
    assert abs(break_even_edge_cents(0.50) - 2.0) < 1e-9, break_even_edge_cents(0.50)
    # Round-tripping doubles it.
    assert abs(break_even_edge_cents(0.50, round_trip=True) - 4.0) < 1e-9
    # Fee as fraction of stake at 50c ~= 4% (0.02 / 0.50).
    assert abs(fee_as_fraction_of_stake(0.50) - 0.04) < 1e-9
    print("kalshi.fees self-check passed:")
    for p in (0.50, 0.70, 0.90, 0.95, 0.99):
        print(f"  price={p:.2f}  fee/contract=${trade_fee(1, p):.2f}  "
              f"fee=%stake={fee_as_fraction_of_stake(p)*100:5.2f}%  "
              f"break-even edge={break_even_edge_cents(p):.1f}c (hold) / "
              f"{break_even_edge_cents(p, round_trip=True):.1f}c (round-trip)")


if __name__ == "__main__":
    _self_check()
