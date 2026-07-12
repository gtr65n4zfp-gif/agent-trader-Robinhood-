"""
PaperBroker — a fully simulated trading account.

Tracks cash and positions in a local JSON file. It never touches real money;
it's the safe sandbox where the whole system will prove itself before we wire
in real Robinhood orders. Prices are passed in (later, from live Robinhood
market data), so this engine works today with no external connection.
"""

import json
import os

from agents import risk_vetoer

from . import config, trade_log

_PORTFOLIO_PATH = os.path.join(config.LOG_DIR, "paper_portfolio.json")


class TradeError(Exception):
    """Raised when an order can't be placed (insufficient cash/shares, risk cap)."""


class PaperBroker:
    def __init__(self):
        self.cash: float = config.PAPER_STARTING_CASH
        self.positions: dict[str, float] = {}   # symbol -> shares
        self.peak_equity: float = config.PAPER_STARTING_CASH  # for the drawdown breaker
        self._load()

    # --- persistence -------------------------------------------------------
    def _load(self) -> None:
        if os.path.exists(_PORTFOLIO_PATH):
            with open(_PORTFOLIO_PATH) as f:
                data = json.load(f)
            self.cash = data["cash"]
            self.positions = data["positions"]
            self.peak_equity = data.get("peak_equity", config.PAPER_STARTING_CASH)

    def _save(self) -> None:
        os.makedirs(config.LOG_DIR, exist_ok=True)
        with open(_PORTFOLIO_PATH, "w") as f:
            json.dump(
                {"cash": self.cash, "positions": self.positions, "peak_equity": self.peak_equity},
                f, indent=2,
            )

    # --- risk gate -----------------------------------------------------------
    def _check_risk(self, symbol: str, side: str, quantity: float, price: float,
                     prices: dict[str, float] | None, atr_pct: float | None,
                     sector_map: dict[str, str] | None = None) -> None:
        """Run the risk vetoer; raise TradeError (and log the veto) if it blocks
        the trade. Nothing can buy or sell through this broker without clearing
        this — the gate lives here, not in whichever script happens to call in."""
        all_prices = {**(prices or {}), symbol: price}
        account = self.account(all_prices)

        if account["total_value"] > self.peak_equity:
            self.peak_equity = account["total_value"]
            self._save()
        drawdown_pct = (
            (self.peak_equity - account["total_value"]) / self.peak_equity
            if self.peak_equity > 0 else 0.0
        )

        # Sector exposure: this symbol's sector plus every OTHER held
        # position sharing it, valued at whatever prices the caller gave us.
        # A symbol missing from sector_map (or an unpriced holding) is
        # silently excluded from the sum rather than failing the check —
        # this is a best-effort aggregate, not a guaranteed-complete one.
        sector = None
        sector_pct = None
        if side == "buy" and sector_map:
            sector = sector_map.get(symbol)
            if sector:
                sector_value = sum(
                    shares * all_prices.get(sym, 0)
                    for sym, shares in self.positions.items()
                    if sector_map.get(sym) == sector
                )
                total_value = account["total_value"]
                projected_sector_value = sector_value + (quantity * price)
                sector_pct = projected_sector_value / total_value if total_value > 0 else float("inf")

        decision = risk_vetoer.review(
            symbol, side, quantity, price, account,
            atr_pct=atr_pct, portfolio_drawdown_pct=drawdown_pct,
            sector=sector, sector_pct=sector_pct,
        )
        if not decision["approved"]:
            trade_log.record(
                "veto", symbol, quantity, price, paper=True, reason=decision["reason"],
                extra={"seat": "risk_vetoer", "checks": decision["checks"], "detail": decision["detail"]},
            )
            raise TradeError(f"Risk vetoer blocked this trade: {decision['reason']}")

    # --- orders ------------------------------------------------------------
    def buy(self, symbol: str, quantity: float, price: float, reason: str = "",
            prices: dict[str, float] | None = None, atr_pct: float | None = None,
            sector_map: dict[str, str] | None = None) -> dict:
        """prices: optional {symbol: price} for every other held position, so
        the risk vetoer can value the whole account accurately. Omit and only
        this symbol's position is valued precisely; others fall back to 0.
        atr_pct: optional volatility reading (see agents.risk_vetoer) that
        scales down the position cap for volatile names — from
        execution.robinhood.get_atr_pct(). Omit to use the flat cap.
        sector_map: optional {symbol: sector} covering this symbol and every
        other held position, from execution.robinhood.get_sectors() — lets
        the vetoer catch sector-level concentration across symbols. Omit to
        skip the sector-concentration check entirely."""
        symbol = symbol.upper()
        if quantity <= 0:
            raise TradeError("Quantity must be positive.")
        self._check_risk(symbol, "buy", quantity, price, prices, atr_pct, sector_map)

        cost = quantity * price
        if cost > self.cash:
            raise TradeError(f"Not enough cash: need ${cost:,.2f}, have ${self.cash:,.2f}.")

        self.cash -= cost
        self.positions[symbol] = self.positions.get(symbol, 0) + quantity
        self._save()
        return trade_log.record("buy", symbol, quantity, price, paper=True, reason=reason,
                                extra={"cost": cost, "cash_after": self.cash})

    def sell(self, symbol: str, quantity: float, price: float, reason: str = "",
             prices: dict[str, float] | None = None) -> dict:
        """See buy() for `prices`. Note: the vetoer's position-concentration
        check and drawdown breaker never block a sell (selling only reduces
        exposure), but its per-trade dollar cap still applies — a single sell
        larger than MAX_TRADE_USD is blocked same as a buy. That's fine for
        manually reasoned trades (split it into two calls); it'll need
        revisiting once automated stop-loss/exit logic exists, so a real exit
        can't get stuck unable to close a position that grew past the cap."""
        symbol = symbol.upper()
        held = self.positions.get(symbol, 0)
        if quantity <= 0:
            raise TradeError("Quantity must be positive.")
        if quantity > held:
            raise TradeError(f"Can't sell {quantity} {symbol}; only hold {held}.")
        self._check_risk(symbol, "sell", quantity, price, prices, atr_pct=None)

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
    # Quick self-test of the paper engine (no network needed). Trades can be
    # blocked by the risk vetoer depending on whatever's already on disk from
    # prior runs — that's expected, not a bug, so TradeError is caught and
    # printed like any other outcome rather than crashing the demo.
    print(config.mode_banner())
    b = PaperBroker()
    print("Start:", b.account())
    try:
        b.buy("AAPL", 5, 200.0, reason="self-test buy")
        print("After buy 5 AAPL @ $200:", b.account({"AAPL": 205.0}))
    except TradeError as e:
        print("Buy blocked:", e)
    try:
        b.sell("AAPL", 2, 205.0, reason="self-test partial sell")
        print("After sell 2 AAPL @ $205:", b.account({"AAPL": 205.0}))
    except TradeError as e:
        print("Sell blocked:", e)
