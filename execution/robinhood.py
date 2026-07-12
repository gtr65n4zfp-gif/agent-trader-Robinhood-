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


# --- Volatility (for the risk vetoer's position sizing) ------------------


def get_atr_pct(symbol: str, price: float, raw_atr: dict) -> float:
    """
    Parse a get_equity_technical_indicators (type="atr") response into ATR
    expressed as a fraction of price — what agents.risk_vetoer.review()
    expects for its atr_pct argument.

    raw_atr is the MCP tool response for a call with symbol=symbol, type="atr",
    interval="day" (or similar), output="latest". price is the symbol's
    current price, used only to convert the dollar ATR into a ratio.
    """
    indicators = raw_atr.get("data", {}).get("indicators", [])
    atr_indicator = next((i for i in indicators if i.get("type") == "atr"), None)
    if atr_indicator is None:
        raise RobinhoodDataError(f"{symbol}: no ATR indicator in response.")

    series = atr_indicator.get("series", [])
    if not series:
        raise RobinhoodDataError(f"{symbol}: ATR series is empty.")

    atr_value = series[-1].get("value")
    if atr_value is None:
        raise RobinhoodDataError(f"{symbol}: latest ATR bar has no value.")
    if price <= 0:
        raise RobinhoodDataError(f"{symbol}: price must be positive to compute ATR%.")

    return float(atr_value) / price


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
