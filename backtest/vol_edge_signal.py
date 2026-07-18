"""
backtest/vol_edge_signal.py — Level 1 (see agents/SPY_OPTIONS_DESIGN.md):
combines Level 0's vol forecast against horizon-matched market-implied
vol (VIX/VIX9D/VIX3M) into an IV-edge, then combines that with the
UNCHANGED technicals+regime directional tilt
(backtest/options_engine.technicals_only_decision(), reused here, never
forked) to decide a structure and side. Regime can only tighten, never
loosen — same "windshield" principle agents/regime.py documents; a
non-tradeable regime forces no-trade regardless of how the vol edge
looks. No-trade is the default outcome, not the exception.
"""

from __future__ import annotations

from . import options_engine, vix_data

# Materiality floor: forecast_RV and market_implied_vol must differ by at
# least this many annualized percentage points before the edge counts as
# tradeable — a stated policy choice, not derived from data, same caveat
# class as execution/config.py's MIN_VOL_SCALAR. Avoids trading on a gap
# too small to plausibly survive the cost model in Task 5.
MIN_EDGE_PCT = 0.02

# Each index's own nominal constant-maturity horizon, in calendar days —
# used only to pick which index is the closest match to a track's ACTUAL
# resolved days-to-expiration (not just its nominal 7/30/45-day label;
# options_data.select_liquid_expiration()'s liquid-Friday snap can push
# the real DTE meaningfully away from the nominal target).
_INDEX_NOMINAL_DTE = {"VIX9D": 9, "VIX": 30, "VIX3M": 91}


def market_implied_vol(vix_bars: list[dict], vix9d_bars: list[dict], vix3m_bars: list[dict],
                        as_of: str, actual_dte_days: int) -> tuple[float | None, str]:
    """
    Horizon-matched market-implied vol, point-in-time (see
    agents/SPY_OPTIONS_DESIGN.md's Level 1 "IV-edge" section). Picks
    whichever of VIX9D/VIX/VIX3M has a nominal maturity closest to
    `actual_dte_days` — the REAL resolved days-to-expiration of the
    signal's contract, not the track's nominal label — then reads that
    index's own value as of `as_of` via vix_data.value_as_of() (already
    no-lookahead safe).

    Returns (value, which_index) so the choice is auditable in logged
    output, not a silent internal decision. value is a FRACTION (e.g.
    0.1877 for a VIX close of 18.77) — CBOE quotes these indexes in
    percentage points, but backtest/vol_forecast.py's engines return
    fractions, so the /100 conversion happens here, once, at the one
    seam these two scales meet — iv_edge() below assumes both its
    arguments are already on the same fractional scale and does not
    re-check or re-convert. value is None if the chosen index has no
    data yet as of that date (VIX9D starts 2011-01-04, VIX3M starts
    2009-09-18) — caller skips this signal, never substitutes a
    different index silently.
    """
    which = min(_INDEX_NOMINAL_DTE, key=lambda idx: abs(_INDEX_NOMINAL_DTE[idx] - actual_dte_days))
    bars_by_index = {"VIX9D": vix9d_bars, "VIX": vix_bars, "VIX3M": vix3m_bars}
    raw_points = vix_data.value_as_of(bars_by_index[which], as_of)
    value = raw_points / 100 if raw_points is not None else None
    return value, which


def iv_edge(forecast_rv: float, implied_vol: float) -> float:
    """forecast_RV - implied_vol, both already-annualized fractions on
    the same trading-day-annualized scale (see backtest/vol_forecast.py's
    own day-count-convention docstring). Positive: forecast vol reads
    ABOVE what's priced in — premium looks cheap. Negative: forecast vol
    reads BELOW what's priced in — premium looks rich."""
    return forecast_rv - implied_vol


def premium_signal(edge: float) -> str | None:
    """"cheap" (favor buying premium), "rich" (favor a defined-risk
    credit structure), or None if `edge` doesn't clear MIN_EDGE_PCT
    either way — no signal, not a weak signal."""
    if edge >= MIN_EDGE_PCT:
        return "cheap"
    if edge <= -MIN_EDGE_PCT:
        return "rich"
    return None


