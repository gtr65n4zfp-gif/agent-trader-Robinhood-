"""
execution/options_paper_broker.py -- a fully simulated options trading
account for the live SPY options pass (see
docs/superpowers/specs/2026-07-17-live-spy-options-design.md).

Mirrors execution.paper_broker.PaperBroker's structure, but units are
CONTRACTS, not shares, and state tracks two independent, optional
position slots keyed by horizon track ("7", "30") rather than a dict of
symbols -- this account only ever trades SPY, on the one-position-per-
track concurrency policy the design locks in. Never touches real money;
paper only, same as PaperBroker.
"""

import json
import os
from datetime import datetime, timezone

from agents import options_risk_vetoer

from . import config, trade_log

_OPTIONS_PORTFOLIO_PATH = os.path.join(config.LOG_DIR, "options_paper_portfolio.json")

# Public (no leading underscore) and imported by automation/run_options_pass.py's
# execute_options_pass(), via OptionsPaperBroker.log_path below -- NOT via this
# constant directly, so there is exactly one source of truth for "which log
# path is this broker actually using" and no chance of the two drifting apart.
# Deliberately a DIFFERENT file from trade_log's own equity default
# (logs/trades.jsonl) -- unlike PaperBroker, this account must never fall
# through to the equity log just because no override was given.
OPTIONS_TRADE_LOG_PATH = os.path.join(config.LOG_DIR, "options_trades.jsonl")

_TRACKS = ("7", "30")


class OptionsTradeError(Exception):
    """Raised when an order can't be placed (insufficient cash, risk cap,
    or the track is already occupied)."""


def parse_option_quote(raw_quote: dict, instrument_id: str) -> dict | None:
    """
    Parse a get_option_quotes response for one instrument into
    {mark_price, bid_price, ask_price} (bid_price/ask_price are None if
    the live quote didn't include them). Returns None if instrument_id
    isn't in the response's results -- caller skips, never fabricates.

    Field names (mark_price/bid_price/ask_price) follow the snake-case
    plus _price convention already confirmed for strike_price/open_price/
    etc. elsewhere in this project's Robinhood parsing -- verify against
    a real response at first live run and adjust here if the actual
    schema differs, same discipline as every other agent-mediated parser
    in this project.
    """
    results = raw_quote.get("data", {}).get("results", [])
    match = next((r for r in results if r.get("instrument_id") == instrument_id), None)
    if match is None:
        return None
    return {
        "mark_price": float(match["mark_price"]) if match.get("mark_price") is not None else None,
        "bid_price": float(match["bid_price"]) if match.get("bid_price") is not None else None,
        "ask_price": float(match["ask_price"]) if match.get("ask_price") is not None else None,
    }


def entry_fill_from_quote(quote: dict) -> tuple[float, bool]:
    """
    Returns (fill_price, used_real_spread). Prefers the real ask (the
    natural spread against the trader, more honest than any estimate);
    falls back to mark_price widened by half the backtest's documented
    round-trip haircut if ask isn't present in the live quote.
    """
    if quote.get("ask_price") is not None:
        return quote["ask_price"], True
    return quote["mark_price"] * (1 + config.OPTIONS_ROUNDTRIP_HAIRCUT_PCT / 2), False


def exit_fill_from_quote(quote: dict) -> tuple[float, bool]:
    """Same as entry_fill_from_quote(), mirrored for the sell side: real
    bid preferred, mark_price narrowed by half the haircut otherwise."""
    if quote.get("bid_price") is not None:
        return quote["bid_price"], True
    return quote["mark_price"] * (1 - config.OPTIONS_ROUNDTRIP_HAIRCUT_PCT / 2), False


