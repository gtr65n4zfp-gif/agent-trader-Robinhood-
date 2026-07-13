"""
backtest/run_risk_metrics.py — computes risk_metrics.risk_adjusted_summary()
for all three accounts of an already-saved run, writes the comparison
into that run's metrics_report.json (isolated location, same as
backtest/metrics.py's report), and prints the table plus the sanity
checks: reconciliation against the known raw P&L/trade-count numbers, and
confirmation the live trade log/portfolio were never touched.

Pure post-processing — see risk_metrics.py's module docstring for the
full no-lookahead/no-re-run/isolation guarantees this inherits.

Run as: python -m backtest.run_risk_metrics <run_id>
"""

import json
import os
import sys

from execution import config, trade_log

from . import risk_metrics


def main(run_id: str) -> None:
    run_dir = os.path.join(config.LOG_DIR, "backtests", run_id)
    cache_dir = os.path.join(config.LOG_DIR, "backtests", "_shared_cache")

    symbols = ["AAPL", "MSFT", "GOOGL", "JPM", "JNJ", "WMT", "CVX", "CAT"]
    windows = [
        ("C", "2018-03-01", "2018-08-31"),
        ("A", "2022-01-03", "2022-06-30"),
        ("B", "2023-01-01", "2023-06-30"),
    ]

    bars_by_symbol: dict[str, list[dict]] = {}
    for sym in symbols:
        merged: list[dict] = []
        for wname, _, _ in windows:
            with open(os.path.join(cache_dir, f"window{wname}_{sym}.json")) as f:
                merged.extend(json.load(f))
        bars_by_symbol[sym] = merged

    # --- Live-isolation check, before touching anything -----------------
    live_before_count = len(trade_log.read_all())
    with open(os.path.join(config.LOG_DIR, "paper_portfolio.json")) as f:
        live_before_portfolio = f.read()

    summaries = {}
    for account in ["council", "baseline", "buyhold"]:
        trades_path = os.path.join(run_dir, f"{account}_trades.jsonl")
        summaries[account] = risk_metrics.risk_adjusted_summary(trades_path, bars_by_symbol, windows)

    table = risk_metrics.comparison_table(summaries)
    print(table)
    print()

    # --- Sanity check 1: reconcile against known raw numbers ------------
    print("=" * 70)
    print("RECONCILIATION AGAINST KNOWN RAW NUMBERS")
    print("=" * 70)
    council_pnl = trade_log.round_trip_stats(log_path=os.path.join(run_dir, "council_trades.jsonl"))
    buyhold_pnl = trade_log.round_trip_stats(log_path=os.path.join(run_dir, "buyhold_trades.jsonl"))
    delta = round(council_pnl["total_realized_pnl"] - buyhold_pnl["total_realized_pnl"], 2)
    print(f"Council total P&L: ${council_pnl['total_realized_pnl']} (count={council_pnl['count']}, expect 39)")
    print(f"Buy-hold total P&L: ${buyhold_pnl['total_realized_pnl']}")
    print(f"Council vs buy-hold delta: ${delta} (expect ~+$176.78)")
    baseline_pnl = trade_log.round_trip_stats(log_path=os.path.join(run_dir, "baseline_trades.jsonl"))
    print(f"Baseline count={baseline_pnl['count']} (expect 834)")
    assert council_pnl["count"] == 39, f"MISMATCH: council count is {council_pnl['count']}, expected 39"
    assert baseline_pnl["count"] == 834, f"MISMATCH: baseline count is {baseline_pnl['count']}, expected 834"
    assert abs(delta - 176.78) < 0.01, f"MISMATCH: council-vs-buyhold delta is {delta}, expected ~176.78"
    print("RECONCILED — trade counts and P&L deltas match the original saved run exactly.")
    print()

    # --- Sanity check 2: live isolation -----------------------------------
    live_after_count = len(trade_log.read_all())
    with open(os.path.join(config.LOG_DIR, "paper_portfolio.json")) as f:
        live_after_portfolio = f.read()
    print("=" * 70)
    print("LIVE ISOLATION CHECK")
    print("=" * 70)
    print(f"Live trade_log entry count before/after: {live_before_count} / {live_after_count}")
    print(f"Live paper_portfolio.json unchanged: {live_before_portfolio == live_after_portfolio}")
    assert live_before_count == live_after_count
    assert live_before_portfolio == live_after_portfolio
    print("CONFIRMED — live logs/trades.jsonl and logs/paper_portfolio.json untouched.")

    # --- Write into the run's isolated metrics report --------------------
    report_path = os.path.join(run_dir, "metrics_report.json")
    existing = {}
    if os.path.exists(report_path):
        with open(report_path) as f:
            existing = json.load(f)
    for name, summary in summaries.items():
        summary_for_json = {k: v for k, v in summary.items() if k != "_curve"}
        existing.setdefault(name, {})["risk_adjusted"] = summary_for_json
    with open(report_path, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"\nWritten to {report_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "full_run_8x3")