def vol_edge_decision(technicals: dict, regime: dict, edge: float | None) -> dict:
    """
    Combines the vol-edge premium signal with the UNCHANGED
    technicals+regime tilt to pick a structure and side. No-trade is the
    default: a non-tradeable regime, a technicals stance below
    judge.CONFIDENCE_THRESHOLD, or an edge inside the materiality band
    all independently force a hold — any one of them is enough, none of
    them can be overridden by the other two looking favorable.

    Returns:
        {"action": "hold", "reason": str}
      or
        {"action": "trade", "structure": "debit"|"credit", "direction":
         "bullish"|"bearish", "option_type": "call"|"put", "edge": float,
         "tilt_rationale": str}

    "option_type" already encodes the credit-spread convention from the
    design doc: bullish+rich -> bull put spread (sells puts); bearish+rich
    -> bear call spread (sells calls); bullish+cheap -> buy a call;
    bearish+cheap -> buy a put. The caller (Task 4/5) resolves the actual
    strikes/legs from this.
    """
    tilt = options_engine.technicals_only_decision(technicals, regime)
    if tilt["action"] == "hold":
        return {"action": "hold", "reason": tilt["rationale"]}

    if edge is None:
        return {"action": "hold", "reason": "no market-implied-vol reading available for this date/track"}
    premium = premium_signal(edge)
    if premium is None:
        return {"action": "hold", "reason": f"vol edge ({edge:+.4f}) inside the ±{MIN_EDGE_PCT:.2%} materiality band"}

    direction = "bullish" if tilt["action"] == "buy" else "bearish"
    if premium == "cheap":
        structure = "debit"
        option_type = "call" if direction == "bullish" else "put"
    else:  # rich
        structure = "credit"
        option_type = "put" if direction == "bullish" else "call"  # bull put spread / bear call spread

    return {
        "action": "trade", "structure": structure, "direction": direction,
        "option_type": option_type, "edge": edge, "premium": premium,
        "tilt_rationale": tilt["rationale"],
    }


