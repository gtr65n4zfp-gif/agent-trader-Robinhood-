# SPY options backtest — results

Companion to `agents/OPTIONS_BACKTEST_DESIGN.md` (the blueprint). Two
passes are documented here, both 2026-07-16: an initial run (below), then
a second pass ("Round 2") that fixed two real methodology problems the
first run had and deliberately widened the evidence base. **Round 2 is
the current, trustworthy result** — read that section first; the
original run is kept below it for the record, not because its numbers
still stand.

## Round 2 — fixed selector, honest spreads, extended multi-regime window

Triggered by three concrete problems with the first pass: (1) the 30-45
day horizon's 18/25 skip rate turned out to be a selection bug, not a
data gap; (2) the cost model had never been checked against anything
resembling a real spread; (3) the whole dataset was one bull-trending
window, which is exactly what the go-live gate's "≥3 regime" requirement
exists to catch.

### Part A — fixed the expiration selector

The original run snapped to "nearest available expiration on any
weekday," which could land on a thinly-traded Monday or Wednesday SPY
weekly. Checked directly: the 680-strike call expiring **2026-05-11 is a
Monday**, and Polygon's point-in-time reference endpoint confirms it
genuinely wasn't listed yet on its signal date (2026-04-09) — only
appearing by 2026-05-04, a week before its own expiration. That's not a
data gap, it's a real lookahead bug: the original selector could pick a
contract that didn't exist yet when the signal fired.

Fix, added to `backtest/options_data.py`:
- `liquid_expirations_between()` — every Friday in a date range (covers
  both weekly and monthly-3rd-Friday expirations; Monday/Wednesday
  weeklies are structurally excluded since they're never Fridays).
- `select_liquid_expiration()` — targets `signal_date + horizon_days`,
  preferring the monthly (3rd Friday) if one falls within a 10-day
  search window, else the earliest qualifying weekly Friday.
- `verify_listed_as_of()` — parses Polygon's
  `/v3/reference/options/contracts?as_of=<signal_date>` response; a
  non-empty `results` list IS the point-in-time listing proof. This is
  the check that was missing before — a contract's later (retrospective)
  existence doesn't prove it was tradeable back on the actual signal date.

Result: of 175 unique (expiration, strike, type) combos needed across
the full extended signal set (see Part C), **164 (93.7%) verified as
genuinely listed on their signal date** — up from the original pass's
effective ~72% "found something" rate, and this time the pass/fail is
a real point-in-time check, not a lucky guess. The 11 genuine misses
cluster around the April 2025 crash window, where a handful of far-strike
monthly contracts simply hadn't been listed yet that early — a real
market constraint, correctly skipped rather than substituted.

### Part B — spread/haircut model, documented honestly

Checked first whether the concern ("the equity 5bps slippage model is too
generous for options") was already true: it wasn't — the options harness
already had its own separate `OPTIONS_ROUNDTRIP_HAIRCUT_PCT` (3%
round-trip), never the equity 5bps figure. The real gap was that the flat
3% was never checked against real bid-ask data. Tried to get real NBBO —
Polygon's `/v3/quotes`, `/v2/last/nbbo`, and `/v3/snapshot/options` all
returned `403 Not Authorized` on the current plan (confirmed directly,
not assumed). No real spread data is available.

Fallback, added as `options_data.estimate_haircut_pct()`: widen the flat
3% floor using the entry day's own high-low range as a real (not
fabricated), point-in-time signal of that day's trading friction —
`max(3% floor, 0.25 × day_range_pct)`, capped at 15%. The 0.25 multiplier
and 15% ceiling are stated policy choices, fixed before this pass was
re-run, not fitted to the outcome. Known simplification: uses only the
entry day's range for both fill legs (the exit day isn't known until
`simulate_option_trade()` is mid-walk through the bars, and plumbing a
per-day callback through that loop wasn't worth the surface for this
pass) — documented, not hidden.

### Part C — extended window, pre-committed rule

**Rule stated before fetching any data or seeing results:** extend the
signal-generation window 12 calendar months further back — from the
original 2025-05-01 start to **2024-05-01** — keeping the same end date.
Chosen purely by that fixed "12 months prior" rule, not by knowing in
advance what market conditions it contained.

