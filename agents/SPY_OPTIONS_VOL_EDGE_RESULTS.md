# SPY vol-edge backtest: results (Task 6, complete, 30–45 day track)

Companion to `agents/SPY_OPTIONS_DESIGN.md` (the blueprint).

**Verdict: FAIL. Do not promote.**

- The GARCH forecast really is better at predicting volatility.
- But the strategy built on it loses money. Credit spreads won 2 of 24
  trades (−$7,114).
- Credit spreads still lost under every exit rule tried after the fact,
  and in the bull-put direction as well.
- There is no consistent-income result here.

This supersedes the earlier "partial" write-up of this file (5 of 8 data
chunks, +$3,167 for GARCH). That run is **not comparable** to this one,
for four reasons:
- its orchestration script was never committed;
- it mixed the 7-day and 30–45 day tracks (188 rows);
- it used a flat 3% cost;
- it covered ~60% of candidates.

This run is fully reproducible from committed code and covers every
candidate on the one track that could be run as designed.

## What was run

```
python -m backtest.run_vol_edge_backtest plan   # signals, forecasts, decisions
python -m backtest.run_vol_edge_backtest legs   # which contracts each trade needs
python -m backtest.run_vol_edge_backtest run    # results -> logs/backtests/vol_edge_results.json
```

**Signals.** The signal stream is the same as Round 2's:
- 94 signal days, 2024-08-02 → 2026-06-10.
- A signal day is any day the unchanged technicals+regime rule says buy
  or sell.
- SPY went from $532.90 to $725.43 over the window (+36%).

**Data.** All data is real Robinhood data:
- 151 option contracts with real daily bars (151 needed, 151 fetched).
- Each contract's OCC symbol was checked against the strike, type and
  expiration requested before use, which rules out transcription
  errors.
- Gap-fill (interpolated) bars were dropped.

**Substitutions.** These were forced by this environment's network
access and fixed before any option data was fetched. They are recorded
in `backtest/run_vol_edge_backtest.py`'s docstring.

1. **Implied vol** comes from Robinhood's VIX history (CBOE is
   blocked).
   - Robinhood has no VIX9D, so **only the 30–45 day track runs**.
   - The 7-day track is not run, rather than run against the wrong
     benchmark.
2. **Point-in-time listing check.** A leg counts as tradeable only if it
   has a real daily bar on the signal date (Polygon is blocked).
3. **Pre-registered** (the original run never recorded these):
   - 63-day lookback for the baseline forecast;
   - the valuation gate trades only when the pricing edge is > 0;
   - the headline stream is the ablation winner.
4. **Targeted contract lookup.** Full SPY chains are too large to fetch
   here, so only the strikes the unchanged selectors can pick were
   looked up:
   - the nearest strike, round(spot);
   - the 1%-away protective strike.

   14 lookups came back empty. Their expirations fell on market holidays
   (Good Friday 2025-04-18, 2025-07-04), so those candidates are
   skipped, never substituted.

**Costs.** Costs use `options_data.estimate_haircut_pct()` on each leg's
entry bar (3% floor, 15% ceiling), the same method as Round 2.

**Exit rules.** Exits use the pre-registered rules unchanged:
- stop at −50% and target at +100% (`config.OPTIONS_STOP_LOSS_PCT` /
  `OPTIONS_TAKE_PROFIT_PCT`);
- applied to the spread's net value for credit spreads;
- ×100 per contract.

## GARCH ablation: forecast accuracy (PASS)

| Engine | MAE | RMSE | Rows closer |
|---|---|---|---|
| Baseline (trailing 63-day realized vol) | 0.1185 | 0.154 | 41 / 94 |
| **GARCH(1,1)** | **0.0772** | **0.1199** | **53 / 94** |

GARCH forecasts realized volatility clearly better. Per the design, the
**GARCH stream is the headline.**

## Headline: GARCH-driven stream (FAIL)

| | Trades | Win rate (95% CI) | Total P&L | Avg / trade | Worst trade | Avg max loss / contract |
|---|---|---|---|---|---|---|
| **Credit spreads** | 24 | **8.3%** (2.3%–25.8%) | **−$7,113.69** | −$296.40 | −$557.09 | $396.25 |
| Debit (long call/put) | 10 | 10.0% | −$5,601.45 | −$560.14 | −$1,187.07 | $1,388.40 |
| All | 34 | 8.8% | −$12,715.14 | −$373.97 | −$1,187.07 | |

**How the credit spreads exited.** 22 hit the stop-loss and 2 the
take-profit. Average win +$227, average loss −$344.

**Decision funnel.** 74 decisions to trade became 34 simulated trades.
The other 40 were skipped:
- 30: valuation edge not positive;
- 8: holiday expiration, no contract;
- 2: no real bar on the signal date.

### Consistency: one spread at a time

This is the realistic small-account view: one spread open at a time,
taken in order.

