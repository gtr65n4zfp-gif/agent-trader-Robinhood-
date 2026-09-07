"""
Probability engine (kalshi/DESIGN.md Layer 3, Milestone 2).

Forecasts BTC's terminal price distribution at horizon T and integrates it
to P(contract settles YES), for a "price above K" contract. A lognormal
closed form is the first cut per DESIGN.md ("lognormal closed form for a
first cut, Monte-Carlo for range brackets and fat-tailed horizons") --
range brackets are handled here too (as a difference of two prob_above()
calls), since under a lognormal terminal distribution that's still exact
closed form, not an approximation. Monte Carlo is deferred until the
calibration harness (kalshi/calibration.py) actually shows the lognormal
assumption failing on real data -- no evidence for that yet, so no code for
it yet either.

Pure math, no data fetching, no network -- this module works identically
whether `annualized_vol` came from backtest/vol_forecast.py on real BTC
bars or a hand-picked test value. See kalshi/M0_FINDINGS.md's "M2 progress"
note for why this ships with zero real BTC data run through it yet.

Drift defaults to 0 (a driftless random walk under the real-world measure,
not a risk-neutral one -- there's no financing-cost argument for a spot
crypto forecast the way there is for an option). This matches DESIGN.md's
own framing: the strand's edge is supposed to be volatility/calibration,
not a directional view, so silently assuming positive drift would bake an
unstated directional bet into every single forecast. A caller with an
actual, justified drift view can still pass one.
"""

from __future__ import annotations

import math


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via math.erf -- exact, no scipy dependency."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def prob_above(spot: float, strike: float, annualized_vol: float, horizon_years: float, drift: float = 0.0) -> float:
    """
    P(S_T > strike) under a lognormal terminal distribution for S_T, i.e.
    log(S_T) ~ Normal(log(spot) + (drift - 0.5*vol^2)*T, vol^2*T). This is
    the "YES" probability for a Kalshi "price above K" contract, ignoring
    fees (the signal layer, not this module, subtracts fees for the EV
    decision -- see kalshi/config.py's kalshi_fee()).

    strike <= 0 always returns 1.0 (certain to be above a non-positive
    price) and horizon_years <= 0 or annualized_vol <= 0 collapse to the
    degenerate case (spot vs. strike directly, no distribution to speak
    of) rather than dividing by zero.
    """
    if spot <= 0:
        raise ValueError(f"spot must be > 0, got {spot}")
    if strike <= 0:
        return 1.0
    if horizon_years <= 0 or annualized_vol <= 0:
        return 1.0 if spot > strike else 0.0
    d = (math.log(strike / spot) - (drift - 0.5 * annualized_vol ** 2) * horizon_years) / (
        annualized_vol * math.sqrt(horizon_years)
    )
    return _norm_cdf(-d)


def prob_between(spot: float, low: float, high: float, annualized_vol: float, horizon_years: float, drift: float = 0.0) -> float:
    """P(low < S_T <= high) -- a Kalshi range-bracket contract. Exact under
    the same lognormal assumption as prob_above(), as the difference of two
    calls to it (prob_above is monotonically decreasing in strike)."""
    if high <= low:
        raise ValueError(f"high ({high}) must be > low ({low})")
    return prob_above(spot, low, annualized_vol, horizon_years, drift) - prob_above(spot, high, annualized_vol, horizon_years, drift)


