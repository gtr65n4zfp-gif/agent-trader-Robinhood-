# SPY vol-edge backtest — results (Task 6, partial)

Companion to `agents/SPY_OPTIONS_DESIGN.md` (the blueprint). This is a
**partial run**, not the final word — real-data fetching for the 170
unique resolved candidates was split into 8 chunks by expiration date;
only **5 of 8 (chunks 2, 3, 4, 6, 8)** are fetched and verified as of this
writeup. Chunks 1, 5, and 7 (~91 more candidates) were not fetched, a
deliberate cost/usage tradeoff, not a data gap — see "What finishing would
add" below. Every number here is real (no MCP calls happen in this
analysis — it consumes only already-fetched, already-verified manifest
data), just incomplete.

## Coverage

| | Baseline-driven | GARCH-driven |
|---|---|---|
| Signals deciding "trade" (of 188 signal×track rows) | 142 | 136 |
| Resolved to a real simulated trade | 57 | 57 |
| Coverage | 40.1% | 41.9% |

70 unique resolved candidates came out of the 5 available chunks (83
candidates fetched, 13 genuine skips — mostly market holidays with no
listed expiration, and one confirmed interpolated/non-genuine entry-day
bar excluded per the no-lookahead policy).

**Cost-model simplification, stated plainly:** the fetch subagents were
asked for each leg's entry-date *close* only, not full entry-day OHLC, so
`options_data.estimate_haircut_pct()`'s day-range-based dynamic haircut
(the one Round 2 used) can't be computed here. This run uses the flat
`config.OPTIONS_ROUNDTRIP_HAIRCUT_PCT` (3% round-trip) for every leg
instead — a real, narrower cost assumption than Round 2's own per-trade
estimate, not hidden.

## GARCH ablation — forecast accuracy

Across all 188 signal×track rows (independent of trade coverage — this
compares forecasts against realized vol regardless of whether a trade was
resolved):

| Engine | MAE | RMSE | Rows won |
|---|---|---|---|
| Baseline (trailing realized vol) | 0.1007 | 0.1466 | 73 / 188 |
| **GARCH(1,1)** | **0.0769** | **0.119** | **115 / 188** |

GARCH's forecast was closer to the realized vol that actually played out
on 115 of 188 rows (61.2%), with a meaningfully lower MAE and RMSE.
**GARCH wins the forecast-accuracy ablation** on this dataset.

## GARCH ablation — trade P&L

Same 188 signals, run through the identical decision/simulation pipeline
twice — once with each engine's forecast driving `forecast_RV` — over
whichever of the 57 candidates per stream happen to be resolved so far:

| | Baseline-driven | **GARCH-driven** |
|---|---|---|
| Trades | 57 | 57 |
| Wins / Losses | 26 / 31 | 33 / 24 |
| Win rate | 45.6% | **57.9%** |
| 95% Wilson CI | 33.4% – 58.4% | 45.0% – 69.8% |
| Total realized P&L | **-$918.73** | **+$3,166.88** |

**GARCH wins the P&L ablation too**, on this partial sample: the
baseline-driven stream is net negative, the GARCH-driven stream is net
positive with a win rate above a coin flip. The two 95% CIs overlap
(baseline's upper bound 58.4% vs. GARCH's lower bound 45.0%), so this
is a real but not yet statistically decisive gap at n=57 per stream —
exactly the kind of result finishing the remaining 3 chunks would sharpen.

### Per-regime / per-side breakdown

| Regime | Side | Baseline n / win rate / P&L | GARCH n / win rate / P&L |
|---|---|---|---|
| low_vol_trend | bullish | 27 / 37.0% / -$1,753.14 | 18 / 50.0% / +$625.23 |
| low_vol_trend | bearish | 4 / 0.0% / -$913.06 | 4 / 0.0% / -$913.06 |
| trending | bearish | 23 / 60.9% / +$1,009.15 | 25 / 60.0% / +$1,165.73 |
| trending | bullish | 3 / 66.7% / +$738.32 | 10 / 90.0% / +$2,288.98 |

