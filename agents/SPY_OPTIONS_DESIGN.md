# SPY options forecasting model — design (vol-edge strand)

DESIGN + BACKTEST strand, fully separate from the live equity automation
loop (`automation/run_pass.py`, `AUTOMATION_DRY_RUN`) and the live options
paper-trading layer (`automation/run_options_pass.py`,
`OPTIONS_AUTOMATION_DRY_RUN`). Nothing here is wired into either.

## The core reframe

Prior attempts to trade SPY options forecast **direction** — the OLS
forecast-seat model (failed its promotion gate,
`docs/superpowers/specs/2026-07-17-forecast-seat-results.md`) and the
existing options backtest's technicals+regime signal (37.6%/39.5% win
rate, `agents/OPTIONS_BACKTEST_RESULTS.md`). Both came up short.

This strand asks a different question: not "will SPY go up" but "is my
forecast of how much SPY will move different from what the market is
paying for, and am I right more than I'm wrong" — the variance risk
premium. Implied vol running above realized vol on average is
well-documented, but also crowded, so a real average edge can still look
terrible in any one tail event. That's why Task 6's backtest window must
include a genuine down-trend/crash period, not just the bull stretch the
original options backtest's first pass mistakenly relied on.

## Guiding principle

No-lookahead throughout (`backtest/data.py`'s point-in-time truncation,
unchanged). No fabricated fills. Isolated results — nothing here touches
`logs/trades.jsonl`, `logs/paper_portfolio.json`,
`logs/options_trades.jsonl`, or `logs/options_paper_portfolio.json`. Win
rate reported with a Wilson CI, never a bare percentage. Every cost
assumption stated as a policy choice or backed by real data, never hidden
or silently tuned to flatter a result.

## Scope

**In scope:** a standalone, backtest-only build producing P&L/win-rate
evidence for a vol-edge-driven options strategy on SPY, reusing the
existing council's price-based seats (`agents.technicals`, `agents.regime`)
for directional tilt, unchanged.

**Out of scope:** wiring into `automation/run_pass.py` or
`automation/run_options_pass.py`; any change to `AUTOMATION_DRY_RUN` or
`OPTIONS_AUTOMATION_DRY_RUN`; any `place_option_order` call; any change to
`PaperBroker`, `OptionsPaperBroker`, `agents/risk_vetoer.py`, or
`agents/options_risk_vetoer.py`. If this backtest shows something real,
wiring it live is a separate, later decision.

## Data feasibility

- **SPY daily bars** — existing (`backtest/data.py`), real data back to
  2015-01-02, comfortably more than GARCH's ~2-year rolling warm-up needs
  at the window's first decision date (2024-05-01).
- **VIX/VIX9D/VIX3M** — not available historically from Robinhood
  (live-quote only) or Polygon (`NOT_AUTHORIZED` on indices aggregates on
  the current plan). **CBOE's public daily-price CSVs**
  (`cdn.cboe.com/api/global/us_indices/daily_prices/{VIX,VIX9D,VIX3M}_History.csv`)
  work with no API key, data back to 1990-01-02 — this is the strand's VIX
  source.
- **Option chain instruments** — existing, unchanged
  (`backtest/options_data.parse_option_instruments()`).
- **Option historical bars are OHLC only** — no bid/ask, volume, IV, or
  greeks (carried over from `agents/OPTIONS_BACKTEST_DESIGN.md`). ATM
  implied vol exists only on *live* quotes, no historical equivalent — so
  the market-implied-vol benchmark here is VIX/VIX9D/VIX3M, not
  per-contract ATM IV. ATM IV only becomes usable if this is ever wired
  into live automation (out of scope here).

## Level 0 — forecast SPY realized vol → a return distribution

Two independent forecasters run at every decision date; GARCH must earn
its place against a baseline, not be assumed superior. Task 6 reports an
explicit ablation (see "Metrics").

**Engine A — GARCH(1,1)** via the `arch` library (new dependency). Fit on
SPY daily log returns, forecasting conditional variance over the trade
horizon, annualized. Captures volatility clustering, which a flat
trailing-window estimate can't represent.

