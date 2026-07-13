"""
backtest/engine.py — the day-stepping loop (see agents/BACKTEST_DESIGN.md,
"Engine (task 3)"). Steps through a historical window date by date,
running the REAL council pipeline unchanged against three parallel,
fully isolated accounts:

    1. council   — regime -> seats -> judge (the actual live pipeline)
    2. baseline  — the ablation/single-model shadow (judge.baseline_decide),
                   regime-blind per its own design, exits skip
                   conviction_drop (no baseline-specific conviction check
                   exists — see BACKTEST_DESIGN.md's "Engine" section for
                   why this is the chosen semantics, not the only
                   possible one)
    3. buyhold   — naive equal-weight buy-and-hold, same fee/slippage
                   model as the other two (a fair comparison shouldn't
                   give the benchmark free perfect fills)

Mirrors automation/run_pass.py's own structure (exit sweep before
entries) rather than calling agents.exits.run_exit_sweep() directly, for
the same reason run_pass.py doesn't: logging needs to target an isolated
log_path, not the module-default shared one.

ISOLATION: every account gets its own portfolio_path/log_path under
logs/backtests/<run-id>/ (execution.trade_log's and PaperBroker's
override params — see agents/BACKTEST_DESIGN.md items 1-2, 5). Nothing
here ever touches logs/trades.jsonl or logs/paper_portfolio.json. No
Robinhood order-placement tool is ever called — only PaperBroker.buy()/
sell() against these isolated accounts.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from agents import exits, judge, regime, technicals
from execution import config, trade_log
from execution.paper_broker import PaperBroker

from . import data as backtest_data


def trading_days(bars_by_symbol: dict[str, list[dict]], start: str, end: str) -> list[str]:
    """Union of every symbol's trading dates within [start, end] — a day
    only ONE symbol traded on is still a valid simulated day (that symbol
    gets evaluated; others are naturally absent that day, same as a
    data-sanity skip elsewhere in this project)."""
    all_dates: set[str] = set()
    for bars in bars_by_symbol.values():
        all_dates.update(b["date"] for b in bars if start <= b["date"] <= end)
    return sorted(all_dates)


def verdict_for(symbol: str, as_of: str, fundamentals_cache: dict) -> dict | None:
    """Look up the most recent cached Fundamentals verdict with a
    boundary date <= as_of for `symbol` — the per-filing-boundary caching
    strategy from BACKTEST_DESIGN.md's "Cost strategy". None if no
    boundary has been reached yet — the caller skips this (symbol, date),
    same fail-safe convention as a missing quote."""
    boundaries = fundamentals_cache.get(symbol, {})
    eligible = [d for d in boundaries if d <= as_of]
    if not eligible:
        return None
    return boundaries[max(eligible)]


def _run_dir(run_id: str) -> str:
    path = os.path.join(config.LOG_DIR, "backtests", run_id)
    os.makedirs(path, exist_ok=True)
    return path


def _account_paths(run_dir: str, name: str) -> tuple[str, str]:
    return (
        os.path.join(run_dir, f"{name}_portfolio.json"),
        os.path.join(run_dir, f"{name}_trades.jsonl"),
    )


def _as_datetime(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def run_backtest(
    run_id: str,
    symbols: list[str],
    start: str,
    end: str,
    bars_by_symbol: dict[str, list[dict]],
    fundamentals_cache: dict,
) -> dict:
    """
    Step through [start, end] date by date for `symbols`, running the real
    council pipeline against three isolated accounts. See module docstring.

    bars_by_symbol: {symbol: data.parse_bars() output} — FULL fetched
    history (including warm-up before `start`), truncated internally per
    date by data.technicals_as_of().
    fundamentals_cache: {symbol: {boundary_date: form_verdict() output}} —
    pre-formed by the interactive pre-pass (see BACKTEST_DESIGN.md's cost
    strategy), never formed by this function — it makes no LLM calls.

    Returns a run summary; the actual trade-by-trade results live in each
    account's isolated trades.jsonl, read by backtest/metrics.py after.
    """
    run_dir = _run_dir(run_id)
    council_portfolio, council_log = _account_paths(run_dir, "council")
    baseline_portfolio, baseline_log = _account_paths(run_dir, "baseline")
    buyhold_portfolio, buyhold_log = _account_paths(run_dir, "buyhold")

    council_broker = PaperBroker(portfolio_path=council_portfolio, log_path=council_log)
    baseline_broker = PaperBroker(portfolio_path=baseline_portfolio, log_path=baseline_log)
    buyhold_broker = PaperBroker(portfolio_path=buyhold_portfolio, log_path=buyhold_log)

    days = trading_days(bars_by_symbol, start, end)
    symbols_skipped_total = 0
    buyhold_bought = False

    for date_str in days:
        now = _as_datetime(date_str)

        # --- Build this day's bundle for every symbol with data --------
        views: dict[str, dict] = {}
        prices: dict[str, float] = {}
        for symbol in symbols:
            fverdict = verdict_for(symbol, date_str, fundamentals_cache)
            if fverdict is None:
                symbols_skipped_total += 1
                continue
            bundle = backtest_data.council_bundle_for(
                symbol, date_str, bars_by_symbol.get(symbol, []),
                config.REGIME_EMA_LOOKBACK_DAYS, fverdict,
            )
            if bundle is None:
                symbols_skipped_total += 1
                continue
            prices[symbol] = bundle["price"]
            views[symbol] = {
                "regime": regime.regime_stance(symbol, bundle["price"], ema=bundle["regime_ema"], atr_pct=bundle["atr_pct"]),
                "technicals": technicals.build_view(symbol, bundle["price"], ema=bundle["ema"], rsi=bundle["rsi"], atr_pct=bundle["atr_pct"]),
                "fundamentals": fverdict,
                "atr_pct": bundle["atr_pct"],
            }

        if not views:
            continue

        # --- Buy-and-hold: buy everything available on day 1, once -----
        # Chunked into MAX_TRADE_USD-sized pieces, same as agents.exits's
        # close_position() — a single per_symbol_cash buy would trip the
        # flat trade-size cap outright. This account still routes through
        # the SAME PaperBroker/risk vetoer as the other two, so it still
        # can't exceed MAX_POSITION_PCT per symbol either; a fair
        # benchmark shouldn't get free capital deployment the council and
        # baseline could never actually achieve under the same rules —
        # loop just stops (not crashes) once the position cap vetoes it.
        if not buyhold_bought:
            per_symbol_cash = config.PAPER_STARTING_CASH / len(symbols)
            for symbol in views:
                price = prices[symbol]
                remaining_cash = per_symbol_cash
                while remaining_cash > 1e-6:
                    chunk_cash = min(remaining_cash, config.MAX_TRADE_USD * 0.99)
                    qty = chunk_cash / price
                    try:
                        buyhold_broker.buy(symbol, qty, price, reason="backtest: buy-and-hold entry",
                                            prices=prices, now=now)
                        remaining_cash -= chunk_cash
                    except Exception:
                        break  # further chunks would just re-veto (e.g. position cap) — stop, don't crash
            buyhold_bought = True

        # --- Council: exit sweep FIRST, then entries ---------------------
        for symbol, shares in list(council_broker.positions.items()):
            if symbol not in views:
                continue
            entry_price = council_broker.cost_basis.get(symbol)
            if entry_price is None:
                continue
            v = views[symbol]
            signal = exits.evaluate_exits(
                entry_price, prices[symbol],
                fundamentals=v["fundamentals"], technicals=v["technicals"], regime=v["regime"],
            )
            if signal is None:
                continue
            fills = exits.close_position(council_broker, symbol, shares, prices[symbol],
                                          reason=f"backtest exit: {signal['path']} — {signal['reason']}", now=now)
            # Separate summary record carrying regime_state + total realized_pnl
            # across however many chunks close_position() split into — same
            # pattern automation/run_pass.py's own "exit" record already uses,
            # so backtest/metrics.py can group P&L by regime the same way.
            realized_pnl = round(sum(f["realized_pnl"] or 0 for f in fills), 2)
            trade_log.record("backtest_exit", symbol, shares, prices[symbol], paper=True,
                              reason=signal["reason"],
                              extra={"path": signal["path"], "realized_pnl": realized_pnl,
                                     "regime_state": v["regime"]["state"], "account": "council"},
                              log_path=council_log)

        for symbol, v in views.items():
            if symbol in council_broker.positions:
                continue
            decision = judge.decide(v["fundamentals"], v["technicals"], regime=v["regime"])
            if decision["action"] == "hold":
                continue
            try:
                if decision["action"] == "buy":
                    council_broker.buy(symbol, decision["target_quantity"], prices[symbol],
                                        reason=f"backtest: {decision['rationale']}",
                                        prices=prices, atr_pct=v["atr_pct"], now=now)
                else:
                    council_broker.sell(symbol, decision["target_quantity"], prices[symbol],
                                         reason=f"backtest: {decision['rationale']}", prices=prices, now=now)
            except Exception:
                pass  # risk-vetoer veto or insufficient cash — same "no path around the gate" as live

        # --- Baseline: exit sweep (mechanical paths only), then entries -
        # Deliberately NOT passing fundamentals/technicals here: skips
        # conviction_drop (which would otherwise call the REAL judge.decide
        # internally, muddying a clean baseline-vs-council comparison) —
        # stop_loss/take_profit/regime_change still apply. See
        # BACKTEST_DESIGN.md's "Engine" section.
        for symbol, shares in list(baseline_broker.positions.items()):
            if symbol not in views:
                continue
            entry_price = baseline_broker.cost_basis.get(symbol)
            if entry_price is None:
                continue
            v = views[symbol]
            signal = exits.evaluate_exits(entry_price, prices[symbol], regime=v["regime"])
            if signal is None:
                continue
            fills = exits.close_position(baseline_broker, symbol, shares, prices[symbol],
                                          reason=f"backtest baseline exit: {signal['path']} — {signal['reason']}", now=now)
            realized_pnl = round(sum(f["realized_pnl"] or 0 for f in fills), 2)
            trade_log.record("backtest_exit", symbol, shares, prices[symbol], paper=True,
                              reason=signal["reason"],
                              extra={"path": signal["path"], "realized_pnl": realized_pnl,
                                     "regime_state": v["regime"]["state"], "account": "baseline"},
                              log_path=baseline_log)

        for symbol, v in views.items():
            if symbol in baseline_broker.positions:
                continue
            decision = judge.baseline_decide(v["fundamentals"], v["technicals"])
            if decision["action"] == "hold":
                continue
            try:
                if decision["action"] == "buy":
                    baseline_broker.buy(symbol, decision["target_quantity"], prices[symbol],
                                         reason=f"backtest baseline: {decision['rationale']}",
                                         prices=prices, now=now)
                else:
                    baseline_broker.sell(symbol, decision["target_quantity"], prices[symbol],
                                          reason=f"backtest baseline: {decision['rationale']}",
                                          prices=prices, now=now)
            except Exception:
                pass

    # --- Close out buy-and-hold at window end, so its return is expressed
    # through the same realized_pnl accounting path as the other two -----
    if days:
        last_day = days[-1]
        now = _as_datetime(last_day)
        final_prices: dict[str, float] = {}
        for symbol in list(buyhold_broker.positions.keys()):
            tech = backtest_data.technicals_as_of(
                symbol, last_day, bars_by_symbol.get(symbol, []), config.REGIME_EMA_LOOKBACK_DAYS
            )
            if tech is not None:
                final_prices[symbol] = tech["price"]
        for symbol, shares in list(buyhold_broker.positions.items()):
            if symbol in final_prices:
                # Chunked close, same reason as the entry above — an
                # accumulated multi-chunk position can easily exceed
                # MAX_TRADE_USD in one sell.
                exits.close_position(buyhold_broker, symbol, shares, final_prices[symbol],
                                      reason="backtest: buy-and-hold window-end close", now=now)

    return {
        "run_dir": run_dir,
        "days_simulated": len(days),
        "symbols_skipped_total": symbols_skipped_total,
        "accounts": {
            "council": {"portfolio_path": council_portfolio, "log_path": council_log},
            "baseline": {"portfolio_path": baseline_portfolio, "log_path": baseline_log},
            "buyhold": {"portfolio_path": buyhold_portfolio, "log_path": buyhold_log},
        },
    }
