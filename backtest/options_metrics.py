"""
backtest/options_metrics.py — aggregate reporting over simulated option
trades (backtest/options_engine.py's simulate_option_trade() output).

Reuses backtest.metrics.wilson_ci() rather than reimplementing a
confidence interval calculation — same reasoning applies here as there:
a bare win rate with no interval is misleading at the trade counts a
single-symbol backtest will realistically produce.
"""

from __future__ import annotations

from . import metrics as equity_metrics


def summarize_option_trades(trades: list[dict]) -> dict:
    """
    trades: a list of simulate_option_trade() outputs. Returns count,
    total realized P&L, win rate, and its 95% Wilson score confidence
    interval — same field names as backtest.metrics.account_summary()'s
    win-rate fields, so options results read consistently next to the
    equity backtest's own numbers.
    """
    n = len(trades)
    wins = sum(1 for t in trades if t["realized_pnl"] > 0)
    total_pnl = round(sum(t["realized_pnl"] for t in trades), 2)
    win_rate = round(wins / n, 4) if n > 0 else None
    return {
        "count": n,
        "wins": wins,
        "losses": n - wins,
        "total_realized_pnl": total_pnl,
        "win_rate": win_rate,
        "win_rate_ci_95": equity_metrics.wilson_ci(wins, n),
    }


if __name__ == "__main__":
    print("Testing summarize_option_trades...")
    trades = [
        {"realized_pnl": 3.5},
        {"realized_pnl": -1.2},
        {"realized_pnl": 2.0},
        {"realized_pnl": -0.8},
    ]
    summary = summarize_option_trades(trades)
    assert summary["count"] == 4
    assert summary["wins"] == 2
    assert summary["losses"] == 2
    assert summary["total_realized_pnl"] == 3.5, summary
    assert summary["win_rate"] == 0.5, summary
    assert summary["win_rate_ci_95"] is not None
    print(f"PASS — 4 trades, 2 wins, total P&L $3.50: {summary}")

    print("\nTesting summarize_option_trades with no trades...")
    empty_summary = summarize_option_trades([])
    assert empty_summary["count"] == 0
    assert empty_summary["win_rate"] is None
    assert empty_summary["win_rate_ci_95"] is None
    print(f"PASS — empty trade list returns None win rate, not a divide-by-zero: {empty_summary}")
