"""
agents/market_switch.py -- the market risk-off switch ("safety switch").

A market-wide extension of the regime filter (agents/regime.py). Like that
filter, it can only TIGHTEN, never loosen:
- RISK-OFF (SPY's own 12-1 trend is clearly down): agents/judge.py blocks
  every new buy, and agents/exits.py closes held positions via the
  "market_risk_off" exit path.
- RISK-ON: no objection. It never originates or forces a trade.
- UNKNOWN (market data missing, stale, gappy, or too short): new buys are
  blocked, because we can't confirm the market is safe. Held positions are
  NOT force-exited, because a data glitch must never trigger a fire sale.
  This is the same "when in doubt, do less" rule as the rest of automation.

The rule and the evidence: agents/RISK_SWITCH_RESULTS.md. It's a
pre-registered test that passed over 24 years of SPY on the strength of the
slow 2002 and 2008 bear markets, failed the recent 9 years in this primary
form, and can't help in fast crashes. It's crash insurance, not a profit
engine.

ONE SOURCE OF TRUTH: backtest/risk_switch_backtest.py imports
market_view() and is_risk_on() from here, so the live switch and the backtest
that justified it can never drift apart.

Agent-mediated like everything else: evaluate() takes the raw monthly
get_equity_historicals response the calling agent fetched (see
config.MARKET_SWITCH_MONTHS_TO_FETCH) and fetches nothing itself.
"""

from __future__ import annotations

from datetime import datetime

from execution import config, robinhood
from execution.robinhood import RobinhoodDataError
from strategies import time_series_momentum as tsm

# Monthly-bar windows -- exactly what was backtested.
LOOKBACK_MONTHS = 12
SKIP_MONTHS = 1
# Completed months needed for a real read: the 12-1 return needs
# LOOKBACK_MONTHS + 1 closes, and the typical-move estimate needs
# MIN_READINGS_FOR_TYPICAL readings on top of that.
MIN_COMPLETED_MONTHS = LOOKBACK_MONTHS + tsm.MIN_READINGS_FOR_TYPICAL

VARIANTS = {
    "bearish_only": "step aside only when the market's 12-1 trend is clearly DOWN (pre-registered primary)",
    "not_bullish": "step aside whenever the market's 12-1 trend is not clearly UP (stricter sensitivity variant)",
}


def is_risk_on(stance: str, variant: str) -> bool:
    if variant == "bearish_only":
        return stance != "bearish"
    if variant == "not_bullish":
        return stance == "bullish"
    raise ValueError(f"unknown variant {variant!r}; expected one of {sorted(VARIANTS)}")


def market_view(closes: list[float], symbol: str | None = None) -> dict | None:
    """The market's momentum view from completed monthly closes (oldest
    first), or None if there isn't enough history to read the trend AND
    estimate its typical size. Callers must treat None as "unknown", never
    as neutral: under bearish_only a neutral stance means risk-on, so
    mistaking "no data" for "neutral" would fail OPEN."""
    momentum = tsm.trailing_return(closes, LOOKBACK_MONTHS, SKIP_MONTHS)
    typical = tsm.typical_abs_return(closes, LOOKBACK_MONTHS, SKIP_MONTHS)
    if momentum is None or typical is None:
        return None
    return tsm.build_view(symbol or config.MARKET_SWITCH_SYMBOL, momentum, typical)


def evaluate(raw_monthly: dict | None, now: datetime | None = None, variant: str | None = None) -> dict:
    """
    The switch's state for this pass. Never raises on bad data; bad data is
    the "unknown" state.

    raw_monthly: the unmodified get_equity_historicals response for
    symbols=[config.MARKET_SWITCH_SYMBOL], interval="month", reaching back
    at least config.MARKET_SWITCH_MONTHS_TO_FETCH months. None if the
    routine didn't (or couldn't) fetch it.
    now: for deterministic testing; defaults to the current time. It decides
    which month is "current" (in progress, so excluded) and what counts as
    stale.

    Returns {seat, symbol, variant, state: "risk_on" | "risk_off" | "unknown",
    tradeable, stance, confidence, reason}. tradeable is True only for
    risk_on.
    """
    variant = variant or config.MARKET_SWITCH_VARIANT
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; expected one of {sorted(VARIANTS)}")
    symbol = config.MARKET_SWITCH_SYMBOL
    base = {"seat": "market_switch", "symbol": symbol, "variant": variant}

    def _unknown(why: str) -> dict:
        return {**base, "state": "unknown", "tradeable": False, "stance": None, "confidence": 0.0,
                "reason": f"market data unusable ({why}) -- blocking new buys, not forcing exits"}

    if raw_monthly is None:
        return _unknown("no monthly market bars supplied")
    try:
        closes = robinhood.get_completed_monthly_closes(symbol, raw_monthly, now=now,
                                                        min_months=MIN_COMPLETED_MONTHS)
    except RobinhoodDataError as e:
        return _unknown(str(e))
    view = market_view(closes, symbol)
    if view is None:
        return _unknown("not enough history for a trend read")

    risk_on = is_risk_on(view["stance"], variant)
    return {
        **base,
        "state": "risk_on" if risk_on else "risk_off",
        "tradeable": risk_on,
        "stance": view["stance"],
        "confidence": view["confidence"],
        "reason": (f"{symbol} 12-1 trend {view['stance']} ({'; '.join(view['reasons'])}) -- "
                   + ("no objection" if risk_on else "RISK-OFF: blocking new buys, exiting holdings")),
    }


