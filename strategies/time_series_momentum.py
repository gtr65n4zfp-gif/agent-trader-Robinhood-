"""
strategies/time_series_momentum.py -- the trend-following (time-series
momentum) strategy signal. First entry in STRATEGY_LIBRARY.md's build order.

THE RULE, in one line: if an instrument's own price has trended up over the
past ~year, lean long; if it's trended down, lean bearish; if it's gone
roughly nowhere, stay out. This is the classic "12-1" momentum signal
(Moskowitz, Ooi & Pedersen, SSRN 2089463): measure the trailing 12-month
return but SKIP the most recent month, because very-short-term moves tend to
reverse and would otherwise pollute the longer trend read.

LONG-ONLY IN THIS SYSTEM: the paper literature trades both sides, but
execution/paper_broker.py cannot short ("Can't sell X; only hold Y"). So
here a bearish stance can only mean "don't hold it / get out," never "short
it." backtest/momentum_backtest.py scores the long-only version as the
headline for exactly this reason.

The windows are in BARS, not calendar time: the defaults (252 lookback, 21
skip) assume daily bars; pass 12 and 1 for monthly bars, the frequency the
momentum literature itself uses.

Domain-isolated like agents/technicals.py: it only ever sees this symbol's
own closes, nothing else, and emits the SAME structured view shape
every strategy in this project returns -- {seat, symbol, stance, confidence,
reasons} -- so agents/judge.py and the backtest consume it with no special
cases. Pure, rule-based, no network, no fetching, no model to fit: given the
closes, the output is fully determined.

Not wired into the live gate yet -- it must clear its backtest first (see
STRATEGY_LIBRARY.md's promotion gate and backtest/momentum_backtest.py),
exactly like every other strategy here.
"""

from __future__ import annotations

from statistics import median

# "12-1" convention, in trading days: ~252 trading days = 12 months of
# lookback; skip the most recent ~21 (one month). Both are the textbook
# momentum windows, not tuned against this project's own data -- same
# caveat agents/technicals.py's thresholds carry, to be revisited once a
# real paper track record exists (Milestone 4).
LOOKBACK_DAYS = 252
SKIP_DAYS = 21

# Neutral band: a trailing return smaller in magnitude than this fraction of
# the symbol's own typical momentum move counts as "no real trend" -> a
# neutral, no-trade stance rather than a coin-flip. Same shape as
# agents/forecast_seat.py's 0.25x-typical band, for consistency.
NEUTRAL_BAND_FRAC = 0.25

# Fewest historical momentum readings needed to estimate "typical" move size
# reliably. Below this, we can't scale confidence honestly, so we degrade to
# a neutral, zero-confidence view rather than guessing -- the same
# "never fake a stance" discipline as agents/forecast_seat.py's missing-data
# path.
_MIN_READINGS_FOR_TYPICAL = 20


def trailing_return(closes: list[float], lookback_days: int = LOOKBACK_DAYS,
                    skip_days: int = SKIP_DAYS) -> float | None:
    """
    The 12-1 momentum value as of the LAST close in `closes`: the return
    from `lookback_days` ago up to `skip_days` ago. None if there isn't
    enough history (never extrapolated).

    closes: this symbol's closing prices in chronological order, already
    truncated to the as-of date by the caller (backtest/data.py's
    bars_through() is the no-lookahead choke point -- this function does no
    truncation of its own).
    """
    if lookback_days <= skip_days:
        raise ValueError(f"lookback_days ({lookback_days}) must exceed skip_days ({skip_days})")
    # Need the close at index -(lookback_days + 1) to exist.
    if len(closes) < lookback_days + 1:
        return None
    start = closes[-(lookback_days + 1)]
    end = closes[-(skip_days + 1)]
    if start <= 0:
        return None
    return (end - start) / start


def typical_abs_return(closes: list[float], lookback_days: int, skip_days: int) -> float | None:
    """
    Median absolute 12-1 momentum reading across all historical dates in
    `closes` where it's computable -- the symbol's own "normal" trend
    magnitude, used to set the neutral band and scale confidence. None if
    there aren't enough readings to estimate it honestly.

    Computed from history only; it does not peek past the last close (each
    reading at index i uses only closes[:i+1]), so it carries no lookahead
    of its own.
    """
    readings: list[float] = []
    for i in range(lookback_days, len(closes)):
        start = closes[i - lookback_days]
        end = closes[i - skip_days]
        if start > 0:
            readings.append(abs((end - start) / start))
    if len(readings) < _MIN_READINGS_FOR_TYPICAL:
        return None
    return median(readings)