if __name__ == "__main__":
    import json

    print("Testing iv_edge...")
    e = iv_edge(0.30, 0.20)
    assert abs(e - 0.10) < 1e-12, e
    print(f"PASS — forecast 30% vs implied 20% -> edge +0.10 (forecast above implied, premium looks cheap): {e}")

    e2 = iv_edge(0.15, 0.25)
    assert abs(e2 - (-0.10)) < 1e-12, e2
    print(f"PASS — forecast 15% vs implied 25% -> edge -0.10 (forecast below implied, premium looks rich): {e2}")

    print("\nTesting premium_signal...")
    assert premium_signal(0.05) == "cheap"
    assert premium_signal(-0.05) == "rich"
    assert premium_signal(0.01) is None  # inside the +/-2% materiality band
    assert premium_signal(-0.01) is None
    assert premium_signal(MIN_EDGE_PCT) == "cheap"  # exactly at the boundary counts
    assert premium_signal(-MIN_EDGE_PCT) == "rich"
    print("PASS — cheap/rich past the materiality band, None inside it (boundary itself counts).")

    print("\nTesting market_implied_vol — picks the closest-maturity index...")
    SCRATCH = "/private/tmp/claude-501/-Users-ethandungo-agent-trader/f77a7381-786c-45b3-8f03-7b93713c619c/scratchpad"
    with open(f"{SCRATCH}/VIX_History.csv") as f:
        vix_bars = vix_data.parse_cboe_csv(f.read())
    with open(f"{SCRATCH}/VIX9D_History.csv") as f:
        vix9d_bars = vix_data.parse_cboe_csv(f.read())
    with open(f"{SCRATCH}/VIX3M_History.csv") as f:
        vix3m_bars = vix_data.parse_cboe_csv(f.read())

    v, which = market_implied_vol(vix_bars, vix9d_bars, vix3m_bars, "2025-04-07", actual_dte_days=8)
    assert which == "VIX9D", which
    assert v is not None and v > 0, v
    print(f"PASS — an 8-day actual DTE picks VIX9D (nominal 9d), reads {v} on 2025-04-07.")

    v2, which2 = market_implied_vol(vix_bars, vix9d_bars, vix3m_bars, "2025-04-07", actual_dte_days=33)
    assert which2 == "VIX", which2
    print(f"PASS — a 33-day actual DTE picks VIX (nominal 30d), reads {v2} on 2025-04-07.")

    v3, which3 = market_implied_vol(vix_bars, vix9d_bars, vix3m_bars, "2025-04-07", actual_dte_days=70)
    assert which3 == "VIX3M", which3
    print(f"PASS — a 70-day actual DTE picks VIX3M (nominal 91d, closer than VIX's 30d), reads {v3} on 2025-04-07.")

    print("\nTesting market_implied_vol — None before an index's own inception, not a guess...")
    v4, which4 = market_implied_vol(vix_bars, vix9d_bars, vix3m_bars, "2005-01-03", actual_dte_days=8)
    assert which4 == "VIX9D" and v4 is None, (which4, v4)  # VIX9D doesn't start until 2011
    print(f"PASS — VIX9D as of 2005-01-03 (before its 2011-01-04 inception) is None, not fabricated.")

    print("\nTesting vol_edge_decision — real technicals+regime, real vol edge, end to end...")
    from datetime import datetime
    from agents import regime as agents_regime
    from agents import technicals as agents_technicals
    from execution import config
    from . import data as backtest_data
    from . import vol_forecast

    with open(f"{SCRATCH}/spy_bars_2019_2026.json") as f:
        spy_bars = json.load(f)

    as_of = "2025-04-07"  # the real April 2025 SPY selloff bottom
    ind = backtest_data.technicals_as_of("SPY", as_of, spy_bars, config.REGIME_EMA_LOOKBACK_DAYS)
    assert ind is not None, "expected real indicators on a real trading day"
    tech = agents_technicals.build_view("SPY", ind["price"], ema=ind["ema"], rsi=ind["rsi"], atr_pct=ind["atr_pct"])
    reg = agents_regime.regime_stance("SPY", ind["price"], ema=ind["regime_ema"], atr_pct=ind["atr_pct"])
    print(f"  real technicals on {as_of}: stance={tech['stance']} confidence={tech['confidence']}")
    print(f"  real regime on {as_of}: state={reg['state']} tradeable={reg['tradeable']}")

    forecast_rv = vol_forecast.baseline_forecast_annualized_vol(spy_bars, as_of, lookback_days=20)
    implied, which_index = market_implied_vol(vix_bars, vix9d_bars, vix3m_bars, as_of, actual_dte_days=7)
    assert forecast_rv is not None and implied is not None
    edge = iv_edge(forecast_rv, implied)
    print(f"  forecast_RV (20d trailing baseline): {forecast_rv:.4f}  vs.  implied ({which_index}): {implied:.4f}  "
          f"->  edge: {edge:.4f}")

    decision = vol_edge_decision(tech, reg, edge)
    assert decision["action"] in ("hold", "trade"), decision
    print(f"PASS — end-to-end decision on the real crash date, real inputs throughout, no fabrication: {decision}")

    print("\nTesting vol_edge_decision — non-tradeable regime forces hold regardless of vol edge...")
    non_tradeable_regime = {
        "seat": "regime", "symbol": "SPY", "state": "ranging",
        "volatility": "normal", "trend": "sideways", "tradeable": False,
        "reason": "no directional edge, sitting out",
    }
    bullish_technicals = {
        "seat": "technicals", "symbol": "SPY", "stance": "bullish",
        "confidence": 0.7, "reasons": ["strong signal"],
    }
    forced_hold = vol_edge_decision(bullish_technicals, non_tradeable_regime, edge=0.10)  # a large, clearly-tradeable edge
    assert forced_hold["action"] == "hold", forced_hold
    print(f"PASS — non-tradeable regime forces hold even with a strongly bullish tilt and a large vol edge: {forced_hold}")

    print("\nTesting vol_edge_decision — cheap premium + bullish -> debit call...")
    tradeable_regime = {
        "seat": "regime", "symbol": "SPY", "state": "trending",
        "volatility": "normal", "trend": "up", "tradeable": True,
        "reason": "normal volatility, clear up trend",
    }
    cheap_bullish = vol_edge_decision(bullish_technicals, tradeable_regime, edge=0.10)
    assert cheap_bullish == {
        "action": "trade", "structure": "debit", "direction": "bullish", "option_type": "call",
        "edge": 0.10, "premium": "cheap", "tilt_rationale": cheap_bullish["tilt_rationale"],
    }, cheap_bullish
    print(f"PASS — cheap premium + bullish tilt -> debit call: {cheap_bullish}")

    print("\nTesting vol_edge_decision — rich premium + bullish -> credit put (bull put spread)...")
    rich_bullish = vol_edge_decision(bullish_technicals, tradeable_regime, edge=-0.10)
    assert rich_bullish["structure"] == "credit" and rich_bullish["option_type"] == "put", rich_bullish
    print(f"PASS — rich premium + bullish tilt -> credit spread using PUTS (bull put spread): {rich_bullish}")

    bearish_technicals = {
        "seat": "technicals", "symbol": "SPY", "stance": "bearish",
        "confidence": 0.7, "reasons": ["strong bearish signal"],
    }
    print("\nTesting vol_edge_decision — rich premium + bearish -> credit call (bear call spread)...")
    rich_bearish = vol_edge_decision(bearish_technicals, tradeable_regime, edge=-0.10)
    assert rich_bearish["structure"] == "credit" and rich_bearish["option_type"] == "call", rich_bearish
    print(f"PASS — rich premium + bearish tilt -> credit spread using CALLS (bear call spread): {rich_bearish}")

    print("\nAll vol_edge_signal tests passed.")
