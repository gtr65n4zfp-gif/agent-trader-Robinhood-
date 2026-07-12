"""
Robinhood MCP wrapper — READ-ONLY.

The official Robinhood MCP server (agent.robinhood.com/mcp/trading) is
OAuth-gated and only reachable through an authenticated MCP session (this
project's is set up via `/mcp` -> robinhood-trading -> Authenticate). There's
no Robinhood credential or token anywhere in this codebase — these functions
don't open a network connection themselves. Instead they take the
*already-fetched* raw JSON that an MCP-connected agent got back from calling
get_equity_quotes / get_accounts / get_portfolio / get_equity_positions, and
normalize it into plain, typed Python values. The calling agent makes the
actual MCP tool calls and passes the responses straight through.

Every function here is read-only by construction: nothing in this module
places, reviews, or cancels an order, and nothing here writes to Robinhood.
Simulated trades still go through execution.paper_broker.PaperBroker — this
module only ever supplies *prices* and a *real account snapshot*, never
executes anything.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import config


class RobinhoodDataError(Exception):
    """Raised when an MCP response is missing or malformed data we need."""


# --- Quotes ------------------------------------------------------------


def _extract_price(quote_result: dict) -> float:
    """Pick the current price out of one get_equity_quotes result entry."""
    quote = quote_result.get("quote", {})
    symbol = quote.get("symbol", "?")
    if not quote.get("has_traded", False):
        raise RobinhoodDataError(f"{symbol}: has not traded, no reliable price.")
    if quote.get("state") != "active":
        raise RobinhoodDataError(f"{symbol}: quote state is {quote.get('state')!r}, not active.")
    price = quote.get("last_trade_price") or quote.get("last_non_reg_trade_price")
    if price is None:
        raise RobinhoodDataError(f"{symbol}: no trade price in quote data.")
    return float(price)


def get_quotes(symbols: list[str], raw_quotes: dict) -> dict[str, float]:
    """
    Parse a get_equity_quotes MCP tool response into {symbol: price}.

    raw_quotes is the unmodified JSON returned by calling the MCP tool
    get_equity_quotes with `symbols`. This function does not call the tool
    itself — the caller (the agent) must fetch it and pass it in here.
    """
    results = raw_quotes.get("data", {}).get("results", [])
    prices: dict[str, float] = {}
    for result in results:
        sym = result.get("quote", {}).get("symbol")
        if sym is None:
            continue
        prices[sym] = _extract_price(result)

    missing = [s.upper() for s in symbols if s.upper() not in prices]
    if missing:
        raise RobinhoodDataError(f"No quote returned for: {', '.join(missing)}")
    return prices


def get_quote(symbol: str, raw_quote: dict) -> float:
    """
    Parse a single symbol's live price out of a get_equity_quotes response.

    raw_quote is the MCP tool response for a call with symbols=[symbol].
    """
    return get_quotes([symbol], raw_quote)[symbol.upper()]


def get_quote_age_minutes(symbol: str, raw_quote: dict, now: datetime | None = None) -> float:
    """
    How many minutes old a get_quote() price actually is, per its own
    venue timestamp — a quote can parse successfully and still be stale
    (the feed hasn't ticked, or it's a market holiday and the "latest"
    trade is from days earlier). Picks whichever of venue_last_trade_time
    / venue_last_non_reg_trade_time is more recent, the same preference
    _extract_price() uses for the price itself. Used by
    execution.config.MAX_QUOTE_AGE_MINUTES — automation's per-symbol
    fail-safe staleness check (see automation/run_pass.py).

    raw_quote is the same MCP response get_quote() takes. now: for
    testing; defaults to the current UTC time.
    """
    results = raw_quote.get("data", {}).get("results", [])
    quote = next((r.get("quote", {}) for r in results if r.get("quote", {}).get("symbol") == symbol.upper()), None)
    if quote is None:
        raise RobinhoodDataError(f"{symbol}: no quote in response to check freshness against.")

    timestamps = [t for t in (quote.get("venue_last_trade_time"), quote.get("venue_last_non_reg_trade_time")) if t]
    if not timestamps:
        raise RobinhoodDataError(f"{symbol}: quote has no venue timestamp to check freshness against.")

    latest = max(datetime.fromisoformat(t) for t in timestamps)
    now = now or datetime.now(timezone.utc)
    return (now - latest).total_seconds() / 60.0


# --- Technical indicators (ATR for the risk vetoer, RSI/EMA for the -----
# --- Technicals seat) ------------------------------------------------------


def _extract_indicator_value(
    symbol: str, indicator_type: str, raw_indicator: dict, expected_period: int | None = None
) -> float:
    """Pull the latest value for one indicator type out of a
    get_equity_technical_indicators response. Shared by every indicator
    parser below — they all read the same shape, just different `type`.

    expected_period: if given, asserts the response's own reported
    params.period matches — this is what makes get_regime_ema() and
    get_ema() structurally unable to be swapped for each other. Passing
    the wrong response raises here instead of silently computing a trend
    off the wrong lookback (the bug this exists to prevent)."""
    indicators = raw_indicator.get("data", {}).get("indicators", [])
    match = next((i for i in indicators if i.get("type") == indicator_type), None)
    if match is None:
        raise RobinhoodDataError(f"{symbol}: no {indicator_type} indicator in response.")

    if expected_period is not None:
        actual_period = match.get("params", {}).get("period")
        if actual_period != expected_period:
            raise RobinhoodDataError(
                f"{symbol}: expected {indicator_type} period={expected_period}, "
                f"got period={actual_period!r} — wrong indicator response passed in."
            )

    series = match.get("series", [])
    if not series:
        raise RobinhoodDataError(f"{symbol}: {indicator_type} series is empty.")

    value = series[-1].get("value")
    if value is None:
        raise RobinhoodDataError(f"{symbol}: latest {indicator_type} bar has no value.")
    return float(value)


def get_atr_pct(symbol: str, price: float, raw_atr: dict) -> float:
    """
    Parse a get_equity_technical_indicators (type="atr") response into ATR
    expressed as a fraction of price — what agents.risk_vetoer.review()
    expects for its atr_pct argument.

    raw_atr is the MCP tool response for a call with symbol=symbol, type="atr",
    interval="day" (or similar), output="latest". price is the symbol's
    current price, used only to convert the dollar ATR into a ratio.
    """
    if price <= 0:
        raise RobinhoodDataError(f"{symbol}: price must be positive to compute ATR%.")
    return _extract_indicator_value(symbol, "atr", raw_atr) / price


def get_rsi(symbol: str, raw_rsi: dict) -> float:
    """
    Parse a get_equity_technical_indicators (type="rsi") response into the
    latest RSI reading (0-100) — used by agents.technicals for momentum.

    raw_rsi is the MCP tool response for a call with symbol=symbol,
    type="rsi", interval="day" (or similar), output="latest".
    """
    return _extract_indicator_value(symbol, "rsi", raw_rsi)


def get_ema(symbol: str, raw_ema: dict) -> float:
    """
    Parse a get_equity_technical_indicators (type="ema") response into the
    SHORT-period EMA value (price-scale) — used by agents.technicals to
    gauge near-term trend direction (price vs. its EMA). Distinct from
    get_regime_ema() below: Technicals isn't tied to one canonical period,
    so no period is enforced here — whatever period the caller requested
    is what's returned.

    raw_ema is the MCP tool response for a call with symbol=symbol,
    type="ema", interval="day" (or similar), output="latest".
    """
    return _extract_indicator_value(symbol, "ema", raw_ema)


def get_regime_ema(symbol: str, raw_regime_ema: dict) -> float:
    """
    Parse a get_equity_technical_indicators (type="ema") response into the
    REGIME-period EMA value — used by agents.regime to classify trend on a
    slower, less noisy lookback than agents.technicals's short EMA (see
    execution.config.REGIME_EMA_LOOKBACK_DAYS). This is a genuinely
    distinct reading, not the same number reused: the response's own
    reported period is validated against REGIME_EMA_LOOKBACK_DAYS, so
    passing get_ema()'s short-period response here (or vice versa) raises
    instead of silently deriving regime trend from the wrong lookback —
    the two signals can now actually disagree, restoring the isolation
    between Technicals and the regime filter.

    raw_regime_ema is the MCP tool response for a call with symbol=symbol,
    type="ema", period=config.REGIME_EMA_LOOKBACK_DAYS, interval="day".
    A day-interval EMA needs several period-lengths of warm-up bars to be
    accurate — request start_time at least ~3x REGIME_EMA_LOOKBACK_DAYS
    of history back when building that call, not just enough for one bar.
    """
    return _extract_indicator_value(
        symbol, "ema", raw_regime_ema, expected_period=config.REGIME_EMA_LOOKBACK_DAYS
    )


# --- Sector (for the risk vetoer's concentration check) -------------------


def get_sectors(symbols: list[str], raw_fundamentals: dict) -> dict[str, str]:
    """
    Parse a get_equity_fundamentals MCP tool response into {symbol: sector}.

    raw_fundamentals is the unmodified JSON returned by calling the MCP tool
    get_equity_fundamentals with `symbols`. Sector strings come straight from
    Robinhood's own classification (e.g. "Electronic Technology") — they're
    not normalized against any other taxonomy.
    """
    results = raw_fundamentals.get("data", {}).get("results", [])
    sectors: dict[str, str] = {}
    for result in results:
        sym = result.get("symbol")
        sector = result.get("sector")
        if sym and sector:
            sectors[sym] = sector

    missing = [s.upper() for s in symbols if s.upper() not in sectors]
    if missing:
        raise RobinhoodDataError(f"No sector returned for: {', '.join(missing)}")
    return sectors


def get_sector(symbol: str, raw_fundamentals: dict) -> str:
    """Parse a single symbol's sector out of a get_equity_fundamentals response."""
    return get_sectors([symbol], raw_fundamentals)[symbol.upper()]


