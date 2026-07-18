"""
backtest/options_valuation.py — Level 2's valuation step (see
agents/SPY_OPTIONS_DESIGN.md): prices a candidate option using MY OWN
forecast sigma (from backtest/vol_forecast.py's Level 0 engines), via
the standard Black-Scholes formula, and compares that "my model price"
against the real historical market premium to compute an edge. Positive
edge = the candidate is worth pursuing.

r=0 (no risk-free rate, no dividend yield) — a stated simplification,
consistent with Level 0's own mu-approximately-zero assumption
(backtest/vol_forecast.py): short-dated SPY options' price sensitivity
to rates/dividends is a second-order effect next to the vol-mispricing
bet this whole strand is actually about. Time-to-expiry uses the SAME
trading-day annualization convention locked in during Task 2
(T = trading_days / 252) — never calendar days — for internal
consistency with the forecast sigma's own scale.

Nothing here fetches data or simulates a trade forward in time — that's
Task 5's job (fills) and options_engine.simulate_option_trade() /
the 2-leg spread engine. This module only answers "is this specific,
already-selected candidate priced favorably relative to my own forecast,
right now, on the signal date."
"""

from __future__ import annotations

import math


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via the error function — math.erf is
    standard library, no new dependency (scipy came in transitively via
    `arch` in Task 2, but this module deliberately doesn't lean on it —
    keeps this module's own dependency footprint at zero beyond stdlib)."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def black_scholes_price(spot: float, strike: float, trading_days_to_expiry: int,
                         sigma: float, option_type: str) -> float:
    """
    Standard Black-Scholes price for a European call/put, r=0 (see
    module docstring for why). trading_days_to_expiry: the option's
    ACTUAL resolved days to expiration, in TRADING days (the same unit
    backtest/vol_forecast.py's engines already annualize against) — never
    calendar days directly. sigma: annualized vol — MY forecast, not the
    market's implied one; using the market's own implied vol here would
    just reproduce the market price, telling us nothing.

    Returns intrinsic value (not a Black-Scholes computation) at
    trading_days_to_expiry <= 0 (already at/past expiration) or
    sigma <= 0 (a degenerate or missing forecast) rather than raising or
    dividing by zero — same "return a sane boundary value, don't crash"
    precedent as options_engine.simulate_option_trade()'s own intrinsic-
    value fallback at expiration.
    """
    if option_type not in ("call", "put"):
        raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")

    if trading_days_to_expiry <= 0 or sigma <= 0:
        return max(0.0, spot - strike) if option_type == "call" else max(0.0, strike - spot)

    T = trading_days_to_expiry / 252
    d1 = (math.log(spot / strike) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if option_type == "call":
        return spot * _norm_cdf(d1) - strike * _norm_cdf(d2)
    return strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def single_leg_edge(my_model_price: float, market_price: float, structure: str) -> float:
    """
    Debit (buying): edge = my_model_price - market_price. Positive means
    I think it's worth more than it costs — buy.
    Credit (selling a single leg — not the 2-leg spread, see
    spread_edge() below): edge = market_price - my_model_price. Positive
    means the premium collected is worth more than I think the risk is.
    """
    if structure == "debit":
        return my_model_price - market_price
    if structure == "credit":
        return market_price - my_model_price
    raise ValueError(f"structure must be 'debit' or 'credit', got {structure!r}")


def spread_model_value(spot: float, sold_strike: float, bought_strike: float,
                        trading_days_to_expiry: int, sigma: float, option_type: str) -> float:
    """
    MY model's net value of a 2-leg vertical credit spread
    (backtest.options_data.select_spread_strikes()'s output): what I'd
    sell the near (sold) leg for minus what I'd pay for the protective
    (bought) leg, both priced with MY forecast sigma. Positive in the
    normal case (the sold leg is nearer the money and thus worth more
    than the further-OTM protective leg).
    """
    sold_price = black_scholes_price(spot, sold_strike, trading_days_to_expiry, sigma, option_type)
    bought_price = black_scholes_price(spot, bought_strike, trading_days_to_expiry, sigma, option_type)
    return sold_price - bought_price


def spread_edge(my_model_net_credit: float, market_net_credit: float) -> float:
    """
    Credit spread edge: market_net_credit - my_model_net_credit.
    Positive means the market is paying MORE for this spread than my own
    model thinks it's worth — the premium collected looks rich relative
    to the risk taken on, by my own forecast.
    """
    return market_net_credit - my_model_net_credit


if __name__ == "__main__":
    print("Testing black_scholes_price — ATM call/put parity sanity check...")
    # For an ATM option (spot == strike) with r=0, call and put should be
    # priced IDENTICALLY -- a textbook put-call parity identity at r=0,
    # not something specific to this implementation, so a good sanity
    # check that the formula itself is right.
    call_atm = black_scholes_price(650.0, 650.0, trading_days_to_expiry=21, sigma=0.15, option_type="call")
    put_atm = black_scholes_price(650.0, 650.0, trading_days_to_expiry=21, sigma=0.15, option_type="put")
    assert abs(call_atm - put_atm) < 1e-9, (call_atm, put_atm)
    print(f"PASS — ATM call and put price identically at r=0 (put-call parity): {call_atm:.4f}")

    print("\nTesting black_scholes_price — higher sigma means a more expensive option...")
    low_vol_price = black_scholes_price(650.0, 650.0, trading_days_to_expiry=21, sigma=0.10, option_type="call")
    high_vol_price = black_scholes_price(650.0, 650.0, trading_days_to_expiry=21, sigma=0.30, option_type="call")
    assert high_vol_price > low_vol_price, (low_vol_price, high_vol_price)
    print(f"PASS — sigma=0.10 -> {low_vol_price:.4f}, sigma=0.30 -> {high_vol_price:.4f} (higher vol, richer price).")

    print("\nTesting black_scholes_price — at/past expiration returns intrinsic value, not a formula blowup...")
    itm_call_at_expiry = black_scholes_price(660.0, 650.0, trading_days_to_expiry=0, sigma=0.15, option_type="call")
    assert itm_call_at_expiry == 10.0, itm_call_at_expiry  # max(0, 660-650)
    otm_put_at_expiry = black_scholes_price(660.0, 650.0, trading_days_to_expiry=0, sigma=0.15, option_type="put")
    assert otm_put_at_expiry == 0.0, otm_put_at_expiry  # max(0, 650-660) -> 0, OTM
    print(f"PASS — at expiration, intrinsic value directly (no sigma/T division): ITM call={itm_call_at_expiry}, OTM put={otm_put_at_expiry}")

    print("\nTesting black_scholes_price — invalid option_type raises clearly...")
    try:
        black_scholes_price(650.0, 650.0, 21, 0.15, "iron_condor")
        raise AssertionError("should have raised ValueError")
    except ValueError as e:
        print(f"PASS — raised clearly: {e}")

    print("\nTesting single_leg_edge...")
    debit_edge = single_leg_edge(my_model_price=7.50, market_price=6.00, structure="debit")
    assert abs(debit_edge - 1.50) < 1e-9, debit_edge
    print(f"PASS — debit: my model says 7.50, market charges 6.00 -> edge +1.50 (looks cheap, buy): {debit_edge}")

    credit_edge = single_leg_edge(my_model_price=5.00, market_price=6.50, structure="credit")
    assert abs(credit_edge - 1.50) < 1e-9, credit_edge
    print(f"PASS — credit: market pays 6.50, my model says it's worth 5.00 -> edge +1.50 (looks rich, sell): {credit_edge}")

    print("\nTesting spread_model_value and spread_edge...")
    net_value = spread_model_value(spot=650.0, sold_strike=645.0, bought_strike=635.0,
                                    trading_days_to_expiry=21, sigma=0.15, option_type="put")
    assert net_value > 0, net_value  # selling the nearer (more expensive) leg, buying the further (cheaper) one
    print(f"PASS — my model's net value of a 645/635 put spread (sell 645, buy 635): {net_value:.4f} (positive, as expected)")

    edge_rich = spread_edge(my_model_net_credit=net_value, market_net_credit=net_value + 0.50)
    assert abs(edge_rich - 0.50) < 1e-9, edge_rich
    print(f"PASS — market pays 0.50 more for this spread than my model thinks it's worth -> edge +0.50 (rich, sell): {edge_rich}")

    # --- Real-world plausibility check: use Task 3's real end-to-end
    # decision inputs (April 2025 crash date) to confirm a resolved
    # candidate's price comes out in a SANE range, not a formula bug
    # producing something absurd on real numbers.
    print("\nTesting black_scholes_price against a real, known SPY level and forecast vol...")
    # SPY closed ~504.38 on 2025-04-07 (the real crash-bottom date used
    # throughout Tasks 2-3's tests); a 7-day-out ATM call priced at a
    # forecast vol in the same ballpark as what Task 2/3 actually
    # produced that day (~30-60% annualized, real crash-level vol)
    # should land somewhere in the single-digit-to-low-teens dollar
    # range for SPY's price level -- not zero, not larger than spot.
    real_atm_price = black_scholes_price(504.38, 504.0, trading_days_to_expiry=5, sigma=0.45, option_type="call")
    assert 3.0 < real_atm_price < 40.0, real_atm_price
    print(f"PASS — a 5-trading-day ATM call on real SPY 504.38, 45% forecast vol: ${real_atm_price:.2f} "
          f"(sane relative to SPY's actual price level that day, not a formula blowup).")

    print("\nAll options_valuation tests passed.")
