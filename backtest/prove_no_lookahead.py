"""
backtest/prove_no_lookahead.py — the correctness proof agents/
BACKTEST_DESIGN.md's "No-lookahead proof (task 5)" calls for: the
technicals produced for date D must be IDENTICAL regardless of how much
data past D the underlying bar series happens to contain. If truncating
future data ever changes what's produced for D, nothing downstream is
trustworthy.

Run against REAL fetched data (a real AAPL bar cache) — not synthetic —
because the whole point is proving the actual data-layer code path is
lookahead-safe, not a simplified stand-in for it.

Run as: python -m backtest.prove_no_lookahead
"""

import json
import os

from execution import config

from . import data as backtest_data

_BAR_CACHE = os.path.join(
    config.LOG_DIR, "backtests", "_shared_cache", "aapl_2021-12-01_2022-06-29.json"
)


def prove_technicals_no_lookahead() -> None:
    """
    For several real cutoff dates D, compute technicals_as_of() three
    ways and assert all three agree:
      (a) against the full ~7-month fetched series (includes everything
          up to 2022-06-29, i.e. up to ~3.5 months of "future" data
          relative to the latest D tested)
      (b) against a version of the series with EVERYTHING after D
          physically removed before being passed in at all — simulating
          "the data source never had future data to begin with"
      (c) against a version truncated at a DIFFERENT point past D (D+20
          trading days) — proving the amount of extra future data present
          doesn't matter either, not just its presence/absence
    """
    print("=" * 70)
    print("PROVING: technicals_as_of(symbol, D, bars) is invariant to how")
    print("much data past D the bar series contains")
    print("=" * 70)

    with open(_BAR_CACHE) as f:
        raw = json.load(f)
    full_bars = backtest_data.parse_bars("AAPL", raw)
    print(f"Full fetched series: {full_bars[0]['date']} .. {full_bars[-1]['date']} ({len(full_bars)} bars)")

    test_dates = ["2022-02-15", "2022-03-15", "2022-04-14", "2022-05-16"]
    for d in test_dates:
        result_full = backtest_data.technicals_as_of("AAPL", d, full_bars, config.REGIME_EMA_LOOKBACK_DAYS)

        truncated_at_d = backtest_data.bars_through(full_bars, d)
        result_truncated = backtest_data.technicals_as_of(
            "AAPL", d, truncated_at_d, config.REGIME_EMA_LOOKBACK_DAYS
        )

        # A different truncation point past D (20 trading days later) —
        # proves it's not just "future data present/absent" but "amount
        # of future data present" that must not matter.
        idx_d = next(i for i, b in enumerate(full_bars) if b["date"] == d)
        mid_truncated = full_bars[: idx_d + 20]
        result_mid = backtest_data.technicals_as_of(
            "AAPL", d, mid_truncated, config.REGIME_EMA_LOOKBACK_DAYS
        )

        assert result_full == result_truncated == result_mid, (
            f"LOOKAHEAD LEAK on {d}: full={result_full} truncated={result_truncated} mid={result_mid}"
        )
        print(f"  {d}: IDENTICAL across full/truncated-at-D/truncated-at-D+20 "
              f"(price={result_full['price']}, ema={result_full['ema']:.2f}, "
              f"rsi={result_full['rsi']:.1f}, atr_pct={result_full['atr_pct']:.4f})")

    print("PASS — technicals are provably lookahead-safe for all tested dates.\n")


if __name__ == "__main__":
    prove_technicals_no_lookahead()
    print("=" * 70)
    print("NO-LOOKAHEAD PROOF PASSED")
    print("=" * 70)
