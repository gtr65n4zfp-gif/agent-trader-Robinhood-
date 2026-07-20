"""
backtest/options_metrics.py — aggregate reporting over simulated option
trades (backtest/options_engine.py's simulate_option_trade() output).

Reuses backtest.metrics.wilson_ci() rather than reimplementing a
confidence interval calculation — same reasoning applies here as there:
a bare win rate with no interval is misleading at the trade counts a
single-symbol backtest will realistically produce.
"""

from __future__ import annotations

import math

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


def summarize_by_regime_and_side(trades: list[dict], regime_key: str = "regime_state",
                                  side_key: str = "side") -> dict:
    """
    Task 6's per-regime AND per-side breakdown (see
    agents/SPY_OPTIONS_DESIGN.md's "Metrics" section) — Round 2 assembled
    this by hand for one write-up; this formalizes it as reusable code.

    trades: simulate_option_trade()/simulate_spread_trade() outputs, each
    carrying regime_key and side_key alongside realized_pnl (the caller
    attaches these when building the trade dict — this module doesn't
    know where they came from). Returns
    {regime_value: {side_value: summarize_option_trades() output}} — a
    trade missing either key is grouped under the string "unknown" for
    that axis rather than raising, since a genuinely incomplete signal
    (e.g. no regime tag) shouldn't crash the whole report.
    """
    groups: dict[str, dict[str, list[dict]]] = {}
    for t in trades:
        regime = t.get(regime_key, "unknown")
        side = t.get(side_key, "unknown")
        groups.setdefault(regime, {}).setdefault(side, []).append(t)
    return {
        regime: {side: summarize_option_trades(side_trades) for side, side_trades in sides.items()}
        for regime, sides in groups.items()
    }


def compare_forecast_accuracy(rows: list[dict], engines: tuple[str, ...] = ("baseline", "garch")) -> dict:
    """
    The GARCH ablation's forecast-accuracy half (see
    agents/SPY_OPTIONS_DESIGN.md's "Metrics" — "1. Forecast accuracy").

    rows: each needs a "realized_vol" (the ACTUAL annualized realized vol
    that played out over the row's own forward window, computed strictly
    after the fact) plus "{engine}_rv" for each name in `engines` (the
    forecast made at signal time). A row missing "realized_vol" or a
    given engine's forecast is excluded from THAT engine's error stats
    (never fabricated, never silently zero-filled) but still counts
    toward the win-count denominator only when both engines have a usable
    forecast for that row.

    Returns {engine: {count, mae, rmse}} plus "win_count": {engine: N} and
    "win_count_total" — how many rows each engine's forecast landed closer
    to the realized figure, out of every row where both forecasts and the
    realized value were all available.
    """
    errors: dict[str, list[float]] = {e: [] for e in engines}
    wins = {e: 0 for e in engines}
    win_total = 0
    for row in rows:
        realized = row.get("realized_vol")
        if realized is None:
            continue
        row_errors = {}
        for e in engines:
            forecast = row.get(f"{e}_rv")
            if forecast is None:
                continue
            err = forecast - realized
            errors[e].append(err)
            row_errors[e] = abs(err)
        if len(row_errors) == len(engines):
            win_total += 1
            best = min(row_errors, key=row_errors.get)
            wins[best] += 1

    report: dict = {"win_count": wins, "win_count_total": win_total}
    for e in engines:
        errs = errors[e]
        n = len(errs)
        if n == 0:
            report[e] = {"count": 0, "mae": None, "rmse": None}
            continue
        mae = sum(abs(x) for x in errs) / n
        rmse = math.sqrt(sum(x * x for x in errs) / n)
        report[e] = {"count": n, "mae": round(mae, 4), "rmse": round(rmse, 4)}
    return report


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

    print("\nTesting summarize_by_regime_and_side...")
    regime_trades = [
        {"realized_pnl": 5.0, "regime_state": "trending", "side": "bullish"},
        {"realized_pnl": -2.0, "regime_state": "trending", "side": "bullish"},
        {"realized_pnl": 3.0, "regime_state": "trending", "side": "bearish"},
        {"realized_pnl": -1.0, "regime_state": "high_vol_trending", "side": "bearish"},
        {"realized_pnl": 2.0},  # missing both keys -- grouped under "unknown"/"unknown"
    ]
    breakdown = summarize_by_regime_and_side(regime_trades)
    assert breakdown["trending"]["bullish"]["count"] == 2, breakdown
    assert breakdown["trending"]["bearish"]["count"] == 1, breakdown
    assert breakdown["high_vol_trending"]["bearish"]["count"] == 1, breakdown
    assert breakdown["unknown"]["unknown"]["count"] == 1, breakdown
    print(f"PASS — grouped by regime then side, missing keys fall to 'unknown' rather than crashing: "
          f"{list(breakdown.keys())}")

    print("\nTesting compare_forecast_accuracy — GARCH closer on every row...")
    rows = [
        {"realized_vol": 0.20, "baseline_rv": 0.30, "garch_rv": 0.22},
        {"realized_vol": 0.15, "baseline_rv": 0.10, "garch_rv": 0.16},
        {"realized_vol": 0.50, "baseline_rv": 0.25, "garch_rv": 0.45},
    ]
    acc = compare_forecast_accuracy(rows)
    assert acc["win_count"]["garch"] == 3 and acc["win_count"]["baseline"] == 0, acc
    assert acc["win_count_total"] == 3, acc
    assert acc["garch"]["mae"] < acc["baseline"]["mae"], acc
    print(f"PASS — GARCH closer on all 3 rows, lower MAE/RMSE and full win count: "
          f"garch={acc['garch']}, baseline={acc['baseline']}")

    print("\nTesting compare_forecast_accuracy — a row missing one engine's forecast is excluded from win-count...")
    rows_partial = rows + [{"realized_vol": 0.30, "baseline_rv": 0.28}]  # no garch_rv this row
    acc_partial = compare_forecast_accuracy(rows_partial)
    assert acc_partial["baseline"]["count"] == 4, acc_partial  # baseline error still counted
    assert acc_partial["garch"]["count"] == 3, acc_partial  # garch error NOT counted (no forecast)
    assert acc_partial["win_count_total"] == 3, acc_partial  # win-count only over rows with BOTH forecasts
    print(f"PASS — the incomplete row's baseline error still counts, but it's excluded from the win-count "
          f"(needs both engines): {acc_partial['win_count']}, total={acc_partial['win_count_total']}")

    print("\nTesting compare_forecast_accuracy — a row missing realized_vol entirely is skipped for both engines...")
    rows_no_realized = rows + [{"baseline_rv": 0.20, "garch_rv": 0.22}]
    acc_no_realized = compare_forecast_accuracy(rows_no_realized)
    assert acc_no_realized["baseline"]["count"] == 3 and acc_no_realized["garch"]["count"] == 3, acc_no_realized
    print("PASS — a row with no realized_vol to compare against never fabricates an error for either engine.")
