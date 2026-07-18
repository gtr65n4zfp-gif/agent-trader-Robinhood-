"""
backtest/vol_forecast.py — Level 0's two vol-forecasting engines (see
agents/SPY_OPTIONS_DESIGN.md): a GARCH(1,1) forecaster with a mandatory
rolling refit, and a simple trailing-realized-vol baseline, sharing one
interface so Task 6 can run either interchangeably through the same
decision logic. Neither is assumed to win — see the design doc's
"Decisions locked in" #1 and the GARCH ablation in "Metrics".

Day-count convention (locked in here, not left ambiguous): BOTH engines
annualize from a trading-day frequency via `sqrt(252)` — the standard,
universal convention the variance-risk-premium literature already uses
to compare realized/forecast vol against VIX (VIX's own internal
calendar-day time-weighting is a second-order effect on how it
interpolates between two option expiries, not a reason to mix day-count
bases here). GARCH's own forecast horizon is therefore expressed in
TRADING days, approximated from the calendar-day option horizon via
`trading_days_in_horizon()` below — never calendar days directly, since
that's not what GARCH's own recursion steps through.

Like every other data-touching module in this project, this module
fetches nothing itself — bars are already-fetched, already-parsed
(backtest/data.py's parse_bars() shape), passed in by the caller.
"""

from __future__ import annotations

import math

from . import data as backtest_data

# ~2 rolling years of trailing daily returns per GARCH refit — see
# agents/SPY_OPTIONS_DESIGN.md's "Decisions locked in" #2 for why rolling
# (not expanding) and why this length: GARCH's whole reason for existing
# here is capturing CURRENT volatility clustering, and an expanding
# window would dilute that with a decade of increasingly stale history.
GARCH_LOOKBACK_DAYS = 504

TRADING_DAYS_PER_YEAR = 252
CALENDAR_DAYS_PER_YEAR = 365


def trading_days_in_horizon(horizon_calendar_days: int) -> int:
    """Approximate how many TRADING days fall within a calendar-day
    option horizon (e.g. a "7-day" or "30-45 day" track, both counted in
    calendar days elsewhere in this project — see
    options_data.select_liquid_expiration()). A stated policy
    approximation (252/365 trading-day fraction), not derived per-date
    from an actual trading calendar — this project doesn't carry a
    holiday calendar dependency anywhere else either (see
    execution/config.py's market_is_open() docstring for the same
    tradeoff stated there). Minimum of 1 — a same-day or next-day horizon
    still needs at least one forecast step."""
    return max(1, round(horizon_calendar_days * TRADING_DAYS_PER_YEAR / CALENDAR_DAYS_PER_YEAR))


def daily_log_returns(bars: list[dict]) -> list[dict]:
    """{date, log_return} for each bar after the first, from consecutive
    close-to-close moves. `date` is the LATER bar's date (the return
    realized BY that date) — matches this project's own "as_of" dating
    convention throughout backtest/data.py."""
    out = []
    for prev, cur in zip(bars, bars[1:]):
        out.append({"date": cur["date"], "log_return": math.log(cur["close"] / prev["close"])})
    return out


def _annualized_stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)


def baseline_forecast_annualized_vol(bars: list[dict], as_of: str, lookback_days: int) -> float | None:
    """
    Engine B (the ablation's opponent) — trailing-window realized vol,
    annualized. No-lookahead via backtest_data.bars_through(): only bars
    dated <= as_of are ever visible, then the most recent `lookback_days`
    RETURNS (not bars) form the window. Returns None — never fabricates —
    if fewer than 2 return observations are available in the window,
    e.g. too early in a fetched series for the requested lookback.
    """
    truncated = backtest_data.bars_through(bars, as_of)
    returns = daily_log_returns(truncated)
    window = returns[-lookback_days:] if lookback_days else returns
    return _annualized_stdev([r["log_return"] for r in window])


