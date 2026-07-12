"""
Exit engine (see agents/COUNCIL_DESIGN.md's "Exit logic" section) — the
council's other half. Entries get most of the attention, but a trade has
no outcome and P&L is meaningless without a defined way to close it.

Unlike the entry gate (Judge: conjunctive, all seats must agree), exits
are disjunctive — the first path that fires wins, checked in priority
order:

    1. stop_loss        — hard, mechanical, not overridable by the judge.
    2. take_profit       — target hit, gains locked in.
    3. [regime_change]   — NOT BUILT YET; the seam for it is right here,
                            between take_profit and conviction_drop, per
                            COUNCIL_DESIGN.md's listed order. Next
                            milestone, not this one.
    4. conviction_drop   — the thesis that justified holding has weakened.

Once a position is open, the priority is capital preservation, not
confirmation — any one reason is enough, unlike entries.

check_stop_loss / check_take_profit / check_conviction_drop / evaluate_exits
are pure functions: no PaperBroker calls, no trade_log writes, no market
data fetched. close_position() and run_exit_sweep() are the impure layer
that actually acts on a fired exit signal — same split as
agents/judge.py (decide()/baseline_decide() are pure; the calling script
does the logging) and agents/risk_vetoer.py (review() is pure; PaperBroker
is what writes veto records).
"""

from execution import config, trade_log
from execution.paper_broker import PaperBroker

from . import judge


def check_stop_loss(entry_price: float, current_price: float) -> dict:
    """Fires if current_price has fallen STOP_LOSS_PCT or more below
    entry_price. Hard and mechanical — no seat input, just price."""
    pct_change = (current_price - entry_price) / entry_price
    fires = pct_change <= -config.STOP_LOSS_PCT
    reason = (
        f"price {pct_change * 100:+.1f}% vs entry, breached the "
        f"-{config.STOP_LOSS_PCT * 100:.0f}% stop-loss"
        if fires else
        f"price {pct_change * 100:+.1f}% vs entry, within the stop-loss band"
    )
    return {"path": "stop_loss", "fires": fires, "reason": reason}


def check_take_profit(entry_price: float, current_price: float) -> dict:
    """Fires if current_price has risen TAKE_PROFIT_PCT or more above
    entry_price. Same mechanical nature as check_stop_loss, opposite side."""
    pct_change = (current_price - entry_price) / entry_price
    fires = pct_change >= config.TAKE_PROFIT_PCT
    reason = (
        f"price {pct_change * 100:+.1f}% vs entry, cleared the "
        f"+{config.TAKE_PROFIT_PCT * 100:.0f}% take-profit target"
        if fires else
        f"price {pct_change * 100:+.1f}% vs entry, below the take-profit target"
    )
    return {"path": "take_profit", "fires": fires, "reason": reason}


def check_conviction_drop(
    fundamentals: dict, technicals: dict, quantity: float = judge.DEFAULT_QUANTITY
) -> dict:
    """
    Re-run the Judge on a HELD symbol's fresh seat outputs. Fires if the
    Judge no longer supports holding it — the re-decision isn't a "buy"
    (a fresh hold or a flip to "sell" both count), or its confidence has
    fallen below CONVICTION_DROP_THRESHOLD even though direction hasn't
    changed. The original entry decision doesn't get re-litigated here;
    only whether the thesis still holds *right now*.

    fundamentals / technicals: fresh agents.fundamentals_seat.form_verdict()
    and agents.technicals.build_view() output for the held symbol — a new
    read, not what justified the original entry.
    """
    redecision = judge.decide(fundamentals, technicals, quantity=quantity)
    thesis_holds = (
        redecision["action"] == "buy"
        and redecision["confidence"] >= config.CONVICTION_DROP_THRESHOLD
    )
    fires = not thesis_holds
    reason = (
        f"Judge re-decision: {redecision['action']} (confidence {redecision['confidence']}) — "
        + ("thesis no longer supports holding" if fires else "thesis still holds")
    )
    return {"path": "conviction_drop", "fires": fires, "reason": reason, "redecision": redecision}


def evaluate_exits(
    entry_price: float,
    current_price: float,
    fundamentals: dict | None = None,
    technicals: dict | None = None,
    quantity: float = judge.DEFAULT_QUANTITY,
) -> dict | None:
    """
    Run every applicable exit path in priority order; return the first
    that fires, or None if the position should stay open.

    fundamentals / technicals: optional, required only for
    check_conviction_drop — omit either to skip that check entirely (same
    graceful-degrade pattern as agents.risk_vetoer.review()'s optional
    args), e.g. when fresh seat re-reads aren't available for this pass.
    """
    stop = check_stop_loss(entry_price, current_price)
    if stop["fires"]:
        return stop

    profit = check_take_profit(entry_price, current_price)
    if profit["fires"]:
        return profit

    # Regime-change exit would slot in here, per COUNCIL_DESIGN.md's
    # listed priority order — not built yet (next milestone).

    if fundamentals is not None and technicals is not None:
        conviction = check_conviction_drop(fundamentals, technicals, quantity=quantity)
        if conviction["fires"]:
            return conviction

    return None


