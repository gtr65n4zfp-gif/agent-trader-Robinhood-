"""
Live-quote-into-paper-trade demo.

Proves that live Robinhood market data can flow into the safe PaperBroker
engine with zero real-money risk: fetch a real quote, run the proposed trade
past the risk vetoer (agents/risk_vetoer.py), buy it on paper only if
approved, print the paper account valued at the live price, and log the
outcome either way.

Like execution/robinhood.py, this script is agent-mediated: it can't open
the OAuth-gated Robinhood MCP connection itself, so it expects the raw
get_equity_quotes MCP response to already be fetched (by an MCP-connected
agent) and saved to a JSON file. Run it as:

    python -m execution.demo_live_paper SYMBOL QUOTE_JSON_PATH [QUANTITY] [REASON]
"""

import json
import sys

from agents import risk_vetoer

from . import config, robinhood, trade_log
from .paper_broker import PaperBroker


def run_demo(symbol: str, quantity: float, raw_quote: dict, reason: str) -> dict:
    """
    Propose buying `quantity` shares of `symbol` on paper at its live MCP
    price. The risk vetoer gates the trade first — a veto blocks it before
    PaperBroker ever sees it, and gets logged just like an executed trade.
    """
    price = robinhood.get_quote(symbol, raw_quote)

    broker = PaperBroker()
    account = broker.account({symbol.upper(): price})
    decision = risk_vetoer.review(symbol, "buy", quantity, price, account)

    print(config.mode_banner())
    print(f"Live price for {symbol.upper()}: ${price:,.2f}")
    verdict = "APPROVED" if decision["approved"] else "VETOED"
    print(f"Risk vetoer: {verdict} — {decision['reason']}")

    if not decision["approved"]:
        veto_entry = trade_log.record(
            "veto",
            symbol,
            quantity,
            price,
            paper=True,
            reason=decision["reason"],
            extra={"seat": "risk_vetoer", "checks": decision["checks"], "detail": decision["detail"]},
        )
        print(f"Trade blocked — never sent to PaperBroker. Logged: {veto_entry}")
        print(f"Paper account (unchanged): {account}")
        return account

    trade = broker.buy(symbol, quantity, price, reason=reason)
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
