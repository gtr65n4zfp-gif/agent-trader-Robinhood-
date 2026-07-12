"""
Live-quote-into-paper-trade demo.

Proves that live Robinhood market data can flow into the safe PaperBroker
engine with zero real-money risk: fetch a real quote, buy it on paper,
print the paper account valued at the live price, and log the outcome.

The risk vetoer (agents/risk_vetoer.py) isn't called here directly — it's
enforced inside PaperBroker.buy() itself, so this script can't accidentally
skip it. A veto surfaces as a TradeError.

Like execution/robinhood.py, this script is agent-mediated: it can't open
the OAuth-gated Robinhood MCP connection itself, so it expects the raw
get_equity_quotes MCP response to already be fetched (by an MCP-connected
agent) and saved to a JSON file. Run it as:

    python -m execution.demo_live_paper SYMBOL QUOTE_JSON_PATH [QUANTITY] [REASON]
"""

import json
import sys

from . import config, robinhood
from .paper_broker import PaperBroker, TradeError


def run_demo(symbol: str, quantity: float, raw_quote: dict, reason: str) -> dict:
    """Buy `quantity` shares of `symbol` on paper at its live MCP price."""
    price = robinhood.get_quote(symbol, raw_quote)

    print(config.mode_banner())
    print(f"Live price for {symbol.upper()}: ${price:,.2f}")

    broker = PaperBroker()
    try:
        trade = broker.buy(symbol, quantity, price, reason=reason)
    except TradeError as e:
        print(f"Trade blocked: {e}")
        return broker.account({symbol.upper(): price})

    account = broker.account({symbol.upper(): price})
    print(f"Trade logged: {trade}")
    print(f"Paper account (valued at live price): {account}")
    return account


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            "Usage: python -m execution.demo_live_paper SYMBOL QUOTE_JSON_PATH "
            "[QUANTITY] [REASON]\n\n"
            "QUOTE_JSON_PATH must contain the raw JSON response of the MCP tool "
            "get_equity_quotes for SYMBOL — fetch it via an MCP-connected agent "
            "session first (this script has no Robinhood connection of its own)."
        )
        sys.exit(1)

    cli_symbol = sys.argv[1]
    with open(sys.argv[2]) as f:
        cli_raw_quote = json.load(f)
    cli_quantity = float(sys.argv[3]) if len(sys.argv) > 3 else 1
    cli_reason = sys.argv[4] if len(sys.argv) > 4 else "demo: live quote -> paper buy"

    run_demo(cli_symbol, cli_quantity, cli_raw_quote, cli_reason)