**Engine B — baseline**: trailing N-day realized vol from SPY's own daily
log returns, annualized (`std(log returns) * sqrt(252)`). N differs per
horizon track (shorter window for the 7-day track, longer for 30-45 day).
Both engines run on every decision date and feed Task 6's ablation; only
the ablation-winner drives the live trading signal in Level 1.

### THE CRITICAL RULE: GARCH must be refit at every decision date, on trailing data only

The classic GARCH-backtest trap: fitting once over full history (including
post-signal dates) leaks future information into every "forecast." At each
decision date `D`:

1. Slice SPY's daily log returns to a trailing window ending at `D`
   (rolling, not expanding) — strictly `returns[D - lookback : D]`.
2. Fit a fresh GARCH(1,1) on exactly that slice.
3. Forecast conditional variance forward over the trade horizon, annualize.
4. Discard the fit — no state carries to the next decision date.

Task 6's report includes a direct, programmatic check that the trailing
window used at `D` never extends past `D`.

### Horizon-aggregation

GARCH(1,1) natively forecasts one-step-ahead variance; the trade horizon
is 7 or 30-45 CALENDAR days, converted via
`trading_days_in_horizon()` (`backtest/vol_forecast.py`, a stated `252/365`
approximation). Uses `arch`'s built-in multi-step forecast
(`model_fit.forecast(horizon=N, method="analytic")`) to get forecasted
daily variance for each of the `N` horizon days, sums those variances
(valid under the model's conditional-independence assumption), then
annualizes via `sqrt(252 / N)` — the same trading-day convention the
baseline engine uses, so the two engines are genuinely comparable.

### From forecast vol to a return distribution

`log(S_T / S_0) ~ Normal(mu, sigma_horizon^2)`, with
`sigma_horizon = forecast_annualized_vol * sqrt(trading_days_in_horizon / 252)`
— trading days throughout, never calendar days (this was corrected during
Task 2's implementation; the original draft mixed day-count bases).
`mu` is fixed at ~zero — Level 0 answers "how wide," not "which way."

## Level 1 — the IV-edge signal, and the directional/regime tilt

Whichever engine wins Task 6's ablation (lower forecast error vs. realized
vol) becomes `forecast_RV` for the live trading decision. If GARCH doesn't
beat the baseline, the baseline drives the signal instead.

**IV-edge:** `forecast_RV - market_implied_vol`, both annualized
percentages. Horizon-matched: the 7-day track compares against **VIX9D**,
the 30-45 day track against **VIX** or **VIX3M** (whichever is closer to
the track's actual DTE at signal time).

- `forecast_RV` materially **below** implied → premium rich → favor the
  DEFINED-RISK credit structure.
- `forecast_RV` materially **above** implied → premium cheap → favor the
  debit structure.
- A stated minimum gap (a Task 2 policy constant) is required before
  either side triggers.

**Directional/regime tilt:** `agents.technicals.build_view()` and
`agents.regime.regime_stance()`, reused unchanged — the same functions
`backtest/options_engine.technicals_only_decision()` already calls for
SPY. Bullish tilt → sell a bull put spread or buy a call; bearish tilt →
sell a bear call spread or buy a put; non-tradeable regime (`ranging` /
`low_vol_ranging`) sits out entirely.

## Level 2 — decision, structure selection, and valuation

**Structure set — exactly two:**

1. **Debit: a single long call or put, ATM.** Reuses
   `options_engine.simulate_option_trade()` unchanged. Defined-risk (max
   loss = premium paid).
2. **Credit: a two-leg vertical spread** (bull put on bullish/neutral
   tilt, bear call on bearish/neutral tilt) — sell the near strike, buy a
   further OTM strike as protection. New engineering surface: a new
   two-leg simulation nets both legs' entry credit and exit cost, with
   stop/target/expiration applied to the spread's **net value**.

**Valuation:** price the candidate using Black-Scholes with **my own
forecast sigma** (Level 0) instead of market-implied vol — new,
self-contained function, compared against the option's real historical
close on the signal date:
- Debit: `edge = my_model_price - market_price` (positive → buy).
- Credit: `edge = market_price - my_model_price` (positive → sell).

A positive edge past Level 1's materiality threshold is what makes a
candidate an actual trade — Level 1 picks the side, Level 2 confirms the
specific candidate is priced favorably.

## Horizon and expiration selection

Unchanged: `options_data.select_liquid_expiration()` — Friday/monthly
expirations only, weekly exclusion and point-in-time listing check
(`verify_listed_as_of()`) both carry over. Same two tracks as the live
options layer (7-day, 30-45 day).

## Cost modeling — fills

Debit leg: `options_data.estimate_haircut_pct()` unchanged. Credit spread:
each leg gets its own entry/exit haircut (widening net entry credit down,
net exit cost up), then netted. No real historical NBBO exists — SPY
spreads are tight but nonzero, so the estimate still widens both legs
rather than assuming a free two-leg fill.

## Metrics

Reuses `backtest/options_metrics.summarize_option_trades()`,
`backtest/metrics.wilson_ci()`, `backtest/options_metrics.compare_to_buyhold()`
— the credit spread's `realized_pnl`/`entry_fill` follow a documented
convention (net credit as a negative "cost," net debit-to-close as a
negative "proceeds") so existing win/loss/P&L math applies without a
special case.

**New:** a per-regime and per-side breakdown function (Round 2 assembled
this by hand; Task 6 formalizes it as reusable code).

**Trading baseline already exists — no new run needed.** Round 2 (37.6%
win rate 7-day, 39.5% 30-45 day, `agents/OPTIONS_BACKTEST_RESULTS.md`) is
"buy premium on every qualifying technicals+regime signal, no vol-edge
filter." Task 6 shows whether the vol-edge/valuation layer beats that
number on the same signal stream and window. (Distinct from Engine B, the
trailing-RV *vol-forecast* baseline — disambiguated throughout as
**trading baseline** vs. **vol-forecast baseline**.)

**Forecast-vs-realized logging:** for every signal evaluated (traded or
not), log both engines' forecasts, `forecast_sigma_horizon` for whichever
drove the trade signal, `market_implied_vol`, `edge`, and the ACTUAL
realized vol over `[signal_date, signal_date + horizon]` once that window
has passed (annualized stdev of SPY's own daily log returns over that
forward window, computed strictly after the fact — never available to the
`signal_date` decision itself). Doesn't gate Task 6's headline results;
raw material for a future calibration check.

**The GARCH ablation — required, reported honestly either way.** Task 6
answers two questions using the forecast-vs-realized log:
1. **Forecast accuracy:** MAE/RMSE of each engine's forecast against
   realized vol, plus a win-count (how often GARCH was closer).
2. **Trade P&L:** two parallel decision streams over the same signal set,
   window, and cost model — GARCH driving `forecast_RV` vs. baseline
   driving it — win rate, Wilson CI, and total P&L for each.

If GARCH doesn't beat the baseline on accuracy, P&L, or both, Task 6's
writeup says so plainly — this design does not pre-decide GARCH is worth
its added complexity before the ablation says so.

## Backtest window

**Reuses Round 2's extended window** (2024-05-01 → 2026-06-10, contains a
real crash/down-trend period — the 2025-04-02→04-07 selloff and reversal —
not just a bull stretch), pre-committed before any data was fetched or
result seen. Same signals (technicals+regime, unchanged) fire on the same
dates Round 2 already documented; this strand adds the vol-edge/valuation
filter on top.

## New files

- `backtest/vol_forecast.py` — both Level 0 engines (GARCH rolling-refit
  via `arch`, and the trailing-window baseline), one shared interface.
- `backtest/vix_data.py` — parses CBOE CSV text into a
  point-in-time-safe `{date: {open, high, low, close}}`.
- `backtest/vol_edge_signal.py` — Level 1: `iv_edge()`, `premium_signal()`,
  `market_implied_vol()` (horizon-matched VIX9D/VIX/VIX3M lookup by
  closest nominal maturity to actual resolved DTE), and
  `vol_edge_decision()` (combines with unchanged
  `options_engine.technicals_only_decision()`).
- `backtest/options_valuation.py` — Black-Scholes-with-my-own-sigma
  (`black_scholes_price()`, r=0, stdlib `math.erf`), plus
  `single_leg_edge()`, `spread_model_value()`, `spread_edge()`.
- `backtest/options_data.py` addition — `select_spread_strikes()`: sold
  leg reuses `select_contract()`'s ATM logic; protective leg is the
  closest listed strike clearing a stated 1% minimum width.
- `backtest/options_spread_engine.py` — `simulate_spread_trade()`:
  day-by-day fill walk matched by date across both legs, stop/target/
  expiration on the spread's net value, same `pnl_pct` sign convention as
  the debit engine so `config.OPTIONS_STOP_LOSS_PCT`/`TAKE_PROFIT_PCT`
  apply unchanged to both structures. Verified against a real SPY
  615/605 put spread (2026-05-15 expiration, real closes 2026-04-20→05-01).
- `backtest/options_metrics.py` addition — per-regime/per-side breakdown
  and the GARCH-vs-baseline ablation reporting.

## Known limitations

- GARCH(1,1) is a new dependency and failure surface — a rolling refit
  across ~100-200 decision-date/track combinations could hit a
  convergence failure or degenerate fit; the fail-safe convention is skip
  that signal (logged), never a fabricated forecast.
- Market-implied-vol benchmark is VIX/VIX9D/VIX3M, not per-contract ATM
  IV, for this strand specifically — a real data constraint, not a
  stylistic choice.
- The credit spread's cost model widens each leg independently — not a
  joint two-leg spread-cost model, which real market microstructure would
  price somewhat differently.
- Reuses Round 2's exact signal stream and window — cannot show whether a
  *different* signal (or a purely vol-driven entry with no directional
  gate) would do better; a legitimate separate follow-up, out of scope
  here.
- SPY vol-selling is a well-known, heavily arbitraged trade — a real
  average edge is still consistent with severe tail risk in an untested
  future regime. The multi-regime window surfaces this, doesn't eliminate it.
- GARCH(1,1) itself is a simplification (constant long-run variance,
  symmetric response to positive/negative shocks); asymmetric variants
  (GJR-GARCH, EGARCH) are a natural, explicitly deferred follow-up — YAGNI
  until GARCH(1,1) has proven it beats the simple baseline.

## Decisions locked in

1. **Vol engine: GARCH(1,1) via `arch`, rolling-refit at every decision
   date, run against a trailing-realized-vol baseline** — Task 6's
   ablation decides which drives the trading signal.
2. **Rolling window, ~2 years (~504 trading days) of trailing daily
   returns per refit** — not expanding, to keep GARCH capturing *current*
   clustering rather than diluting it with a decade of stale history.
3. **Realized-vol evaluation metric: annualized stdev of SPY's daily log
   returns over `[signal_date, signal_date + horizon]`**, computed
   strictly after that window has closed.
4. **Market-implied-vol source: VIX/VIX9D/VIX3M via CBOE's free public
   CSVs**, not Polygon (blocked) or ATM IV (unavailable historically).
5. **Structure set: exactly two** — single-leg long call/put (debit) and
   a two-leg vertical credit spread. No iron condors, calendars, or naked
   shorts.
6. **Directional tilt: `agents.technicals` + `agents.regime`, unchanged,
   zero forking.**
7. **Backtest window: Round 2's existing extended window
   (2024-05-01 → 2026-06-10).**
8. **Trading baseline: the existing Round 2 options backtest results**,
   distinct from the vol-forecast baseline in decision 1.
9. **Valuation: Black-Scholes with my own forecast sigma** (from whichever
   engine wins the ablation), compared against real historical market
   premium.

## Task 6 status — partial results in, 3 of 8 fetch chunks still open

Real-data fetch for the 170 unique resolved candidates, split into 8
chunks by expiration date. Chunks 2, 3, 4, 6, 8 done and verified (real,
non-fabricated resolved/skip data); chunks 1, 5, 7 (~91 candidates) were
not fetched, a deliberate cost/usage tradeoff. A full backtest run
against the 5 available chunks is written up in
`agents/SPY_OPTIONS_VOL_EDGE_RESULTS.md`: **GARCH beats the trailing-RV
baseline on both forecast accuracy (MAE 0.077 vs 0.101, wins 115/188
rows) and trade P&L (57.9% win rate / +$3,167 vs 45.6% / -$919, n=57
trades each)** — a real, promising result, but explicitly partial
(~60% of candidates, overlapping confidence intervals) until the
remaining 3 chunks are fetched and the run redone.
