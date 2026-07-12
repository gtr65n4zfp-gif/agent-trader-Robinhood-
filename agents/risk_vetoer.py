"""
Risk vetoer — first seat of the trade council (see agents/COUNCIL_DESIGN.md).

Pure veto power: given a proposed trade and the current account state, checks
the proposal against the hard risk caps in execution/config.py
(MAX_TRADE_USD, MAX_POSITION_PCT). It never originates a trade — only
approves or rejects one that already exists elsewhere (a human today,
eventually the Fundamentals/Technicals seats + Judge). No LLM and no
judgment call: this seat is pure arithmetic against fixed limits, which is
why it's the first one built.

A veto is a normal, expected outcome, not an error — this module never
raises for a failed check. It only reads account state that's handed to it;
it doesn't touch PaperBroker or place anything itself.
"""

from execution import config


def review(symbol: str, side: str, quantity: float, price: float, account: dict) -> dict:
    """
    Check a proposed trade against the risk caps.

    account: a PaperBroker.account(prices) snapshot (or an equivalently
    shaped dict) valued at current prices, so positions_value/total_value
    are accurate for the position-concentration check.

    Returns a decision dict with `approved`, a human-readable `reason`, the
    individual `checks`, and the numbers behind them in `detail`.
    """
    symbol = symbol.upper()
    side = side.lower()
    cost = quantity * price

    checks = {"within_trade_cap": cost <= config.MAX_TRADE_USD}
    detail = {"cost": round(cost, 2), "max_trade_usd": config.MAX_TRADE_USD}

    if side == "buy":
        total_value = account.get("total_value", 0)
        current_position_value = account.get("positions", {}).get(symbol, 0) * price
        projected_position_value = current_position_value + cost
        position_pct = projected_position_value / total_value if total_value > 0 else float("inf")

        checks["within_position_cap"] = position_pct <= config.MAX_POSITION_PCT
        detail["projected_position_pct"] = round(position_pct, 4)
        detail["max_position_pct"] = config.MAX_POSITION_PCT
    else:
        # Selling only reduces exposure — concentration cap doesn't apply.
        checks["within_position_cap"] = True

    approved = all(checks.values())
    failed = [name for name, ok in checks.items() if not ok]
    reason = "within all risk limits" if approved else f"failed: {', '.join(failed)}"

    return {
        "seat": "risk_vetoer",
        "approved": approved,
        "reason": reason,
        "checks": checks,
        "detail": detail,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "price": price,
    }


if __name__ == "__main__":
    # Self-test against a fixed, synthetic account snapshot — deterministic,
    # doesn't depend on whatever PaperBroker state happens to be on disk.
    demo_account = {
        "cash": 9000.0,
        "positions": {"AAPL": 4.0},
        "positions_value": 800.0,
        "total_value": 9800.0,
        "starting_cash": 10000.0,
    }

    print("Small, well-diversified buy (should pass):")
    print(review("MSFT", "buy", 1, 300.0, demo_account))

    print("\nBuy that exceeds the per-trade dollar cap (should fail):")
    print(review("AAPL", "buy", 10, 200.0, demo_account))

    print("\nBuy under the dollar cap but over position concentration (should fail):")
    print(review("AAPL", "buy", 2, 200.0, demo_account))

    print("\nSell (never blocked by the position cap):")
    print(review("AAPL", "sell", 4, 200.0, demo_account))
