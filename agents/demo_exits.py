"""
Exit-logic demo — proves the three exit paths that existed when this demo
was written (stop_loss, take_profit, conviction_drop) fire correctly and
close positions through PaperBroker, with zero real-money risk. A fourth
path, regime_change, was added later — see agents/demo_regime.py for its
dedicated proof, kept separate rather than folded in here.

Uses an ISOLATED paper account (logs/demo_exits_portfolio.json, via
PaperBroker's portfolio_path override) rather than the shared one, reset
at the start of every run — so this proof is deterministic and doesn't
depend on whatever state the shared paper account happens to be in (it's
currently mid-drawdown from earlier, unrelated demos this session). See
PaperBroker.__init__'s docstring for why that override exists. Entry and
exit prices below are controlled, not live quotes — the point here is
proving the exit logic, not live-data integration (already proven
elsewhere: execution/demo_live_paper.py, agents/demo_council.py).

Run it as: python -m agents.demo_exits
"""

import os

from . import exits, technicals
from execution import config, trade_log
from execution.paper_broker import PaperBroker

_DEMO_PORTFOLIO_PATH = os.path.join(config.LOG_DIR, "demo_exits_portfolio.json")


def _fresh_broker() -> PaperBroker:
    """Start from a clean isolated account every run, not whatever the
    demo portfolio file held from a prior run."""
    if os.path.exists(_DEMO_PORTFOLIO_PATH):
        os.remove(_DEMO_PORTFOLIO_PATH)
    return PaperBroker(portfolio_path=_DEMO_PORTFOLIO_PATH)


def run_demo() -> None:
    print(config.mode_banner())
    broker = _fresh_broker()

    before = trade_log.round_trip_stats()
    print(f"Round-trip stats before this demo (shared trade_log): {before}")

    # --- Open three small, fresh positions at known entry prices --------
    # NOTE: trade_log is shared (see PaperBroker.__init__'s docstring) —
    # MAX_TRADES_PER_DAY reads it globally, regardless of which portfolio
    # is active. A long testing session can genuinely exhaust today's
    # count before this demo ever runs, blocking even a brand-new
    # isolated account's first buy. That's a real interaction, not a bug
    # in the exit logic being proven here — worked around for just the
    # opens below (restored immediately after) rather than silently
    # raised for good.
    original_cap = config.MAX_TRADES_PER_DAY
    trades_today = trade_log.count_trades_today()
    if trades_today >= original_cap:
        print(f"\n(Shared trade_log already has {trades_today} trades today, at/over "
              f"MAX_TRADES_PER_DAY={original_cap} — temporarily raising it to open this "
              f"demo's positions, restored right after.)")
        config.MAX_TRADES_PER_DAY = trades_today + 3

    entries = {"JNJ": 250.0, "KO": 80.0, "XOM": 140.0}
    print("\nOpening positions:")
    known_prices: dict[str, float] = {}
    for symbol, price in entries.items():
        # Pass prices for every already-opened position too — otherwise
        # _check_risk values them at $0 (account()'s documented behavior
        # for a symbol missing from `prices`), making the account look
        # like it's lost money it hasn't and tripping the daily-loss
        # breaker on nothing.
        trade = broker.buy(symbol, 3, price, prices=known_prices,
                           reason=f"demo_exits: open {symbol} for exit-path proof")
        known_prices[symbol] = price
        print(f"  {symbol}: {trade}")

    config.MAX_TRADES_PER_DAY = original_cap

    print(f"\nAccount after opens: {broker.account(entries)}")

    # --- Force each exit path with controlled current prices ------------
    # JNJ: drop well past STOP_LOSS_PCT below its entry fill.
    jnj_price = round(broker.cost_basis["JNJ"] * (1 - config.STOP_LOSS_PCT - 0.02), 2)
    # KO: push well past TAKE_PROFIT_PCT above its entry fill.
    ko_price = round(broker.cost_basis["KO"] * (1 + config.TAKE_PROFIT_PCT + 0.02), 2)
    # XOM: price unchanged — only conviction_drop should fire, forced by a
    # fresh Fundamentals/Technicals re-read that no longer supports holding.
    xom_price = broker.cost_basis["XOM"]
    current_prices = {"JNJ": jnj_price, "KO": ko_price, "XOM": xom_price}

    weakened_fundamentals = {
        "seat": "fundamentals", "symbol": "XOM", "stance": "neutral", "confidence": 0.2,
        "reasons": ["demo: fresh re-read — the thesis that justified opening this position has weakened"],
    }
    weakened_technicals = technicals.build_view("XOM", price=xom_price)  # no EMA/RSI -> neutral, 0 confidence
    seat_views = {"XOM": (weakened_fundamentals, weakened_technicals)}

    print(f"\nForced prices for the exit sweep: {current_prices}")
    print(f"XOM re-read for conviction_drop: fundamentals={weakened_fundamentals['stance']} "
          f"({weakened_fundamentals['confidence']}), technicals={weakened_technicals['stance']} "
          f"({weakened_technicals['confidence']})")

    closures = exits.run_exit_sweep(broker, current_prices, seat_views=seat_views)

    print("\nExit sweep results:")
    for c in closures:
        print(f"  {c['symbol']:5} closed via {c['path']:15} realized P&L ${c['realized_pnl']:+.2f} — {c['reason']}")

    print(f"\nAccount after exits: {broker.account(current_prices)}")

    after = trade_log.round_trip_stats()
    print(f"\nRound-trip stats after this demo: {after}")
    print(f"New completed round-trips from this run: {after['count'] - before['count']}")
    print(f"Realized P&L from this run: ${after['total_realized_pnl'] - before['total_realized_pnl']:+.2f}")


if __name__ == "__main__":
    run_demo()
