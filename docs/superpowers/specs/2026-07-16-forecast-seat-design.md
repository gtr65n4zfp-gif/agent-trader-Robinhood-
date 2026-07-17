# SPY forecast seat — design

Blueprint only — no implementation yet. Companion to
`agents/OPTIONS_BACKTEST_DESIGN.md` and `agents/OPTIONS_BACKTEST_RESULTS.md`
(same session, same underlying data/rigor), but a separate, standalone
experiment: does a statistical forecast of SPY's own future price add
real signal beyond what `agents.technicals` and `agents.regime` already
see?

## Guiding principle

> Prove it before it's live. Every existing component in this project
> (the regime filter, the options backtest) was validated in isolation
> before touching any real gate. This is no different — SPY-only,
> standalone, until a validation harness proves the model beats a naive
> baseline with a real margin. If it doesn't clear that bar, that's a
> valid, reportable result on its own — not a reason to keep tuning until
> it does.

## Honest framing, stated up front

Short-horizon single-index price forecasting (SPY, 1-45 days) is one of
the most heavily studied and hardest-to-win problems in finance. Most
serious attempts don't reliably beat a naive baseline out of sample. This
design is built around that reality: start with the simplest model that
can honestly answer "is there any real signal here at all," not with an
assumption that there is one.

## Scope

**In scope:** a standalone statistical baseline forecasting SPY's own
forward return at two horizons (7-day, 30-45-day, matching the options
backtest's own horizons), validated via chronological train/test split
against a naive baseline, exposed as a seat-shaped function
(`agents/forecast_seat.py`) usable by any symbol, and a SPY-specific
decision wrapper requiring Technicals + Forecast agreement (mirroring
`backtest/options_engine.technicals_only_decision()`).

**Explicitly deferred, pending what the validation harness shows:** any
wiring into `agents/judge.py`'s real gate, any symbol beyond SPY, any
paper or live trading integration, refitting cadence/automation. The
end-state intent (once proven) is for the forecast seat to become a
required third seat in judge.py's conjunctive gate (agreeing AND with
Fundamentals AND Technicals) for whichever symbols it's eventually
validated on — but that step is out of scope for this design and this
implementation pass.

## New dependency

`numpy` — the project's `requirements.txt` currently has only `requests`.
Needed for the regression's closed-form solve (`numpy.linalg.lstsq`).
Chosen deliberately over `scikit-learn` or any deep-learning library:
this is plain OLS, no hyperparameters, no training loop, no non-convergence
failure mode to debug.

## New files

```
research/forecast_model.py     — fit(), predict(), coefficient storage
research/forecast_model_params.json  — committed, fitted coefficients (like config.py's tuned constants)
agents/forecast_seat.py        — pure function: features -> {stance, confidence, reasons}
backtest/forecast_backtest.py  — validation harness + SPY decision wrapper
```

Nothing here touches `agents/judge.py`, `execution/paper_broker.py`, or
any live/automation path.

## Features and target

Features (all reused, unchanged, from `backtest/data.py`'s existing
point-in-time `technicals_as_of()` — already no-lookahead-safe by
construction, since every indicator there is computed from `bars_through()`-
truncated history):

- `pct_from_ema` — `(price - ema) / ema`, the same momentum/trend number
  `agents/technicals.py` already computes.
- `rsi` — mean-reversion signal, Wilder's RSI, already computed.
- `atr_pct` — volatility context, already computed.
- `recent_5d_return` — trailing 5-day return, a NEW indicator added to
  `backtest/data.py` alongside the existing ones (same no-lookahead
  truncation discipline as everything else there).

Target: forward return over the horizon, `(close[t+N] - close[t]) / close[t]`
for N=7 and N=30 — two independently fit models (different coefficients,
different training runs), never a single model parameterized at
prediction time by horizon.

## Model

Plain OLS linear regression via `numpy.linalg.lstsq` (closed-form, no
iterative fitting, no hyperparameters). 3-4 features deliberately kept
small relative to the available training rows (a few hundred to ~1000
daily bars) — enough to test whether momentum/mean-reversion/volatility
carry forward signal, not so many that the fit is noise-fitting.

**Fitting cadence:** fit once per horizon, on the training split (see
Validation harness below), coefficients committed to
`research/forecast_model_params.json` — mirroring how `config.py` holds
other tuned constants. Not refit live on every decision call. Refitting
is a deliberate, manual, future step (out of scope here), so behavior is
stable, auditable, and can't silently leak from an accidental refit near
live data.

## Stance / confidence mapping