if __name__ == "__main__":
    # Self-test with synthetic raw MCP responses -- deterministic, offline.
    from datetime import timezone

    NOW = datetime(2026, 7, 15, 14, 30, tzinfo=timezone.utc)   # July 2026 is in progress

    def _raw(closes: list[float], last_month: tuple[int, int] = (2026, 7), drop: set | None = None) -> dict:
        """Monthly bars ending at last_month (inclusive), oldest first."""
        y, m = last_month
        months = []
        for _ in closes:
            months.append((y, m))
            y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        months.reverse()
        bars = [{"begins_at": f"{yy:04d}-{mm:02d}-01T00:00:00Z", "close_price": f"{c:.4f}", "volume": 1000}
                for (yy, mm), c in zip(months, closes) if not (drop and (yy, mm) in drop)]
        return {"data": {"results": [{"symbol": "SPY", "interval": "month", "bars": bars}]}}

    rising = [100 * 1.01 ** i for i in range(49)]
    falling = [100 * 1.01 ** i for i in range(35)] + [100 * 1.01 ** 34 * 0.97 ** i for i in range(1, 15)]

    print("Steady rise -> risk_on, tradeable...")
    s = evaluate(_raw(rising), now=NOW)
    print(f"  {s['state']}: {s['reason']}")
    assert s["state"] == "risk_on" and s["tradeable"], s

    print("\nYear-long slide -> risk_off, NOT tradeable...")
    s = evaluate(_raw(falling), now=NOW)
    print(f"  {s['state']}: {s['reason']}")
    assert s["state"] == "risk_off" and not s["tradeable"], s

    print("\nThe in-progress month is ignored: a huge rally THIS month can't flip a risk-off read...")
    s = evaluate(_raw(falling[:-1] + [falling[-2] * 3.0]), now=NOW)
    assert s["state"] == "risk_off", s
    print(f"  still {s['state']} -- PASS")

    print("\nFail-safes -> unknown (blocks buys, forces nothing):")
    for name, raw in (("no data", None),
                      ("stale (ends 3 months ago)", _raw(rising, last_month=(2026, 4))),
                      ("gap in the months", _raw(rising, drop={(2025, 3)})),
                      ("too short", _raw(rising[-20:])),
                      ("wrong interval", {"data": {"results": [{**_raw(rising)["data"]["results"][0],
                                                                "interval": "day"}]}})):
        s = evaluate(raw, now=NOW)
        print(f"  {name:28} -> {s['state']} (tradeable={s['tradeable']}): {s['reason'][:80]}...")
        assert s["state"] == "unknown" and not s["tradeable"], (name, s)

    print("\nVariant: a trendless market is risk-on under bearish_only, risk-off under not_bullish...")
    # Built so the 12-1 window's two endpoints are EQUAL (a 0% trend) while
    # earlier history swings hard (a large typical move) -> clearly neutral.
    # Three 12-month legs (+3%/mo, -3%/mo, +3%/mo), then a 6-up/5-down
    # "mountain" that returns exactly to where the 12-1 window starts.
    hist = []
    for leg, step in enumerate((1.03, 0.97, 1.03)):
        for _ in range(12):
            hist.append((hist[-1] if hist else 100.0) * step)
    start = hist[-1]                                   # completed month -13
    up = [start * 1.03 ** k for k in range(1, 7)]
    down = [up[-1] * (start / up[-1]) ** (k / 5) for k in range(1, 6)]   # ends exactly at `start`
    completed = hist + up + down + [start]             # 48 completed months; month -2 == start
    trendless = completed + [start * 1.5]              # + the in-progress month (ignored)
    v = market_view(robinhood.get_completed_monthly_closes("SPY", _raw(trendless), now=NOW))
    print(f"  stance: {v['stance']} ({v['reasons'][0]})")
    assert v["stance"] == "neutral", v
    assert evaluate(_raw(trendless), now=NOW, variant="bearish_only")["state"] == "risk_on"
    assert evaluate(_raw(trendless), now=NOW, variant="not_bullish")["state"] == "risk_off"
    print("  bearish_only -> risk_on, not_bullish -> risk_off -- PASS")

    print("\nPASS -- market switch: correct states, in-progress month ignored, every bad-data path fails safe.")
