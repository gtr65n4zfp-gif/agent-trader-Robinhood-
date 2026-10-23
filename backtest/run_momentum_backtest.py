"""
backtest/run_momentum_backtest.py -- real-data driver for the time-series
momentum screen (backtest/momentum_backtest.py). Results write-up:
agents/MOMENTUM_RESULTS.md.

Agent-mediated like everything else here: it never calls Robinhood itself.
The calling agent fetches MONTHLY bars with the Robinhood MCP tool
get_equity_historicals (interval="month", adjustment_type left at its
'split' default -- split-adjusted is required, or a stock split reads as a
crash) and saves the raw JSON response to disk; this script reads that file.

    python -m backtest.run_momentum_backtest RAW_MONTHLY_JSON_PATH

Monthly bars with the literature's own parameters: 12-bar lookback, skip 1,
score the next 1 bar -- so nothing overlaps and Sharpe annualizes with 12.

DATA HYGIENE (fail-safe, same spirit as automation/run_pass.py's per-symbol
sanity check):
  - gap-fill bars (interpolated=true) are dropped -- they carry no new info.
  - a TRADING HALT breaks price continuity: a zero-volume bar is a frozen,
    carried-forward price, not a real one. Everything up to and including
    the last zero-volume bar is discarded, so momentum is only ever read
    across real, continuous trading. (Found the hard way: NBIS -- formerly
    Yandex -- sat frozen at $18.94 with zero volume for 31 months during its
    2022-2024 suspension.)
  - a symbol left with too little continuous history to produce a single
    scored decision is reported as EXCLUDED, never silently scored.
"""

from __future__ import annotations

import json
import os
import sys

from execution import config

from . import data as backtest_data
from . import momentum_backtest as mb

LOOKBACK, SKIP, HORIZON, PERIODS_PER_YEAR = 12, 1, 1, 12

# Named market periods for the go-live gate's ">= 3 regimes" check. Chosen
# from well-known market history, not tuned: each is a window of DECISION
# months (the signal still reads all prior history).
REGIME_WINDOWS = [
    ("late-cycle bull + 2018 selloffs", "2017-01-01", "2019-12-31"),
    ("COVID crash + recovery",          "2020-01-01", "2021-12-31"),
    ("2022 bear market",                "2022-01-01", "2022-12-31"),
    ("2023-26 bull",                    "2023-01-01", "2026-12-31"),
]


def clean_bars(symbol: str, raw: dict) -> tuple[list[dict], str | None]:
    """parse_bars() plus the hygiene rules in the module docstring. Returns
    (bars, note) -- note explains anything that was dropped."""
    # Drop gap-fill bars before parsing (parse_bars keeps every bar it's given).
    filtered = {"data": {"results": [
        {**r, "bars": [b for b in r.get("bars", []) if not b.get("interpolated")]}
        for r in raw.get("data", {}).get("results", [])
    ]}}
    bars = backtest_data.parse_bars(symbol, filtered)
    halted = [i for i, b in enumerate(bars) if not b.get("volume")]
    if halted:
        cut = halted[-1]
        note = (f"dropped {cut + 1} bars through {bars[cut]['date'][:7]} "
                f"(zero-volume trading halt / frozen price); {len(bars) - cut - 1} continuous bars remain")
        return bars[cut + 1:], note
    return bars, None


def _fmt(x, pct=True):
    if x is None:
        return "  n/a"
    return f"{x * 100:+6.1f}%" if pct else f"{x:5.2f}"


