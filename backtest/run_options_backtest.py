"""
backtest/run_options_backtest.py — ties options_data + options_engine +
options_metrics together over a pre-fetched run manifest.

Agent-mediated, same reasoning as backtest/data.py's parse_bars(): the
actual MCP tool calls (get_option_instruments, get_option_historicals)
happen in whatever interactive session drives a real historical run —
this module only ever consumes their already-fetched raw JSON, passed in
via each signal's raw_instruments/raw_historicals fields. Nothing in this
module calls an MCP tool, and nothing in this module calls any order-
placing tool. See agents/OPTIONS_BACKTEST_DESIGN.md.
"""

from __future__ import annotations

from execution import config

from . import options_data, options_engine, options_metrics


def run_one_signal(signal: dict) -> dict | None:
    """
    signal: {
        "date": "YYYY-MM-DD",             # entry/signal date
        "side": "buy" | "sell",           # from technicals_only_decision()'s action
        "spot": float,                    # SPY close on `date`
        "expiration_date": "YYYY-MM-DD",  # already resolved via options_data.nearest_expiration()
        "spot_at_expiration": float,      # SPY close on expiration_date
        "raw_instruments": dict,          # get_option_instruments() raw response for expiration_date
        "raw_historicals": dict,          # get_option_historicals() raw response for the selected contract
    }

    Returns simulate_option_trade() output with realized_pnl scaled to
    per-contract dollars (config.OPTIONS_CONTRACT_MULTIPLIER applied here,
    not inside simulate_option_trade() — see that function's docstring),
    or None if no usable contract or bar data was found — the caller
    skips this signal, same fail-safe convention as everywhere else in
    this project. Never guesses a
    substitute contract or a fabricated price.

    NOTE on options_data.parse_option_bars(): it raises ValueError if
    contract["id"] isn't found in signal["raw_historicals"]'s results —
    deliberately, matching backtest/data.py's parse_bars() precedent, so a
    genuine caller-wiring bug (fetching the wrong contract's historicals)
    fails loudly rather than silently. But a contract Robinhood genuinely
    has zero historical bars for (common for thin/never-traded strikes) is
    NOT a bug — it's a normal "skip this signal" case per this plan's
    Global Constraints. This function is the seam where that distinction
    gets drawn: catch the exception here, at the orchestration layer,
    rather than changing parse_option_bars() itself (resolved as a Task 1
    reviewer finding — see the plan's task-1-report.md if it still exists).
    """
    instruments = options_data.parse_option_instruments(signal["raw_instruments"])
    contract = options_data.select_contract(signal["spot"], signal["side"], instruments)
    if contract is None:
        return None

    try:
        bars = options_data.parse_option_bars(signal["raw_historicals"], contract["id"])
    except ValueError:
        return None
    if not bars:
        return None

    entry_bar = next((b for b in bars if b["date"] == signal["date"]), None)
    if entry_bar is None:
        return None

    bars_after_entry = [b for b in bars if b["date"] > signal["date"]]

    trade = options_engine.simulate_option_trade(
        entry_close=entry_bar["close"],
        bars_after_entry=bars_after_entry,
        side=signal["side"],
        strike=contract["strike"],
        expiration_date=signal["expiration_date"],
        spot_at_expiration=signal["spot_at_expiration"],
        stop_loss_pct=config.OPTIONS_STOP_LOSS_PCT,
        take_profit_pct=config.OPTIONS_TAKE_PROFIT_PCT,
        haircut_pct=config.OPTIONS_ROUNDTRIP_HAIRCUT_PCT,
    )
    # simulate_option_trade() returns realized_pnl as a raw PER-SHARE
    # premium delta (see its own docstring) — this is the seam where that
    # gets scaled to actual per-contract dollars. entry_fill/exit_fill stay
    # unscaled (they're genuinely per-share quoted prices; only P&L is a
    # settled dollar amount).
    trade["realized_pnl"] = round(trade["realized_pnl"] * config.OPTIONS_CONTRACT_MULTIPLIER, 2)
    return trade


def run_backtest(signals: list[dict]) -> dict:
    """
    Runs every signal through run_one_signal(), collects whatever trades
    were actually produced (skipped signals excluded, never guessed), and
    reports options_metrics.summarize_option_trades() over them.
    """
    trades = [t for t in (run_one_signal(s) for s in signals) if t is not None]
    return {
        "signals_total": len(signals),
        "signals_skipped": len(signals) - len(trades),
        "trades": trades,
        "summary": options_metrics.summarize_option_trades(trades),
    }


if __name__ == "__main__":
    print("Testing run_one_signal — end to end with a fabricated signal...")
    signal = {
        "date": "2026-01-06",
        "side": "buy",
        "spot": 618.5,
        "expiration_date": "2026-01-16",
        "spot_at_expiration": 615.0,
        "raw_instruments": {
            "data": {"instruments": [
                {"id": "call-620", "strike_price": "620.0000", "type": "call",
                 "expiration_date": "2026-01-16"},
                {"id": "put-620", "strike_price": "620.0000", "type": "put",
                 "expiration_date": "2026-01-16"},
            ]}
        },
        "raw_historicals": {
            "data": {"results": [
                {"instrument_id": "call-620", "bars": [
                    {"begins_at": "2026-01-06T00:00:00Z", "open_price": "6.0", "high_price": "6.5",
                     "low_price": "5.8", "close_price": "6.0"},
                    {"begins_at": "2026-01-07T00:00:00Z", "open_price": "6.0", "high_price": "6.3",
                     "low_price": "2.5", "close_price": "2.7"},
                ]}
            ]}
        },
    }
    result = run_one_signal(signal)
    assert result is not None
    assert result["exit_reason"] == "stop_loss", result
    print(f"PASS — full pipeline wired correctly, stopped out: {result}")

    print("\nTesting run_one_signal — no matching contract type (skip, not crash)...")
    signal_no_put = dict(signal, side="sell")
    signal_no_put["raw_instruments"] = {
        "data": {"instruments": [
            {"id": "call-620", "strike_price": "620.0000", "type": "call",
             "expiration_date": "2026-01-16"},
        ]}
    }
    assert run_one_signal(signal_no_put) is None
    print("PASS — bearish signal with no put available returns None, not a crash or a substitute.")

    print("\nTesting run_one_signal — entry date missing from bars (skip, not crash)...")
    signal_missing_entry = dict(signal, date="2026-01-05")
    assert run_one_signal(signal_missing_entry) is None
    print("PASS — no bar on the entry date returns None, never fabricates an entry price.")

    print("\nTesting run_one_signal — contract has zero historical bars anywhere (skip, not crash)...")
    signal_no_historicals = dict(signal)
    signal_no_historicals["raw_historicals"] = {
        "data": {"results": [{"instrument_id": "some-other-contract-id", "bars": []}]}
    }
    assert run_one_signal(signal_no_historicals) is None
    print("PASS — options_data.parse_option_bars()'s ValueError (instrument_id not in results) "
          "is caught here and treated as a skip, not an unhandled crash.")

    print("\nTesting run_backtest — mix of a usable and an unusable signal...")
    report = run_backtest([signal, signal_no_put, signal_missing_entry])
    assert report["signals_total"] == 3
    assert report["signals_skipped"] == 2
    assert report["summary"]["count"] == 1
    assert report["summary"]["losses"] == 1
    print(f"PASS — 1 of 3 signals produced a trade, 2 skipped and excluded from the summary: {report['summary']}")