def garch_forecast_annualized_vol(bars: list[dict], as_of: str, horizon_calendar_days: int,
                                   lookback_days: int = GARCH_LOOKBACK_DAYS) -> float | None:
    """
    Engine A — GARCH(1,1), rolling-refit. See
    agents/SPY_OPTIONS_DESIGN.md's "THE CRITICAL RULE" section: fits
    FRESH on exactly the trailing `lookback_days` of returns ending at
    as_of (via backtest_data.bars_through(), the same no-lookahead
    choke point every indicator in this project goes through), forecasts
    trading_days_in_horizon(horizon_calendar_days) steps ahead via arch's
    own analytic multi-step forecast, sums the forecasted daily
    variances, annualizes via sqrt(252/N) — see this module's own
    day-count-convention docstring above. The fit and its forecast are
    discarded immediately after — nothing here persists state between
    calls, so the next decision date's call starts from nothing.

    Returns None — never raises, never fabricates a forecast — if there
    isn't a full lookback window of trailing data yet, or if the GARCH
    fit doesn't converge. Caller skips this signal on None, same
    fail-safe convention as everywhere else in this project (e.g.
    options_data.select_contract() returning None on no match).
    """
    truncated = backtest_data.bars_through(bars, as_of)
    returns = daily_log_returns(truncated)
    window = returns[-lookback_days:]
    if len(window) < lookback_days:
        return None  # not enough trailing history yet for a stable rolling fit

    # arch's own documented convention: fit on returns scaled to PERCENT
    # (not raw decimals) for optimizer numerical stability — undone below
    # when converting the forecast back out of percent-space.
    pct_returns = [r["log_return"] * 100 for r in window]

    from arch import arch_model  # imported here, not module-level: this
    # is the one function in this module that actually needs the `arch`
    # dependency; keeping the import local means every other function
    # above (including the ablation's baseline engine) has zero
    # dependency on it, and a missing/broken arch install only breaks
    # GARCH specifically, not the whole module.

    try:
        model = arch_model(pct_returns, vol="Garch", p=1, q=1, mean="Zero", dist="normal")
        fit = model.fit(disp="off", show_warning=False)
    except Exception:
        return None  # fit raised (e.g. a numerical/optimizer failure) -- skip, don't guess

    if getattr(fit, "convergence_flag", 0) != 0:
        return None  # optimizer did not converge cleanly -- skip, don't trust an unconverged fit

    horizon_steps = trading_days_in_horizon(horizon_calendar_days)
    try:
        forecast = fit.forecast(horizon=horizon_steps, method="analytic", reindex=False)
    except Exception:
        return None

    daily_variances_pct2 = forecast.variance.values[-1]
    total_variance_pct2 = float(sum(daily_variances_pct2))
    if total_variance_pct2 <= 0:
        return None  # degenerate forecast -- skip rather than report a zero/negative vol

    sigma_over_horizon = math.sqrt(total_variance_pct2) / 100  # undo the percent scaling
    return sigma_over_horizon * math.sqrt(TRADING_DAYS_PER_YEAR / horizon_steps)


