"""
backtest/momentum_backtest.py -- validation harness for the time-series
momentum strategy (strategies/time_series_momentum.py). The promotion gate
from STRATEGY_LIBRARY.md, in code: does trend-following actually beat
buy-and-hold on this symbol's own history, before it's ever allowed into the
live gate.

Agent-mediated like every other backtest module: it does NOT fetch bars or
call any MCP tool. Whatever real historical run drives this passes in
already-fetched bars (backtest/data.py's parse_bars() output);
backtest/run_momentum_backtest.py is the real-data driver, and the
self-test below uses synthetic bars so it runs offline.

WHAT IT MEASURES, in plain terms: step through history one bar at a time; on
each bar, ask the strategy "in or out?" using ONLY prices up to that bar (no
peeking ahead -- backtest/data.py's bars_through() is the choke point), then
look at what the price ACTUALLY did over the next `horizon` bars.

THE HEADLINE IS LONG-ONLY. execution/paper_broker.py cannot short, so the
variant this system can actually trade is: bullish -> hold it, anything
else -> cash. That's what `long_only` scores, and what `beats_buyhold`
judges. `long_short` (the paper literature's version) is reported for
reference only -- this system can't trade it.

Three yardsticks per variant: total compounded return, Sharpe ratio
(risk-adjusted), and max drawdown (worst peak-to-trough). Costs: every
position change pays config.SLIPPAGE_BPS per side -- the same fill haircut
PaperBroker applies -- so turnover isn't free.

HONEST LIMITATIONS (stated, not hidden):
- Directional accuracy is scored on every bar with overlapping forward
  windows when horizon > 1, so its confidence interval is optimistic.
  (Monthly bars with horizon=1 don't overlap.)
- This is a screening harness: a single symbol's own history, no position
  sizing, no risk caps. A signal that clears it still has to go through the
  full engine (backtest/engine.py) + PaperBroker before it means anything.
"""

from __future__ import annotations

import math

from execution import config
from strategies import time_series_momentum as tsm

from . import data as backtest_data
from . import metrics as equity_metrics

# Default horizon is in BARS: 21 daily bars ~= 1 month, the momentum
# literature's rebalance period. For monthly bars pass horizon=1.
HORIZON_DAYS = 21


def _forward_return(closes: list[float], i: int, horizon: int) -> float | None:
    """Return from close[i] to close[i + horizon], or None if there aren't
    that many bars left after i."""
    if i + horizon >= len(closes):
        return None
    start = closes[i]
    if start <= 0:
        return None
    return (closes[i + horizon] - start) / start


def _compound(rets: list[float]) -> float:
    equity = 1.0
    for r in rets:
        equity *= 1.0 + r
    return equity - 1.0


def _sharpe(rets: list[float], periods_per_year: float) -> float | None:
    """Annualized mean/stdev of per-step returns (risk-free rate taken as 0
    -- a stated simplification). None if there's too little data or no
    variation to divide by."""
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    if var <= 0:
        return None
    return mean / math.sqrt(var) * math.sqrt(periods_per_year)


def _max_drawdown(rets: list[float]) -> float:
    """Worst peak-to-trough fall of the compounded equity curve, as a
    negative fraction (e.g. -0.35 = a 35% drawdown). 0.0 if it never fell."""
    equity = peak = 1.0
    worst = 0.0
    for r in rets:
        equity *= 1.0 + r
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def _summary(rets: list[float], periods_per_year: float, cash: list[float] | None = None) -> dict:
    """Total return and drawdown on the raw returns; Sharpe on EXCESS
    returns over cash when a per-step cash series is supplied (the
    textbook definition -- with no cash series it's the raw-return Sharpe,
    i.e. a 0% risk-free rate)."""
    excess = [r - c for r, c in zip(rets, cash)] if cash else rets
    sharpe = _sharpe(excess, periods_per_year)
    return {
        "total_return": round(_compound(rets), 4),
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "max_drawdown": round(_max_drawdown(rets), 4),
    }


