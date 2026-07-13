"""
backtest/risk_metrics.py — risk-adjusted and exposure metrics computed by
REPLAYING an already-saved backtest run's trade log against the
already-cached historical prices.

PURE POST-PROCESSING. Makes no LLM calls, calls no MCP tool, never
re-runs the council/seats/judge/regime/risk-vetoer, and never reads or
writes logs/trades.jsonl or logs/paper_portfolio.json (the live audit
trail) — only the isolated logs/backtests/<run-id>/*_trades.jsonl files
and the cached bars already fetched for that run. See
agents/BACKTEST_DESIGN.md for the run's own isolation guarantees, which
this module inherits by construction (it only ever opens files under
logs/backtests/).

WHY THIS EXISTS: raw realized-trade P&L (backtest/metrics.py) answers
"how much did each strategy make" but not "at what risk" — the council
made 39 trades, the baseline made 834; comparing their dollar totals
directly is apples-to-oranges. Everything here derives from a DAILY
MARK-TO-MARKET EQUITY CURVE instead, so time spent flat in cash and
unrealized drawdown are counted as the risk they are, not invisible.
"""

from __future__ import annotations

import math

from execution import config, trade_log

from .metrics import wilson_ci

TRADING_DAYS_PER_YEAR = 252  # standard annualization factor — stated explicitly, used everywhere below


# --- Daily equity curve reconstruction ------------------------------------


def build_daily_equity_curve(
    trades_path: str,
    bars_by_symbol: dict[str, list[dict]],
    windows: list[tuple[str, str, str]],
) -> list[dict]:
    """
    Reconstruct cash/positions/equity for every simulated trading day,
    purely from the already-logged fills and already-cached bars.

    windows: [(name, start, end), ...] in the SAME chronological order the
    run was actually executed in (see the full-run script) — this is what
    defines the day axis (concatenated per-window trading days) and where
    the window-boundary "seams" are (see compute_returns()).

    THE MATCHING PROBLEM: fills don't carry their simulated date directly
    — trade_log.record()'s "timestamp" field is the REAL wall-clock time
    the entry was written (today), not the simulated date it represents.
    The only per-fill signal available is `quoted_price` (the exact bar
    close engine.py priced the decision at, logged verbatim before
    slippage). Reverse-looking-up date from (symbol, price) alone is NOT
    reliable — real closing-price duplicates exist across this combined
    3-window, 3-different-calendar-year dataset (e.g. JPM repeats a close
    to the cent ~40 times). The fix: fills are logged in STRICT
    chronological order (engine.py finishes one whole simulated day,
    council then baseline then buy-and-hold, before advancing to the
    next — see engine.py's day loop), so a SEQUENTIAL single-pass merge
    against the day axis is unambiguous even when the same price recurs
    elsewhere in the dataset: at each day, only fills at the FRONT of the
    (already-ordered) queue are ever considered.

    cash is read directly from each fill's own `cash_after` field (what
    PaperBroker actually computed, including slippage/fees) rather than
    recomputed here — no arithmetic can drift from what really happened.

    Returns one record per trading day: {date, window, cash,
    positions_value, equity, exposure_pct, n_positions}.
    """
    fills = [e for e in trade_log.read_all(log_path=trades_path) if e["action"] in ("buy", "sell")]
    close_by_symbol_date = {sym: {b["date"]: b["close"] for b in bars} for sym, bars in bars_by_symbol.items()}

    cash = config.PAPER_STARTING_CASH
    positions: dict[str, float] = {}
    fill_idx = 0
    n_fills = len(fills)
    curve: list[dict] = []

    for wname, wstart, wend in windows:
        day_axis = sorted({
            b["date"] for bars in bars_by_symbol.values() for b in bars if wstart <= b["date"] <= wend
        })
        for date in day_axis:
            while fill_idx < n_fills:
                e = fills[fill_idx]
                sym = e["symbol"]
                today_close = close_by_symbol_date.get(sym, {}).get(date)
                if today_close is None or abs(e["quoted_price"] - today_close) > 1e-6:
                    break  # next fill doesn't belong to today — stop consuming, move to next day
                if e["action"] == "buy":
                    positions[sym] = positions.get(sym, 0.0) + e["quantity"]
                else:
                    positions[sym] = positions.get(sym, 0.0) - e["quantity"]
                    if abs(positions[sym]) < 1e-9:
                        del positions[sym]
                cash = e["cash_after"]
                fill_idx += 1

            positions_value = 0.0
            for sym, shares in positions.items():
                price = close_by_symbol_date.get(sym, {}).get(date)
                if price is None:
                    raise ValueError(
                        f"{sym}: held {shares} shares on {date} but no cached close exists for that "
                        f"day — a real cross-window data gap, not a value to silently treat as $0 "
                        f"(see execution/paper_broker.py's documented $0-unpriced-position gotcha)."
                    )
                positions_value += shares * price

            equity = cash + positions_value
            curve.append({
                "date": date, "window": wname, "cash": cash, "positions_value": positions_value,
                "equity": equity, "exposure_pct": (positions_value / equity if equity > 0 else 0.0),
                "n_positions": len(positions),
            })

    assert fill_idx == n_fills, (
        f"Reconciliation failure: {n_fills - fill_idx} of {n_fills} fills in {trades_path} never "
        f"matched a trading day — the equity reconstruction is wrong, stop and investigate."
    )
    return curve


