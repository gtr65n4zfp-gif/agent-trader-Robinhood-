"""
Calibration harness (kalshi/DESIGN.md Layer 8 / go-live gate).

The go-live gate (DESIGN.md) requires "a Brier score that beats the naive
'trade the price' baseline" and a reliability diagram whose buckets land
in-band -- NOT just positive PnL, because a directional lucky streak can
look profitable while being badly miscalibrated. These are the two metrics
that check.

Pure math over (forecast, outcome) pairs -- no data fetching, no network,
no dependency on kalshi/fair_value.py or kalshi/market_data.py directly (it
takes whatever probabilities and 0/1 outcomes it's handed, real or
synthetic). Self-tested here on synthetic fixtures with known correct
answers; has NOT been run against a single real Kalshi settlement yet --
see kalshi/M0_FINDINGS.md's "M2 progress" note for why.
"""

from __future__ import annotations


def brier_score(forecasts: list[float], outcomes: list[int]) -> float:
    """
    Mean squared error between forecast probabilities and 0/1 outcomes --
    lower is better calibrated (0.0 = perfect, 0.25 = what a constant
    "always guess 50%" forecaster scores against a 50/50 base rate, 1.0 =
    maximally wrong on every call). Standard proper scoring rule: a
    forecaster cannot improve their expected Brier score by reporting
    anything other than their true belief, which is exactly why it's the
    right metric for "is this model honest," not just "is this model
    profitable."
    """
    if not forecasts:
        raise ValueError("forecasts must be non-empty")
    if len(forecasts) != len(outcomes):
        raise ValueError(f"forecasts and outcomes must be the same length, got {len(forecasts)} and {len(outcomes)}")
    for o in outcomes:
        if o not in (0, 1):
            raise ValueError(f"outcomes must be 0 or 1, got {o}")
    return sum((f - o) ** 2 for f, o in zip(forecasts, outcomes)) / len(forecasts)


def reliability_buckets(forecasts: list[float], outcomes: list[int], n_buckets: int = 10) -> list[dict]:
    """
    Groups (forecast, outcome) pairs into n_buckets equal-width probability
    bins (e.g. 10 buckets = deciles: [0.0-0.1), [0.1-0.2), ..., [0.9-1.0])
    and reports, per non-empty bucket: how many forecasts landed there, the
    mean forecast, and the actual realized outcome rate. A well-calibrated
    forecaster's mean forecast and realized rate should track closely in
    every bucket -- "our 70%-confidence calls come true about 70% of the
    time" is exactly what this checks, bucket by bucket, rather than
    averaging calibration error away across the whole range the way a
    single aggregate score can.
    """
    if not forecasts:
        raise ValueError("forecasts must be non-empty")
    if len(forecasts) != len(outcomes):
        raise ValueError(f"forecasts and outcomes must be the same length, got {len(forecasts)} and {len(outcomes)}")
    if n_buckets < 1:
        raise ValueError(f"n_buckets must be >= 1, got {n_buckets}")

    bucket_width = 1.0 / n_buckets
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(n_buckets)]
    for f, o in zip(forecasts, outcomes):
        if not (0.0 <= f <= 1.0):
            raise ValueError(f"forecast must be in [0.0, 1.0], got {f}")
        idx = min(int(f / bucket_width), n_buckets - 1)  # forecast==1.0 lands in the last bucket, not off the end
        buckets[idx].append((f, o))

    result = []
    for idx, pairs in enumerate(buckets):
        if not pairs:
            continue
        mean_forecast = sum(f for f, _ in pairs) / len(pairs)
        realized_rate = sum(o for _, o in pairs) / len(pairs)
        result.append({
            "bucket_range": (idx * bucket_width, (idx + 1) * bucket_width),
            "count": len(pairs),
            "mean_forecast": mean_forecast,
            "realized_rate": realized_rate,
            "calibration_gap": mean_forecast - realized_rate,
        })
    return result


def beats_naive_baseline(model_forecasts: list[float], baseline_forecasts: list[float], outcomes: list[int]) -> bool:
    """
    The go-live gate's actual comparison: does the model's Brier score beat
    the "just trade the market's own price as your probability estimate"
    baseline? If not, the fair-value engine adds no information over the
    book itself and has no business sizing trades regardless of any
    backtested PnL number.
    """
    return brier_score(model_forecasts, outcomes) < brier_score(baseline_forecasts, outcomes)