def run_validation(bars: list[dict], symbol: str = "TEST", horizon: int = HORIZON_DAYS,
                   lookback: int = tsm.LOOKBACK_DAYS, skip: int = tsm.SKIP_DAYS,
                   periods_per_year: float = 12, cost_bps: float = config.SLIPPAGE_BPS,
                   score_from: str | None = None, score_to: str | None = None,
                   cash_annual_by_year: dict[int, float] | None = None) -> dict:
    """
    Step through `bars` (chronological parse_bars() output), generate the
    momentum signal at each bar from only the bars up to that bar, score it
    against the realized forward return, and compare against buy-and-hold.

    lookback / skip / horizon are in BARS (see module docstring).
    periods_per_year: how many non-overlapping steps make a year, for
    annualizing Sharpe -- 12 for both monthly bars with horizon=1 and daily
    bars with horizon=21.
    score_from / score_to: optional inclusive "YYYY-MM-DD" bounds on which
    decision dates are SCORED (for per-regime breakdowns). The signal still
    reads all history before score_from -- only the scoring is windowed, so
    a window's first decision isn't starved of lookback.
    cash_annual_by_year: optional {year: annual cash rate as a fraction,
    e.g. 0.05}. When given, a long-only step spent OUT of the market earns
    that year's rate / periods_per_year instead of 0%, and every Sharpe is
    computed on excess return over that cash rate. None = cash earns 0%.

    Returns per-symbol metrics plus the raw counts pool_results() needs.
    """
    closes = [b["close"] for b in bars]
    dates = [b["date"] for b in bars]
    cost = cost_bps / 10_000.0

    scored = 0
    bullish_calls = bearish_calls = neutral_calls = 0
    directional_correct = 0      # bullish->went up, or bearish->went down
    bullish_correct = 0          # bullish->went up (what long-only cares about)
    up_periods = 0               # unconditional base rate: "always bullish" baseline

    lo_rets: list[float] = []    # long-only (what this system can trade)
    step_dates: list[str] = []   # decision date of each non-overlapping step
    step_longs: list[int] = []   # long-only position taken that step (1 = holding, 0 = cash)
    step_cash: list[float] = []  # cash return available that step (0.0 unless cash_annual_by_year)
    ls_rets: list[float] = []    # long-short (reference only)
    bh_rets: list[float] = []    # buy-and-hold
    lo_pos = ls_pos = 0
    bh_started = False
    long_steps = 0
    step_cursor = None

    for i in range(len(closes)):
        fwd = _forward_return(closes, i, horizon)
        if fwd is None:
            continue
        if (score_from and dates[i] < score_from) or (score_to and dates[i] > score_to):
            continue
        history = [b["close"] for b in backtest_data.bars_through(bars, dates[i])]
        momentum = tsm.trailing_return(history, lookback, skip)
        typical = tsm.typical_abs_return(history, lookback, skip)
        if momentum is None or typical is None:
            continue  # not enough history yet -- not a decision, not scored
        view = tsm.build_view(symbol, momentum, typical)

        scored += 1
        went_up = fwd >= 0
        up_periods += went_up
        if view["stance"] == "bullish":
            bullish_calls += 1
            directional_correct += went_up
            bullish_correct += went_up
        elif view["stance"] == "bearish":
            bearish_calls += 1
            directional_correct += not went_up
        else:
            neutral_calls += 1

        # Non-overlapping P&L steps: a fresh position decision only once the
        # previous horizon has fully elapsed.
        if step_cursor is None or i >= step_cursor + horizon:
            new_lo = 1 if view["stance"] == "bullish" else 0
            new_ls = {"bullish": 1, "bearish": -1}.get(view["stance"], 0)
            cash = (cash_annual_by_year.get(int(dates[i][:4]), 0.0) / periods_per_year
                    if cash_annual_by_year else 0.0)
            # Out of the market = sitting in cash, earning cash.
            lo_rets.append((fwd if new_lo else cash) - abs(new_lo - lo_pos) * cost)
            ls_rets.append((new_ls * fwd if new_ls else cash) - abs(new_ls - ls_pos) * cost)
            bh_rets.append(fwd - (0.0 if bh_started else cost))
            lo_pos, ls_pos, bh_started = new_lo, new_ls, True
            long_steps += new_lo
            step_dates.append(dates[i])
            step_longs.append(new_lo)
            step_cash.append(cash)
            step_cursor = i

    directional_calls = bullish_calls + bearish_calls
    acc = directional_correct / directional_calls if directional_calls else None
    acc_ci = equity_metrics.wilson_ci(directional_correct, directional_calls)
    base = up_periods / scored if scored else None

    cash_series = step_cash if cash_annual_by_year else None
    long_only = _summary(lo_rets, periods_per_year, cash_series)
    buy_hold = _summary(bh_rets, periods_per_year, cash_series)

    return {
        "symbol": symbol,
        "scored_periods": scored,
        "bullish_calls": bullish_calls,
        "bearish_calls": bearish_calls,
        "neutral_calls": neutral_calls,
        "directional_correct": directional_correct,
        "bullish_correct": bullish_correct,
        "up_periods": up_periods,
        "strategy_accuracy": round(acc, 4) if acc is not None else None,
        "strategy_accuracy_ci_95": tuple(round(x, 4) for x in acc_ci) if acc_ci else None,
        "baseline_accuracy": round(base, 4) if base is not None else None,   # "always bullish"
        "bullish_hit_rate": round(bullish_correct / bullish_calls, 4) if bullish_calls else None,
        "steps": len(lo_rets),
        # Per-step series, so a driver can combine symbols into an
        # equal-weight portfolio by date (see pool_portfolio()).
        "step_returns": [{"date": d, "long": pos, "cash": round(c, 6),
                          "long_only": round(lo, 6), "buy_hold": round(bh, 6)}
                         for d, pos, c, lo, bh in zip(step_dates, step_longs, step_cash, lo_rets, bh_rets)],
        "time_in_market": round(long_steps / len(lo_rets), 4) if lo_rets else None,
        "long_only": long_only,
        "long_short_reference": _summary(ls_rets, periods_per_year, cash_series),
        "buy_hold": buy_hold,
        # Promotion-gate checks (screening-grade -- see module docstring).
        # beats_baseline: accuracy CI lower bound clears the always-bullish
        # base rate ("clearly separated", same discipline as
        # backtest/forecast_backtest.py).
        "beats_baseline": bool(acc_ci and base is not None and acc_ci[0] > base),
        # beats_buyhold: long-only Sharpe above buy-and-hold Sharpe --
        # risk-adjusted, per STRATEGY_LIBRARY.md's promotion gate.
        "beats_buyhold": (long_only["sharpe"] is not None and buy_hold["sharpe"] is not None
                          and long_only["sharpe"] > buy_hold["sharpe"]),
        "shallower_drawdown": long_only["max_drawdown"] > buy_hold["max_drawdown"],
    }


