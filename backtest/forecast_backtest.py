"""
backtest/forecast_backtest.py — validation harness and SPY decision
wrapper for the forecast seat (see
docs/superpowers/specs/2026-07-16-forecast-seat-design.md).

Two independent pieces in this file:
1. The validation harness (chronological_split, naive_drift_direction,
   evaluate_model, evaluate_naive_baseline, run_validation) -- decides
   whether research.forecast_model's OLS baseline beats a naive-drift
   baseline on held-out data. This is the promotion gate: nothing past
   this point is meaningful unless a model clears it.
2. spy_forecast_decision() -- the SPY-only decision wrapper, mirroring
   backtest.options_engine.technicals_only_decision(), requiring
   Technicals AND Forecast to agree. Built regardless of what a specific
   fitted model's validation shows (it's just plumbing), but only
   meaningful to actually USE once a real model has cleared the gate
   above -- see the design doc's "Promotion gate" section.

Agent-mediated like every other backtest module here: this file doesn't
fetch SPY bars or call any MCP tool itself -- whatever real historical
run drives this passes already-fetched rows/targets in.
"""

from __future__ import annotations

from agents import judge
from research import forecast_model

from . import metrics as equity_metrics


def chronological_split(rows: list[dict], targets: list[float], train_frac: float = 0.75):
    """
    Splits by COUNT, not by any date field -- the first `train_frac`
    fraction of rows (in the order given; callers must already have them
    chronologically sorted, same convention backtest/data.py's
    bars_through() relies on) become train, the remainder become test.
    Never shuffles -- shuffling would leak future rows into training
    through overlapping-window features (recent_5d_return, EMA, etc. all
    depend on nearby prior rows).

    Returns ((train_rows, train_targets), (test_rows, test_targets)).
    Raises ValueError if train_frac produces an empty train or test set --
    fails loud rather than silently evaluating against zero rows.
    """
    n = len(rows)
    if n != len(targets):
        raise ValueError(f"rows ({n}) and targets ({len(targets)}) must be the same length")
    split_idx = int(n * train_frac)
    if split_idx <= 0 or split_idx >= n:
        raise ValueError(f"train_frac={train_frac} produces an empty train or test set for n={n} rows")
    return (
        (rows[:split_idx], targets[:split_idx]),
        (rows[split_idx:], targets[split_idx:]),
    )


def naive_drift_direction(train_targets: list[float]) -> str:
    """The naive baseline's single, fixed prediction for every test row:
    whichever direction the TRAINING targets averaged toward. Computed
    once from train_targets only -- never recomputed from test data,
    same discipline as research.forecast_model.fit()'s typical_abs_return."""
    avg = sum(train_targets) / len(train_targets)
    return "bullish" if avg >= 0 else "bearish"


def _actual_direction(value: float) -> str:
    return "bullish" if value >= 0 else "bearish"


def evaluate_model(model: dict, test_rows: list[dict], test_targets: list[float]) -> dict:
    """Directional accuracy + 95% Wilson CI + MAE/RMSE for a fitted model
    against a held-out test set. Never touches training data -- the model
    passed in must already be fit."""
    n = len(test_rows)
    correct = 0
    abs_errors = []
    sq_errors = []
    for row, actual in zip(test_rows, test_targets):
        predicted = forecast_model.predict(row, model)
        if _actual_direction(predicted) == _actual_direction(actual):
            correct += 1
        abs_errors.append(abs(predicted - actual))
        sq_errors.append((predicted - actual) ** 2)
    return {
        "n": n,
        "correct": correct,
        "accuracy": round(correct / n, 4),
        "accuracy_ci_95": equity_metrics.wilson_ci(correct, n),
        "mae": round(sum(abs_errors) / n, 6),
        "rmse": round((sum(sq_errors) / n) ** 0.5, 6),
    }


def evaluate_naive_baseline(train_targets: list[float], test_targets: list[float]) -> dict:
    """Same metrics as evaluate_model(), but for the fixed naive-drift
    prediction (see naive_drift_direction()) instead of a fitted model."""
    direction = naive_drift_direction(train_targets)
    n = len(test_targets)
    correct = sum(1 for t in test_targets if _actual_direction(t) == direction)
    return {
        "n": n,
        "correct": correct,
        "accuracy": round(correct / n, 4),
        "accuracy_ci_95": equity_metrics.wilson_ci(correct, n),
        "predicted_direction": direction,
    }


def run_validation(rows: list[dict], targets: list[float], train_frac: float = 0.75) -> dict:
    """
    The full harness: split, fit on train only, evaluate on test only,
    compare against the naive-drift baseline computed from that same
    train split. rows/targets must already be in chronological order --
    this function does not sort them (see chronological_split()).

    `beats_baseline` is the promotion gate from the design doc: True only
    if the model's accuracy CI lower bound is ABOVE the baseline's point
    accuracy -- "clearly separated," not just nominally higher.
    """
    (train_rows, train_targets), (test_rows, test_targets) = chronological_split(rows, targets, train_frac)
    model = forecast_model.fit(train_rows, train_targets)
    model_eval = evaluate_model(model, test_rows, test_targets)
    baseline_eval = evaluate_naive_baseline(train_targets, test_targets)
    return {
        "model": model,
        "model_eval": model_eval,
        "baseline_eval": baseline_eval,
        "beats_baseline": model_eval["accuracy_ci_95"][0] > baseline_eval["accuracy"],
    }