def main(raw_path: str) -> dict:
    with open(raw_path) as f:
        raw = json.load(f)
    symbols = [r["symbol"] for r in raw["data"]["results"]]

    full: list[dict] = []
    excluded: dict[str, str] = {}
    bars_by_symbol: dict[str, list[dict]] = {}
    for sym in symbols:
        bars, note = clean_bars(sym, raw)
        res = mb.run_validation(bars, symbol=sym, horizon=HORIZON, lookback=LOOKBACK, skip=SKIP,
                                periods_per_year=PERIODS_PER_YEAR)
        if res["scored_periods"] == 0:
            excluded[sym] = note or "not enough history for a single scored decision"
            continue
        if note:
            res["data_note"] = note
        full.append(res)
        bars_by_symbol[sym] = bars

    print(f"{config.mode_banner()} -- time-series momentum, MONTHLY bars, "
          f"12-1 signal, LONG-ONLY (this system can't short), {config.SLIPPAGE_BPS:g} bps/side costs\n")
    print("FULL PERIOD, per symbol (long-only strategy vs. buy-and-hold):")
    print(f"{'sym':5} {'months':>6} {'in-mkt':>7} | {'LO ret':>8} {'LO Shp':>6} {'LO DD':>8} | "
          f"{'BH ret':>8} {'BH Shp':>6} {'BH DD':>8} | beats BH (Sharpe)  shallower DD")
    for r in full:
        lo, bh = r["long_only"], r["buy_hold"]
        print(f"{r['symbol']:5} {r['scored_periods']:6} {r['time_in_market'] * 100:6.0f}% | "
              f"{_fmt(lo['total_return']):>8} {_fmt(lo['sharpe'], False):>6} {_fmt(lo['max_drawdown']):>8} | "
              f"{_fmt(bh['total_return']):>8} {_fmt(bh['sharpe'], False):>6} {_fmt(bh['max_drawdown']):>8} | "
              f"{'YES' if r['beats_buyhold'] else 'no':^17}  {'YES' if r['shallower_drawdown'] else 'no':^12}")
    for sym, why in excluded.items():
        print(f"{sym:5} EXCLUDED -- {why}")
    for r in full:
        if r.get("data_note"):
            print(f"  note {r['symbol']}: {r['data_note']}")

    pooled = mb.pool_results(full)
    print(f"\nPOOLED accuracy across {pooled['symbols']} symbols:")
    print(f"  bullish calls: {pooled['bullish_calls']}, right {pooled['bullish_hit_rate']} of the time "
          f"(95% CI {pooled['bullish_hit_rate_ci_95']}) vs. base up-month rate {pooled['baseline_up_rate']}")
    print(f"  all directional calls: {pooled['directional_calls']}, accuracy {pooled['strategy_accuracy']} "
          f"(95% CI {pooled['strategy_accuracy_ci_95']})")
    print(f"  beat buy-and-hold on Sharpe: {pooled['beats_buyhold_count']}/{pooled['symbols']} symbols; "
          f"shallower drawdown: {pooled['shallower_drawdown_count']}/{pooled['symbols']}")

    regimes = []
    print("\nBY MARKET PERIOD (average per-symbol total return over the window, long-only vs. buy-and-hold):")
    for name, start, end in REGIME_WINDOWS:
        rows = [mb.run_validation(bars_by_symbol[r["symbol"]], symbol=r["symbol"], horizon=HORIZON,
                                  lookback=LOOKBACK, skip=SKIP, periods_per_year=PERIODS_PER_YEAR,
                                  score_from=start, score_to=end) for r in full]
        rows = [x for x in rows if x["scored_periods"] > 0]
        if not rows:
            continue
        avg_lo = sum(x["long_only"]["total_return"] for x in rows) / len(rows)
        avg_bh = sum(x["buy_hold"]["total_return"] for x in rows) / len(rows)
        avg_in = sum(x["time_in_market"] for x in rows) / len(rows)
        lo_better = sum(1 for x in rows if x["long_only"]["total_return"] > x["buy_hold"]["total_return"])
        months = max(x["scored_periods"] for x in rows)
        print(f"  {name:32} ({months:3} months): long-only {_fmt(avg_lo)}  vs  buy-and-hold {_fmt(avg_bh)}   "
              f"in market {avg_in * 100:3.0f}% of the time; long-only ahead on {lo_better}/{len(rows)} symbols")
        regimes.append({"name": name, "from": start, "to": end, "months": months,
                        "avg_long_only_return": round(avg_lo, 4), "avg_buy_hold_return": round(avg_bh, 4),
                        "avg_time_in_market": round(avg_in, 4), "long_only_ahead": lo_better,
                        "symbols": len(rows)})

    out = {"params": {"bars": "month", "lookback": LOOKBACK, "skip": SKIP, "horizon": HORIZON,
                      "cost_bps_per_side": config.SLIPPAGE_BPS, "long_only": True},
           "per_symbol": full, "excluded": excluded, "pooled": pooled, "regimes": regimes}
    out_path = os.path.join(config.LOG_DIR, "backtests", "momentum_monthly_results.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nFull results: {out_path}")
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
