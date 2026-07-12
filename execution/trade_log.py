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
) -> dict:
    """Append a trade record and return it."""
    os.makedirs(config.LOG_DIR, exist_ok=True)
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
    with open(_LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def read_all() -> list[dict]:
    """Return every logged trade (newest last)."""
    if not os.path.exists(_LOG_PATH):
        return []
    with open(_LOG_PATH) as f:
        return [json.loads(line) for line in f if line.strip()]


def count_trades_today() -> int:
    """How many paper buys/sells have actually executed today (UTC calendar
    day) — vetoes and other non-fills don't count. Used by the risk
    vetoer's daily trade-frequency circuit breaker."""
    today = datetime.now(timezone.utc).date().isoformat()
    return sum(
        1 for e in read_all()
        if e["mode"] == "paper" and e["action"] in ("buy", "sell") and e["timestamp"].startswith(today)
    )


def round_trip_stats() -> dict:
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

    Returns {count, total_realized_pnl, wins, losses}.
    """
    closes = [
        e for e in read_all()
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
