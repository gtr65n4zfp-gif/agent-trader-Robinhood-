"""
agents/options_risk_vetoer.py — dedicated risk gate for the live SPY
options pass (see
docs/superpowers/specs/2026-07-17-live-spy-options-design.md).

Mirrors agents.risk_vetoer's principles, scaled for options units
(quantity * OPTIONS_CONTRACT_MULTIPLIER * price, not shares * price) —
a separate module rather than an extension of the equity vetoer, since
this account only ever holds SPY options and doesn't need sector
concentration or ATR-based share-count sizing, both meaningless here.

Same discipline as the equity vetoer: pure veto power, never originates
a trade, never raises for a failed check — returns approved=False with
a reason. The caller (execution.options_paper_broker.OptionsPaperBroker)
is what raises OptionsTradeError.

Closing a position is never blocked by any of these caps — capital
preservation always wins, same reasoning as the equity vetoer's sell
side.
"""

from execution import config


def review(
    action: str,
    contract_cost: float,
    account: dict,
    trades_today: int | None = None,
    daily_loss_pct: float | None = None,
) -> dict:
    """
    Check a proposed options trade against the risk caps.

    action: "open" or "close" -- only "open" is ever subject to the
    caps below.
    contract_cost: quantity * config.OPTIONS_CONTRACT_MULTIPLIER *
    entry_fill -- the actual dollar cost of this trade.
    account: an OptionsPaperBroker.account(current_marks) snapshot (or
    an equivalently shaped dict) valued at current marks, so
    total_value is accurate for the position-percentage check.
    trades_today: how many buys/sells have already executed today.
    Optional -- omit to skip the daily trade-count breaker.
    daily_loss_pct: how far current total_value sits below today's
    starting equity (e.g. 0.03 = 3% down since the day began). Optional
    -- omit to skip the daily-loss breaker. Checked against
    config.MAX_DAILY_LOSS_PCT -- the existing equity constant, reused
    here rather than duplicated with a separate options-specific one.

    Returns a decision dict with `approved`, a human-readable `reason`,
    the individual `checks`, and the numbers behind them in `detail`.
    """
    if action not in ("open", "close"):
        raise ValueError(f"action must be 'open' or 'close', got {action!r}")

    detail = {"contract_cost": round(contract_cost, 2)}

    if action == "close":
        return {
            "seat": "options_risk_vetoer", "approved": True,
            "reason": "closing a position is never blocked",
            "checks": {}, "detail": detail,
            "action": action, "contract_cost": round(contract_cost, 2),
        }

    checks = {"within_trade_cap": contract_cost <= config.OPTIONS_MAX_TRADE_USD}
    detail["max_trade_usd"] = config.OPTIONS_MAX_TRADE_USD

    total_value = account.get("total_value", 0)
    position_pct = contract_cost / total_value if total_value > 0 else float("inf")
    checks["within_position_pct"] = position_pct <= config.OPTIONS_MAX_POSITION_PCT
    detail["projected_position_pct"] = round(position_pct, 4)
    detail["max_position_pct"] = config.OPTIONS_MAX_POSITION_PCT

    if trades_today is not None:
        checks["within_daily_trade_limit"] = trades_today < config.OPTIONS_MAX_TRADES_PER_DAY
        detail["trades_today"] = trades_today
        detail["max_trades_per_day"] = config.OPTIONS_MAX_TRADES_PER_DAY

    if daily_loss_pct is not None:
        checks["within_daily_loss_limit"] = daily_loss_pct < config.MAX_DAILY_LOSS_PCT
        detail["daily_loss_pct"] = round(daily_loss_pct, 4)
        detail["max_daily_loss_pct"] = config.MAX_DAILY_LOSS_PCT

    approved = all(checks.values())
    failed = [name for name, ok in checks.items() if not ok]
    reason = "within all risk limits" if approved else f"failed: {', '.join(failed)}"

    return {
        "seat": "options_risk_vetoer", "approved": approved, "reason": reason,
        "checks": checks, "detail": detail,
        "action": action, "contract_cost": round(contract_cost, 2),
    }


if __name__ == "__main__":
    demo_account = {"cash": 8000.0, "positions_value": 2000.0, "total_value": 10000.0}

    print("Testing review -- small open, well within every cap (should pass)...")
    d1 = review("open", 500.0, demo_account, trades_today=0, daily_loss_pct=0.0)
    assert d1["approved"] is True, d1
    print(f"PASS — small open approved: {d1}")

    print("\nTesting review -- open exceeding the trade-cost cap (should fail)...")
    d2 = review("open", 3000.0, demo_account)
    assert d2["approved"] is False and "within_trade_cap" in d2["reason"], d2
    print(f"PASS — over OPTIONS_MAX_TRADE_USD blocked: {d2}")

    print("\nTesting review -- open under the trade cap but over position-pct (should fail)...")
    small_account = {"cash": 900.0, "positions_value": 0.0, "total_value": 900.0}
    d3 = review("open", 300.0, small_account)  # 300/900 = 33% > 25%
    assert d3["approved"] is False and "within_position_pct" in d3["reason"], d3
    print(f"PASS — over OPTIONS_MAX_POSITION_PCT blocked: {d3}")

    print("\nTesting review -- daily trade-count breaker (should fail)...")
    d4 = review("open", 500.0, demo_account, trades_today=2)
    assert d4["approved"] is False and "within_daily_trade_limit" in d4["reason"], d4
    print(f"PASS — trades_today at the OPTIONS_MAX_TRADES_PER_DAY cap blocked: {d4}")

    print("\nTesting review -- daily-loss breaker (should fail)...")
    d5 = review("open", 500.0, demo_account, daily_loss_pct=0.06)  # cap is 0.05
    assert d5["approved"] is False and "within_daily_loss_limit" in d5["reason"], d5
    print(f"PASS — daily_loss_pct over MAX_DAILY_LOSS_PCT blocked: {d5}")

    print("\nTesting review -- closing is never blocked, even far over every cap...")
    d6 = review("close", 999999.0, demo_account, trades_today=999, daily_loss_pct=0.99)
    assert d6["approved"] is True, d6
    print(f"PASS — close approved unconditionally: {d6}")

    print("\nTesting review -- rejects an invalid action...")
    try:
        review("hold", 500.0, demo_account)
        raise AssertionError("should have raised ValueError")
    except ValueError as e:
        print(f"PASS — raised clearly: {e}")