def pool_results(results: list[dict]) -> dict:
    """Pool directional accuracy across symbols -- one symbol's ~100 monthly
    calls give a very wide confidence interval; pooling the counts is the
    honest way to get a tighter one. Per-symbol P&L is NOT pooled into a
    portfolio here (no sizing model in this harness)."""
    calls = sum(r["bullish_calls"] + r["bearish_calls"] for r in results)
    correct = sum(r["directional_correct"] for r in results)
    bull_calls = sum(r["bullish_calls"] for r in results)
    bull_correct = sum(r["bullish_correct"] for r in results)
    scored = sum(r["scored_periods"] for r in results)
    ups = sum(r["up_periods"] for r in results)
    ci = equity_metrics.wilson_ci(correct, calls)
    bull_ci = equity_metrics.wilson_ci(bull_correct, bull_calls)
    return {
        "symbols": len(results),
        "directional_calls": calls,
        "strategy_accuracy": round(correct / calls, 4) if calls else None,
        "strategy_accuracy_ci_95": tuple(round(x, 4) for x in ci) if ci else None,
        "bullish_calls": bull_calls,
        "bullish_hit_rate": round(bull_correct / bull_calls, 4) if bull_calls else None,
        "bullish_hit_rate_ci_95": tuple(round(x, 4) for x in bull_ci) if bull_ci else None,
        "baseline_up_rate": round(ups / scored, 4) if scored else None,
        "beats_buyhold_count": sum(1 for r in results if r["beats_buyhold"]),
        "shallower_drawdown_count": sum(1 for r in results if r["shallower_drawdown"]),
    }


def pool_portfolio(results: list[dict], periods_per_year: float = 12,
                   score_from: str | None = None, score_to: str | None = None) -> dict:
    """
    Combine symbols into an EQUAL-WEIGHT PORTFOLIO, the way the momentum
    literature evaluates the strategy (a basket, not one name at a time).

    Each step date, capital is split equally across every symbol that has a
    scored step that date. Strategy side: each slot either holds its symbol
    (bullish) or sits in cash. Benchmark side: every slot holds its symbol.
    A symbol only enters the basket once it has enough history to be scored,
    and both sides use exactly the same symbols each date -- apples to apples.

    score_from / score_to: optional inclusive "YYYY-MM-DD" bounds on which
    step dates count (for per-regime windows).
    """
    by_date: dict[str, list[dict]] = {}
    for r in results:
        for st in r.get("step_returns", []):
            if (score_from and st["date"] < score_from) or (score_to and st["date"] > score_to):
                continue
            by_date.setdefault(st["date"], []).append(st)
    dates = sorted(by_date)
    lo, bh, exposure, breadth, cash = [], [], [], [], []
    for d in dates:
        rows = by_date[d]
        lo.append(sum(x["long_only"] for x in rows) / len(rows))
        bh.append(sum(x["buy_hold"] for x in rows) / len(rows))
        cash.append(rows[0].get("cash", 0.0))   # same rate for every symbol that date
        exposure.append(sum(x["long"] for x in rows) / len(rows))
        breadth.append(len(rows))
    cash_series = cash if any(cash) else None
    strat, bench = _summary(lo, periods_per_year, cash_series), _summary(bh, periods_per_year, cash_series)
    return {
        "months": len(dates),
        "first": dates[0] if dates else None,
        "last": dates[-1] if dates else None,
        "avg_symbols": round(sum(breadth) / len(breadth), 1) if breadth else 0,
        "avg_exposure": round(sum(exposure) / len(exposure), 4) if exposure else None,
        "long_only": strat,
        "buy_hold": bench,
        "beats_buyhold": (strat["sharpe"] is not None and bench["sharpe"] is not None
                          and strat["sharpe"] > bench["sharpe"]),
        "shallower_drawdown": strat["max_drawdown"] > bench["max_drawdown"],
    }


