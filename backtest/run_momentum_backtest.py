"""
backtest/run_momentum_backtest.py -- real-data driver for the time-series
momentum screen (backtest/momentum_backtest.py). Results write-up:
agents/MOMENTUM_RESULTS.md.

Agent-mediated like everything else here: it never calls Robinhood itself.
The calling agent fetches MONTHLY bars with the Robinhood MCP tool
get_equity_historicals (interval="month", adjustment_type left at its
'split' default -- split-adjusted is required, or a stock split reads as a
crash) and saves the raw JSON response to disk; this script reads that file.

    python -m backtest.run_momentum_backtest LABEL RAW_JSON [RAW_JSON ...] [--cash-tbill]

LABEL names the universe (it goes into the output filename). If it matches
a universe in backtest/universes.py, exactly that universe's symbols are
tested -- extra symbols in the raw files are ignored, and any listed symbol
missing from them is reported as EXCLUDED, never silently dropped. Pass several
raw files when the universe needed more than one MCP call (the tool takes up
to 10 symbols per call). Results are reported per symbol, pooled, and as an
EQUAL-WEIGHT PORTFOLIO (momentum_backtest.pool_portfolio()) -- the portfolio
view is the headline, since that's how the momentum literature itself
evaluates the strategy.

--cash-tbill: months spent out of the market earn an approximate 3-month
Treasury-bill rate (TBILL_ANNUAL below) instead of 0%, and Sharpe is
measured on excess return over that rate. Default is cash at 0%, which
understates a strategy that spends a lot of time in cash.

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
from .universes import UNIVERSES

LOOKBACK, SKIP, HORIZON, PERIODS_PER_YEAR = 12, 1, 1, 12

# Approximate ANNUAL AVERAGE 3-month US Treasury-bill rates, as fractions.
# APPROXIMATE, from general knowledge of the rate history, not fetched data:
# the price-only bars this project gets from the Robinhood MCP can't supply
# it (T-bill ETFs like BIL pay their yield as dividends, so their price
# series stays ~flat). 2025-2026 are the least certain. Good enough for the
# question this answers -- "does paying fair interest on idle cash flip the
# verdict?" -- not for a precise figure.
TBILL_ANNUAL = {
    1999: 0.0464, 2000: 0.0582, 2001: 0.0339, 2002: 0.0160, 2003: 0.0101, 2004: 0.0137,
    2005: 0.0315, 2006: 0.0473, 2007: 0.0435, 2008: 0.0137, 2009: 0.0015, 2010: 0.0014,
    2011: 0.0005, 2012: 0.0009, 2013: 0.0006, 2014: 0.0003,
    2015: 0.0005, 2016: 0.0032, 2017: 0.0093, 2018: 0.0194, 2019: 0.0206, 2020: 0.0037,
    2021: 0.0004, 2022: 0.0202, 2023: 0.0507, 2024: 0.0497, 2025: 0.042, 2026: 0.037,
}

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


def load_raw(raw_paths: list[str]) -> dict[str, dict]:
    """Read saved get_equity_historicals responses: {symbol: the raw response
    that contains it}. A symbol appearing in several files keeps the last."""
    raw_by_symbol: dict[str, dict] = {}
    for path in raw_paths:
        with open(path) as f:
            raw = json.load(f)
        for r in raw["data"]["results"]:
            raw_by_symbol[r["symbol"]] = raw
    return raw_by_symbol


def select_universe(label: str, raw_by_symbol: dict[str, dict]) -> tuple[dict[str, dict], dict[str, str]]:
    """If `label` names a universe in backtest/universes.py, keep exactly its
    symbols and report any listed symbol missing from the raw data (never a
    silent drop). An unknown label keeps everything that was loaded."""
    universe = UNIVERSES.get(label)
    if universe is None:
        return raw_by_symbol, {}
    missing = {sym: "listed in backtest/universes.py but missing from the raw data files"
               for sym in universe if sym not in raw_by_symbol}
    return {sym: raw_by_symbol[sym] for sym in universe if sym in raw_by_symbol}, missing


def _fmt(x, pct=True):
    if x is None:
        return "  n/a"
    return f"{x * 100:+6.1f}%" if pct else f"{x:5.2f}"


def main(label: str, raw_paths: list[str], cash_tbill: bool = False) -> dict:
    cash = TBILL_ANNUAL if cash_tbill else None
    raw_by_symbol, excluded = select_universe(label, load_raw(raw_paths))
    if cash_tbill:
        label = f"{label}_tbill"

    full: list[dict] = []
    bars_by_symbol: dict[str, list[dict]] = {}
    for sym, raw in raw_by_symbol.items():
        bars, note = clean_bars(sym, raw)
        res = mb.run_validation(bars, symbol=sym, horizon=HORIZON, lookback=LOOKBACK, skip=SKIP,
                                periods_per_year=PERIODS_PER_YEAR, cash_annual_by_year=cash)
        if res["scored_periods"] == 0:
            excluded[sym] = note or "not enough history for a single scored decision"
            continue
        if note:
            res["data_note"] = note
        full.append(res)
        bars_by_symbol[sym] = bars

    print(f"[{label}] {config.mode_banner()} -- time-series momentum, MONTHLY bars, "
          f"12-1 signal, LONG-ONLY (this system can't short), {config.SLIPPAGE_BPS:g} bps/side costs, "
          f"cash earns {'~3-month T-bill (approx.)' if cash_tbill else '0%'}\n")
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

    port = mb.pool_portfolio(full, periods_per_year=PERIODS_PER_YEAR)
    print(f"\nEQUAL-WEIGHT PORTFOLIO (headline) -- {port['months']} months "
          f"{port['first'][:7]}..{port['last'][:7]}, ~{port['avg_symbols']} symbols per month:")
    _print_portfolio("full period", port)

    regimes = []
    print("\nPORTFOLIO BY MARKET PERIOD:")
    for name, start, end in REGIME_WINDOWS:
        rp = mb.pool_portfolio(full, periods_per_year=PERIODS_PER_YEAR, score_from=start, score_to=end)
        if not rp["months"]:
            continue
        _print_portfolio(name, rp)
        regimes.append({"name": name, "from": start, "to": end, **rp})

    out = {"label": label,
           "params": {"bars": "month", "lookback": LOOKBACK, "skip": SKIP, "horizon": HORIZON,
                      "cost_bps_per_side": config.SLIPPAGE_BPS, "long_only": True,
                      "cash": "approx 3-month T-bill" if cash_tbill else "0%"},
           "per_symbol": full, "excluded": excluded, "pooled": pooled,
           "portfolio": port, "regimes": regimes}
    out_path = os.path.join(config.LOG_DIR, "backtests", f"momentum_monthly_results_{label}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nFull results: {out_path}")
    return out


def _print_portfolio(name: str, p: dict) -> None:
    lo, bh = p["long_only"], p["buy_hold"]
    print(f"  {name:32} ({p['months']:3} mo, in market {p['avg_exposure'] * 100:3.0f}%): "
          f"strategy {_fmt(lo['total_return'])} Sharpe {_fmt(lo['sharpe'], False)} DD {_fmt(lo['max_drawdown'])}"
          f"  |  buy-and-hold {_fmt(bh['total_return'])} Sharpe {_fmt(bh['sharpe'], False)} "
          f"DD {_fmt(bh['max_drawdown'])}  |  beats on Sharpe: {'YES' if p['beats_buyhold'] else 'no'}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--cash-tbill"]
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    main(args[0], args[1:], cash_tbill="--cash-tbill" in sys.argv[1:])
