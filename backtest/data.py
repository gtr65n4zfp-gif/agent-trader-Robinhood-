"""
backtest/data.py — the historical, point-in-time data layer (see
agents/BACKTEST_DESIGN.md, "Data layer (task 2)").

Like execution/robinhood.py, the bar-parsing half of this module is
agent-mediated: parse_bars() takes an already-fetched raw
get_equity_historicals response (the calling agent makes that MCP call
and passes the JSON straight through — this module never calls Robinhood
itself). The fundamentals half calls agents.fundamentals_seat.build_brief()
directly, since that function already makes its own live HTTP calls to
SEC's public API (no MCP, no agent-mediation needed there — same as the
live system).

THE CARDINAL RULE: everything here that answers "what did the council see
on date D" must be computed from data truncated at D — see
agents/BACKTEST_DESIGN.md's "no lookahead" section and
backtest/prove_no_lookahead.py for the correctness proof.
"""

from __future__ import annotations

import bisect


# --- Bars --------------------------------------------------------------


def parse_bars(symbol: str, raw_historicals: dict) -> list[dict]:
    """
    Parse a get_equity_historicals response into a clean, chronologically
    sorted list of {date, open, high, low, close, volume} for `symbol` —
    date is a plain "YYYY-MM-DD" string (bars are UTC-midnight-labeled
    daily bars, so the date portion alone is unambiguous), matching the
    string format SEC filing/filed dates already use, so date comparisons
    are simple string comparisons everywhere in this module.

    raw_historicals is the unmodified JSON returned by calling the MCP
    tool get_equity_historicals with symbols=[symbol], interval="day".
    This function does not call the tool itself.
    """
    results = raw_historicals.get("data", {}).get("results", [])
    match = next((r for r in results if r.get("symbol") == symbol.upper()), None)
    if match is None:
        raise ValueError(f"{symbol}: no results in get_equity_historicals response.")

    bars = []
    for b in match.get("bars", []):
        bars.append({
            "date": b["begins_at"][:10],
            "open": float(b["open_price"]),
            "high": float(b["high_price"]),
            "low": float(b["low_price"]),
            "close": float(b["close_price"]),
            "volume": b.get("volume"),
        })
    bars.sort(key=lambda b: b["date"])
    return bars


def bars_through(bars: list[dict], as_of: str) -> list[dict]:
    """Truncate a chronologically sorted bar list to dates <= as_of — the
    single choke point every indicator function below goes through, so
    "no lookahead" is enforced in one place, not re-implemented per
    indicator."""
    dates = [b["date"] for b in bars]
    idx = bisect.bisect_right(dates, as_of)
    return bars[:idx]


# --- Indicators ----------------------------------------------------------
# Standard, textbook formulas (the same "textbook technical-analysis
# levels" agents/technicals.py's own docstring already calls them) — not
# guaranteed byte-identical to Robinhood's own server-side computation
# (that implementation isn't public), but the well-established reference
# formulas, computed here because live indicators only ever answer "as of
# right now," never "as of a past date using only data available then."


def ema_series(closes: list[float], period: int) -> list[float | None]:
    """Classic EMA: SMA-seeded, then exponentially smoothed. None for
    every index before `period` closes are available."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) < period:
        return out
    multiplier = 2 / (period + 1)
    seed = sum(closes[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(closes)):
        prev = (closes[i] - prev) * multiplier + prev
        out[i] = prev
    return out


def _rsi_from_avgs(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def rsi_series(closes: list[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI: SMA-seeded average gain/loss, then Wilder-smoothed.
    None for every index before `period` deltas are available."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, n)]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, n)]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = _rsi_from_avgs(avg_gain, avg_loss)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = _rsi_from_avgs(avg_gain, avg_loss)
    return out


def atr_series(bars: list[dict], period: int = 14) -> list[float | None]:
    """Wilder's ATR (dollar terms, not %) — true range averaged the same
    Wilder-smoothed way as RSI above. None for every index before
    `period` true ranges are available."""
    n = len(bars)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    trs = []
    for i in range(1, n):
        high, low, prev_close = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    atr = sum(trs[:period]) / period
    out[period] = atr
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
        out[i + 1] = atr
    return out


# --- Council-ready bundle --------------------------------------------------


def technicals_as_of(symbol: str, as_of: str, bars: list[dict], regime_ema_period: int) -> dict | None:
    """
    Compute {price, ema, rsi, atr_pct, regime_ema} as they would have read
    on `as_of`, using ONLY bars up to and including that date — the
    no-lookahead choke point for prices/technicals (see bars_through()).

    bars: this symbol's FULL fetched bar history (parse_bars() output) —
    truncation happens here, not before this function is called, so the
    same full series can be reused across every simulated date without
    re-fetching.

    Returns None if there isn't enough trailing history yet for every
    indicator, or as_of isn't a trading day in this bar series — the
    caller should treat this the same as automation/run_pass.py treats a
    failed data-sanity check: skip this (symbol, date), never trade on it.
    """
    truncated = bars_through(bars, as_of)
    if not truncated or truncated[-1]["date"] != as_of:
        return None  # no bar ON as_of (non-trading day, or before history starts)

    closes = [b["close"] for b in truncated]
    price = closes[-1]

    ema9 = ema_series(closes, 9)[-1]
    rsi14 = rsi_series(closes, 14)[-1]
    atr14 = atr_series(truncated, 14)[-1]
    regime_ema = ema_series(closes, regime_ema_period)[-1]

    if None in (ema9, rsi14, atr14, regime_ema):
        return None  # not enough warm-up history yet for this date

    return {
        "price": price,
        "ema": ema9,
        "rsi": rsi14,
        "atr_pct": atr14 / price,
        "regime_ema": regime_ema,
    }


def fundamentals_as_of(ticker: str, as_of: str) -> dict:
    """Thin wrapper over agents.fundamentals_seat.build_brief() — this
    seat already makes its own live HTTP calls directly (no MCP, no
    agent-mediation, same as the live system), so no parsing layer is
    needed here, just the as_of passthrough. The caller still forms the
    actual verdict (stance/confidence/reasons) by reading this brief —
    see BACKTEST_DESIGN.md's "Cost strategy" for the per-filing-boundary
    caching this is meant to be called under, not per-day."""
    from agents import fundamentals_seat
    return fundamentals_seat.build_brief(ticker, as_of=as_of)


def council_bundle_for(symbol: str, as_of: str, bars: list[dict], regime_ema_period: int,
                        fundamentals_verdict: dict) -> dict | None:
    """
    Combine technicals_as_of() with an already-formed Fundamentals verdict
    into the same shape the council pipeline expects:
    {price, atr_pct, rsi, ema, regime_ema, fundamentals_verdict}.

    fundamentals_verdict: agents.fundamentals_seat.form_verdict() output,
    looked up by the caller from the per-filing-boundary cache (see
    backtest/engine.py) — this function does not form it and does not
    call an LLM.

    Returns None (skip this symbol/date, same fail-safe convention as
    automation/run_pass.py) if technicals_as_of() couldn't produce a
    reading for this date.
    """
    tech = technicals_as_of(symbol, as_of, bars, regime_ema_period)
    if tech is None:
        return None
    return {**tech, "fundamentals_verdict": fundamentals_verdict}