Same shape as every other seat (`{seat, symbol, stance, confidence,
reasons}`, matching `agents/technicals.py`'s `build_view()` return shape):

- `typical_abs_return` — the median absolute forward return observed in
  the TRAINING window only, computed once and stored alongside the
  fitted coefficients (never recomputed from test-period data). This is
  the one scale factor the whole mapping below is built from, so there's
  no second, arbitrarily-chosen constant needing separate justification.
- `stance`: `"bullish"` if predicted return > `+0.25 × typical_abs_return`,
  `"bearish"` if < `-0.25 × typical_abs_return`, else `"neutral"`. The
  neutral band is deliberately narrow (a quarter of a typical move) —
  the model only needs to distinguish "some real signal" from "noise
  around zero," not clear a high bar to register a stance at all;
  `judge.CONFIDENCE_THRESHOLD` (already 0.5) is what actually decides
  whether a stance is strong enough to act on.
- `confidence`: `min(1.0, abs(predicted_return) / typical_abs_return)` —
  an average-sized predicted move lands near 0.5 confidence; a larger one
  pushes toward 1.0.
- Missing/insufficient warm-up data (same condition `technicals_as_of()`
  already detects) → neutral, zero-confidence. Never guess from a partial
  feature set.

## Validation harness (`backtest/forecast_backtest.py`)

**Split:** a single, fixed chronological cut, decided before fitting
anything and never moved based on results (same pre-commitment discipline
as this session's regime-window extension): **the first 75% of available
trading days, by count, form the training set; the remaining 25% form
the test set.** Exact row counts, not an approximate proportion. No
shuffling, no k-fold — shuffling would leak future rows into training
through overlapping-window features. The split boundary is asserted
(train-end strictly precedes test-start) and fails loudly if violated.

**Baselines — the bar is NOT 50%:**
1. Coin-flip floor (50%) — sanity check only.
2. Naive-drift baseline — "always predict the direction of the training
   period's average return" (in practice, almost always bullish, since
   SPY has genuine long-run upward drift). **This is the real bar to
   clear.** A model that can't beat "stocks go up" by a real margin isn't
   adding anything.

**Reported** (same shape as `backtest/options_metrics.py`'s existing
conventions): directional accuracy + 95% Wilson CI on the TEST period
only, MAE/RMSE of predicted vs. actual return, and deltas against both
baselines — separately for each horizon.

**Promotion gate:** the model is only considered validated — worth
building the SPY decision wrapper around — if its directional-accuracy
CI is clearly separated from (not just nominally above) the naive-drift
baseline's accuracy, on the held-out test period. If it isn't, that's a
valid, final, reportable result for this pass — no further tuning to
force a pass.

## SPY decision wrapper

Only built out if the validation gate above is cleared. Lives in
`backtest/forecast_backtest.py`, mirrors
`options_engine.technicals_only_decision()`:

- Regime filter checked first; can only force HOLD (tighten, never
  loosen — same rule as everywhere else this filter is used).
- Requires Technicals AND Forecast to agree on direction AND both clear
  `judge.CONFIDENCE_THRESHOLD` — Fundamentals still excluded for SPY
  (same existing rationale: structurally empty for an ETF trust, per
  `agents/OPTIONS_BACKTEST_DESIGN.md`'s "Signal source" section).
- Any disagreement, either seat below threshold, or non-tradeable regime
  → HOLD. Same conjunctive, no-trade-by-default shape as `judge.py`.

## Testing

Self-tests in each new module's `__main__` block, matching every
existing module's convention (`agents/technicals.py`,
`backtest/options_engine.py`, etc.):

- `research/forecast_model.py` — `fit()` recovers known coefficients on
  synthetic noise-free data with a planted linear relationship;
  `predict()` output shape.
- `agents/forecast_seat.py` — stance/confidence threshold behavior at
  and around the `±0.25 × typical_abs_return` neutral band; missing-feature
  → neutral, zero-confidence path.
- `backtest/forecast_backtest.py` — split-boundary correctness (fails
  loud on overlap), baseline computation correctness, and one end-to-end
  run against a small fabricated dataset with a known planted
  relationship (asserts the fitted model recovers it and beats the naive
  baseline on that synthetic case).

## Known limitations, stated plainly

- Small-data risk: a few hundred to ~1000 daily bars is a thin dataset
  for any regression: even a 3-4 feature OLS can overfit train data that
  doesn't hold out-of-sample. The validation harness's whole purpose is
  to catch this honestly, not paper over it.
- Single train/test split, not walk-forward-with-refitting or k-fold —
  simplest to reason about and audit, but means the reported CI reflects
  one specific historical test window, not an average over many. A
  follow-up pass could walk the split forward in time and check
  consistency, similar to this session's calls-only time-split finding.
- `recent_5d_return` is a new indicator, not yet used by any live seat —
  it only feeds this forecast model for now.
- Same open questions from `OPTIONS_BACKTEST_DESIGN.md` still apply where
  relevant: SPY's Fundamentals leg is structurally unusable, and none of
  this touches `PaperBroker`, live switches, or order placement.

## Decisions locked in

1. Statistical OLS baseline first, not classical ML or deep learning —
   revisit only if this shows something real worth refining.
2. Both horizons (7-day, 30-45-day), two independent models.
3. Output shape matches existing seats exactly: `{stance, confidence,
   reasons}`.
4. SPY only, standalone, until the validation harness's promotion gate is
   cleared — no `judge.py` wiring, no other symbols, in this pass.
5. Model fit once, coefficients committed to a params file — not refit
   live.

No open questions remain — ready for an implementation plan.
