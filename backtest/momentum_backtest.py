"""
backtest/momentum_backtest.py -- validation harness for the time-series
momentum strategy (strategies/time_series_momentum.py). The promotion gate
from STRATEGY_LIBRARY.md, in code: does trend-following actually beat
(a) a naive always-long baseline and (b) buy-and-hold, on this symbol's
own history -- before it's ever allowed into the live gate.

Agent-mediated like every other backtest module: it does NOT fetch bars or
call any MCP tool. Whatever real historical run drives this passes in
already-fetched bars (backtest/data.py's parse_bars() output); the self-test
below uses synthetic bars so it runs offline with no data.

WHAT IT MEASURES, in plain terms: step through history day by day; on each
day, ask the strategy "long, short, or stay out?" using ONLY prices up to
that day (no peeking ahead -- backtest/data.py's bars_through() is the choke
point), then look at what the price ACTUALLY did over the next `horizon_days`
and score whether the call was right. Two yardsticks:
  - directional accuracy vs. a naive "always bullish" baseline, and
  - a simple cumulative return from following the signal vs. buy-and-hold.

HONEST LIMITATION (stated, not hidden): overlapping forward-return windows
make the per-day observations autocorrelated, so the confidence interval is
optimistic -- this is a screening harness to catch a signal with NO edge, not
a publication-grade significance test. The real per-trade accounting still
happens through the full engine (backtest/engine.py) + PaperBroker, with
modeled fees/slippage, once a signal clears this screen.
"""

from __future__ import annotations

from strategies import time_series_momentum as tsm

from . import data as backtest_data
from . import metrics as equity_metrics

# Forward horizon the signal is scored against: ~1 month, matching the
# monthly rebalance the 12-1 momentum literature uses.
HORIZON_DAYS = 21


def _forward_return(closes: list[float], i: int, horizon_days: int) -> float | None:
    """Return from close[i] to close[i + horizon_days], or None if there
    aren't that many bars left after i."""
    if i + horizon_days >= len(closes):
        return None
    start = closes[i]
    if start <= 0:
        return None
    return (closes[i + horizon_days] - start) / start


def run_validation(bars: list[dict], horizon_days: int = HORIZON_DAYS,
                   lookback_days: int = tsm.LOOKBACK_DAYS, skip_days: int = tsm.SKIP_DAYS) -> dict:
    """
    Step through `bars` (chronological parse_bars() output), generate the
    momentum signal at each date from only the bars up to that date, score
    it against the realized forward return, and compare to the two baselines.

    Returns a dict with the strategy's and baselines' directional accuracy
    (+ Wilson CI), a cumulative-return comparison, and `beats_baseline` /
    `beats_buyhold` flags -- the promotion-gate checks from
    STRATEGY_LIBRARY.md.
    """
    closes = [b["close"] for b in bars]
    dates = [b["date"] for b in bars]

    # Signal needs lookback_days+1 of history; forward return needs
    # horizon_days of future. Both must fit, so only interior dates are
    # scorable.
    directional_correct = 0
    directional_total = 0          # non-neutral calls only (a "stay out" has no direction to score)
    baseline_correct = 0           # naive "always bullish"
    scored_total = 0               # every date with both a reading and a forward return
    strat_cum_return = 0.0         # sum of (signal direction) * (forward return) over non-overlapping steps
    buyhold_cum_return = 0.0
    neutrals = 0

    step_cursor = -10**9           # for non-overlapping cumulative-return accounting

    for i in range(len(closes)):
        fwd = _forward_return(closes, i, horizon_days)
        if fwd is None:
            continue
        # bars_through() keeps this lookahead-safe even though we already
        # sliced -- it's the project's single truncation choke point, so the
        # reading here is computed exactly as the live/engine path would.
        history = backtest_data.bars_through(bars, dates[i])
        view = tsm.signal_from_closes(bars[0]["symbol"] if bars[0].get("symbol") else "TEST",
                                      [b["close"] for b in history], lookback_days, skip_days)
        if view["confidence"] == 0.0 and view["stance"] == "neutral" and len(history) < lookback_days + 1:
            continue  # not enough history yet -- not a real "stay out" decision

        scored_total += 1
        # Baseline: always predict up.
        if fwd >= 0:
            baseline_correct += 1

        if view["stance"] == "neutral":
            neutrals += 1
        else:
            directional_total += 1
            predicted_up = view["stance"] == "bullish"
            if predicted_up == (fwd >= 0):
                directional_correct += 1

        # Non-overlapping cumulative return: only take a fresh position once
        # the previous horizon has elapsed, so the forward windows don't
        # overlap in the P&L tally.
        if i >= step_cursor + horizon_days:
            if view["stance"] == "bullish":
                strat_cum_return += fwd
            elif view["stance"] == "bearish":
                strat_cum_return += -fwd
            # neutral -> no position, contributes 0
            buyhold_cum_return += fwd   # buy-and-hold is always long over the same step
            step_cursor = i

    strat_acc = round(directional_correct / directional_total, 4) if directional_total else None
    baseline_acc = round(baseline_correct / scored_total, 4) if scored_total else None
    strat_ci = equity_metrics.wilson_ci(directional_correct, directional_total) if directional_total else None

    return {
        "scored_days": scored_total,
        "directional_calls": directional_total,
        "neutral_days": neutrals,
        "strategy_accuracy": strat_acc,
        "strategy_accuracy_ci_95": strat_ci,
        "baseline_accuracy": baseline_acc,          # naive "always bullish"
        "strategy_cum_return": round(strat_cum_return, 4),
        "buyhold_cum_return": round(buyhold_cum_return, 4),
        # Promotion-gate checks (screening-grade -- see module docstring's
        # limitation note). "beats_baseline" uses the CI lower bound vs the
        # baseline point accuracy -- "clearly separated," same discipline as
        # backtest/forecast_backtest.py's own gate.
        "beats_baseline": bool(strat_ci and baseline_acc is not None and strat_ci[0] > baseline_acc),
        "beats_buyhold": strat_cum_return > buyhold_cum_return,
    }


