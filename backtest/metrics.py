"""
backtest/metrics.py — reads an isolated account's trade log and reports
the numbers agents/BACKTEST_DESIGN.md's "Metrics (task 4)" section calls
for: win rate WITH a confidence interval (never a bare %), total realized
P&L (already nets slippage/fees per-fill, same as live), a per-regime
breakdown, and the two benchmark comparisons.

Reads only — never writes a trade, never touches PaperBroker.
"""

from __future__ import annotations

import math

from execution import trade_log


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """
    95% (default z=1.96) Wilson score confidence interval for a binomial
    proportion — chosen over the naive normal approximation because that
    one misbehaves at small n and near 0%/100%, both realistic for a
    backtest slice of a few dozen trades. Returns (lower, upper) as
    fractions in [0, 1], or None if n == 0 (no trades — there's no rate
    to have a confidence interval about at all).
    """
    if n == 0:
        return None
    p_hat = wins / n
    denom = 1 + z**2 / n
    center = (p_hat + z**2 / (2 * n)) / denom
    margin = z * math.sqrt(p_hat * (1 - p_hat) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def account_summary(log_path: str) -> dict:
    """
    Topline metrics for one isolated account's trade log — round trip
    count/P&L/wins/losses via the EXISTING, unmodified round_trip_stats()
    (it already correctly counts only sells with a recorded realized_pnl;
    no reason to duplicate that logic here), plus win rate and its 95%
    Wilson CI.
    """
    stats = trade_log.round_trip_stats(log_path=log_path)
    n = stats["count"]
    win_rate = round(stats["wins"] / n, 4) if n > 0 else None
    ci = wilson_ci(stats["wins"], n)
    return {
        **stats,
        "win_rate": win_rate,
        "win_rate_ci_95": ci,
    }


def per_regime_breakdown(log_path: str) -> dict:
    """
    Group realized P&L and win rate by the regime_state active at exit
    time — read from the "backtest_exit" summary records engine.py writes
    alongside (not instead of) the underlying "sell" fills (see
    engine.py's exit-sweep sections). This is the number the go-live
    gate's ">=3 regime coverage" requirement is actually about: not just
    "did it make money," but "did it hold up across genuinely different
    market conditions," verifiable directly from these tags rather than
    reconstructed after the fact.
    """
    exits_by_regime: dict[str, list[dict]] = {}
    for e in trade_log.read_all(log_path=log_path):
        if e.get("action") != "backtest_exit":
            continue
        state = e.get("regime_state", "unknown")
        exits_by_regime.setdefault(state, []).append(e)

    breakdown = {}
    for state, entries in exits_by_regime.items():
        pnls = [e["realized_pnl"] for e in entries]
        wins = sum(1 for p in pnls if p > 0)
        n = len(pnls)
        breakdown[state] = {
            "count": n,
            "total_realized_pnl": round(sum(pnls), 2),
            "wins": wins,
            "losses": n - wins,
            "win_rate": round(wins / n, 4) if n > 0 else None,
            "win_rate_ci_95": wilson_ci(wins, n),
        }
    return breakdown


def compare(council_log: str, baseline_log: str, buyhold_log: str) -> dict:
    """
    The real question per BACKTEST_DESIGN.md: did the council beat just
    holding, and did the multi-agent structure beat a single-model
    shadow? Reports all three accounts' summaries side by side plus the
    two head-to-head P&L deltas — the report layer, no judgment calls
    (whether a delta is "good" is for a human to read, not this function
    to grade).
    """
    council = account_summary(council_log)
    baseline = account_summary(baseline_log)
    buyhold = account_summary(buyhold_log)
    return {
        "council": {**council, "per_regime": per_regime_breakdown(council_log)},
        "baseline": {**baseline, "per_regime": per_regime_breakdown(baseline_log)},
        "buyhold": buyhold,
        "council_vs_baseline_pnl_delta": round(
            council["total_realized_pnl"] - baseline["total_realized_pnl"], 2
        ),
        "council_vs_buyhold_pnl_delta": round(
            council["total_realized_pnl"] - buyhold["total_realized_pnl"], 2
        ),
    }
