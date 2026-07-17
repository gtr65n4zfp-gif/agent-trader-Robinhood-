# SPY forecast seat — results (first real-data run)

Companion to `docs/superpowers/specs/2026-07-16-forecast-seat-design.md`
(the design) and `docs/superpowers/plans/2026-07-16-forecast-seat.md` (the
implementation plan). This documents the first and only real-data run of
`backtest/forecast_backtest.run_validation()` against actual SPY history,
executed 2026-07-17.

## Data

552 real daily SPY bars (2024-05-01 → 2026-07-15, via Robinhood MCP —
the same fetched series reused from this session's options backtest
work). Features and targets built entirely through the real, committed
repo code (`backtest.data.technicals_as_of()`, with `pct_from_ema`
derived as `(price - ema) / ema` per the fix from the final whole-branch
review), never a scratch reimplementation of the indicator math.

Two independently fit models, one per horizon (7-day, 30-45-day —
snapped to the nearest available trading day, matching the design's
target-date convention), each using `backtest.forecast_backtest.
run_validation()` unchanged: a fixed 75/25 chronological split (no
shuffling, no cherry-picked cut point), fit on train only, evaluated on
test only, compared against the naive-drift baseline computed from that
same train split.

## Results

| Horizon | Test n | Model accuracy | Model 95% CI | Baseline accuracy | Beats baseline? |
|---|---|---|---|---|---|
| 7-day | 122 | 54.1% | 45.3% – 62.7% | 56.6% | **No** |
| 30-45 day | 118 | 60.2% | 51.2% – 68.5% | 55.9% | **No** |

**Neither horizon clears the promotion gate.** For the 7-day model, the
naive "always predict bullish" baseline (justified: SPY's training-period
average return was positive) actually outperforms the fitted model
outright — 56.6% vs. 54.1%. For the 30-45 day model, the point estimate
looks better than baseline (60.2% vs. 55.9%), but the gate requires the
model's confidence-interval **lower bound** to clear the baseline's
accuracy, and 51.2% does not clear 55.9%. This is exactly the case the
gate exists to catch: a better-looking headline number that isn't
actually distinguishable from the naive baseline at this sample size.

Fitted coefficients, for the record (not analyzed further — a rejected
model's coefficients aren't worth over-interpreting, but included for
anyone who re-runs this):

- 7-day: `pct_from_ema -0.363, rsi 0.0005, atr_pct 0.999, recent_5d_return -0.157`, intercept `-0.034`
- 30-45 day: `pct_from_ema -1.577, rsi 0.0028, atr_pct 4.756, recent_5d_return -0.072`, intercept `-0.197`

Neither shows a clean, dominant, intuitive relationship (the `atr_pct`
coefficients in particular are large relative to the others, suggestive
of a model leaning on volatility rather than a genuinely directional
signal) — consistent with failing to generalize out of sample.

## What this means

This is the outcome the design doc's own "honest framing" section
anticipated as the likely one: short-horizon single-index return
forecasting is a genuinely hard problem, and a simple OLS baseline on
four well-established technical features does not find a real edge here,
on this data. That's a legitimate, informative result — not a failure of
execution.

## Consequences, per the design's own decisions

- **No model is promoted.** `research/forecast_model_params.json` is
  intentionally NOT created — the design's promotion gate exists
  specifically to prevent an unproven model from being wired in, and
  this run didn't clear it.
- **`agents/forecast_seat.py` and `spy_forecast_decision()` remain
  unused** — built, tested (against synthetic data), and correct, but
  with no validated model to load. They stay available if a future
  attempt (different features, a longer/different window, a different
  baseline model class) wants to try again through the same harness.
- **Nothing here touches `agents/judge.py`, `PaperBroker`, or any live
  path** — consistent with every constraint from the design and plan.

## Known limitations of this run

- Single 75/25 split, not walk-forward — the reported CI reflects one
  specific test window, not an average across several. A follow-up could
  walk the split forward the same way this session's options-backtest
  work checked calls-only performance across sub-periods.
- Same feature set as designed (`pct_from_ema, rsi, atr_pct,
  recent_5d_return`) — a different or larger feature set was explicitly
  out of scope for this first pass, per the design's "start with the
  simplest baseline" decision.
- 552 bars is a thin dataset for any regression; a longer real-data
  window (if available) would give more trustworthy CIs on a re-run.