if __name__ == "__main__":
    # Self-test with synthetic bars -- deterministic, offline.
    from datetime import date, timedelta

    def _bars(closes: list[float]) -> list[dict]:
        d0 = date(2020, 1, 1)
        return [{"date": (d0 + timedelta(days=i)).strftime("%Y-%m-%d"),
                 "open": c, "high": c * 1.001, "low": c * 0.999, "close": c, "volume": 1000}
                for i, c in enumerate(closes)]

    print("Up-then-down market (long-only momentum SHOULD sidestep the down leg)...")
    up_leg = [100.0 * (1.004 ** i) for i in range(400)]
    down_leg = [up_leg[-1] * (0.996 ** i) for i in range(1, 400)]
    res = run_validation(_bars(up_leg + down_leg))
    print(f"  scored={res['scored_periods']} bull={res['bullish_calls']} bear={res['bearish_calls']} "
          f"neutral={res['neutral_calls']} time_in_market={res['time_in_market']}")
    print(f"  long_only={res['long_only']}")
    print(f"  buy_hold ={res['buy_hold']}")
    assert res["scored_periods"] > 0 and res["bullish_calls"] > 0, res
    assert res["long_only"]["total_return"] > res["buy_hold"]["total_return"], res
    assert res["shallower_drawdown"], res

    print("\nCosts are charged: zero-cost run must do at least as well as the costed one...")
    free = run_validation(_bars(up_leg + down_leg), cost_bps=0.0)
    assert free["long_only"]["total_return"] >= res["long_only"]["total_return"], (free, res)
    print(f"  costed={res['long_only']['total_return']} free={free['long_only']['total_return']}")

    print("\nMonthly-bar parameters run end to end (lookback=12, skip=1, horizon=1)...")
    monthly = _bars([100.0 * (1.01 ** i) for i in range(80)])
    res_m = run_validation(monthly, lookback=12, skip=1, horizon=1)
    print(f"  scored={res_m['scored_periods']} long_only={res_m['long_only']}")
    assert res_m["scored_periods"] > 0, res_m

    print("\nCash earns interest when supplied: long-only total return must rise...")
    with_cash = run_validation(_bars(up_leg + down_leg), cash_annual_by_year={2020: 0.05, 2021: 0.05, 2022: 0.05})
    print(f"  cash@0%={res['long_only']['total_return']}  cash@5%={with_cash['long_only']['total_return']}")
    assert with_cash["long_only"]["total_return"] > res["long_only"]["total_return"], (with_cash, res)
    assert with_cash["buy_hold"]["total_return"] == res["buy_hold"]["total_return"], "buy-hold never holds cash"

    pooled = pool_results([res, res_m])
    print(f"\nPooled: {pooled}")
    assert pooled["directional_calls"] > 0, pooled

    print("\nEqual-weight portfolio of ONE symbol must equal that symbol's own numbers...")
    port = pool_portfolio([res])
    print(f"  portfolio long_only={port['long_only']}  symbol long_only={res['long_only']}")
    # Tolerance, not equality: step_returns are stored rounded to 6 places.
    for side in ("long_only", "buy_hold"):
        for k, tol in (("total_return", 1e-3), ("sharpe", 1e-2), ("max_drawdown", 1e-3)):
            assert abs(port[side][k] - res[side][k]) < tol, (side, k, port[side], res[side])
    assert abs(port["avg_exposure"] - res["time_in_market"]) < 1e-3, (port, res)

    print("\nPASS -- momentum backtest harness scores long-only, charges costs, and pools.")