It worked as intended: the extended range includes SPY's real April 2025
selloff (2025-04-02 close ~$564 → 2025-04-07 intraday low $481.80, roughly
a 15% drawdown in days, followed by a violent one-day +10.8% reversal on
2025-04-09). Regenerating signals from the real council pipeline over the
full 2024-08-02 → 2026-06-10 range produced **94 signals** (vs. 25
before), including real bearish "sell" signals during and immediately
after the crash (2025-04-03, 04-10, 04-16, 04-17, 04-21) — genuine
down-trend exposure, not just more bull market.

One caveat worth stating plainly: the project's regime filter is
*designed* to sit out low-directional-edge chop (`ranging` /
`low_vol_ranging` are never tradeable), so even with the wider calendar
window, the regime states that actually produced trades were still only
`trending` and `low_vol_trend` — the filter did its job of avoiding the
choppiest stretches. The genuine regime diversity that DOES show up in
the trades is captured by **side** instead: `sell` signals cluster around
the crash/down-trend, `buy` signals around the rest — reported as a
supplementary breakdown below for exactly that reason.

### Results — 7-day horizon (extended, corrected)

| Metric | Value |
|---|---|
| Signals | 94 total, 1 skipped (not listed as of signal date), **93 traded** |
| Wins / Losses | 35 / 58 |
| Win rate | **37.6%** |
| 95% Wilson CI | **28.5% – 47.8%** |
| Total realized P&L (net of modeled spreads) | **+$4,676.95** |
| Same-capital buy-and-hold-SPY P&L | +$314.08 |
| Delta (options − buy-and-hold) | +$4,362.87 |

Per-regime:

| Regime | n | Win rate | Total P&L |
|---|---|---|---|
| trending | 72 | 40.3% (CI 29.7–51.8%) | +$4,431.58 |
| low_vol_trend | 21 | 28.6% (CI 13.8–50.0%) | +$245.37 |

Per-side (the real up-trend/down-trend split):

| Side | n | Win rate | Total P&L |
|---|---|---|---|
| buy (bullish) | 61 | 42.6% (CI 31.0–55.1%) | **+$8,477.04** |
| sell (bearish, incl. the crash window) | 32 | 28.1% (CI 15.6–45.4%) | **-$3,800.09** |

### Results — 30-45 day horizon (extended, corrected)

| Metric | Value |
|---|---|
| Signals | 93 total, 12 skipped (not listed as of signal date), **81 traded** |
| Wins / Losses | 32 / 49 |
| Win rate | **39.5%** |
| 95% Wilson CI | **29.6% – 50.4%** |
| Total realized P&L (net of modeled spreads) | **+$14,110.56** |
| Same-capital buy-and-hold-SPY P&L | +$1,668.27 |
| Delta (options − buy-and-hold) | +$12,442.29 |

Per-regime:

| Regime | n | Win rate | Total P&L |
|---|---|---|---|
| trending | 62 | 45.2% (CI 33.4–57.5%) | +$18,659.00 |
| low_vol_trend | 19 | 21.1% (CI 8.5–43.3%) | -$4,548.44 |

Per-side:

| Side | n | Win rate | Total P&L |
|---|---|---|---|
| buy (bullish) | 55 | 47.3% (CI 34.7–60.2%) | **+$24,608.26** |
| sell (bearish, incl. the crash window) | 26 | 23.1% (CI 11.0–42.1%) | **-$10,497.70** |

### The plain read

Widening the dataset from 25 to 93-94 trades and adding a real down-trend
period **tightens the confidence interval a lot** (roughly ±17-18 points
down to about ±9-10 points) but **pulls the win rate below a coin flip**
on both horizons (37.6% and 39.5%) — the original 64% was a bull-regime
artifact, not a durable edge. The strategy is still net profitable in
raw dollars and still beats buy-and-hold by a wide margin, but that's
carried entirely by a smaller number of large winning calls in the
uptrend; the put-buying side loses money outright (-$3,800 and -$10,498)
once real down-trend and crash-adjacent conditions are included. **The
edge does not survive outside the original bull regime** — this is a
low-win-rate, high-payoff-asymmetry profile, not the "this clearly
works" result the narrower first pass suggested.

---

## Round 1 — original run (superseded by Round 2 above)

This section is the first pass's original writeup, kept for the record.
Its headline 64% win rate and "N30 mostly unusable" framing are both
superseded by Round 2's fix (selector bug, not a data gap) and wider
dataset (which reverses the win-rate conclusion). Don't cite these
numbers going forward.

