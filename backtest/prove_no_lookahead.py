"""
backtest/prove_no_lookahead.py — the correctness proof agents/
BACKTEST_DESIGN.md's "No-lookahead proof (task 5)" calls for: the bundle
produced for date D must be IDENTICAL regardless of how much data past D
the underlying source happens to contain. If truncating future data ever
changes what's produced for D, nothing downstream is trustworthy.

Run against REAL fetched data (a real AAPL bar cache, real live SEC
data) — not synthetic — because the whole point is proving the actual
data-layer code path is lookahead-safe, not a simplified stand-in for it.

Run as: python -m backtest.prove_no_lookahead
"""

import json
import os

from agents import fundamentals_seat
from execution import config
from research import sec_client

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


def prove_fundamentals_no_lookahead(ticker: str = "AAPL") -> None:
    """
    For a real historical cutoff date D, compute the Fundamentals brief
    two ways and assert they agree:
      (a) build_brief(ticker, as_of=D) against TODAY's live SEC dataset
          (which contains years of filings AFTER D — the live API always
          returns everything up to the present)
      (b) manually pre-truncating the raw concept points to remove
          everything with filed > D BEFORE running the same trend logic
          — simulating "the data source never had those future filings"

    This is the direct test of the subtle trap this whole design exists
    to catch: `end` (period end) vs `filed` (actually public) are
    different dates, and only `filed` may gate what's "knowable."
    """
    print("=" * 70)
    print(f"PROVING: build_brief({ticker!r}, as_of=D) is invariant to how much")
    print("filing history past D the SEC dataset contains")
    print("=" * 70)

    cik = sec_client.ticker_to_cik(ticker)
    d = "2022-03-15"

    brief_live = fundamentals_seat.build_brief(ticker, as_of=d)

    # Manually replicate the same computation, but truncate each concept's
    # raw points BEFORE they ever reach _trend() — i.e. simulate a data
    # source that literally never had anything past D, rather than
    # relying on fetch_concept_trend()'s own as_of filter to do the work.
    manual_concepts = {}
    from agents.fundamentals_seat import _CONCEPTS, _trend
    for label, tag_candidates in _CONCEPTS.items():
        best_points = []
        for tag in tag_candidates:
            try:
                points = sec_client.get_concept(cik, tag)
            except Exception:
                continue
            points = [p for p in points if p.get("filed") and p["filed"] <= d]
            if points and (not best_points or points[-1]["end"] > best_points[-1]["end"]):
                best_points = points
        manual_concepts[label] = _trend(best_points)

    assert brief_live["concepts"] == manual_concepts, (
        f"LOOKAHEAD LEAK in fundamentals concepts: "
        f"live={brief_live['concepts']} manual={manual_concepts}"
    )

    all_filings = sec_client.get_recent_filings(cik, forms=["10-K", "10-Q", "8-K"], limit=200)
    manual_filings = [f for f in all_filings if f["filing_date"] <= d][:6]
    assert brief_live["recent_filings"] == manual_filings, (
        f"LOOKAHEAD LEAK in recent_filings: live={brief_live['recent_filings']} manual={manual_filings}"
    )

    print(f"  as_of={d}: concepts and recent_filings IDENTICAL between "
          f"as_of-filtered-live-dataset and manually-pre-truncated-dataset")
    for label, trend in brief_live["concepts"].items():
        if trend:
            print(f"    {label}: latest_period={trend['latest_period']} (filed data cannot be from after {d})")
    print(f"    most recent filing used: {brief_live['recent_filings'][0]['filing_date']} "
          f"{brief_live['recent_filings'][0]['form']} (<= {d}: "
          f"{brief_live['recent_filings'][0]['filing_date'] <= d})")
    print("PASS — fundamentals are provably lookahead-safe for the tested date.\n")


if __name__ == "__main__":
    prove_technicals_no_lookahead()
    prove_fundamentals_no_lookahead()
    print("=" * 70)
    print("ALL NO-LOOKAHEAD PROOFS PASSED")
    print("=" * 70)