# --- Real account snapshot ----------------------------------------------


def get_account(
    raw_accounts: dict,
    raw_portfolio: dict,
    raw_positions: dict | None = None,
    account_number: str | None = None,
) -> dict:
    """
    Build a REAL (not paper) account snapshot from raw MCP responses.

    This is the live Robinhood account — clearly separate from
    PaperBroker.account(), which is the simulated one. Nothing here places
    or modifies anything; it only reshapes data already fetched via
    get_accounts / get_portfolio / get_equity_positions.

    raw_accounts:   response of get_accounts, used to resolve account metadata.
    raw_portfolio:  response of get_portfolio for that same account_number.
    raw_positions:  optional response of get_equity_positions for that account.
    account_number: which account to describe; defaults to the is_default one.
    """
    accounts = raw_accounts.get("data", {}).get("accounts", [])
    if not accounts:
        raise RobinhoodDataError("No accounts in get_accounts response.")
    if account_number is not None:
        account = next((a for a in accounts if a.get("account_number") == account_number), None)
        if account is None:
            raise RobinhoodDataError(f"Account {account_number!r} not found in get_accounts response.")
    else:
        account = next((a for a in accounts if a.get("is_default")), accounts[0])

    portfolio = raw_portfolio.get("data", {})
    buying_power = portfolio.get("buying_power", {})

    positions: dict[str, dict] = {}
    if raw_positions is not None:
        for p in raw_positions.get("data", {}).get("positions", []):
            positions[p["symbol"]] = {
                "quantity": float(p["quantity"]),
                "average_buy_price": float(p["average_buy_price"]) if p.get("average_buy_price") else None,
            }

    return {
        "mode": "REAL",  # never confuse this with a PaperBroker snapshot
        "account_number": account.get("account_number"),
        "account_type": account.get("brokerage_account_type"),
        "nickname": account.get("nickname"),
        "cash": float(portfolio.get("cash", 0)),
        "equity_value": float(portfolio.get("equity_value", 0)),
        "total_value": float(portfolio.get("total_value", 0)),
        "buying_power": float(buying_power.get("buying_power", 0)),
        "positions": positions,
    }