if __name__ == "__main__":
    print("Testing chronological_split...")
    rows = [{"i": i} for i in range(20)]
    targets = [float(i) for i in range(20)]
    (train_rows, train_targets), (test_rows, test_targets) = chronological_split(rows, targets, train_frac=0.75)
    assert len(train_rows) == 15 and len(test_rows) == 5, (len(train_rows), len(test_rows))
    assert train_rows[0]["i"] == 0 and train_rows[-1]["i"] == 14, train_rows
    assert test_rows[0]["i"] == 15 and test_rows[-1]["i"] == 19, test_rows
    print("PASS — 20 rows split 15 train / 5 test, in order, no shuffling.")

    print("\nTesting chronological_split rejects a split producing an empty set...")
    try:
        chronological_split(rows[:2], targets[:2], train_frac=1.0)
        raise AssertionError("should have raised ValueError")
    except ValueError as e:
        print(f"PASS — raised clearly: {e}")

    print("\nTesting naive_drift_direction...")
    assert naive_drift_direction([0.01, 0.02, -0.005]) == "bullish"
    assert naive_drift_direction([-0.01, -0.02, 0.005]) == "bearish"
    print("PASS — direction follows the sign of the training targets' average.")

    print("\nTesting evaluate_model on a known example...")
    eval_model = {
        "intercept": 0.0,
        "coefficients": {"pct_from_ema": 1.0, "rsi": 0.0, "atr_pct": 0.0, "recent_5d_return": 0.0},
        "typical_abs_return": 0.01,
    }
    eval_test_rows = [
        {"pct_from_ema": 0.01, "rsi": 50.0, "atr_pct": 0.01, "recent_5d_return": 0.0},   # predicts +0.01
        {"pct_from_ema": -0.01, "rsi": 50.0, "atr_pct": 0.01, "recent_5d_return": 0.0},  # predicts -0.01
        {"pct_from_ema": 0.02, "rsi": 50.0, "atr_pct": 0.01, "recent_5d_return": 0.0},   # predicts +0.02
    ]
    eval_test_targets = [0.02, -0.02, -0.01]  # actual directions: bullish, bearish, bearish
    model_eval = evaluate_model(eval_model, eval_test_rows, eval_test_targets)
    # row0: predicted +0.01 (bullish) vs actual +0.02 (bullish) -> correct
    # row1: predicted -0.01 (bearish) vs actual -0.02 (bearish) -> correct
    # row2: predicted +0.02 (bullish) vs actual -0.01 (bearish) -> WRONG
    assert model_eval["n"] == 3 and model_eval["correct"] == 2, model_eval
    assert model_eval["accuracy"] == 0.6667, model_eval
    print(f"PASS — 2/3 correct direction, MAE/RMSE computed: {model_eval}")

    print("\nTesting evaluate_naive_baseline on the same example...")
    eval_train_targets = [0.01, 0.02, -0.01]  # avg positive -> naive always predicts "bullish"
    baseline_eval = evaluate_naive_baseline(eval_train_targets, eval_test_targets)
    # naive predicts "bullish" for all 3 test rows:
    # row0 actual bullish -> correct; row1 actual bearish -> wrong; row2 actual bearish -> wrong
    assert baseline_eval["predicted_direction"] == "bullish", baseline_eval
    assert baseline_eval["n"] == 3 and baseline_eval["correct"] == 1, baseline_eval
    print(f"PASS — naive baseline predicts bullish for every row, 1/3 correct here: {baseline_eval}")

    print("\nTesting run_validation end-to-end on a synthetic planted relationship...")
    # 40 rows so a 0.75 split gives 30 train / 10 test -- enough for fit() to
    # need >= 5 rows (4 features + intercept) comfortably on both sides.
    synth_rows = []
    synth_targets = []
    for i in range(40):
        pct_from_ema = 0.001 * (i - 20)  # ranges from -0.02 to +0.019, monotonically increasing
        row = {"pct_from_ema": pct_from_ema, "rsi": 50.0, "atr_pct": 0.01, "recent_5d_return": 0.0}
        synth_rows.append(row)
        synth_targets.append(2.0 * pct_from_ema)  # noise-free planted relationship

    result = run_validation(synth_rows, synth_targets, train_frac=0.75)
    assert result["model_eval"]["n"] == 10, result["model_eval"]
    assert result["model_eval"]["accuracy"] == 1.0, result["model_eval"]  # noise-free, perfectly separable by sign
    assert result["baseline_eval"]["accuracy"] == 0.0, result["baseline_eval"]  # train skews negative, test is all-positive
    assert result["beats_baseline"] is True, result
    print(
        "PASS — noise-free planted relationship recovered and beats the naive baseline: "
        f"model accuracy={result['model_eval']['accuracy']}, "
        f"baseline accuracy={result['baseline_eval']['accuracy']}"
    )
