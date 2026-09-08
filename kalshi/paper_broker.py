"""
KalshiPaperBroker — a fully simulated event-contract account.

Unlike the equities PaperBroker (shares that can be sold at any price), a Kalshi
position is a *binary claim*: you buy N contracts of YES (or NO) at some price in
(0,1); at resolution each contract pays $1.00 if your side won and $0.00 if it
lost. This broker models exactly that, with Kalshi's real entry fee applied on
the way in and settlement applied at resolution.

Design choices that keep the simulation honest rather than flattering:
  - The entry fee (kalshi.fees) is deducted from cash the moment a position is
    opened. There is no free lunch.
  - The default lifecycle is buy-and-hold-to-settlement, which pays the fee ONCE.
    Exiting early (sell_before_settlement) pays it a SECOND time, and the broker
    charges it — so a strategy that churns is correctly punished.
  - Sizing is capped by MAX_STAKE_FRACTION of the *current* bankroll, and the
    daily-loss and drawdown breakers block new opens (never settlement).

It holds no opinions about *which* markets to trade — that's the strategy's job.
"""

from dataclasses import dataclass, field

from . import config, fees


class KalshiTradeError(Exception):
    """Raised when a bet can't be placed (bankroll, sizing cap, or a breaker)."""


@dataclass
class Position:
    ticker: str
    side: str            # "YES" or "NO"
    contracts: int
    entry_price: float   # the market's YES price at entry (for reporting), 0..1
    entry_cost: float    # dollars actually paid PER CONTRACT for this side:
                         #   YES -> entry_price ; NO -> 1 - entry_price
    entry_fee: float     # dollars, already deducted from cash
    reason: str = ""

    @property
    def stake(self) -> float:
        """Dollars at risk (excluding the fee already paid) = what we paid."""
        return self.contracts * self.entry_cost


@dataclass
class SettledTicket:
    ticker: str
    side: str
    contracts: int
    entry_price: float       # market YES price at entry (reporting)
    entry_cost: float        # dollars paid per contract for this side
    entry_fee: float
    exit_fee: float          # nonzero only if sold before settlement
    payout: float            # dollars received at resolution/exit
    realized_pnl: float      # payout - stake - all fees
    won: bool
    reason: str = ""


