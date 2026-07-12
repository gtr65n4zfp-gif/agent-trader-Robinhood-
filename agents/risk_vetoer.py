"""
Risk vetoer — first seat of the trade council (see agents/COUNCIL_DESIGN.md).

Pure veto power: given a proposed trade and the current account state, checks
the proposal against the risk caps in execution/config.py — a flat per-trade
dollar cap (MAX_TRADE_USD), a volatility-scaled position-concentration cap
(MAX_POSITION_PCT, scaled by TARGET_DAILY_VOL_PCT/MIN_VOL_SCALAR when a
volatility reading is supplied), a sector-concentration cap (MAX_SECTOR_PCT,
when sector data is supplied), a portfolio-wide drawdown circuit breaker
(MAX_DRAWDOWN_PCT), and two daily circuit breakers — a trade-count cap
(MAX_TRADES_PER_DAY) and an intraday loss cap (MAX_DAILY_LOSS_PCT). It never
originates a trade — only approves or rejects
one that already exists elsewhere (a human today, eventually the
Fundamentals/Technicals seats + Judge). No LLM and no judgment call: every
check here is arithmetic against fixed limits, which is why this seat was
built first.

A veto is a normal, expected outcome, not an error — this module never
raises for a failed check. It only reads state that's handed to it; it
doesn't touch PaperBroker, fetch market data, or place anything itself.
"""

from execution import config


def _vol_scalar(atr_pct: float) -> float:
    """How much to shrink the position cap for a symbol this volatile.
    1.0 at or below the target vol (no shrink — the cap is a ceiling, never
    raised for a calmer-than-target name); floors at MIN_VOL_SCALAR so a very
    volatile name still gets some room instead of an effectively-zero cap."""
    if atr_pct <= 0:
        return 1.0
    return max(config.MIN_VOL_SCALAR, min(1.0, config.TARGET_DAILY_VOL_PCT / atr_pct))


def review(
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    account: dict,
    atr_pct: float | None = None,
    portfolio_drawdown_pct: float | None = None,
    sector: str | None = None,
    sector_pct: float | None = None,
    trades_today: int | None = None,
    daily_loss_pct: float | None = None,
) -> dict:
    """
    Check a proposed trade against the risk caps.

    account: a PaperBroker.account(prices) snapshot (or an equivalently
    shaped dict) valued at current prices, so positions_value/total_value
    are accurate for the position-concentration check.
    atr_pct: this symbol's ATR as a fraction of price (e.g. 0.025 = 2.5%
    average daily range). Optional — omit to fall back to the flat
    MAX_POSITION_PCT cap with no volatility adjustment.
    portfolio_drawdown_pct: how far current total_value sits below the
    account's peak equity (e.g. 0.12 = 12% drawdown). Optional — omit to
    skip the drawdown circuit breaker entirely.
    sector / sector_pct: this symbol's sector, and what fraction of the
    account this trade would put into that sector *in total* (this symbol
    plus every other held position sharing it) — the caller computes the
    aggregate, this seat just checks it against MAX_SECTOR_PCT. Optional —
    omit either to skip the sector-concentration check.
    trades_today: how many buys/sells have already executed today. Optional
    — omit to skip the daily trade-count breaker.
    daily_loss_pct: how far current total_value sits below today's starting
    equity (e.g. 0.03 = 3% down since the day began) — distinct from
    portfolio_drawdown_pct, which is measured from the all-time peak.
    Optional — omit to skip the daily-loss breaker.

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

        position_cap = config.MAX_POSITION_PCT
        detail["max_position_pct"] = config.MAX_POSITION_PCT
        if atr_pct is not None:
            scalar = _vol_scalar(atr_pct)
            position_cap = config.MAX_POSITION_PCT * scalar
            detail["atr_pct"] = round(atr_pct, 4)
            detail["vol_scalar"] = round(scalar, 4)
            detail["effective_position_cap"] = round(position_cap, 4)

        checks["within_position_cap"] = position_pct <= position_cap
        detail["projected_position_pct"] = round(position_pct, 4)

        # Drawdown circuit breaker only blocks new entries, never exits.
        if portfolio_drawdown_pct is not None:
            checks["within_drawdown_limit"] = portfolio_drawdown_pct < config.MAX_DRAWDOWN_PCT
            detail["portfolio_drawdown_pct"] = round(portfolio_drawdown_pct, 4)
            detail["max_drawdown_pct"] = config.MAX_DRAWDOWN_PCT

        # Sector cap: catches concentration that per-symbol caps can't see —
        # several correlated names each individually under MAX_POSITION_PCT
        # while the portfolio is still one big correlated bet.
        if sector is not None and sector_pct is not None:
            checks["within_sector_cap"] = sector_pct <= config.MAX_SECTOR_PCT
            detail["sector"] = sector
            detail["projected_sector_pct"] = round(sector_pct, 4)
            detail["max_sector_pct"] = config.MAX_SECTOR_PCT

        # Daily breakers: catch "several bad trades in one session" long
        # before a sustained drawdown from peak would trip. Buys only —
        # exits are never rate- or loss-limited.
        if trades_today is not None:
            checks["within_daily_trade_limit"] = trades_today < config.MAX_TRADES_PER_DAY
            detail["trades_today"] = trades_today
            detail["max_trades_per_day"] = config.MAX_TRADES_PER_DAY
        if daily_loss_pct is not None:
            checks["within_daily_loss_limit"] = daily_loss_pct < config.MAX_DAILY_LOSS_PCT
            detail["daily_loss_pct"] = round(daily_loss_pct, 4)
            detail["max_daily_loss_pct"] = config.MAX_DAILY_LOSS_PCT
    else:
        # Selling only reduces exposure — concentration cap and the
        # drawdown breaker don't apply; capital preservation always wins.
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

    print("\nCalm name (ATR ~2%, at the target vol — cap stays ~full 10%):")
    print(review("MSFT", "buy", 1, 300.0, demo_account, atr_pct=0.02))

    print("\nVolatile name (ATR ~10% of price — cap shrinks toward the MIN_VOL_SCALAR floor):")
    print(review("MSTR", "buy", 1, 90.0, demo_account, atr_pct=0.10))

    print("\nPortfolio in a 20% drawdown — new buy blocked even though the trade itself is fine:")
    print(review("MSFT", "buy", 1, 300.0, demo_account, portfolio_drawdown_pct=0.20))

    print("\nSame 20% drawdown, but a sell is never blocked by it:")
    print(review("AAPL", "sell", 4, 200.0, demo_account, portfolio_drawdown_pct=0.20))

    print("\nFive correlated tech names, each individually under the position cap,")
    print("but this buy would push combined tech exposure to 32% (should fail):")
    print(review("NVDA", "buy", 1, 300.0, demo_account, sector="Technology", sector_pct=0.32))

    print("\nSame trade, but tech exposure would only reach 18% (should pass):")
    print(review("NVDA", "buy", 1, 300.0, demo_account, sector="Technology", sector_pct=0.18))

    print("\n11 trades already made today, cap is 10 (should fail):")
    print(review("MSFT", "buy", 1, 300.0, demo_account, trades_today=11))

    print("\nAccount down 7% since this morning, cap is 5% (should fail):")
    print(review("MSFT", "buy", 1, 300.0, demo_account, daily_loss_pct=0.07))

    print("\nSame bad day, but a sell is never blocked by either daily breaker:")
    print(review("AAPL", "sell", 4, 200.0, demo_account, trades_today=11, daily_loss_pct=0.07))
