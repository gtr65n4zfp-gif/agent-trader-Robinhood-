"""
backtest/options_metrics.py — aggregate reporting over simulated option
trades (backtest/options_engine.py's simulate_option_trade() output).

Reuses backtest.metrics.wilson_ci() rather than reimplementing a
confidence interval calculation — same reasoning applies here as there:
a bare win rate with no interval is misleading at the trade counts a
single-symbol backtest will realistically produce.
"""

from __future__ import annotations

from execution import config

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


def compare_to_buyhold(trades: list[dict], spy_closes: dict[str, float]) -> dict:
    """
    The benchmark agents/OPTIONS_BACKTEST_DESIGN.md's "Output / metrics"
    section calls for: what would the SAME dollar capital have returned
    holding SPY shares over the SAME [signal_date, exit_date] window,
    instead of buying the option? Same fee treatment as backtest/engine.py's
    own buy-and-hold account (config.SLIPPAGE_BPS against the trader on
    both the buy and the sell) — a fair comparison shouldn't hand the
    benchmark free perfect fills.

    trades: run_options_backtest.run_one_signal() output (each needs
    signal_date, entry_fill, exit_date — entry_fill is still the
    per-share premium; capital is entry_fill * OPTIONS_CONTRACT_MULTIPLIER,
    i.e. what actually got paid for one contract).
    spy_closes: {date: close} for the underlying, e.g. from
    backtest/data.parse_bars(). A trade whose signal_date or exit_date
    isn't in this map is skipped (same fail-safe convention as
    run_one_signal() — never fabricate a missing price), not counted in
    either side of the comparison.
    """
    slippage = config.SLIPPAGE_BPS / 10_000
    per_trade = []
    skipped = 0
    for t in trades:
        entry_close = spy_closes.get(t["signal_date"])
        exit_close = spy_closes.get(t["exit_date"])
        if entry_close is None or exit_close is None:
            skipped += 1
            continue
        capital = t["entry_fill"] * config.OPTIONS_CONTRACT_MULTIPLIER
        shares = capital / (entry_close * (1 + slippage))
        proceeds = shares * (exit_close * (1 - slippage))
        buyhold_pnl = round(proceeds - capital, 2)
        per_trade.append({
            "signal_date": t["signal_date"], "exit_date": t["exit_date"],
            "capital": round(capital, 2), "buyhold_pnl": buyhold_pnl,
            "option_pnl": t["realized_pnl"],
        })

    total_options_pnl = round(sum(t["option_pnl"] for t in per_trade), 2)
    total_buyhold_pnl = round(sum(t["buyhold_pnl"] for t in per_trade), 2)
    return {
        "count": len(per_trade),
        "skipped_missing_price": skipped,
        "total_options_pnl": total_options_pnl,
        "total_buyhold_pnl": total_buyhold_pnl,
        "options_vs_buyhold_delta": round(total_options_pnl - total_buyhold_pnl, 2),
        "per_trade": per_trade,
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

    print("\nTesting compare_to_buyhold...")
    bh_trades = [
        {"signal_date": "2026-01-06", "exit_date": "2026-01-07",
         "entry_fill": 6.0, "realized_pnl": 250.0},
    ]
    spy_closes = {"2026-01-06": 618.5, "2026-01-07": 625.0}
    cmp = compare_to_buyhold(bh_trades, spy_closes)
    assert cmp["count"] == 1 and cmp["skipped_missing_price"] == 0, cmp
    assert cmp["total_options_pnl"] == 250.0, cmp
    assert cmp["total_buyhold_pnl"] > 0, cmp  # SPY rose over the window too
    assert cmp["options_vs_buyhold_delta"] == round(cmp["total_options_pnl"] - cmp["total_buyhold_pnl"], 2), cmp
    print(f"PASS — options P&L vs same-capital buy-and-hold over the same window: {cmp}")

    print("\nTesting compare_to_buyhold with a missing underlying price...")
    cmp_missing = compare_to_buyhold(bh_trades, {"2026-01-06": 618.5})  # no exit-date close
    assert cmp_missing["count"] == 0 and cmp_missing["skipped_missing_price"] == 1, cmp_missing
    print(f"PASS — missing underlying price skips the trade rather than fabricating one: {cmp_missing}")