class OptionsPaperBroker:
    def __init__(self, portfolio_path: str | None = None, log_path: str | None = None):
        """See execution.paper_broker.PaperBroker's __init__ docstring
        for why portfolio_path/log_path exist -- same isolation
        reasoning; almost nothing should pass these."""
        self._portfolio_path = portfolio_path or _OPTIONS_PORTFOLIO_PATH
        # `or OPTIONS_TRADE_LOG_PATH`, NOT a bare pass-through like
        # PaperBroker's own `self._log_path = log_path` -- PaperBroker
        # sharing trade_log's built-in default (logs/trades.jsonl) is
        # correct FOR EQUITY, but this account must never fall through to
        # that same equity default when no override is given.
        self._log_path = log_path or OPTIONS_TRADE_LOG_PATH
        self.cash: float = config.OPTIONS_PAPER_STARTING_CASH
        self.open_positions: dict[str, dict | None] = {t: None for t in _TRACKS}
        self.peak_equity: float = config.OPTIONS_PAPER_STARTING_CASH
        self.day_date: str | None = None
        self.day_start_equity: float = config.OPTIONS_PAPER_STARTING_CASH
        self._load()

    @property
    def log_path(self) -> str:
        """The resolved trade-log path this broker actually writes to
        (never None -- __init__ already applied the OPTIONS_TRADE_LOG_PATH
        fallback). automation.run_options_pass.execute_options_pass() reads
        this so its own direct trade_log.record() calls (no-ops, skips,
        dry-run logging) land in the exact same file as this broker's own
        buy/sell/veto records, by construction, not by the caller
        remembering to keep two paths in sync."""
        return self._log_path

    def _load(self) -> None:
        if os.path.exists(self._portfolio_path):
            with open(self._portfolio_path) as f:
                data = json.load(f)
            self.cash = data["cash"]
            self.open_positions = data.get("open_positions", {t: None for t in _TRACKS})
            self.peak_equity = data.get("peak_equity", config.OPTIONS_PAPER_STARTING_CASH)
            self.day_date = data.get("day_date")
            self.day_start_equity = data.get("day_start_equity", config.OPTIONS_PAPER_STARTING_CASH)

    def _save(self) -> None:
        os.makedirs(config.LOG_DIR, exist_ok=True)
        with open(self._portfolio_path, "w") as f:
            json.dump(
                {
                    "cash": self.cash,
                    "open_positions": self.open_positions,
                    "peak_equity": self.peak_equity,
                    "day_date": self.day_date,
                    "day_start_equity": self.day_start_equity,
                },
                f, indent=2,
            )

    def account(self, current_marks: dict[str, float] | None = None) -> dict:
        """current_marks: optional {track: current mark_price} for
        occupied tracks -- falls back to each position's own entry_fill
        (unrealized P&L reads as flat) for any occupied track not in
        current_marks."""
        current_marks = current_marks or {}
        positions_value = 0.0
        for track, pos in self.open_positions.items():
            if pos is None:
                continue
            mark = current_marks.get(track, pos["entry_fill"])
            positions_value += pos["quantity"] * config.OPTIONS_CONTRACT_MULTIPLIER * mark
        return {
            "cash": round(self.cash, 2),
            "open_positions": {t: (dict(p) if p else None) for t, p in self.open_positions.items()},
            "positions_value": round(positions_value, 2),
            "total_value": round(self.cash + positions_value, 2),
            "starting_cash": config.OPTIONS_PAPER_STARTING_CASH,
        }

    def _roll_day_and_check_risk(self, action: str, contract_cost: float,
                                  current_marks: dict[str, float] | None, now: datetime) -> None:
        account = self.account(current_marks)
        if account["total_value"] > self.peak_equity:
            self.peak_equity = account["total_value"]
            self._save()

        today = now.date().isoformat()
        if self.day_date != today:
            self.day_date = today
            self.day_start_equity = account["total_value"]
            self._save()
        daily_loss_pct = (
            (self.day_start_equity - account["total_value"]) / self.day_start_equity
            if self.day_start_equity > 0 else 0.0
        )
        trades_today = trade_log.count_trades_today(log_path=self._log_path, now=now)

        decision = options_risk_vetoer.review(
            action, contract_cost, account,
            trades_today=trades_today, daily_loss_pct=daily_loss_pct,
        )
        if not decision["approved"]:
            trade_log.record(
                "veto", "SPY", 0, None, paper=True, reason=decision["reason"],
                extra={"seat": "options_risk_vetoer", "checks": decision["checks"], "detail": decision["detail"]},
                log_path=self._log_path,
            )
            raise OptionsTradeError(f"Options risk vetoer blocked this trade: {decision['reason']}")

    def buy_to_open(self, track: str, contract_id: str, strike: float, option_type: str,
                     expiration_date: str, quantity: int, entry_fill: float, reason: str = "",
                     current_marks: dict[str, float] | None = None, now: datetime | None = None) -> dict:
        if track not in _TRACKS:
            raise ValueError(f"unknown track {track!r}, expected one of {_TRACKS}")
        if self.open_positions[track] is not None:
            raise OptionsTradeError(f"track {track} already has an open position")

        now = now or datetime.now(timezone.utc)
        contract_cost = quantity * config.OPTIONS_CONTRACT_MULTIPLIER * entry_fill
        self._roll_day_and_check_risk("open", contract_cost, current_marks, now)

        if contract_cost > self.cash:
            raise OptionsTradeError(f"insufficient cash: need {contract_cost:.2f}, have {self.cash:.2f}")

        self.cash -= contract_cost
        position = {
            "contract_id": contract_id, "strike": strike, "type": option_type,
            "expiration_date": expiration_date, "quantity": quantity,
            "entry_fill": entry_fill, "entry_date": now.date().isoformat(),
        }
        self.open_positions[track] = position
        self._save()
        trade_log.record(
            "buy", "SPY", quantity, entry_fill, paper=True, reason=reason,
            extra={"track": track, "option_type": option_type, "strike": strike,
                   "expiration_date": expiration_date, "contract_id": contract_id},
            log_path=self._log_path,
        )
        return dict(position)

    def close_position(self, track: str, exit_fill: float, reason: str = "",
                        now: datetime | None = None) -> dict:
        if track not in _TRACKS:
            raise ValueError(f"unknown track {track!r}, expected one of {_TRACKS}")
        position = self.open_positions[track]
        if position is None:
            raise OptionsTradeError(f"track {track} has no open position to close")

        now = now or datetime.now(timezone.utc)
        proceeds = position["quantity"] * config.OPTIONS_CONTRACT_MULTIPLIER * exit_fill
        cost_basis = position["quantity"] * config.OPTIONS_CONTRACT_MULTIPLIER * position["entry_fill"]
        realized_pnl = round(proceeds - cost_basis, 2)

        self.cash += proceeds
        self.open_positions[track] = None
        self._save()
        trade_log.record(
            "sell", "SPY", position["quantity"], exit_fill, paper=True, reason=reason,
            extra={"track": track, "realized_pnl": realized_pnl, "option_type": position["type"],
                   "strike": position["strike"], "expiration_date": position["expiration_date"],
                   "contract_id": position["contract_id"]},
            log_path=self._log_path,
        )
        return {**position, "exit_fill": exit_fill, "realized_pnl": realized_pnl}