def close_position(broker: PaperBroker, symbol: str, quantity: float, price: float, reason: str) -> list[dict]:
    """
    Close (fully or partially) a position through PaperBroker.sell(),
    splitting into multiple sells if the full quantity would exceed
    MAX_TRADE_USD — a position must always be closeable, even one that's
    grown past the per-trade dollar cap since it was opened. Exits are
    never blocked by the exposure-reducing breakers (drawdown/sector/
    daily-loss — see PaperBroker.sell()'s docstring), so the only reason
    a close would fail here is the flat dollar cap, which this works
    around instead of accepting.

    Returns the list of individual sell trade records — usually one,
    more if split.
    """
    trades: list[dict] = []
    remaining = quantity
    # 0.99 safety margin: PaperBroker checks the cap against the fill
    # price (post-slippage), not this chunk-sizing estimate.
    max_shares_per_chunk = max(1e-9, (config.MAX_TRADE_USD * 0.99) / price)
    while remaining > 1e-9:
        chunk = min(remaining, max_shares_per_chunk)
        trades.append(broker.sell(symbol, chunk, price, reason=reason))
        remaining -= chunk
    return trades


def run_exit_sweep(
    broker: PaperBroker,
    prices: dict[str, float],
    seat_views: dict[str, tuple[dict, dict]] | None = None,
) -> list[dict]:
    """
    Check every open position against all exit paths and close whichever
    fire. This is the "evaluation loop": the impure orchestrator that
    turns evaluate_exits()'s pure decisions into real PaperBroker.sell()
    calls (via close_position(), so a large position still closes even
    past the per-trade cap) and logs which path fired.

    prices: {symbol: current_price} — a symbol with no price here is
    skipped, not treated as "no exit."
    seat_views: optional {symbol: (fundamentals, technicals)} for the
    conviction_drop check. A symbol missing here just skips that one
    check for that symbol, same as evaluate_exits()'s own graceful
    degrade — stop_loss and take_profit still run regardless.

    Returns one entry per position actually closed:
    {symbol, path, reason, realized_pnl, trades}.
    """
    closures: list[dict] = []
    for symbol, shares in list(broker.positions.items()):  # list(): sell() mutates broker.positions
        if symbol not in prices:
            continue
        entry_price = broker.cost_basis.get(symbol)
        if entry_price is None:
            # No cost basis on record (e.g. a pre-existing position from
            # before cost-basis tracking existed) — nothing to evaluate
            # stop_loss/take_profit against. Not a crash, not an exit.
            continue

        fundamentals, technicals = seat_views.get(symbol, (None, None)) if seat_views else (None, None)
        signal = evaluate_exits(entry_price, prices[symbol], fundamentals, technicals)
        if signal is None:
            continue

        trades = close_position(
            broker, symbol, shares, prices[symbol],
            reason=f"exit: {signal['path']} — {signal['reason']}",
        )
        realized_pnl = round(sum(t["realized_pnl"] or 0 for t in trades), 2)
        trade_log.record(
            "exit", symbol, shares, prices[symbol], paper=True,
            reason=signal["reason"],
            extra={"path": signal["path"], "realized_pnl": realized_pnl, "num_fills": len(trades)},
        )
        closures.append({
            "symbol": symbol, "path": signal["path"], "reason": signal["reason"],
            "realized_pnl": realized_pnl, "trades": trades,
        })
    return closures


if __name__ == "__main__":
    # Self-test of the pure check functions — deterministic, no network
    # or PaperBroker needed. The full close_position()/run_exit_sweep()
    # path is proven live in agents/demo_exits.py instead, since it needs
    # a real broker to act against.
    print("Stop-loss: price 10% below entry (should fire):")
    print(check_stop_loss(entry_price=100.0, current_price=90.0))

    print("\nStop-loss: price 3% below entry (should NOT fire):")
    print(check_stop_loss(entry_price=100.0, current_price=97.0))

    print("\nTake-profit: price 20% above entry (should fire):")
    print(check_take_profit(entry_price=100.0, current_price=120.0))

    print("\nTake-profit: price 5% above entry (should NOT fire):")
    print(check_take_profit(entry_price=100.0, current_price=105.0))

    weak_fundamentals = {
        "seat": "fundamentals", "symbol": "AAPL", "stance": "neutral",
        "confidence": 0.2, "reasons": ["thesis has weakened"],
    }
    still_bullish_technicals = {
        "seat": "technicals", "symbol": "AAPL", "stance": "bullish",
        "confidence": 0.6, "reasons": ["still above EMA"],
    }
    print("\nConviction drop: fundamentals turned neutral, no longer aligned (should fire):")
    print(check_conviction_drop(weak_fundamentals, still_bullish_technicals))

    still_bullish_fundamentals = {
        "seat": "fundamentals", "symbol": "AAPL", "stance": "bullish",
        "confidence": 0.7, "reasons": ["still strong"],
    }
    print("\nConviction drop: both seats still bullish and confident (should NOT fire):")
    print(check_conviction_drop(still_bullish_fundamentals, still_bullish_technicals))

    print("\nevaluate_exits: stop-loss wins even when other paths might also fire "
          "(priority order — stop-loss is checked first):")
    print(evaluate_exits(
        entry_price=100.0, current_price=90.0,
        fundamentals=weak_fundamentals, technicals=still_bullish_technicals,
    ))

    print("\nevaluate_exits: no price trigger, no seat data supplied -> stays open:")
    print(evaluate_exits(entry_price=100.0, current_price=101.0))