if __name__ == "__main__":
    # Self-test with synthetic bars -- deterministic, offline.
    from datetime import date, timedelta

    def _bars(closes: list[float], symbol: str = "TEST") -> list[dict]:
        d0 = date(2020, 1, 1)
        out = []
        for i, c in enumerate(closes):
            out.append({"symbol": symbol, "date": (d0 + timedelta(days=i)).strftime("%Y-%m-%d"),
                        "open": c, "high": c * 1.001, "low": c * 0.999, "close": c, "volume": 1000})
        return out

    print("Trending market (momentum SHOULD have an edge)...")
    # Persistent drift up then down, so trend-following can profit from both
    # legs while a naive always-long baseline gives back the down leg.
    up_leg = [100.0 * (1.004 ** i) for i in range(400)]
    down_leg = [up_leg[-1] * (0.996 ** i) for i in range(1, 400)]
    trending = _bars(up_leg + down_leg)
    res = run_validation(trending)
    print(f"  scored_days={res['scored_days']} directional_calls={res['directional_calls']} "
          f"neutral_days={res['neutral_days']}")
    print(f"  strategy_accuracy={res['strategy_accuracy']} baseline_accuracy={res['baseline_accuracy']}")
    print(f"  strategy_cum_return={res['strategy_cum_return']} buyhold_cum_return={res['buyhold_cum_return']}")
    print(f"  beats_baseline={res['beats_baseline']} beats_buyhold={res['beats_buyhold']}")
    assert res["scored_days"] > 0 and res["directional_calls"] > 0, res
    # On a clean up-then-down trend, following the trend should beat always-long.
    assert res["beats_buyhold"], res

    print("\nPure noise (momentum should NOT magically win -- sanity floor)...")
    noise = _bars([100.0 + (1 if i % 2 else -1) * 0.5 for i in range(600)])
    res_noise = run_validation(noise)
    print(f"  directional_calls={res_noise['directional_calls']} "
          f"neutral_days={res_noise['neutral_days']} "
          f"strategy_cum_return={res_noise['strategy_cum_return']}")
    # Mostly neutral on noise (no real trend to read) -- the point of the band.
    assert res_noise["neutral_days"] > 0, res_noise

    print("\nPASS -- momentum backtest harness runs end-to-end and screens as expected.")
