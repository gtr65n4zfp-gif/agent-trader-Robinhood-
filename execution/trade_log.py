"""
Trade logging. Every order — paper or live — gets appended here as one JSON
line, along with the reasoning behind it. This is your audit trail and, later,
the raw material for measuring whether the system is actually profitable.
"""

import json
import os
from datetime import datetime, timezone

from . import config

_LOG_PATH = os.path.join(config.LOG_DIR, "trades.jsonl")


def record(
    action: str,          # "buy" or "sell"
    symbol: str,
    quantity: float,
    price: float | None,  # None for market orders where we don't know fill yet
    paper: bool,
    reason: str = "",
    extra: dict | None = None,
    log_path: str | None = None,
) -> dict:
    """Append a trade record and return it.

    log_path: override the default shared log (logs/trades.jsonl). Almost
    nothing should pass this — it exists so an isolated caller (e.g. a
    backtest run, see backtest/engine.py) can keep its own audit trail
    completely separate from the live one, the same isolation reasoning as
    PaperBroker.__init__'s portfolio_path."""
    path = log_path or _LOG_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "paper" if paper else "LIVE",
        "action": action,
        "symbol": symbol,
        "quantity": quantity,
        "price": price,
        "reason": reason,
    }
    if extra:
        entry.update(extra)
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def read_all(log_path: str | None = None) -> list[dict]:
    """Return every logged trade (newest last). log_path: see record()."""
    path = log_path or _LOG_PATH
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def count_trades_today(log_path: str | None = None, now: datetime | None = None) -> int:
    """How many paper buys/sells have actually executed today (UTC calendar
    day) — vetoes and other non-fills don't count. Used by the risk
    vetoer's daily trade-frequency circuit breaker.

    log_path: see record(). now: for testing, and for a backtest replaying
    a simulated past date — see PaperBroker's own now parameter, which
    this is threaded from; defaults to the real wall clock."""
    today = (now or datetime.now(timezone.utc)).date().isoformat()
    return sum(
        1 for e in read_all(log_path)
        if e["mode"] == "paper" and e["action"] in ("buy", "sell") and e["timestamp"].startswith(today)
    )


def round_trip_stats(log_path: str | None = None) -> dict:
    """
    The correct definition of the Milestone 5 go-live counter (see
    README's go-live gate): an OPEN alone proves nothing — only a CLOSE
    that realizes P&L against a prior open is a completed round-trip.
    Entries-only counts are exactly the kind of statistically meaningless
    number the go-live gate exists to rule out.

    Counts every paper sell with a recorded realized_pnl (see
    PaperBroker.sell()) — a sell with realized_pnl=None (no cost basis on
    record, e.g. a pre-existing position from before cost-basis tracking
    existed) is a real fill but not a countable round-trip, and is
    excluded rather than treated as a zero.

    log_path: see record().

    Returns {count, total_realized_pnl, wins, losses}.
    """
    closes = [
        e for e in read_all(log_path)
        if e["mode"] == "paper" and e["action"] == "sell" and e.get("realized_pnl") is not None
    ]
    total = sum(e["realized_pnl"] for e in closes)
    wins = sum(1 for e in closes if e["realized_pnl"] > 0)
    losses = sum(1 for e in closes if e["realized_pnl"] <= 0)
    return {
        "count": len(closes),
        "total_realized_pnl": round(total, 2),
        "wins": wins,
        "losses": losses,
    }
