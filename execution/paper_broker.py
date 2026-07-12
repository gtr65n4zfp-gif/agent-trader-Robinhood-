"""
PaperBroker — a fully simulated trading account.

Tracks cash and positions in a local JSON file. It never touches real money;
it's the safe sandbox where the whole system will prove itself before we wire
in real Robinhood orders. Prices are passed in (later, from live Robinhood
market data), so this engine works today with no external connection.
"""

import json
import os

from . import config, trade_log

_PORTFOLIO_PATH = os.path.join(config.LOG_DIR, "paper_portfolio.json")


class TradeError(Exception):
    """Raised when an order can't be placed (insufficient cash/shares, risk cap)."""


class PaperBroker:
    def __init__(self):
        self.cash: float = config.PAPER_STARTING_CASH
        self.positions: dict[str, float] = {}   # symbol -> shares
        self._load()

    # --- persistence -------------------------------------------------------
    def _load(self) -> None:
        if os.path.exists(_PORTFOLIO_PATH):
            with open(_PORTFOLIO_PATH) as f:
                data = json.load(f)
            self.cash = data["cash"]
            self.positions = data["positions"]

    def _save(self) -> None:
        os.makedirs(config.LOG_DIR, exist_ok=True)
        with open(_PORTFOLIO_PATH, "w") as f:
            json.dump({"cash": self.cash, "positions": self.positions}, f, indent=2)

    # --- orders ------------------------------------------------------------
    def buy(self, symbol: str, quantity: float, price: float, reason: str = "") -> dict:
        symbol = symbol.upper()
        cost = quantity * price
        if quantity <= 0:
            raise TradeError("Quantity must be positive.")
        if cost > config.MAX_TRADE_USD:
            raise TradeError(
                f"Order ${cost:,.2f} exceeds the ${config.MAX_TRADE_USD:,.2f} per-trade cap."
            )
        if cost > self.cash:
            raise TradeError(f"Not enough cash: need ${cost:,.2f}, have ${self.cash:,.2f}.")

        self.cash -= cost
        self.positions[symbol] = self.positions.get(symbol, 0) + quantity
        self._save()
        return trade_log.record("buy", symbol, quantity, price, paper=True, reason=reason,
                                extra={"cost": cost, "cash_after": self.cash})

    def sell(self, symbol: str, quantity: float, price: float, reason: str = "") -> dict:
        symbol = symbol.upper()
        held = self.positions.get(symbol, 0)
        if quantity <= 0:
            raise TradeError("Quantity must be positive.")
        if quantity > held:
            raise TradeError(f"Can't sell {quantity} {symbol}; only hold {held}.")

        proceeds = quantity * price
        self.cash += proceeds
        self.positions[symbol] = held - quantity
        if self.positions[symbol] == 0:
            del self.positions[symbol]
        self._save()
        return trade_log.record("sell", symbol, quantity, price, paper=True, reason=reason,
                                extra={"proceeds": proceeds, "cash_after": self.cash})

    # --- account view ------------------------------------------------------
    def account(self, prices: dict[str, float] | None = None) -> dict:
        """Snapshot of the account. Pass a {symbol: price} map to value
        positions at current prices; otherwise positions are listed unvalued."""
        prices = prices or {}
        positions_value = sum(sh * prices.get(sym, 0) for sym, sh in self.positions.items())
        return {
            "cash": round(self.cash, 2),
            "positions": dict(self.positions),
            "positions_value": round(positions_value, 2),
            "total_value": round(self.cash + positions_value, 2),
            "starting_cash": config.PAPER_STARTING_CASH,
        }


if __name__ == "__main__":
    # Quick self-test of the paper engine (no network needed).
    print(config.mode_banner())
    b = PaperBroker()
    print("Start:", b.account())
    b.buy("AAPL", 5, 200.0, reason="self-test buy")
    print("After buy 5 AAPL @ $200:", b.account({"AAPL": 205.0}))
    b.sell("AAPL", 2, 205.0, reason="self-test partial sell")
    print("After sell 2 AAPL @ $205:", b.account({"AAPL": 205.0}))