class KalshiPaperBroker:
    def __init__(self, starting_cash: float | None = None):
        self.cash: float = config.PAPER_STARTING_CASH if starting_cash is None else starting_cash
        self.open_positions: list[Position] = []
        self.settled: list[SettledTicket] = []
        self.peak_bankroll: float = self.cash
        self.day_start_bankroll: float = self.cash
        self._fees_paid: float = 0.0

    # --- bankroll views ----------------------------------------------------
    @property
    def open_stake(self) -> float:
        return sum(p.stake for p in self.open_positions)

    @property
    def bankroll(self) -> float:
        """Cash plus dollars tied up in open positions valued at cost. We do NOT
        mark open positions to the live mid-price — a paper account that
        revalues unsettled binaries to market invites the same self-flattery as
        counting unrealized gains. Bankroll grows only when something settles."""
        return self.cash + self.open_stake

    @property
    def total_fees_paid(self) -> float:
        return self._fees_paid

    # --- day accounting ----------------------------------------------------
    def start_new_day(self) -> None:
        """Reset the daily-loss breaker's reference. Call once per simulated day
        before opening that day's positions."""
        self.day_start_bankroll = self.bankroll

    # --- breakers ----------------------------------------------------------
    def _breaker_blocking_new_bets(self) -> str | None:
        if self.peak_bankroll > 0:
            dd = 1.0 - (self.bankroll / self.peak_bankroll)
            if dd >= config.MAX_DRAWDOWN_FRACTION:
                return (f"drawdown breaker: down {dd*100:.1f}% from peak "
                        f"(cap {config.MAX_DRAWDOWN_FRACTION*100:.0f}%)")
        if self.day_start_bankroll > 0:
            day_loss = 1.0 - (self.bankroll / self.day_start_bankroll)
            if day_loss >= config.MAX_DAILY_LOSS_FRACTION:
                return (f"daily-loss breaker: down {day_loss*100:.1f}% today "
                        f"(cap {config.MAX_DAILY_LOSS_FRACTION*100:.0f}%)")
        return None

    # --- opening a position ------------------------------------------------
    @staticmethod
    def _cost_per_contract(side: str, price: float) -> float:
        """Dollars paid per contract: a YES contract costs the YES price; a NO
        contract costs (1 - YES price). This is THE distinction the binary
        account turns on — get it wrong and NO positions are mispriced."""
        return price if side == "YES" else round(1.0 - price, 2)

    def max_contracts_for(self, cost: float) -> int:
        """How many contracts the stake cap allows given a per-contract COST
        (not the YES price — pass 1-price for a NO position). Fees are on top of
        stake, so a tiny headroom is left implicitly by the later cash check."""
        cost = max(min(cost, 0.99), 0.01)
        stake_budget = config.MAX_STAKE_FRACTION * self.bankroll
        return max(int(stake_budget // cost), 0)

    def buy(self, ticker: str, side: str, contracts: int, price: float,
            reason: str = "") -> Position:
        """Open a position. Applies the stake cap (as a CEILING — an oversized
        request is clamped down, not rejected), the breakers, the entry fee, and
        an insufficient-cash check. Returns the opened Position."""
        config.assert_paper_mode()
        side = side.upper()
        if side not in ("YES", "NO"):
            raise KalshiTradeError(f"side must be YES or NO, got {side!r}")
        if contracts <= 0:
            raise KalshiTradeError("contracts must be positive")
        price = round(price, 2)
        if not (0.01 <= price <= 0.99):
            raise KalshiTradeError(f"price {price} outside tradable (0.01, 0.99)")

        blocked = self._breaker_blocking_new_bets()
        if blocked:
            raise KalshiTradeError(blocked)

        cost = self._cost_per_contract(side, price)
        cap = self.max_contracts_for(cost)
        if cap < 1:
            raise KalshiTradeError(
                f"stake cap ({config.MAX_STAKE_FRACTION*100:.0f}% of bankroll) "
                f"yields zero contracts at cost {cost:.2f}")
        contracts = min(contracts, cap)   # cap is a ceiling, not a rejection

        stake = contracts * cost
        fee = fees.trade_fee(contracts, price)
        if stake + fee > self.cash + 1e-9:
            raise KalshiTradeError(
                f"insufficient cash: need ${stake + fee:.2f}, have ${self.cash:.2f}")

        self.cash -= stake + fee
        self._fees_paid += fee
        pos = Position(ticker, side, contracts, price, cost, fee, reason)
        self.open_positions.append(pos)
        return pos

    # --- resolution --------------------------------------------------------
    def settle(self, ticker: str, yes_won: bool) -> list[SettledTicket]:
        """Resolve every open position on `ticker`. YES contracts pay $1 if
        yes_won, NO contracts pay $1 if not. Returns the tickets settled."""
        settled_now: list[SettledTicket] = []
        remaining: list[Position] = []
        for pos in self.open_positions:
            if pos.ticker != ticker:
                remaining.append(pos)
                continue
            won = (pos.side == "YES" and yes_won) or (pos.side == "NO" and not yes_won)
            payout = pos.contracts * (1.0 if won else 0.0)
            self.cash += payout
            realized = payout - pos.stake - pos.entry_fee
            ticket = SettledTicket(
                ticker=pos.ticker, side=pos.side, contracts=pos.contracts,
                entry_price=pos.entry_price, entry_cost=pos.entry_cost,
                entry_fee=pos.entry_fee, exit_fee=0.0,
                payout=payout, realized_pnl=realized, won=won, reason=pos.reason)
            self.settled.append(ticket)
            settled_now.append(ticket)
            self.peak_bankroll = max(self.peak_bankroll, self.bankroll)
        self.open_positions = remaining
        return settled_now

    def sell_before_settlement(self, ticker: str, price: float) -> list[SettledTicket]:
        """Close open positions on `ticker` at a market price instead of holding
        to resolution. Pays the entry fee a SECOND time (the exit fee) — this is
        the churn penalty, and the reason the default strategy never calls it."""
        config.assert_paper_mode()
        price = round(price, 2)
        settled_now: list[SettledTicket] = []
        remaining: list[Position] = []
        for pos in self.open_positions:
            if pos.ticker != ticker:
                remaining.append(pos)
                continue
            # A YES holder sells at `price`; a NO holder's contract is worth
            # (1 - price) when YES trades at `price`.
            unit = price if pos.side == "YES" else (1.0 - price)
            unit = min(max(unit, 0.0), 1.0)
            exit_fee = fees.trade_fee(pos.contracts, min(max(price, 0.01), 0.99))
            payout = pos.contracts * unit
            self.cash += payout - exit_fee
            self._fees_paid += exit_fee
            realized = payout - pos.stake - pos.entry_fee - exit_fee
            ticket = SettledTicket(
                ticker=pos.ticker, side=pos.side, contracts=pos.contracts,
                entry_price=pos.entry_price, entry_cost=pos.entry_cost,
                entry_fee=pos.entry_fee, exit_fee=exit_fee, payout=payout,
                realized_pnl=realized, won=realized > 0,
                reason=pos.reason + " [early exit]")
            self.settled.append(ticket)
            settled_now.append(ticket)
            self.peak_bankroll = max(self.peak_bankroll, self.bankroll)
        self.open_positions = remaining
        return settled_now