def build_view(symbol: str, momentum: float | None, typical_abs_return: float | None) -> dict:
    """
    Reduce a 12-1 momentum reading into the standard strategy view.

    momentum: trailing_return() output for this symbol as of the decision
    date, or None if there wasn't enough history.
    typical_abs_return: typical_abs_return() output -- the symbol's own
    normal trend magnitude, used to scale. None if it couldn't be estimated.

    Returns {seat, symbol, stance, confidence, reasons}. A missing momentum
    OR a missing typical falls through to a neutral, zero-confidence view --
    never a guessed direction.
    """
    symbol = symbol.upper()

    if momentum is None or typical_abs_return is None or typical_abs_return <= 0:
        return {
            "seat": "momentum", "symbol": symbol, "stance": "neutral",
            "confidence": 0.0,
            "reasons": ["insufficient history for a trend read"],
        }

    band = NEUTRAL_BAND_FRAC * typical_abs_return
    if momentum > band:
        stance = "bullish"
    elif momentum < -band:
        stance = "bearish"
    else:
        stance = "neutral"

    # A move equal to the symbol's own typical magnitude is full confidence;
    # a move at the neutral band is 0.25; capped at 1.0. Same scaling shape
    # as agents/forecast_seat.py.
    confidence = 0.0 if stance == "neutral" else round(min(1.0, abs(momentum) / typical_abs_return), 4)

    return {
        "seat": "momentum", "symbol": symbol, "stance": stance,
        "confidence": confidence,
        "reasons": [
            f"12-1 trailing return {momentum * 100:+.1f}% "
            f"(typical {typical_abs_return * 100:.1f}%, band ±{band * 100:.1f}%)"
        ],
    }


def signal_from_closes(symbol: str, closes: list[float],
                       lookback_days: int = LOOKBACK_DAYS, skip_days: int = SKIP_DAYS) -> dict:
    """
    Convenience: compute the momentum reading AND the typical move size from
    one chronological close series (already truncated to the as-of date by
    the caller), then build the view. This is what the backtest and any
    future live wiring call; build_view() stays separately testable with
    hand-made numbers.
    """
    momentum = trailing_return(closes, lookback_days, skip_days)
    typical = typical_abs_return(closes, lookback_days, skip_days)
    return build_view(symbol, momentum, typical)


if __name__ == "__main__":
    # Self-test with synthetic closes -- deterministic, no network needed.

    # A long, steady uptrend -> bullish with real confidence.
    up = [100.0 * (1.003 ** i) for i in range(300)]
    view = signal_from_closes("TEST", up)
    print("Steady uptrend:", view)
    assert view["stance"] == "bullish", view
    assert view["confidence"] > 0.0, view

    # A long, steady downtrend -> bearish.
    down = [100.0 * (0.997 ** i) for i in range(300)]
    view = signal_from_closes("TEST", down)
    print("Steady downtrend:", view)
    assert view["stance"] == "bearish", view

    # Neutral band: a move SMALL relative to the symbol's own typical trend
    # reads as "no real trend" -> neutral, zero confidence. Tested directly
    # with hand-made numbers (a self-normalized signal makes a synthetic
    # "flat" series a poor fixture -- a tiny oscillation is still a trend
    # relative to its own tiny typical move; that's correct behavior).
    view = build_view("TEST", momentum=0.001, typical_abs_return=0.01)  # 0.1% move vs 1% typical, band ±0.25%
    print("Inside the neutral band:", view)
    assert view["stance"] == "neutral" and view["confidence"] == 0.0, view

    # Just OUTSIDE the band -> directional, with partial confidence.
    view = build_view("TEST", momentum=0.004, typical_abs_return=0.01)  # 0.4% > 0.25% band
    print("Just outside the band:", view)
    assert view["stance"] == "bullish" and 0.0 < view["confidence"] < 1.0, view

    # Too little history -> neutral, zero confidence, not a guess.
    view = signal_from_closes("TEST", up[:50])
    print("Insufficient history:", view)
    assert view["stance"] == "neutral" and view["confidence"] == 0.0, view

    # trailing_return skips the most recent month: a sharp DROP in only the
    # last few days must not flip a year-long uptrend's reading negative.
    spiked_down = up[:-5] + [up[-6] * 0.80] * 5
    r = trailing_return(spiked_down)
    print(f"12-1 return ignores a last-week crash: {r * 100:+.1f}% (still positive)")
    assert r is not None and r > 0, r

    print("\nPASS -- all time-series-momentum signal self-tests passed.")