## Data sources

- **SPY daily bars** (2025-05-01 → 2026-07-15) — real data via Robinhood
  MCP's `get_equity_historicals`.
- **Option contract resolution** (expiration snapping, ATM strike) — real
  data via Robinhood MCP's `get_option_instruments`. All 49 contracts this
  run needed resolved on the first exact-strike lookup.
- **Option daily price bars** — Robinhood MCP's `get_option_historicals`
  was unavailable this session (every call, including a minimal
  single-contract request, failed with `unknown tool`, while sibling
  Robinhood tools on the same server worked normally — looked like a
  server-side gap, not a request problem). **Polygon.io's options
  aggregates API was used instead** for this one data leg only, via the
  user's own API key (stored in `config/.env`, git-ignored, confirmed to
  have options-data entitlement). Contract selection, decision logic,
  trade simulation, and metrics are all the same unmodified repo code
  (`backtest/options_data.py`, `backtest/options_engine.py`,
  `backtest/options_metrics.py`) — only the upstream of the bars changed,
  reshaped into the same `{data: {results: [...]}}` shape
  `options_data.parse_option_bars()` already expects from Robinhood, so
  the real parsing/simulation path ran unchanged. If `get_option_historicals`
  comes back, future runs can drop the Polygon step entirely.

## Signal generation