if __name__ == "__main__":
    import tempfile

    print("Testing the default log path is options-specific, never the equity default...")
    assert OPTIONS_TRADE_LOG_PATH.endswith("options_trades.jsonl"), OPTIONS_TRADE_LOG_PATH
    # portfolio_path points at a nonexistent file so __init__'s _load() is a
    # no-op (no read, no write) -- this only exercises log_path resolution.
    no_override = OptionsPaperBroker(portfolio_path=os.path.join(tempfile.gettempdir(), "unused.json"))
    assert no_override.log_path == OPTIONS_TRADE_LOG_PATH, no_override.log_path
    print(f"PASS — a broker built with no log_path override resolves to the options-specific file: {no_override.log_path}")

    print("\nTesting parse_option_quote -- found and not-found cases...")
    raw = {"data": {"results": [{"instrument_id": "abc", "mark_price": "6.50",
                                  "bid_price": "6.40", "ask_price": "6.60"}]}}
    q = parse_option_quote(raw, "abc")
    assert q == {"mark_price": 6.5, "bid_price": 6.4, "ask_price": 6.6}, q
    assert parse_option_quote(raw, "not-there") is None
    print(f"PASS — parsed a matching instrument, None for a non-matching one: {q}")

    print("\nTesting entry_fill_from_quote / exit_fill_from_quote -- real spread preferred...")
    fill, real = entry_fill_from_quote(q)
    assert fill == 6.6 and real is True, (fill, real)
    fill2, real2 = exit_fill_from_quote(q)
    assert fill2 == 6.4 and real2 is True, (fill2, real2)
    print(f"PASS — entry fills at ask (6.6), exit fills at bid (6.4), both flagged real: {fill}, {fill2}")

    print("\nTesting entry_fill_from_quote -- haircut fallback when bid/ask absent...")
    mark_only = {"mark_price": 6.0, "bid_price": None, "ask_price": None}
    fill3, real3 = entry_fill_from_quote(mark_only)
    assert real3 is False, (fill3, real3)
    assert abs(fill3 - 6.09) < 1e-9, fill3  # 6.0 * (1 + 0.03/2) = 6.09
    print(f"PASS — falls back to mark + half the haircut, flagged not-real: {fill3}")

    with tempfile.TemporaryDirectory() as tmp:
        portfolio_path = os.path.join(tmp, "options_portfolio.json")
        log_path = os.path.join(tmp, "options_trades.jsonl")

        print("\nTesting OptionsPaperBroker.buy_to_open — opens a position, deducts cash...")
        broker = OptionsPaperBroker(portfolio_path=portfolio_path, log_path=log_path)
        start_cash = broker.cash
        pos = broker.buy_to_open("7", "contract-1", 685.0, "call", "2026-08-01", 1, 6.0, reason="test entry")
        assert broker.open_positions["7"] == pos, pos
        assert abs(broker.cash - (start_cash - 600.0)) < 1e-6, broker.cash  # 1 * 100 * 6.0
        print(f"PASS — opened track 7, cash reduced by contract cost: cash={broker.cash}")

        print("\nTesting buy_to_open — rejects a second position in the same track...")
        try:
            broker.buy_to_open("7", "contract-2", 690.0, "call", "2026-08-01", 1, 5.0)
            raise AssertionError("should have raised OptionsTradeError")
        except OptionsTradeError as e:
            print(f"PASS — raised clearly: {e}")

        print("\nTesting buy_to_open — a different track (30) can still open...")
        pos30 = broker.buy_to_open("30", "contract-3", 700.0, "put", "2026-09-01", 1, 10.0, reason="test entry 2")
        assert broker.open_positions["30"] == pos30, pos30
        print(f"PASS — track 30 opened independently of track 7: {pos30}")

        print("\nTesting close_position — credits cash, computes realized_pnl, clears the slot...")
        cash_before_close = broker.cash
        closed = broker.close_position("7", 9.0, reason="test exit")
        assert broker.open_positions["7"] is None, broker.open_positions
        assert closed["realized_pnl"] == 300.0, closed  # (9.0 - 6.0) * 100
        assert abs(broker.cash - (cash_before_close + 900.0)) < 1e-6, broker.cash
        print(f"PASS — track 7 closed, +$300 realized, cash credited: {closed}")

        print("\nTesting close_position — raises when the track has nothing open...")
        try:
            broker.close_position("7", 9.0)
            raise AssertionError("should have raised OptionsTradeError")
        except OptionsTradeError as e:
            print(f"PASS — raised clearly: {e}")

        print("\nTesting buy_to_open — vetoed when it exceeds OPTIONS_MAX_TRADE_USD...")
        try:
            # entry_fill * 100 = 3000, over the $2500 cap
            broker.buy_to_open("7", "contract-4", 685.0, "call", "2026-08-01", 1, 30.0)
            raise AssertionError("should have raised OptionsTradeError")
        except OptionsTradeError as e:
            assert "risk vetoer" in str(e).lower(), e
            print(f"PASS — risk vetoer blocked an oversized trade: {e}")

        print("\nTesting persistence — a fresh broker instance loads the same state from disk...")
        reloaded = OptionsPaperBroker(portfolio_path=portfolio_path, log_path=log_path)
        assert reloaded.open_positions["30"] == pos30, reloaded.open_positions
        assert reloaded.open_positions["7"] is None, reloaded.open_positions
        print(f"PASS — reloaded broker matches: cash={reloaded.cash}, open_positions={reloaded.open_positions}")