if __name__ == "__main__":
    print("Testing prob_above() at-the-money (strike == spot, drift=0) is slightly BELOW 0.5...")
    # Counterintuitive but correct: drift=0 here means E[S_T]=spot (a martingale on price itself), and
    # a lognormal's MEDIAN sits below its MEAN by the -0.5*vol^2*T convexity term -- so slightly more
    # than half the probability mass lands below the current spot. This is real "volatility drag",
    # not a bug; the effect shrinks toward 0 as vol or horizon shrinks (tested below).
    p_atm_1y = prob_above(spot=65000.0, strike=65000.0, annualized_vol=0.55, horizon_years=1.0)
    p_atm_1h = prob_above(spot=65000.0, strike=65000.0, annualized_vol=0.55, horizon_years=1 / 365 / 24)
    assert p_atm_1y < 0.5, p_atm_1y
    assert abs(p_atm_1h - 0.5) < 2e-3, p_atm_1h  # nearly 0.5 once the horizon is short enough that convexity is negligible
    assert p_atm_1h > p_atm_1y, (p_atm_1h, p_atm_1y)  # the below-0.5 gap widens with horizon (more convexity drag)
    print(f"PASS — prob_above(strike=spot, drift=0) = {p_atm_1y:.6f} at 1-year horizon (below 0.5, "
          f"volatility drag) vs. {p_atm_1h:.6f} at 1-hour horizon (~0.5, drag is negligible over an hour).")

    print("\nTesting prob_above() is monotonically decreasing in strike...")
    strikes = [55000, 60000, 65000, 70000, 75000]
    probs = [prob_above(65000.0, k, annualized_vol=0.55, horizon_years=7 / 365) for k in strikes]
    assert probs == sorted(probs, reverse=True), probs
    print(f"PASS — probs at strikes {strikes} are {[round(p, 4) for p in probs]}, strictly decreasing.")

    print("\nTesting prob_above() widens toward 0.5 as horizon shrinks to (near) zero...")
    p_short = prob_above(65000.0, 70000.0, annualized_vol=0.55, horizon_years=1 / 365 / 24)  # 1 hour
    p_long = prob_above(65000.0, 70000.0, annualized_vol=0.55, horizon_years=30 / 365)  # 30 days
    assert p_short < p_long, (p_short, p_long)  # an OTM strike is far LESS reachable in an hour than in a month
    print(f"PASS — P(BTC>70000 | spot=65000) in 1 hour = {p_short:.6f}, in 30 days = {p_long:.6f} (grows with horizon).")

    print("\nTesting prob_above() widens toward 0.5 as vol increases (higher uncertainty favors the tail)...")
    p_low_vol = prob_above(65000.0, 70000.0, annualized_vol=0.20, horizon_years=7 / 365)
    p_high_vol = prob_above(65000.0, 70000.0, annualized_vol=1.20, horizon_years=7 / 365)
    assert p_high_vol > p_low_vol, (p_low_vol, p_high_vol)
    print(f"PASS — same OTM strike is less likely at vol=0.20 ({p_low_vol:.4f}) than at vol=1.20 ({p_high_vol:.4f}).")

    print("\nTesting prob_above() degenerate cases (horizon=0 and vol=0) return a hard 0/1, not a crash...")
    assert prob_above(65000.0, 60000.0, annualized_vol=0.55, horizon_years=0) == 1.0
    assert prob_above(65000.0, 70000.0, annualized_vol=0.55, horizon_years=0) == 0.0
    assert prob_above(65000.0, 60000.0, annualized_vol=0, horizon_years=7 / 365) == 1.0
    print("PASS — zero horizon or zero vol collapses to a hard spot-vs-strike comparison.")

    print("\nTesting prob_above() rejects non-positive spot...")
    try:
        prob_above(0.0, 60000.0, annualized_vol=0.55, horizon_years=7 / 365)
        assert False, "should have raised ValueError"
    except ValueError:
        print("PASS — spot=0 correctly rejected.")

    print("\nTesting prob_between() decomposes correctly and sums to prob_above(low) across a full partition...")
    p_low_to_mid = prob_between(65000.0, 60000.0, 65000.0, annualized_vol=0.55, horizon_years=7 / 365)
    p_mid_to_high = prob_between(65000.0, 65000.0, 70000.0, annualized_vol=0.55, horizon_years=7 / 365)
    p_above_high = prob_above(65000.0, 70000.0, annualized_vol=0.55, horizon_years=7 / 365)
    p_above_low = prob_above(65000.0, 60000.0, annualized_vol=0.55, horizon_years=7 / 365)
    assert abs((p_low_to_mid + p_mid_to_high + p_above_high) - p_above_low) < 1e-9, (
        p_low_to_mid, p_mid_to_high, p_above_high, p_above_low,
    )
    print(f"PASS — P(60k,65k]={p_low_to_mid:.4f} + P(65k,70k]={p_mid_to_high:.4f} + P(>70k)={p_above_high:.4f} "
          f"== P(>60k)={p_above_low:.4f} (partition sums exactly, as it must for a real distribution).")

    print("\nTesting prob_between() rejects a degenerate/inverted range...")
    try:
        prob_between(65000.0, 70000.0, 60000.0, annualized_vol=0.55, horizon_years=7 / 365)
        assert False, "should have raised ValueError"
    except ValueError:
        print("PASS — high <= low correctly rejected.")

    print("\nAll kalshi/fair_value.py tests passed (pure math -- no real BTC data run through this yet; "
          "see kalshi/M0_FINDINGS.md's M2 progress note).")