def compute_returns(curve: list[dict]) -> list[float]:
    """
    Daily returns r_t = equity_t/equity_{t-1} - 1, computed ONLY between
    consecutive days within the SAME window. The transition between one
    window's last day and the next window's first day spans a multi-year
    calendar gap (e.g. 2018-08-30 -> 2022-01-03) — not a real one-day
    price move, so treating it as a daily return would inject a single
    enormous fake observation into volatility/Sharpe/Sortino. Equity
    LEVELS still carry continuously across that seam for drawdown (see
    max_drawdown()) — a real paper account genuinely would be marked at
    the new price when it reopens, and that IS a real risk of holding
    through an unobserved gap. This function only excludes it from the
    smoothed daily-return series.
    """
    returns = []
    for i in range(1, len(curve)):
        if curve[i]["window"] != curve[i - 1]["window"]:
            continue
        prev_eq, eq = curve[i - 1]["equity"], curve[i]["equity"]
        if prev_eq > 0:
            returns.append(eq / prev_eq - 1)
    return returns


# --- Return/risk metrics (section A) --------------------------------------


def _stdev(values: list[float]) -> float | None:
    """Sample standard deviation, ddof=1 — None (not an error) if fewer
    than 2 observations, since sample variance is undefined at n<2."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def annualized_volatility(returns: list[float]) -> float | None:
    sd = _stdev(returns)
    return sd * math.sqrt(TRADING_DAYS_PER_YEAR) if sd is not None else None


def sharpe_ratio(returns: list[float], rf_daily: float = 0.0) -> float | None:
    """rf_daily defaults to 0 — a documented simplification (paper trading
    has no real financing cost/opportunity rate attached), not a claim
    that the true risk-free rate was zero over these windows."""
    sd = _stdev(returns)
    if sd is None or sd == 0:
        return None
    mean_excess = sum(r - rf_daily for r in returns) / len(returns)
    return mean_excess / sd * math.sqrt(TRADING_DAYS_PER_YEAR)


def sortino_ratio(returns: list[float], target: float = 0.0) -> float | None:
    """downside_deviation uses ALL periods (min(0, r-target)^2, zeroed for
    up periods), not just the downside subset — this is the standard
    Sortino definition and matches what was specified."""
    n = len(returns)
    if n == 0:
        return None
    mean_excess = sum(r - target for r in returns) / n
    downside_sq = [min(0.0, r - target) ** 2 for r in returns]
    downside_deviation = math.sqrt(sum(downside_sq) / n)
    if downside_deviation == 0:
        return None  # no downside periods at all — ratio is undefined, not infinite
    return mean_excess / downside_deviation * math.sqrt(TRADING_DAYS_PER_YEAR)


def max_drawdown(curve: list[dict]) -> dict:
    """
    Max drawdown over the FULL equity curve (including cross-window
    seams — see compute_returns()'s docstring for why levels stay
    continuous even though returns don't). Reported as a positive
    magnitude with the peak/trough dates that produced it.
    """
    if not curve:
        return {"max_drawdown_pct": None, "peak_date": None, "trough_date": None}
    running_max = curve[0]["equity"]
    running_max_date = curve[0]["date"]
    worst = 0.0
    worst_peak_date = running_max_date
    worst_trough_date = curve[0]["date"]
    for point in curve:
        if point["equity"] > running_max:
            running_max = point["equity"]
            running_max_date = point["date"]
        if running_max > 0:
            dd = point["equity"] / running_max - 1
            if dd < worst:
                worst = dd
                worst_peak_date = running_max_date
                worst_trough_date = point["date"]
    return {"max_drawdown_pct": round(-worst, 4), "peak_date": worst_peak_date, "trough_date": worst_trough_date}


def cagr(curve: list[dict], returns: list[float]) -> float | None:
    """
    Annualized return over the TRADING-DAY basis (len(returns) actual
    simulated days / 252), not real calendar time — this backtest spans
    2018/2022/2023 with multi-year unobserved gaps in between, so "years
    elapsed on a calendar" would badly understate the return by diluting
    it over ~5 years of mostly-unsimulated time. This convention treats
    the ~373 actual trading days as if compressed into continuous time —
    stated explicitly because it's a real, debatable choice, not the only
    valid one.
    """
    if not curve or not returns:
        return None
    start_eq, end_eq = curve[0]["equity"], curve[-1]["equity"]
    if start_eq <= 0:
        return None
    years = len(returns) / TRADING_DAYS_PER_YEAR
    if years <= 0:
        return None
    return (end_eq / start_eq) ** (1 / years) - 1


def calmar_ratio(annualized_return: float | None, mdd: dict) -> float | None:
    if annualized_return is None or mdd["max_drawdown_pct"] in (None, 0):
        return None
    return annualized_return / mdd["max_drawdown_pct"]


# --- Exposure / turnover metrics (section B) ------------------------------


def exposure_metrics(curve: list[dict], trades_path: str) -> dict:
    if not curve:
        return {}
    days_in_market = sum(1 for p in curve if p["n_positions"] > 0)
    time_in_market = round(days_in_market / len(curve), 4)
    avg_exposure = round(sum(p["exposure_pct"] for p in curve) / len(curve), 4)
    total_dollar_days_deployed = round(sum(p["positions_value"] for p in curve), 2)

    stats = trade_log.round_trip_stats(log_path=trades_path)
    total_realized_pnl = stats["total_realized_pnl"]
    profit_per_dollar_day = (
        round(total_realized_pnl / total_dollar_days_deployed, 6) if total_dollar_days_deployed > 0 else None
    )

    fills = [e for e in trade_log.read_all(log_path=trades_path) if e["action"] in ("buy", "sell")]
    total_notional = sum(e["quantity"] * e["price"] for e in fills)
    avg_equity = sum(p["equity"] for p in curve) / len(curve)
    turnover = round(total_notional / avg_equity, 4) if avg_equity > 0 else None

    return {
        "time_in_market": time_in_market,
        "avg_exposure": avg_exposure,
        "total_dollar_days_deployed": total_dollar_days_deployed,
        "profit_per_dollar_day": profit_per_dollar_day,
        "turnover": turnover,
        "trade_count": stats["count"],  # round trips — same definition backtest/metrics.py already uses
    }


# --- Trade-level metrics (section C) --------------------------------------


def trade_level_metrics(trades_path: str) -> dict:
    closes = [
        e for e in trade_log.read_all(log_path=trades_path)
        if e["mode"] == "paper" and e["action"] == "sell" and e.get("realized_pnl") is not None
    ]
    n = len(closes)
    pnls = [e["realized_pnl"] for e in closes]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    gross_profit = sum(wins)
    gross_loss = -sum(losses)  # magnitude
    avg_win = round(sum(wins) / len(wins), 4) if wins else None
    avg_loss = round(sum(losses) / len(losses), 4) if losses else None  # kept negative, matches realized_pnl sign
    profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 0 else None
    expectancy_per_trade = round(sum(pnls) / n, 4) if n > 0 else None
    payoff_ratio = round(avg_win / abs(avg_loss), 4) if (avg_win is not None and avg_loss not in (None, 0)) else None

    return {
        "n": n,
        "win_rate": round(len(wins) / n, 4) if n > 0 else None,
        "win_rate_ci_95": wilson_ci(len(wins), n),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "expectancy_per_trade": expectancy_per_trade,
        "payoff_ratio": payoff_ratio,
    }


# --- Full per-strategy summary ---------------------------------------------


def risk_adjusted_summary(trades_path: str, bars_by_symbol: dict, windows: list[tuple[str, str, str]]) -> dict:
    curve = build_daily_equity_curve(trades_path, bars_by_symbol, windows)
    returns = compute_returns(curve)
    mdd = max_drawdown(curve)
    ann_return = cagr(curve, returns)

    return {
        "n_equity_days": len(curve),
        "n_return_periods": len(returns),  # N next to every ratio below, per the brief
        "start_equity": round(curve[0]["equity"], 2) if curve else None,
        "end_equity": round(curve[-1]["equity"], 2) if curve else None,
        "annualized_volatility": (
            round(annualized_volatility(returns), 4) if annualized_volatility(returns) is not None else None
        ),
        "sharpe_ratio": (round(sharpe_ratio(returns), 4) if sharpe_ratio(returns) is not None else None),
        "sortino_ratio": (round(sortino_ratio(returns), 4) if sortino_ratio(returns) is not None else None),
        "max_drawdown": mdd,
        "annualized_return": round(ann_return, 4) if ann_return is not None else None,
        "calmar_ratio": (
            round(calmar_ratio(ann_return, mdd), 4) if calmar_ratio(ann_return, mdd) is not None else None
        ),
        "exposure": exposure_metrics(curve, trades_path),
        "trade_level": trade_level_metrics(trades_path),
        "_curve": curve,  # kept for the caller (e.g. plotting/further checks); not part of the printed table
    }


def _fmt_ci(ci: tuple[float, float] | None) -> str:
    return f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci is not None else "None"


def comparison_table(summaries: dict[str, dict]) -> str:
    """summaries: {"council": ..., "baseline": ..., "buyhold": ...} — each
    a risk_adjusted_summary() output. Renders the single comparison table
    the brief asks for, N printed next to every ratio."""
    names = ["council", "baseline", "buyhold"]
    rows = [
        ("N (return periods)", lambda s: s["n_return_periods"]),
        ("Start equity", lambda s: s["start_equity"]),
        ("End equity", lambda s: s["end_equity"]),
        ("Annualized volatility", lambda s: s["annualized_volatility"]),
        ("Sharpe ratio", lambda s: s["sharpe_ratio"]),
        ("Sortino ratio", lambda s: s["sortino_ratio"]),
        ("Max drawdown", lambda s: s["max_drawdown"]["max_drawdown_pct"]),
        ("Annualized return", lambda s: s["annualized_return"]),
        ("Calmar ratio", lambda s: s["calmar_ratio"]),
        ("Time in market", lambda s: s["exposure"]["time_in_market"]),
        ("Avg exposure", lambda s: s["exposure"]["avg_exposure"]),
        ("Total $-days deployed", lambda s: s["exposure"]["total_dollar_days_deployed"]),
        ("Profit per $-day", lambda s: s["exposure"]["profit_per_dollar_day"]),
        ("Turnover", lambda s: s["exposure"]["turnover"]),
        ("Trade count (round trips)", lambda s: s["exposure"]["trade_count"]),
        ("Win rate (n)", lambda s: f"{s['trade_level']['win_rate']} (n={s['trade_level']['n']})"),
        ("Win rate 95% CI", lambda s: _fmt_ci(s["trade_level"]["win_rate_ci_95"])),
        ("Avg win", lambda s: s["trade_level"]["avg_win"]),
        ("Avg loss", lambda s: s["trade_level"]["avg_loss"]),
        ("Profit factor", lambda s: s["trade_level"]["profit_factor"]),
        ("Expectancy/trade", lambda s: s["trade_level"]["expectancy_per_trade"]),
        ("Payoff ratio", lambda s: s["trade_level"]["payoff_ratio"]),
    ]
    col_w = 20
    header = f"{'metric':<28}" + "".join(f"{n:>{col_w}}" for n in names)
    lines = [header, "-" * len(header)]
    for label, fn in rows:
        vals = [str(fn(summaries[n])) for n in names]
        lines.append(f"{label:<28}" + "".join(f"{v:>{col_w}}" for v in vals))
    lines.append("")
    lines.append("Max drawdown peak -> trough:")
    for n in names:
        mdd = summaries[n]["max_drawdown"]
        lines.append(f"  {n:<10} {mdd['peak_date']} -> {mdd['trough_date']}")
    return "\n".join(lines)