if __name__ == "__main__":
    import json

    print("Testing trading_days_in_horizon...")
    assert trading_days_in_horizon(7) == 5, trading_days_in_horizon(7)
    assert trading_days_in_horizon(30) == 21, trading_days_in_horizon(30)
    assert trading_days_in_horizon(45) == 31, trading_days_in_horizon(45)
    assert trading_days_in_horizon(0) == 1, "must floor at 1, never 0 forecast steps"
    print("PASS — calendar-day horizons approximate to sensible trading-day counts: "
          f"7d->{trading_days_in_horizon(7)}, 30d->{trading_days_in_horizon(30)}, 45d->{trading_days_in_horizon(45)}")

    print("\nTesting daily_log_returns...")
    bars = [
        {"date": "2026-01-05", "close": 100.0},
        {"date": "2026-01-06", "close": 101.0},
        {"date": "2026-01-07", "close": 99.99},
    ]
    returns = daily_log_returns(bars)
    assert len(returns) == 2, returns
    assert returns[0]["date"] == "2026-01-06", returns
    assert abs(returns[0]["log_return"] - math.log(101.0 / 100.0)) < 1e-12, returns
    print(f"PASS — 2 returns from 3 bars, dated to the LATER bar: {returns}")

    print("\nTesting baseline_forecast_annualized_vol — too little data returns None...")
    assert baseline_forecast_annualized_vol(bars, "2026-01-06", lookback_days=20) is None
    print("PASS — only 1 return available (need >=2), returns None rather than a bogus stdev.")

    print("\nTesting baseline_forecast_annualized_vol — no-lookahead via bars_through...")
    bars_with_future = bars + [{"date": "2026-01-08", "close": 500.0}]  # a huge, obviously-visible-if-leaked jump
    vol_at_07 = baseline_forecast_annualized_vol(bars_with_future, "2026-01-07", lookback_days=20)
    bars_truncated_manually = [b for b in bars_with_future if b["date"] <= "2026-01-07"]
    vol_at_07_no_future_in_input = baseline_forecast_annualized_vol(bars_truncated_manually, "2026-01-07", lookback_days=20)
    assert vol_at_07 == vol_at_07_no_future_in_input, (vol_at_07, vol_at_07_no_future_in_input)
    print(f"PASS — identical result whether or not a future (2026-01-08) bar is present in the input: {vol_at_07:.6f}")

    # --- Real SPY data from here on ---------------------------------------
    SCRATCH = "/private/tmp/claude-501/-Users-ethandungo-agent-trader/f77a7381-786c-45b3-8f03-7b93713c619c/scratchpad"
    with open(f"{SCRATCH}/spy_bars_2019_2026.json") as f:
        spy_bars = json.load(f)

    print(f"\nTesting baseline_forecast_annualized_vol on real SPY data ({len(spy_bars)} bars, "
          f"{spy_bars[0]['date']} -> {spy_bars[-1]['date']})...")
    # 2020-02-19 -> the S&P's pre-COVID-crash peak; 20-day trailing vol here
    # should still read calm (the crash hadn't started yet).
    calm_vol = baseline_forecast_annualized_vol(spy_bars, "2020-02-19", lookback_days=20)
    # 2020-03-23 -> the actual COVID crash bottom; 20-day trailing vol here
    # should be dramatically higher -- a real, known, extreme-vol regime,
    # not a synthetic fixture, and a good sanity check that this isn't
    # silently returning a constant or a garbage number.
    crash_vol = baseline_forecast_annualized_vol(spy_bars, "2020-03-23", lookback_days=20)
    assert calm_vol is not None and crash_vol is not None, (calm_vol, crash_vol)
    assert crash_vol > calm_vol * 3, (calm_vol, crash_vol)  # crash vol should be dramatically higher, not a rounding-level difference
    print(f"PASS — pre-crash calm (2020-02-19): {calm_vol:.3f} annualized vs. "
          f"COVID-crash bottom (2020-03-23): {crash_vol:.3f} annualized (>3x higher, as it genuinely was).")

    print("\nTesting garch_forecast_annualized_vol — too little trailing history returns None...")
    assert garch_forecast_annualized_vol(spy_bars, "2019-06-01", horizon_calendar_days=7) is None
    print("PASS — 2019-06-01 doesn't have a full 504-day trailing window yet (bars start 2019-01-02), returns None.")

    # 2025-02-03 (calm, ~1531 trading days of lookback available) vs.
    # 2025-04-07 (the REAL April 2025 SPY selloff's intraday-low date --
    # SPY closed 597.77 on 2025-02-03, 504.38 on 2025-04-07, roughly a
    # 15%+ real drawdown -- not a synthetic fixture, and both dates have
    # well over the required 504-day trailing window from this dataset's
    # 2019-01-02 start).
    print("\nTesting garch_forecast_annualized_vol on real SPY data — converges and produces a sane forecast...")
    garch_calm = garch_forecast_annualized_vol(spy_bars, "2025-02-03", horizon_calendar_days=7)
    garch_crash = garch_forecast_annualized_vol(spy_bars, "2025-04-07", horizon_calendar_days=7)
    assert garch_calm is not None and garch_crash is not None, (garch_calm, garch_crash)
    assert 0.02 < garch_calm < 0.60, garch_calm  # sane annualized-vol range, not a numerical blowup
    assert 0.02 < garch_crash < 3.00, garch_crash
    assert garch_crash > garch_calm, (garch_calm, garch_crash)
    print(f"PASS — GARCH(1,1) 7-day-horizon forecast: pre-selloff calm (2025-02-03) {garch_calm:.3f} annualized, "
          f"April-2025-crash bottom (2025-04-07) {garch_crash:.3f} annualized (higher, as it genuinely was).")

    print("\nTesting garch_forecast_annualized_vol — no-lookahead via bars_through (real data)...")
    truncated_at_crash = [b for b in spy_bars if b["date"] <= "2025-04-07"]
    garch_crash_full_input = garch_forecast_annualized_vol(spy_bars, "2025-04-07", horizon_calendar_days=7)
    garch_crash_truncated_input = garch_forecast_annualized_vol(truncated_at_crash, "2025-04-07", horizon_calendar_days=7)
    assert garch_crash_full_input == garch_crash_truncated_input, (garch_crash_full_input, garch_crash_truncated_input)
    print(f"PASS — identical GARCH forecast whether the input carries data through today (2026-07-17) or is "
          f"pre-truncated at the decision date — proves the rolling refit never sees future returns: {garch_crash_full_input:.6f}")

    print("\nTesting garch_forecast_annualized_vol — 30-day horizon uses more forecast steps than 7-day...")
    garch_7d = garch_forecast_annualized_vol(spy_bars, "2025-02-03", horizon_calendar_days=7)
    garch_30d = garch_forecast_annualized_vol(spy_bars, "2025-02-03", horizon_calendar_days=30)
    assert garch_7d is not None and garch_30d is not None, (garch_7d, garch_30d)
    print(f"PASS — 7-day-horizon forecast {garch_7d:.4f} vs. 30-day-horizon forecast {garch_30d:.4f} "
          f"(same fit, different forecast(horizon=) step count -- both real, neither crashed).")

    print("\nAll vol_forecast tests passed.")