The identical n/win-rate/P&L on `low_vol_trend`/bearish isn't a bug —
both engines happened to pick the same structure and option type on those
4 signals, so they resolved to the exact same simulated trades. The
biggest divergence is `trending`/bullish, where GARCH both traded more
often (10 vs. 3) and won more (90% vs. 67%) — small-n, worth re-checking
once the remaining chunks are in rather than over-reading a 10-trade cell.

### vs. SPY buy-and-hold (debit trades only)

Vertical credit spreads don't have a clean "capital committed" figure to
compare against a same-dollar SPY position (you receive a credit, you
don't pay one), so this comparison is restricted to the debit (long
call/put) trades only, both n too small to read much into yet:

| | Baseline-driven (n=12) | GARCH-driven (n=4) |
|---|---|---|
| Options P&L | -$2,793.53 | +$195.66 |
| Buy-and-hold P&L | +$30.70 | +$49.44 |
| Delta | -$2,824.23 | +$146.22 |

### vs. Round 2's trading baseline

Round 2 (`agents/OPTIONS_BACKTEST_RESULTS.md`) — buy premium on every
qualifying technicals+regime signal, no vol-edge filter — scored 37.6%
(7-day) / 39.5% (30-45 day) win rate. The GARCH-driven vol-edge stream's
57.9% win rate on this partial sample is well above both, and the
baseline-driven vol-edge stream's 45.6% is also above Round 2's numbers —
though Round 2's 93-94 signals and this run's 57-trade partial sample
aren't the same denominator, so this is a promising early read, not a
like-for-like final comparison yet.

## The plain read (partial)

On the data fetched so far, this is a real, honestly-computed result in
GARCH's favor on both questions the ablation was designed to answer:
better forecasts, better P&L, versus the simpler trailing-RV baseline. It
is **not yet the final Task 6 answer** — 60% of unique candidates are
resolved, the trade-level P&L comparison's confidence intervals still
overlap, and the debit-vs-buy-hold comparison has too few trades to read.
The direction of the result is consistent and non-trivial; whether it
holds up at full coverage is the reason to finish the remaining chunks
before treating this as conclusive.

## What finishing would add

Chunks 1, 5, and 7 (~91 more candidates) would raise the resolved-trade
count from 57 to an estimated ~130-140 per stream, tightening both Wilson
CIs meaningfully and adding whatever regime/side mix those specific dates
carry (chunk boundaries were drawn by expiration date, not by regime, so
there's no reason to expect the missing chunks skew the result in either
direction — but that's an assumption, not a checked fact, until they're
actually in).

## Known limitations

- Flat 3% round-trip haircut for every leg (see "Coverage" above) — a
  real simplification versus Round 2's dynamic entry-range estimate,
  because the manifest fetches only captured each leg's entry-date close.
- Partial data: 5 of 8 chunks (~60% of unique candidates). Every number
  above is subject to revision once the remaining 3 chunks are fetched.
- Same limitations already stated in `agents/SPY_OPTIONS_DESIGN.md`'s
  "Known limitations" section apply unchanged (GARCH(1,1) simplifications,
  VIX-not-ATM-IV benchmark, independent per-leg spread cost model, reused
  Round 2 signal stream, crowded-trade tail risk).

## Code added this run

- `backtest/options_metrics.summarize_by_regime_and_side()` — formalizes
  the per-regime/per-side breakdown Round 2 assembled by hand.
- `backtest/options_metrics.compare_forecast_accuracy()` — the GARCH
  ablation's forecast-accuracy half (MAE/RMSE/win-count between engines).
- The orchestration script tying candidates, both simulation engines, and
  both new reporting functions together for this run is scratch-local
  (consumes non-committed fetched manifest data) — the reusable logic it
  calls is all in the two committed functions above plus the existing
  `options_engine.py` / `options_spread_engine.py` / `options_metrics.py`
  functions from Tasks 1-5.
