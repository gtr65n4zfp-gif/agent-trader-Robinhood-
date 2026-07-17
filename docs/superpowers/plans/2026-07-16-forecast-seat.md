# SPY Forecast Seat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the statistical-baseline forecast seat and its validation harness for SPY, per `docs/superpowers/specs/2026-07-16-forecast-seat-design.md` — the code and its self-tests only, not a real-data run.

**Architecture:** A new `recent_5d_return` indicator feeds a plain OLS regression (`research/forecast_model.py`) fit once per horizon; its output is exposed through a seat-shaped function (`agents/forecast_seat.py`) matching `agents.technicals.build_view()`'s shape exactly; a validation harness (`backtest/forecast_backtest.py`) proves (on real data, in a later, separate session) whether the model beats a naive-drift baseline before a SPY-only decision wrapper in the same file is ever actually used.

**Tech Stack:** Python 3, `numpy` (new dependency — the project's only other one is `requests`).

## Global Constraints

- New dependency is `numpy` only — not scikit-learn, not any deep-learning library.
- SPY only, standalone — nothing in this plan touches `agents/judge.py`, `execution/paper_broker.py`, or any live/automation path.
- Model fit once per horizon; coefficients committed to `research/forecast_model_params.json` — never refit live on a decision call.
- Chronological train/test split: the first 75% of rows **by count** are train, the remaining 25% are test — decided before fitting, never moved based on results. No shuffling.
- Promotion gate: a model only counts as validated if its directional-accuracy CI **lower bound** exceeds the naive-drift baseline's point accuracy — not just nominally above 50%.
- Seat output shape matches every existing seat exactly: `{seat, symbol, stance, confidence, reasons}`.
- Missing **features** (insufficient warm-up) → neutral, zero-confidence (soft path, never guessed). Missing **model** → raise `ValueError` (hard failure — a caller-wiring bug, not a data gap).
- **This plan does not fit the model on real data.** `research/forecast_model_params.json` is never created by any task here with hand-picked or fabricated "real" numbers — every test uses synthetic data with a planted, known relationship. Actually fitting on real SPY history, running `run_validation()` for real, and deciding whether the model is promoted is a separate, later interactive session (same split this project already used for the options backtest: code + synthetic self-tests committed first, the real run happens after, by a human/agent actually driving it against live data).
- Every new module gets a `if __name__ == "__main__":` self-test block with `assert`/`print("PASS — ...")` — matching every existing module in this codebase (there is no pytest setup here; do not add one).

---

### Task 1: `recent_5d_return` indicator in `backtest/data.py`

**Files:**
- Modify: `backtest/data.py`

**Interfaces:**
- Produces: `recent_return_series(closes: list[float], period: int = 5) -> list[float | None]`. `technicals_as_of()`'s returned dict gains a new key, `"recent_5d_return": float`, alongside its existing `price`/`ema`/`rsi`/`atr_pct`/`regime_ema` keys.

- [ ] **Step 1: Add `recent_return_series()` and confirm it isn't there yet**

Run: `python3 -c "from backtest.data import recent_return_series"`
Expected: `ImportError: cannot import name 'recent_return_series'`

- [ ] **Step 2: Add the function**

In `backtest/data.py`, insert this function immediately after `atr_series()` (before the `# --- Council-ready bundle ---` comment):

```python
def recent_return_series(closes: list[float], period: int = 5) -> list[float | None]:
    """Trailing N-day simple return: (close[i] - close[i - period]) / close[i - period].
    None for every index before `period` prior closes exist -- same
    None-padding convention as ema_series()/rsi_series()/atr_series() above."""
    n = len(closes)
    out: list[float | None] = [None] * n
    for i in range(period, n):
        prev = closes[i - period]
        out[i] = (closes[i] - prev) / prev if prev else None
    return out
```

- [ ] **Step 3: Wire it into `technicals_as_of()`**

In `backtest/data.py`, find this block inside `technicals_as_of()`:

```python
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
```

Replace it with:

```python
    ema9 = ema_series(closes, 9)[-1]
    rsi14 = rsi_series(closes, 14)[-1]
    atr14 = atr_series(truncated, 14)[-1]
    regime_ema = ema_series(closes, regime_ema_period)[-1]
    recent_5d_return = recent_return_series(closes, 5)[-1]

    if None in (ema9, rsi14, atr14, regime_ema, recent_5d_return):
        return None  # not enough warm-up history yet for this date

    return {
        "price": price,
        "ema": ema9,
        "rsi": rsi14,
        "atr_pct": atr14 / price,
        "regime_ema": regime_ema,
        "recent_5d_return": recent_5d_return,
    }
```

- [ ] **Step 4: Add the self-test block**

`backtest/data.py` has no `__main__` block today — add one at the end of the file:

```python
if __name__ == "__main__":
    from datetime import date, timedelta

    print("Testing recent_return_series...")
    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 110.0, 111.0]
    returns = recent_return_series(closes, period=5)
    assert returns[:5] == [None, None, None, None, None], returns
    assert abs(returns[5] - (110.0 - 100.0) / 100.0) < 1e-9, returns
    assert abs(returns[6] - (111.0 - 101.0) / 101.0) < 1e-9, returns
    print(f"PASS -- 5-day trailing return computed correctly, None-padded before warm-up: {returns}")

    print("\nTesting technicals_as_of includes recent_5d_return...")
    start = date(2026, 1, 1)
    bars = []
    price = 100.0
    for i in range(30):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        price += 0.5
        bars.append({"date": d, "open": price, "high": price + 0.2, "low": price - 0.2, "close": price, "volume": 1000})
    as_of = bars[-1]["date"]
    result = technicals_as_of("SPY", as_of, bars, regime_ema_period=20)
    assert result is not None, "expected a full bundle once warm-up is satisfied"
    assert "recent_5d_return" in result, result
    assert result["recent_5d_return"] > 0, result  # steadily rising prices -> positive 5d return
    print(f"PASS -- technicals_as_of() bundle now includes recent_5d_return: {result['recent_5d_return']:.4f}")
```

- [ ] **Step 5: Run it and verify all PASS**

Run: `cd /Users/ethandungo/agent-trader && python3 -m backtest.data`
Expected: both `PASS` lines print, no `AssertionError`, exit code 0.

- [ ] **Step 6: Commit**

```bash
cd /Users/ethandungo/agent-trader
git add backtest/data.py
git commit -m "Add recent_5d_return indicator, feeds the forecast seat's model

Trailing 5-day return alongside the existing EMA/RSI/ATR/regime_ema
bundle in technicals_as_of() -- same no-lookahead truncation, additive
change, no existing consumer's behavior changes."
```

---

### Task 2: `numpy` dependency + `research/forecast_model.py`

**Files:**
- Modify: `requirements.txt`
- Create: `research/forecast_model.py`

**Interfaces:**
- Consumes: feature dicts shaped like `backtest.data.technicals_as_of()`'s bundle (specifically the four keys in `FEATURE_KEYS`).
- Produces: `FEATURE_KEYS: list[str]`, `fit(rows: list[dict], targets: list[float]) -> dict`, `predict(features: dict, model: dict) -> float`, `save_model(model: dict, path: str) -> None`, `load_model(path: str) -> dict`. The `model` dict shape: `{"intercept": float, "coefficients": {feature: float, ...}, "typical_abs_return": float}`.

- [ ] **Step 1: Add the `numpy` dependency**

In `requirements.txt`, add a line so the file reads:

```
requests
numpy
```

Run: `cd /Users/ethandungo/agent-trader && source .venv/bin/activate && pip install numpy`
Expected: numpy installs successfully.

Run: `python3 -c "import numpy; print(numpy.__version__)"`
Expected: prints a version number, exit code 0.

- [ ] **Step 2: Confirm the module doesn't exist yet**

Run: `python3 -c "from research import forecast_model"`
Expected: `ModuleNotFoundError: No module named 'research.forecast_model'`

- [ ] **Step 3: Create `research/forecast_model.py`**

```python
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
```

- [ ] **Step 4: Run it and verify all PASS**

Run: `cd /Users/ethandungo/agent-trader && python3 -m research.forecast_model`
Expected: four `PASS` lines print, no `AssertionError`, exit code 0.

- [ ] **Step 5: Commit**

```bash
cd /Users/ethandungo/agent-trader
git add requirements.txt research/forecast_model.py
git commit -m "Add research/forecast_model.py: OLS fit/predict for the forecast seat

Closed-form regression via numpy.linalg.lstsq, no hyperparameters.
Coefficients meant to be fit once and committed (save_model()), never
refit live. numpy is the project's first numeric dependency."
```

---

### Task 3: `agents/forecast_seat.py`

**Files:**
- Create: `agents/forecast_seat.py`

**Interfaces:**
- Consumes: `research.forecast_model.predict(features, model)`.
- Produces: `build_view(symbol: str, features: dict | None, model: dict) -> dict`, returning `{"seat": "forecast", "symbol": str, "stance": "bullish"|"bearish"|"neutral", "confidence": float, "reasons": list[str]}`.

- [ ] **Step 1: Confirm the module doesn't exist yet**

Run: `python3 -c "from agents import forecast_seat"`
Expected: `ModuleNotFoundError: No module named 'agents.forecast_seat'`

- [ ] **Step 2: Create `agents/forecast_seat.py`**

```python
"""
agents/forecast_seat.py -- the forecast seat (see
docs/superpowers/specs/2026-07-16-forecast-seat-design.md).

Domain-isolated like every other seat: only ever touches already-computed
features (research.forecast_model.predict()'s inputs) and an already-fit
model dict -- never fetches data, never fits a model itself. Same
bullish/bearish/neutral + confidence shape as agents.technicals.build_view(),
so judge-style conjunctive gates can consume it identically.

Two distinct "missing" cases, handled differently on purpose:
- Missing FEATURES (insufficient warm-up history, same condition
  backtest.data.technicals_as_of() already detects and returns None for)
  is a normal data gap -- falls through to a neutral, zero-confidence
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
    features: {pct_from_ema, rsi, atr_pct, recent_5d_return} (same keys
    research.forecast_model.FEATURE_KEYS expects) or None if there wasn't
    enough warm-up history yet for this date.
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
    print(f"PASS -- large predicted return -> bullish, confidence capped at 1.0: {view}")

    print("\nTesting build_view -- confident bearish prediction...")
    bearish_features = {"pct_from_ema": -0.05, "rsi": 50.0, "atr_pct": 0.01, "recent_5d_return": 0.0}
    view = build_view("SPY", bearish_features, model)
    assert view["stance"] == "bearish", view
    print(f"PASS -- large negative predicted return -> bearish: {view}")

    print("\nTesting build_view -- prediction inside the neutral band...")
    neutral_features = {"pct_from_ema": 0.001, "rsi": 50.0, "atr_pct": 0.01, "recent_5d_return": 0.0}
    view = build_view("SPY", neutral_features, model)
    assert view["stance"] == "neutral", view
    print(f"PASS -- small predicted return within +/-0.25x typical -> neutral: {view}")

    print("\nTesting build_view -- missing features (insufficient warm-up)...")
    view = build_view("SPY", None, model)
    assert view["stance"] == "neutral" and view["confidence"] == 0.0, view
    print(f"PASS -- no features -> neutral, zero-confidence, not a guess: {view}")

    print("\nTesting build_view -- missing model raises, doesn't default...")
    try:
        build_view("SPY", bullish_features, None)
        raise AssertionError("should have raised ValueError")
    except ValueError as e:
        print(f"PASS -- raised clearly: {e}")
```

- [ ] **Step 3: Run it and verify all PASS**

Run: `cd /Users/ethandungo/agent-trader && python3 -m agents.forecast_seat`
Expected: five `PASS` lines print, no `AssertionError`, exit code 0.

- [ ] **Step 4: Commit**

```bash
cd /Users/ethandungo/agent-trader
git add agents/forecast_seat.py
git commit -m "Add agents/forecast_seat.py: seat-shaped wrapper around the forecast model

Same {stance, confidence, reasons} shape as agents.technicals.build_view().
Missing features -> neutral (data gap); missing model -> raises
(caller-wiring bug) -- these are deliberately different failure modes."
```

---

### Task 4: `backtest/forecast_backtest.py` — validation harness

**Files:**
- Create: `backtest/forecast_backtest.py`

**Interfaces:**
- Consumes: `research.forecast_model.fit`, `research.forecast_model.predict`, `backtest.metrics.wilson_ci` (existing, reused unchanged).
- Produces: `chronological_split(rows, targets, train_frac=0.75) -> tuple`, `naive_drift_direction(train_targets: list[float]) -> str`, `evaluate_model(model, test_rows, test_targets) -> dict`, `evaluate_naive_baseline(train_targets, test_targets) -> dict`, `run_validation(rows, targets, train_frac=0.75) -> dict` (keys: `model`, `model_eval`, `baseline_eval`, `beats_baseline`).

- [ ] **Step 1: Confirm the module doesn't exist yet**

Run: `python3 -c "from backtest import forecast_backtest"`
Expected: `ModuleNotFoundError: No module named 'backtest.forecast_backtest'`

- [ ] **Step 2: Create `backtest/forecast_backtest.py` with the validation harness**

```python
"""
backtest/forecast_backtest.py -- validation harness and SPY decision
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
```

- [ ] **Step 3: Add the validation-harness self-tests**

Append to the end of `backtest/forecast_backtest.py` (this creates the file's `__main__` block; Task 5 appends more to it later):

```python
if __name__ == "__main__":
    print("Testing chronological_split...")
    rows = [{"i": i} for i in range(20)]
    targets = [float(i) for i in range(20)]
    (train_rows, train_targets), (test_rows, test_targets) = chronological_split(rows, targets, train_frac=0.75)
    assert len(train_rows) == 15 and len(test_rows) == 5, (len(train_rows), len(test_rows))
    assert train_rows[0]["i"] == 0 and train_rows[-1]["i"] == 14, train_rows
    assert test_rows[0]["i"] == 15 and test_rows[-1]["i"] == 19, test_rows
    print("PASS -- 20 rows split 15 train / 5 test, in order, no shuffling.")

    print("\nTesting chronological_split rejects a split producing an empty set...")
    try:
        chronological_split(rows[:2], targets[:2], train_frac=0.99)
        raise AssertionError("should have raised ValueError")
    except ValueError as e:
        print(f"PASS -- raised clearly: {e}")

    print("\nTesting naive_drift_direction...")
    assert naive_drift_direction([0.01, 0.02, -0.005]) == "bullish"
    assert naive_drift_direction([-0.01, -0.02, 0.005]) == "bearish"
    print("PASS -- direction follows the sign of the training targets' average.")

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
    print(f"PASS -- 2/3 correct direction, MAE/RMSE computed: {model_eval}")

    print("\nTesting evaluate_naive_baseline on the same example...")
    eval_train_targets = [0.01, 0.02, -0.01]  # avg positive -> naive always predicts "bullish"
    baseline_eval = evaluate_naive_baseline(eval_train_targets, eval_test_targets)
    # naive predicts "bullish" for all 3 test rows:
    # row0 actual bullish -> correct; row1 actual bearish -> wrong; row2 actual bearish -> wrong
    assert baseline_eval["predicted_direction"] == "bullish", baseline_eval
    assert baseline_eval["n"] == 3 and baseline_eval["correct"] == 1, baseline_eval
    print(f"PASS -- naive baseline predicts bullish for every row, 1/3 correct here: {baseline_eval}")

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
        "PASS -- noise-free planted relationship recovered and beats the naive baseline: "
        f"model accuracy={result['model_eval']['accuracy']}, "
        f"baseline accuracy={result['baseline_eval']['accuracy']}"
    )
```

- [ ] **Step 4: Run it and verify all PASS**

Run: `cd /Users/ethandungo/agent-trader && python3 -m backtest.forecast_backtest`
Expected: six `PASS` lines print, no `AssertionError`, exit code 0.

- [ ] **Step 5: Commit**

```bash
cd /Users/ethandungo/agent-trader
git add backtest/forecast_backtest.py
git commit -m "Add backtest/forecast_backtest.py: validation harness (promotion gate)

Chronological 75/25 split, naive-drift baseline, Wilson CI comparison.
beats_baseline is True only when the model's CI lower bound clears the
baseline's point accuracy -- not just nominally above 50%."
```

---

### Task 5: `spy_forecast_decision()` — SPY decision wrapper

**Files:**
- Modify: `backtest/forecast_backtest.py`

**Interfaces:**
- Consumes: `judge.CONFIDENCE_THRESHOLD` (existing, unchanged).
- Produces: `spy_forecast_decision(technicals: dict, forecast: dict, regime: dict, quantity: float = 1) -> dict`, same return shape as `judge.decide()`/`options_engine.technicals_only_decision()`.

- [ ] **Step 1: Confirm the function doesn't exist yet**

Run: `python3 -c "from backtest.forecast_backtest import spy_forecast_decision"`
Expected: `ImportError: cannot import name 'spy_forecast_decision'`

- [ ] **Step 2: Add `spy_forecast_decision()`**

In `backtest/forecast_backtest.py`, insert this function after `run_validation()` and before the `if __name__ == "__main__":` block:

```python
def spy_forecast_decision(technicals: dict, forecast: dict, regime: dict, quantity: float = 1) -> dict:
    """
    Same shape as judge.decide()'s return value, but mirrors
    backtest.options_engine.technicals_only_decision(): the gate requires
    Technicals AND Forecast (not Fundamentals -- still structurally empty
    for SPY, see agents/OPTIONS_BACKTEST_DESIGN.md's "Signal source")
    to agree on direction and both clear judge.CONFIDENCE_THRESHOLD.
    Regime gate is checked first, exactly as judge.decide() and
    technicals_only_decision() both do, and can only force a HOLD, never
    override one.

    Only meaningful to actually wire into anything once a real fitted
    model has cleared run_validation()'s promotion gate -- see this
    module's docstring and the design doc's "Promotion gate" section.
    Building this function doesn't imply that gate has been cleared.
    """
    symbol = technicals["symbol"]
    if regime["symbol"] != symbol or forecast["symbol"] != symbol:
        raise ValueError(
            f"seat symbol mismatch: regime={regime['symbol']!r}, "
            f"forecast={forecast['symbol']!r} vs {symbol!r}"
        )

    seat_inputs = {"technicals": technicals, "forecast": forecast, "regime": regime}

    if not regime["tradeable"]:
        return {
            "seat": "judge_forecast", "action": "hold", "symbol": symbol,
            "target_quantity": 0, "confidence": 0.0,
            "rationale": f"No-trade is the default: regime filter -- {regime['state']}: {regime['reason']}",
            "seat_inputs": seat_inputs,
        }

    t_stance, t_conf = technicals["stance"], technicals["confidence"]
    f_stance, f_conf = forecast["stance"], forecast["confidence"]

    aligned = t_stance == f_stance and t_stance in ("bullish", "bearish")
    both_confident = t_conf >= judge.CONFIDENCE_THRESHOLD and f_conf >= judge.CONFIDENCE_THRESHOLD

    if not (aligned and both_confident):
        return {
            "seat": "judge_forecast", "action": "hold", "symbol": symbol,
            "target_quantity": 0, "confidence": round(min(t_conf, f_conf), 4),
            "rationale": (
                f"No-trade is the default: technicals={t_stance}({t_conf}), "
                f"forecast={f_stance}({f_conf}) -- need agreement AND both "
                f">= {judge.CONFIDENCE_THRESHOLD}"
            ),
            "seat_inputs": seat_inputs,
        }

    action = "buy" if t_stance == "bullish" else "sell"
    return {
        "seat": "judge_forecast", "action": action, "symbol": symbol,
        "target_quantity": quantity, "confidence": round(min(t_conf, f_conf), 4),
        "rationale": (
            f"Technicals and Forecast both {t_stance} (confidence "
            f"{t_conf}/{f_conf}) -- clears {judge.CONFIDENCE_THRESHOLD}; "
            f"SPY has no usable Fundamentals leg."
        ),
        "seat_inputs": seat_inputs,
    }
```

- [ ] **Step 3: Add its self-tests**

In `backtest/forecast_backtest.py`, append to the existing `if __name__ == "__main__":` block (after the `run_validation` test's final `print`):

```python
    print("\nTesting spy_forecast_decision -- confident bullish agreement -> buy...")
    bullish_technicals = {"seat": "technicals", "symbol": "SPY", "stance": "bullish", "confidence": 0.6, "reasons": ["price above EMA"]}
    bullish_forecast = {"seat": "forecast", "symbol": "SPY", "stance": "bullish", "confidence": 0.7, "reasons": ["model predicts +1.5% return"]}
    tradeable_regime = {"seat": "regime", "symbol": "SPY", "state": "trending", "volatility": "normal", "trend": "up", "tradeable": True, "reason": "normal volatility, clear up trend"}
    d1 = spy_forecast_decision(bullish_technicals, bullish_forecast, tradeable_regime)
    assert d1["action"] == "buy" and d1["target_quantity"] == 1, d1
    print(f"PASS -- confident bullish technicals + forecast + tradeable regime -> buy: {d1}")

    print("\nTesting spy_forecast_decision -- non-tradeable regime forces hold regardless...")
    non_tradeable_regime = {"seat": "regime", "symbol": "SPY", "state": "ranging", "volatility": "normal", "trend": "sideways", "tradeable": False, "reason": "no directional edge, sitting out"}
    d2 = spy_forecast_decision(bullish_technicals, bullish_forecast, non_tradeable_regime)
    assert d2["action"] == "hold", d2
    print(f"PASS -- non-tradeable regime forces hold regardless of seat agreement: {d2}")

    print("\nTesting spy_forecast_decision -- technicals and forecast disagree -> hold...")
    bearish_forecast = {"seat": "forecast", "symbol": "SPY", "stance": "bearish", "confidence": 0.7, "reasons": ["model predicts -1.5% return"]}
    d3 = spy_forecast_decision(bullish_technicals, bearish_forecast, tradeable_regime)
    assert d3["action"] == "hold", d3
    print(f"PASS -- technicals bullish but forecast bearish -> hold, not averaged: {d3}")

    print("\nTesting spy_forecast_decision -- forecast confidence below threshold -> hold...")
    weak_forecast = {"seat": "forecast", "symbol": "SPY", "stance": "bullish", "confidence": 0.2, "reasons": ["model predicts a small return"]}
    d4 = spy_forecast_decision(bullish_technicals, weak_forecast, tradeable_regime)
    assert d4["action"] == "hold", d4
    print(f"PASS -- forecast below CONFIDENCE_THRESHOLD -> hold, not a weak buy: {d4}")

    print("\nTesting spy_forecast_decision -- symbol mismatch raises...")
    mismatched_forecast = {**bullish_forecast, "symbol": "AAPL"}
    try:
        spy_forecast_decision(bullish_technicals, mismatched_forecast, tradeable_regime)
        raise AssertionError("should have raised ValueError")
    except ValueError as e:
        print(f"PASS -- raised clearly: {e}")
```

- [ ] **Step 4: Run it and verify all PASS**

Run: `cd /Users/ethandungo/agent-trader && python3 -m backtest.forecast_backtest`
Expected: eleven `PASS` lines total print (six from Task 4 plus five new ones), no `AssertionError`, exit code 0.

- [ ] **Step 5: Commit**

```bash
cd /Users/ethandungo/agent-trader
git add backtest/forecast_backtest.py
git commit -m "Add spy_forecast_decision(): SPY-only Technicals+Forecast gate

Mirrors options_engine.technicals_only_decision() -- Fundamentals still
excluded for SPY, regime can only force a hold. Not wired into judge.py
or any live path; only meaningful once a real model clears
run_validation()'s promotion gate in a separate, later session."
```

---

## What this plan deliberately does NOT do

- Does not fetch real SPY data or fit a real model. `research/forecast_model_params.json` does not exist after this plan — it's created by a later, separate interactive session that fetches real bars (via Robinhood MCP / Polygon, same as the options backtest) and actually runs `run_validation()` for real.
- Does not touch `agents/judge.py`, `execution/paper_broker.py`, `execution/config.py`, or any automation/live path.
- Does not decide whether the forecast seat is "promoted" — that's an outcome of the real run above, not something this plan can determine with synthetic data.