25 real signals from `agents.regime` + `agents.technicals` +
`backtest/options_engine.technicals_only_decision()` run against the real
SPY bars above (SPY has no usable Fundamentals leg — see the design doc's
"Signal source" section for why that's an isolated, SPY-only exception).
14 buy (bullish) signals, 11 sell (bearish), spanning 2025-10-27 to
2026-06-10.

## Code added this run

- `backtest/options_metrics.compare_to_buyhold()` — same capital, same
  [signal_date, exit_date] window, invested in SPY shares instead of the
  option, with `config.SLIPPAGE_BPS` charged against the trader on both
  legs (same "no free fills for the benchmark" principle
  `backtest/engine.py`'s own buy-and-hold account already uses).
- `backtest/run_options_backtest.run_backtest()` now takes an optional
  `spy_closes` map and reports `vs_buyhold` when supplied; `run_one_signal()`
  now carries `signal_date` on each trade so it can be paired back to the
  underlying's price on that date.

## Results — 7-day expiration horizon

25/25 signals produced a usable trade (0 skipped).

| Metric | Value |
|---|---|
| Trades | 25 |
| Wins / Losses | 16 / 9 |
| Win rate | **64.0%** |
| 95% Wilson CI | 44.5% – 79.8% |
| Total realized P&L | **+$8,606.31** |
| Same-capital buy-and-hold-SPY P&L (same windows) | +$48.72 |
| Delta (options − buy-and-hold) | **+$8,557.59** |

<details>
<summary>All 25 trades</summary>

| Signal date | Entry | Exit | Exit reason | Realized P&L |
|---|---|---|---|---|
| 2025-10-27 | 5.278 | 1.8026 | stop_loss | -$347.54 |
| 2025-10-28 | 5.349 | 1.4972 | stop_loss | -$385.18 |
| 2025-10-29 | 5.3084 | 2.1178 | stop_loss | -$319.07 |
| 2025-11-20 | 9.0538 | 4.3143 | stop_loss | -$473.95 |
| 2026-03-12 | 9.6729 | 4.1468 | stop_loss | -$552.61 |
| 2026-03-13 | 10.4443 | 4.3241 | stop_loss | -$612.02 |
| 2026-03-18 | 9.2974 | 4.2355 | stop_loss | -$506.19 |
| 2026-03-19 | 8.3535 | 8.9733 | expiration_last_bar | +$61.99 |
| 2026-03-23 | 8.6072 | 21.2858 | take_profit | +$1,267.87 |
| 2026-03-24 | 8.9929 | 20.222 | take_profit | +$1,122.92 |
| 2026-03-26 | 7.3892 | 15.1985 | take_profit | +$780.93 |
| 2026-04-08 | 6.2727 | 17.8285 | take_profit | +$1,155.58 |
| 2026-04-09 | 5.5114 | 14.4992 | take_profit | +$898.78 |
| 2026-04-10 | 6.0697 | 15.6714 | take_profit | +$960.16 |
| 2026-04-13 | 5.8667 | 14.6962 | take_profit | +$882.95 |
| 2026-04-14 | 5.7144 | 16.2525 | take_profit | +$1,053.81 |
| 2026-04-15 | 5.1765 | 11.426 | take_profit | +$624.95 |
| 2026-04-16 | 5.075 | 10.3228 | take_profit | +$524.78 |
| 2026-04-21 | 8.4245 | 7.88 | expiration_last_bar | -$54.45 |
| 2026-04-23 | 6.77 | 10.5001 | expiration_last_bar | +$373.01 |
| 2026-04-28 | 6.2524 | 11.7117 | expiration_last_bar | +$545.93 |
| 2026-04-29 | 4.669 | 9.6431 | take_profit | +$497.41 |
| 2026-05-04 | 5.6028 | 17.2276 | take_profit | +$1,162.48 |
| 2026-05-22 | 5.3084 | 10.6084 | take_profit | +$530.00 |
| 2026-06-10 | 8.7188 | 2.8565 | stop_loss | -$586.23 |

</details>

## Results — 30-45 day expiration horizon

Only **7 of 25** signals produced a usable trade — **18 were skipped**,
and it's a real finding, not a bug: for those 18, the ATM contract 30 days
out had **zero trading history on the actual signal date**. Checked one
directly — the 680-strike call expiring 2026-05-11 has its first bar on
2026-05-04, a week before its own expiration, even though the matching
signal fired on 2026-04-09. SPY's further-dated ATM weeklies apparently
don't carry real trading history that early. Per this project's
never-fabricate rule, `run_one_signal()` correctly skipped these rather
than guessing an entry price.

With only 7 trades, this horizon is **below the ~20-trade floor** the
project's own go-live gate already uses for a win rate to mean anything —
reported for completeness, not as evidence either way.

| Metric | Value |
|---|---|
| Trades | 7 |
| Wins / Losses | 3 / 4 |
| Win rate | 42.9% (not meaningful at n=7) |
| 95% Wilson CI | 15.8% – 75.0% |
| Total realized P&L | +$1,627.60 |
| Same-capital buy-and-hold-SPY P&L (same windows) | +$183.28 |
| Delta (options − buy-and-hold) | +$1,444.32 |

<details>
<summary>All 7 trades</summary>

| Signal date | Entry | Exit | Exit reason | Realized P&L |
|---|---|---|---|---|
| 2025-10-28 | 12.4236 | 5.6342 | stop_loss | -$678.94 |
| 2025-10-29 | 12.2206 | 5.6342 | stop_loss | -$658.64 |
| 2026-03-18 | 16.2603 | 3.0831 | stop_loss | -$1,317.72 |
| 2026-04-08 | 14.413 | 28.762 | take_profit | +$1,434.90 |
| 2026-04-15 | 13.0021 | 34.9281 | take_profit | +$2,192.60 |
| 2026-04-29 | 14.1592 | 29.7273 | take_profit | +$1,556.81 |
| 2026-06-10 | 15.3772 | 6.3631 | stop_loss | -$901.41 |

</details>

## Interpretation

- The 7-day horizon is the only one with enough trades (25, 0 skipped) to
  say anything: a 64% win rate (95% CI 44.5–79.8%, still wide at n=25) and
  a large P&L edge over buy-and-hold. Most of that edge is leverage, not
  magic — the same dollar capital buys far more directional exposure in
  an ATM option than in shares, so a real SPY uptrend (which is most of
  what this window contained) shows up massively amplified on the options
  side. The flip side of that leverage is real: several of the losing
  trades hit their -50% premium stop within days.
- The 30-45 day horizon isn't usable as tested — not because the strategy
  failed, but because the data needed to test it (real trading history on
  further-dated ATM SPY weeklies, that far ahead of expiration) mostly
  doesn't exist yet at signal time.
- Every limitation already listed in `OPTIONS_BACKTEST_DESIGN.md` still
  applies unchanged: SPY's Fundamentals leg is structurally omitted, ATM
  strike selection is a v1 default, the 3%-round-trip cost haircut is a
  stated policy choice rather than a bid/ask-calibrated number, and none
  of this touches `PaperBroker`, live switches, or order placement.
- This is one ~9-month window (bounded by how far back Polygon/Robinhood's
  historical option data actually goes), not the ">=3 distinct market
  regimes" the Milestone 5 go-live gate requires — this result alone
  doesn't clear that bar, even setting the options-vs-equity distinction
  aside.
