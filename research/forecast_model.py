"""
research/forecast_model.py -- the SPY forecast seat's statistical model
(see docs/superpowers/specs/2026-07-16-forecast-seat-design.md).

Plain OLS linear regression, closed-form via numpy.linalg.lstsq -- no
iterative fitting, no hyperparameters, nothing that can silently fail to
converge. Deliberately a small, fixed feature set (see FEATURE_KEYS)
relative to the available training rows, so this is testing whether
there's ANY real forward-return signal in well-established
momentum/mean-reversion/volatility features, not fitting noise.

Fit once per horizon on a training split (backtest/forecast_backtest.py
owns that split), coefficients committed to a params file via
save_model() -- never refit live on every decision call, same
"committed, not recomputed" convention execution/config.py's own tuned
constants already follow.
"""

from __future__ import annotations

import json

import numpy as np

FEATURE_KEYS = ["pct_from_ema", "rsi", "atr_pct", "recent_5d_return"]


def fit(rows: list[dict], targets: list[float]) -> dict:
    """
    rows: feature dicts, each with every key in FEATURE_KEYS present (see
    backtest.data.technicals_as_of()'s bundle -- this function doesn't
    compute features itself, only fits against already-computed ones).
    targets: forward return for each row, same length and order as rows.

    Returns a model dict: {"intercept", "coefficients": {feature: weight},
    "typical_abs_return"} -- typical_abs_return is the median absolute
    value in `targets` (the TRAINING targets passed in here, nothing
    else), used later by agents.forecast_seat to scale confidence and
    set the neutral band. Never recomputed from test-period data by
    design: whatever's passed to `targets` here IS what typical_abs_return
    is derived from.
    """
    if len(rows) != len(targets):
        raise ValueError(f"rows ({len(rows)}) and targets ({len(targets)}) must be the same length")
    if len(rows) < len(FEATURE_KEYS) + 1:
        raise ValueError(
            f"need at least {len(FEATURE_KEYS) + 1} rows to fit {len(FEATURE_KEYS)} "
            f"features + intercept, got {len(rows)}"
        )

    X = np.array([[1.0] + [row[k] for k in FEATURE_KEYS] for row in rows])
    y = np.array(targets, dtype=float)
    coeffs, *_ = np.linalg.lstsq(X, y, rcond=None)
    typical_abs_return = float(np.median(np.abs(y)))

    return {
        "intercept": float(coeffs[0]),
        "coefficients": {k: float(c) for k, c in zip(FEATURE_KEYS, coeffs[1:])},
        "typical_abs_return": typical_abs_return,
    }


def predict(features: dict, model: dict) -> float:
    """Predicted forward return for one row of features, given a fitted
    model dict (fit() output, or load_model() output)."""
    total = model["intercept"]
    for k in FEATURE_KEYS:
        total += model["coefficients"][k] * features[k]
    return total


def save_model(model: dict, path: str) -> None:
    """Commits a fitted model to disk as plain JSON -- see module
    docstring for why this is a deliberate, manual step, not something
    agents.forecast_seat ever calls itself."""
    with open(path, "w") as f:
        json.dump(model, f, indent=2)


def load_model(path: str) -> dict:
    """Loads a previously-fit model dict from disk (save_model()'s
    output)."""
    with open(path) as f:
        return json.load(f)


if __name__ == "__main__":
    import os
    import tempfile

    print("Testing fit() recovers a known, noise-free linear relationship...")
    rows = []
    targets = []
    for i in range(20):
        row = {
            "pct_from_ema": 0.01 * i,
            "rsi": 50.0,
            "atr_pct": 0.01,
            "recent_5d_return": 0.0,
        }
        # Planted relationship: target = 0.5 * pct_from_ema, no noise, no
        # dependence on the other three features (their coefficients
        # should come out ~0).
        target = 0.5 * row["pct_from_ema"]
        rows.append(row)
        targets.append(target)

    model = fit(rows, targets)
    assert abs(model["coefficients"]["pct_from_ema"] - 0.5) < 1e-6, model
    assert abs(model["coefficients"]["rsi"]) < 1e-6, model
    assert abs(model["coefficients"]["atr_pct"]) < 1e-6, model
    assert abs(model["coefficients"]["recent_5d_return"]) < 1e-6, model
    assert abs(model["intercept"]) < 1e-6, model
    print(f"PASS -- recovered pct_from_ema coefficient 0.5 exactly, others ~0: {model['coefficients']}")

    print("\nTesting predict() against the fitted model...")
    pred = predict({"pct_from_ema": 0.04, "rsi": 50.0, "atr_pct": 0.01, "recent_5d_return": 0.0}, model)
    assert abs(pred - 0.02) < 1e-6, pred  # 0.5 * 0.04 == 0.02
    print(f"PASS -- predict() matches the planted relationship: {pred:.6f}")

    print("\nTesting fit() rejects mismatched rows/targets lengths...")
    try:
        fit(rows, targets[:-1])
        raise AssertionError("should have raised ValueError")
    except ValueError as e:
        print(f"PASS -- raised clearly: {e}")

    print("\nTesting save_model()/load_model() round-trip...")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "model.json")
        save_model(model, path)
        loaded = load_model(path)
        assert loaded == model, (loaded, model)
        pred2 = predict({"pct_from_ema": 0.04, "rsi": 50.0, "atr_pct": 0.01, "recent_5d_return": 0.0}, loaded)
        assert abs(pred2 - pred) < 1e-9, (pred2, pred)
    print("PASS -- model survives a save/load round-trip and predicts identically.")