if __name__ == "__main__":
    print("Testing brier_score() perfect forecaster (always right, full confidence) scores 0.0...")
    assert brier_score([1.0, 0.0, 1.0], [1, 0, 1]) == 0.0
    print("PASS — perfect forecaster: Brier = 0.0.")

    print("\nTesting brier_score() maximally wrong forecaster (always full-confidence wrong) scores 1.0...")
    assert brier_score([1.0, 0.0], [0, 1]) == 1.0
    print("PASS — maximally wrong forecaster: Brier = 1.0.")

    print("\nTesting brier_score() constant 50% forecaster against a 50/50 base rate scores 0.25...")
    assert brier_score([0.5, 0.5, 0.5, 0.5], [1, 0, 1, 0]) == 0.25
    print("PASS — always-guess-50% forecaster: Brier = 0.25, the textbook reference value.")

    print("\nTesting brier_score() rejects mismatched lengths and invalid outcomes...")
    try:
        brier_score([0.5, 0.5], [1])
        assert False, "should have raised ValueError"
    except ValueError:
        print("PASS — mismatched lengths correctly rejected.")
    try:
        brier_score([0.5], [2])
        assert False, "should have raised ValueError"
    except ValueError:
        print("PASS — outcome=2 (not 0/1) correctly rejected.")

    print("\nTesting reliability_buckets() on a WELL-calibrated synthetic forecaster...")
    # 10 forecasts at 0.7, exactly 7 of which actually happened -- a textbook well-calibrated bucket.
    well_calibrated_forecasts = [0.7] * 10
    well_calibrated_outcomes = [1] * 7 + [0] * 3
    buckets = reliability_buckets(well_calibrated_forecasts, well_calibrated_outcomes, n_buckets=10)
    assert len(buckets) == 1, buckets  # all 10 forecasts land in the same [0.7, 0.8) bucket
    assert buckets[0]["count"] == 10
    assert abs(buckets[0]["mean_forecast"] - 0.7) < 1e-9
    assert abs(buckets[0]["realized_rate"] - 0.7) < 1e-9
    assert abs(buckets[0]["calibration_gap"]) < 1e-9
    print(f"PASS — 10 forecasts at 0.7 with 7/10 realized: calibration_gap = {buckets[0]['calibration_gap']:.6f} (~0, well-calibrated).")

    print("\nTesting reliability_buckets() on a BADLY-calibrated (systematically overconfident) forecaster...")
    overconfident_forecasts = [0.9] * 10
    overconfident_outcomes = [1] * 5 + [0] * 5  # claims 90% confidence, right only half the time
    bad_buckets = reliability_buckets(overconfident_forecasts, overconfident_outcomes, n_buckets=10)
    assert len(bad_buckets) == 1, bad_buckets
    assert bad_buckets[0]["calibration_gap"] > 0.3, bad_buckets  # mean_forecast (0.9) far exceeds realized_rate (0.5)
    print(f"PASS — 10 forecasts at 0.9 with only 5/10 realized: calibration_gap = {bad_buckets[0]['calibration_gap']:.4f} "
          f"(large positive gap correctly flags overconfidence).")

    print("\nTesting reliability_buckets() spans multiple buckets and skips empty ones...")
    mixed_forecasts = [0.05, 0.15, 0.85, 0.95]
    mixed_outcomes = [0, 0, 1, 1]
    mixed_buckets = reliability_buckets(mixed_forecasts, mixed_outcomes, n_buckets=10)
    assert len(mixed_buckets) == 4, mixed_buckets  # 4 distinct deciles occupied, 6 empty ones correctly omitted
    print(f"PASS — 4 forecasts spanning 4 distinct deciles produce exactly 4 non-empty buckets (not 10, not fewer).")

    print("\nTesting reliability_buckets() forecast==1.0 lands in the LAST bucket, not out of range...")
    edge_buckets = reliability_buckets([1.0], [1], n_buckets=10)
    assert len(edge_buckets) == 1 and edge_buckets[0]["bucket_range"] == (0.9, 1.0), edge_buckets
    print(f"PASS — forecast=1.0 correctly lands in bucket {edge_buckets[0]['bucket_range']}, not off the end.")

    print("\nTesting beats_naive_baseline() — a model that agrees with reality beats one that just echoes a coin-flip market price...")
    outcomes_biased = [1, 1, 1, 0, 1, 1, 0, 1, 1, 1]  # a market that's actually 80% likely to resolve YES
    model = [0.8] * 10          # model correctly identifies the true 80% base rate
    naive_market_price = [0.5] * 10  # naive baseline: market/book was pricing it as a coin flip
    assert beats_naive_baseline(model, naive_market_price, outcomes_biased) is True
    print("PASS — a model correctly identifying an 80% true rate beats a naive 50/50 baseline, as it must.")

    print("\nAll kalshi/calibration.py tests passed (synthetic fixtures only -- no real Kalshi settlements yet; "
          "see kalshi/M0_FINDINGS.md's M2 progress note).")