| | Spreads | Win rate | Total | Months with an exit | Positive months | Worst month |
|---|---|---|---|---|---|---|
| GARCH | 10 | 10% | −$3,047.09 | 10 | **1** | −$557.09 |

By exit month:
- 2024: Aug −$246, Sep −$396, Dec −$314.
- 2025: Jan −$241, Feb −$456, Mar −$270, **Apr +$226**, Nov −$542.
- 2026: Apr −$252, Jun −$557.

**Against the owner's target of $50–100 a week (~$200–430 a month):**
- this lost about **$300 in a typical active month**;
- it was positive in 1 month of 10;
- it made no trade at all in 13 of the 23 months.

It is not a consistent-income strategy.

### Safety switch view

The armed market switch (`agents/market_switch.py`, `bearish_only`) read
**risk-on for every month** of this window, so it blocked 0 spreads.
That is the expected result: 2024–26 had no slow bear market, which is
the only thing it is built to sit out. It can't rescue this result, and
it wasn't tested by it either.

## Why it lost: a structural bias in the valuation gate

The bias has three parts:

1. **Every credit spread actually traded was a *bearish* bear-call
   spread**, sold at the money into a market that rose 36%. 14 of the 24
   were stopped out within 1–4 days, on real ~1–2% SPY rallies.
2. **The 35 *bullish* bull-put spreads the rule wanted were all
   blocked** by the valuation gate (29 "edge not positive", the rest
   holidays or no bar).
3. **The cause:**
   - `options_valuation` prices both legs at one flat sigma.
   - The real market prices the **put skew**: the protective OTM put
     costs extra (crash insurance), and the OTM call costs less.
   - So against a flat-vol model, every bull put spread looks *cheap*
     and every bear call spread looks *rich*.
   - Example: on 2024-10-14 the gate's model valued a 584/578 bull put
     at $2.67 against a market price of $1.96.
   - The gate therefore always picks the call-selling side, which was the
     losing side in a rising market.
   - The r=0 assumption adds a smaller push the same way (≈$0.10 per
     6-wide spread).

So the "edge" the gate found was mostly the market's normal skew, not
mispricing.

## Post-hoc diagnostics (chosen after seeing the result; never a headline)

These answer one question: *is it the exit rule, or the trade itself?*

| Variant (GARCH credit decisions) | Trades | Win rate | Total | Worst trade | One-at-a-time positive months |
|---|---|---|---|---|---|
| Pre-registered (−50% stop / +100% target) | 24 | 8.3% | −$7,113.69 | −$557.09 | 1 / 10 |
| Hold every spread to expiration | 24 | 25.0% | −$10,550.54 | −$955.75 | 1 / 6 |
| Take profit at 50% of credit, stop when loss = credit | 24 | 33.3% | −$6,626.62 | −$584.87 | 2 / 10 |
| The 29 blocked bull put spreads, simulated anyway | 29 | 41.4% (25.5%–59.3%) | −$1,236.69 | −$330.55 | 2 / 8 |

**It's the trade, not the exit rule.** Every exit variant loses.

The bull put spreads, the classic "income" trade and the right side of
a rising market, still lost slightly:
- 41% wins, average win +$185, average loss −$203;
- that held even though they were sold at the money in a +36% year.

At-the-money, 1%-wide, 30–45 day spreads gave back their credit about
as often as they kept it.

## The non-headline stream, for the record

The **baseline-driven** stream made **+$7,863.92**:
- its 22 long calls made +$15,359 (64% wins);
- its credit spreads lost −$7,495, just like GARCH's.

This is **not** evidence for the strategy:
- it's the stream that *lost* the pre-registered ablation;
- picking it now would be cherry-picking;
- its profit is 22 bullish long calls during a +36% rally, which is
  mostly market exposure, with ~$1,289 at risk per contract;
- the GARCH stream's long calls and puts, on the same kind of signals,
  lost −$5,601.

## Plain read

- GARCH is a better volatility forecaster.
- Turning that into option trades, through this valuation gate and these
  spread rules, lost money on real prices.
- The gate is biased toward selling calls.
- Even the unbiased direction, selling put spreads, didn't make money
  here.
- It fails the go-live gate, which needs positive returns *and*
  consistency. It does not get wired into the live options path.

**What could change the answer:** a strategy that sells further-OTM
puts and uses an IV-skew-aware valuation (per-strike implied vol rather
than one VIX number). That would be a **new, separately pre-registered
test**, not a tweak to this one.

## Known limitations

- **Thin, single-regime sample.** The window is ~2 years and mostly a
  bull market, with only the April 2025 crash as stress.
- **No 7-day track.** VIX9D isn't available here.
- **Approximate costs.** They are a daily-range estimate, not real
  bid/ask (unavailable).
- **Approximate entry prices.** Entries fill at the signal day's close.
- **The design doc's limitations apply unchanged:** GARCH(1,1)
  simplifications, VIX as the IV benchmark, per-leg cost model, and the
  reused Round 2 signals.
