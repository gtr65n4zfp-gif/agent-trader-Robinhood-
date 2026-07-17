"""
agents/forecast_seat.py — the forecast seat (see
docs/superpowers/specs/2026-07-16-forecast-seat-design.md).

Domain-isolated like every other seat: only ever touches already-computed
features (research.forecast_model.predict()'s inputs) and an already-fit
model dict -- never fetches data, never fits a model itself. Same
bullish/bearish/neutral + confidence shape as agents.technicals.build_view(),
so judge-style conjunctive gates can consume it identically.

Two distinct "missing" cases, handled differently on purpose:
- Missing FEATURES (insufficient warm-up history, same condition
  backtest.data.technicals_as_of() already detects and returns None for)
  is a normal data gap — falls through to a neutral, zero-confidence
  view, never guessed.
- A missing MODEL is a caller-wiring bug, not a data gap (it means
  research/forecast_model_params.json was never loaded) -- raises
  instead of silently defaulting, so a wiring mistake fails loudly
  rather than quietly reporting a fake neutral stance forever.
"""

from __future__ import annotations

from research import forecast_model


def build_view(symbol: str, features: dict | None, model: dict) -> dict:
    """
    features: dict with {pct_from_ema, rsi, atr_pct, recent_5d_return}, or
    None if there wasn't enough warm-up history yet. NOTE: pct_from_ema must
    be derived by the caller as (price - ema) / ema from backtest.data.
    technicals_as_of()'s bundle; the other three keys (rsi, atr_pct,
    recent_5d_return) are direct bundle keys.
    model: a fitted model dict (research.forecast_model.load_model()
    output) -- required, never None.

    Returns {seat, symbol, stance, confidence, reasons}.
    """
    if model is None:
        raise ValueError(
            f"{symbol}: no forecast model supplied -- caller must "
            "research.forecast_model.load_model() first"
        )

    symbol = symbol.upper()

    if features is None:
        return {
            "seat": "forecast", "symbol": symbol, "stance": "neutral",
            "confidence": 0.0, "reasons": ["insufficient warm-up history"],
        }

    predicted_return = forecast_model.predict(features, model)
    typical = model["typical_abs_return"]
    band = 0.25 * typical

    if predicted_return > band:
        stance = "bullish"
    elif predicted_return < -band:
        stance = "bearish"
    else:
        stance = "neutral"

    confidence = min(1.0, abs(predicted_return) / typical) if typical > 0 else 0.0

    return {
        "seat": "forecast", "symbol": symbol, "stance": stance,
        "confidence": round(confidence, 4),
        "reasons": [f"model predicts {predicted_return * 100:+.2f}% return"],
    }


if __name__ == "__main__":
    model = {
        "intercept": 0.0,
        "coefficients": {"pct_from_ema": 1.0, "rsi": 0.0, "atr_pct": 0.0, "recent_5d_return": 0.0},
        "typical_abs_return": 0.02,
    }

    print("Testing build_view -- confident bullish prediction...")
    bullish_features = {"pct_from_ema": 0.05, "rsi": 50.0, "atr_pct": 0.01, "recent_5d_return": 0.0}
    view = build_view("SPY", bullish_features, model)
    assert view["stance"] == "bullish", view
    assert view["confidence"] == 1.0, view  # |0.05| / 0.02 capped at 1.0
    print(f"PASS — large predicted return -> bullish, confidence capped at 1.0: {view}")

    print("\nTesting build_view -- confident bearish prediction...")
    bearish_features = {"pct_from_ema": -0.05, "rsi": 50.0, "atr_pct": 0.01, "recent_5d_return": 0.0}
    view = build_view("SPY", bearish_features, model)
    assert view["stance"] == "bearish", view
    print(f"PASS — large negative predicted return -> bearish: {view}")

    print("\nTesting build_view -- prediction inside the neutral band...")
    neutral_features = {"pct_from_ema": 0.001, "rsi": 50.0, "atr_pct": 0.01, "recent_5d_return": 0.0}
    view = build_view("SPY", neutral_features, model)
    assert view["stance"] == "neutral", view
    print(f"PASS — small predicted return within +/-0.25x typical -> neutral: {view}")

    print("\nTesting build_view -- missing features (insufficient warm-up)...")
    view = build_view("SPY", None, model)
    assert view["stance"] == "neutral" and view["confidence"] == 0.0, view
    print(f"PASS — no features -> neutral, zero-confidence, not a guess: {view}")

    print("\nTesting build_view -- missing model raises, doesn't default...")
    try:
        build_view("SPY", bullish_features, None)
        raise AssertionError("should have raised ValueError")
    except ValueError as e:
        print(f"PASS — raised clearly: {e}")
