# SPY options forecasting model — design (vol-edge strand)

Blueprint only — no implementation yet, per instruction. This describes a
DESIGN + BACKTEST strand, fully separate from the live equity automation
loop (`automation/run_pass.py`, its launchd schedule, `AUTOMATION_DRY_RUN`)
and from the live options paper-trading layer
(`docs/superpowers/specs/2026-07-17-live-spy-options-design.md`,
`automation/run_options_pass.py`, `OPTIONS_AUTOMATION_DRY_RUN`). Nothing
here is wired into either. **Stop after this document for review — Tasks
2-6 do not start until it's approved.**

## The core reframe

Every prior attempt in this project to trade SPY options forecast
**direction** — the OLS forecast-seat model predicted price movement
(failed its own promotion gate at both horizons,
`docs/superpowers/specs/2026-07-17-forecast-seat-results.md`), and the
existing options backtest's technicals+regime signal also forecasts
direction (37.6%/39.5% win rate once the bull-regime bias was corrected
for, `agents/OPTIONS_BACKTEST_RESULTS.md`). Both came up short.

This strand asks a different question. For an option, the payoff depends
on a **distribution** of outcomes, not a point forecast — and the market
already publishes its own view of that distribution's width: implied
volatility. VIX is the standardized, liquid, freely-available expression
of it for SPY specifically. The edge this strand looks for is not "will
SPY go up" — it's **"is my forecast of how much SPY will move different
from what the market is currently paying for, and am I right more than
I'm wrong."** That's the variance risk premium: implied vol running above
realized vol on average is one of the most well-documented, real effects
in index options — and also one of the most crowded trades that exists,
so a real average edge can still look terrible in any one tail event.
That tension is exactly why the backtest (Task 6) requires a genuine
down-trend/crash window, not just the bull stretch the original options
backtest's first pass mistakenly relied on.

## Guiding principle (same discipline, not a new one)

> The instrument and the question change; the trustworthiness bar
> doesn't. No-lookahead throughout (reuses `backtest/data.py`'s existing
> point-in-time truncation, unchanged). No fabricated fills. Isolated
> results — nothing here touches `logs/trades.jsonl`,
> `logs/paper_portfolio.json`, `logs/options_trades.jsonl`, or
> `logs/options_paper_portfolio.json`. Win rate reported with a Wilson CI,
> never a bare percentage. Every cost assumption stated as a policy choice
> or backed by real data, never hidden or silently tuned to flatter a
> result.

## Scope

**In scope:** a standalone, backtest-only build producing P&L/win-rate
evidence for a vol-edge-driven options strategy on SPY, reusing the
existing council's price-based seats (`agents.technicals`, `agents.regime`)
for the directional tilt, unchanged.

**Explicitly out of scope, this strand:** wiring into
`automation/run_pass.py` or `automation/run_options_pass.py`; any change
to `AUTOMATION_DRY_RUN` or `OPTIONS_AUTOMATION_DRY_RUN`; any
`place_option_order` call; any change to `PaperBroker`,
`OptionsPaperBroker`, `agents/risk_vetoer.py`, or
`agents/options_risk_vetoer.py`. If this backtest shows something real,
wiring it live is a separate, later decision — not assumed here.

## Data feasibility (verified before writing this, not assumed)

Same discipline as `agents/OPTIONS_BACKTEST_DESIGN.md`'s own "Data
feasibility" section — checked directly this session, not inferred:

- **SPY daily bars** — existing, unchanged (`backtest/data.py`,
  `backtest/data.parse_bars()`, Robinhood `get_equity_historicals`).
  **Depth confirmed sufficient for GARCH's warm-up need**, not assumed:
  fetched real SPY daily bars back to 2015-01-02 directly this session —
  real data, not a gap or a stub. That's roughly 9 years of trailing
  history before the backtest window even starts (2024-05-01), comfortably
  more than the ~2 years a rolling GARCH refit needs (see "Vol engine"
  below) at the window's very first decision date.
- **VIX (and VIX9D/VIX3M for term structure) — historically available,
  but not from either existing data source.** Robinhood exposes VIX only
  as a *live* index quote (`get_indexes` / `get_index_quotes` — confirmed
  directly: VIX = 18.77 as of 2026-07-17 16:15 ET), with no
  historical-index tool at all. Polygon's indices aggregates
  (`/v2/aggs/ticker/I:VIX/...`) returned `NOT_AUTHORIZED` on the current
  plan — the same class of gap that blocked options NBBO/quotes earlier
  this project (`agents/OPTIONS_BACKTEST_DESIGN.md`'s "Data feasibility"),
  confirmed directly, not assumed. **CBOE's own public daily-price CSVs**
  (`cdn.cboe.com/api/global/us_indices/daily_prices/{VIX,VIX9D,VIX3M}_History.csv`)
  are confirmed working right now: no API key, no MCP session, plain
  HTTP GET, data back to 1990-01-02, current through today — today's VIX
  close (18.77) matches Robinhood's live quote exactly, a strong
  cross-check that this is correct, real data. This becomes the strand's
  VIX data source — genuinely simpler than the Polygon-key-gated flow the
  original options backtest needed for option bars, since it needs no
  entitlement at all.
- **Option chain instruments** — existing, unchanged
  (`backtest/options_data.parse_option_instruments()`).
- **Option historical bars are OHLC only — no bid/ask, no volume, no
  implied volatility or greeks** (confirmed limitation, carried over
  unchanged from `agents/OPTIONS_BACKTEST_DESIGN.md`). This directly
  constrains Level 1 below: **ATM implied vol from the chain is only
  available live**, not historically — confirmed directly this session: a
  real `get_option_quotes` call for a live SPY 743-strike 7/24 call
  returned `implied_volatility: 0.141636`, a field that exists on live
  quotes but has no historical equivalent anywhere in this project's data
  access. So for this backtest strand, **the market-implied-vol
  benchmark is VIX/VIX9D/VIX3M, not per-contract ATM IV.** ATM IV becomes
  usable only once (if) this is ever wired into the live automation —
  explicitly out of scope here, not conflated with the backtest signal.

## Level 0 — forecast SPY realized vol → a return distribution

Two independent forecasters run at every decision date, not one — GARCH
must **earn** its place against a simple baseline, not be assumed
superior. Task 6 reports an explicit ablation between them (see
"Metrics" below); if GARCH doesn't beat the baseline, the design says so
plainly rather than picking a winner in advance.

**Engine A — GARCH(1,1)**, via the `arch` library (not currently a
dependency of this project — genuinely new, `pip install arch`, Task 2).
Fit on SPY daily log returns, forecasting the conditional variance over
the trade horizon, then annualized. GARCH's whole reason for existing
here is capturing **volatility clustering** — large moves cluster
together, small moves cluster together, a fact a flat trailing-window
estimate structurally can't represent (every day in the window gets
equal weight, recent shocks get diluted the same as old calm days).

**Engine B — the baseline (for the ablation)**: trailing N-day realized
vol from SPY's own daily log returns, annualized
(`std(log returns) * sqrt(252)`) — the same "start with the simplest
baseline" instinct the forecast-seat design used for its own first pass
(`docs/superpowers/specs/2026-07-16-forecast-seat-design.md`). N is
chosen per horizon track (a shorter trailing window for the 7-day track,
a longer one for the 30-45 day track — exact values are a Task 2 policy
choice, stated plainly, not fitted to any result). This is Engine A's
opponent, not a fallback — both run on every decision date, both feed
Task 6's ablation, and only ONE (whichever wins the ablation, or a
documented tie-break if neither clearly wins) actually drives the live
trading signal in Level 1.

### THE CRITICAL RULE: GARCH must be refit at every decision date, on trailing data only

This is the classic GARCH-backtest trap, named explicitly because it's
the #1 way a vol-forecasting backtest lies: fitting the model **once**
over the full history (including dates after the signal) leaks future
information into every "forecast" the backtest reports, since the
model's own parameters were shaped by data the trader wouldn't have had
yet. **Never done here.** At each decision date `D` in the pre-committed
backtest window:

1. Slice SPY's daily log returns to a trailing window ending at `D`
   (rolling, not expanding — see "Decisions locked in" for why) —
   strictly `returns[D - lookback : D]`, nothing dated after `D`.
2. Fit a fresh GARCH(1,1) on exactly that slice. `D`'s own fit knows
   nothing about `D+1` onward.
3. Forecast the conditional variance forward over the trade horizon from
   that fit, annualize it.
4. Discard the fit. The next decision date repeats from step 1 with its
   own trailing slice — no state, no parameters, and no fitted object
   carries over between decision dates.

Same no-lookahead discipline `backtest/data.py`'s `bars_through()`
already enforces for price data, applied to a fitted model instead of a
raw series — the model itself must never see the future, not just the
inputs it's fed at inference time. Task 6's report includes a direct
check that this held: the trailing window used for the fit at `D` never
extends past `D`, verified programmatically, not just asserted in prose.

### Horizon-aggregation — turning a 1-day GARCH forecast into an N-day one

GARCH(1,1) natively forecasts **one-step-ahead** conditional variance;
the trade horizon is 7 or 30-45 CALENDAR days (the same convention
`options_data.select_liquid_expiration()` already uses), but GARCH's own
recursion steps through TRADING days — so `N` below is
`trading_days_in_horizon(horizon_calendar_days)` (`backtest/vol_forecast.py`,
a stated `252/365` approximation, not a per-date trading-calendar lookup —
same tradeoff `execution/config.py`'s `market_is_open()` already accepts
elsewhere in this project), never the raw calendar-day horizon number
directly. Uses the `arch` library's own built-in multi-step forecasting
(`model_fit.forecast(horizon=N, method="analytic")`,
which propagates the GARCH(1,1) recursion forward analytically under the
standard assumption that the forecast day's variance still follows the
fitted recursion — not a hand-rolled aggregation) to get the forecasted
daily variance for each of the `N` horizon days, then **sums** those `N`
daily variances (variance of a multi-day return is the sum of the daily
variances under the model's own conditional-independence assumption,
the same identity `sigma_horizon = sigma_daily * sqrt(N)` uses when daily
vol is constant — here it isn't constant day-to-day, so the sum-then-
square-root is over the actual forecasted path, not a flat multiplier),
then annualizes via `sqrt(252 / N)` for comparison against VIX/VIX9D/VIX3M
and the baseline engine on the same trading-day-annualized scale — the
same `sqrt(252)` convention the baseline engine uses to annualize its own
daily estimate directly, so the two engines' outputs are genuinely
comparable, not just superficially both labeled "annualized." GARCH's own
annualized-and-horizon-scaled output is put on that same footing before
either one is compared to anything else, so the ablation in Task 6
compares like with like.

### From forecast vol to a return distribution

Whichever engine's forecast is used, the output becomes a lognormal
distribution over the horizon:
`log(S_T / S_0) ~ Normal(mu, sigma_horizon^2)`, with
`sigma_horizon = forecast_annualized_vol * sqrt(trading_days_in_horizon / 252)`
— **trading days, not calendar days** (a correction made during Task 2's
actual implementation, not left as originally drafted here: both this
formula and the GARCH horizon-aggregation above now consistently use the
standard `sqrt(252)`-trading-day annualization convention throughout, the
same one the variance-risk-premium literature uses when comparing
realized/forecast vol against VIX. The original draft of this formula
used `horizon_days / 365` — calendar days — which would have quietly
mixed two different day-count bases between this formula and the
GARCH/baseline engines, whose own outputs are trading-day annualized.
`trading_days_in_horizon()` is the single, shared conversion — implemented
in `backtest/vol_forecast.py` — used everywhere a calendar-day option
horizon needs to become a trading-day count). `mu` is fixed at
(approximately) zero/risk-free drift, deliberately — Level 0 answers
**"how wide,"** not **"which way."** Direction is Level 1's job, kept
structurally separate so a directional bet and a vol bet are never
silently conflated into one number.

## Level 1 — the IV-edge signal, and the directional/regime tilt

**Which engine's forecast drives the trade signal:** both GARCH and the
baseline run at every decision date and both feed Task 6's ablation
report, but only ONE number actually becomes `forecast_RV` for the live
trading decision below — whichever engine Task 6's ablation shows
forecasts realized vol more accurately (lower error against what
actually played out, see "Metrics"). If Task 2/6 finds GARCH doesn't
beat the baseline, the baseline drives the signal instead — this design
does not assume GARCH wins going in.

**IV-edge:** `forecast_RV - market_implied_vol`, both already annualized
percentages, directly comparable, no unit conversion needed (VIX and its
siblings are quoted as annualized-vol percentages by CBOE's own
convention). Horizon-matched, not a flat "always compare to 30-day VIX"
approach — the 7-day track compares against **VIX9D**, the 30-45 day
track compares against **VIX** (the classic 30-day index) or **VIX3M**
depending on which is closer to that track's actual DTE at signal time.
Comparing a 7-day forecast against a mismatched 30-day implied figure
would be exactly the kind of subtlety worth getting wrong quietly — this
avoids it using data that's already confirmed free and available for
every relevant tenor.

- `forecast_RV` materially **below** implied → premium is rich → favor
  the DEFINED-RISK credit structure.
- `forecast_RV` materially **above** implied → premium is cheap → favor
  the debit structure.
- A stated minimum gap (a Task 2 policy constant, not derived from data,
  same caveat class as `config.MIN_VOL_SCALAR`) is required before either
  side triggers — avoids trading on a gap too small to survive costs.

**Directional/regime tilt:** `agents.technicals.build_view()` and
`agents.regime.regime_stance()`, reused completely unchanged — both are
already price-based and already point-in-time-safe via
`backtest/data.py`'s existing truncation, exactly the same functions
`backtest/options_engine.technicals_only_decision()` already calls for
SPY. This strand does not reimplement or fork them. The tilt decides
which side of a structure to take (bullish tilt → sell a bull put spread
or buy a call; bearish tilt → sell a bear call spread or buy a put); a
non-tradeable regime (`ranging` / `low_vol_ranging`) sits out entirely,
same "windshield" principle `agents/regime.py` already documents — this
also resolves the case where the tilt is genuinely ambiguous while
premium still looks rich or cheap.

## Level 2 — decision, structure selection, and valuation

**Structure set — deliberately minimal, two structures only:**

1. **Debit (cheap premium): a single long call or put, ATM.** Reuses
   `options_engine.simulate_option_trade()` completely unchanged — this
   is exactly what the existing options backtest already simulates.
   Already defined-risk (max loss = premium paid) with zero new
   simulation code needed.
2. **Credit (rich premium): a two-leg vertical spread** (bull put spread
   on a bullish/neutral tilt, bear call spread on a bearish/neutral
   tilt) — sell the near strike, buy a further OTM strike of the same
   type/expiry as protection, so max loss is capped at the strike width
   minus the credit received. This is genuinely new engineering surface:
   `simulate_option_trade()` only ever tracks one leg. A new two-leg
   simulation is needed (Task 2/5), netting both legs' entry credit and
   exit cost, with stop/target/expiration rules applied to the **spread's
   net value**, not each leg independently.

**Valuation — is the candidate actually mispriced relative to my own
forecast?** Price the candidate structure using a standard Black-Scholes
formula, but with **my own forecast sigma** (from Level 0) as the
volatility input instead of the market's implied one — this is a new,
small, self-contained function (no existing code in this repo prices an
option; this is new surface, not a reuse). Compare that "my model price"
to the actual historical market premium (the option's own close on the
signal date, from `options_data.parse_option_bars()`, same data already
used for entry fills):
- Debit case: `edge = my_model_price - market_price`. Positive edge means
  I think it's worth more than it costs — buy.
- Credit case: `edge = market_price - my_model_price`. Positive edge
  means I think the premium collected is worth more than the risk I'm
  taking on — sell.

A positive edge, past the same materiality threshold Level 1 uses, is
what makes a candidate an actual trade — the IV-edge signal picks the
**side** (buy vol vs. sell vol), this valuation step confirms the
**specific candidate** is priced favorably relative to my own forecast
distribution, not just that vol looks directionally rich or cheap in the
abstract.

## Horizon and expiration selection

Unchanged: `options_data.select_liquid_expiration()`, reused exactly as
the existing options backtest already uses it — Friday/monthly
expirations only, the Monday/Wednesday-weekly exclusion and the
point-in-time listing check (`verify_listed_as_of()`) both carry over
with zero modification. Same two tracks as the live options layer's own
design (7-day, 30-45 day) for consistency across the project, though this
strand's backtest can run either or both independently.

## Cost modeling — fills

Debit leg: reuses `options_data.estimate_haircut_pct()` unchanged, same
entry/exit haircut logic already validated in the existing options
backtest. Credit spread: each leg needs its own entry/exit haircut
applied (widening the spread's net entry credit down and net exit cost
up, symmetric to the single-leg case), then netted — a small, direct
extension of the existing function's usage, not a new cost model. No real
NBBO exists historically (confirmed, see "Data feasibility" above and
`agents/OPTIONS_BACKTEST_DESIGN.md`'s own finding) — SPY's spreads are
tight but nonzero, so the estimate still widens on both legs rather than
assuming a free two-leg fill.

## Metrics

Reuses `backtest/options_metrics.summarize_option_trades()`,
`backtest/metrics.wilson_ci()`, and
`backtest/options_metrics.compare_to_buyhold()` largely unchanged — the
credit spread's `realized_pnl`/`entry_fill` need a documented convention
(net credit as a negative "cost," net debit-to-close as a negative
"proceeds," so the existing win/loss and P&L math applies without a
special case), but the aggregation layer itself doesn't need to be
rebuilt.

**New:** a first-class per-regime **and** per-side breakdown function.
`agents/OPTIONS_BACKTEST_RESULTS.md`'s Round 2 already reported this kind
of breakdown, but it was assembled by hand for that one write-up, not
built as reusable code — Task 6 formalizes it, since the go-live gate's
"≥3 regime coverage" question and the "does this only work on the buy
side" question are exactly what already mattered once in this project's
own history and will matter again here.

**The naive-always-long-premium TRADING baseline already exists — no new
run needed** (distinct from Engine B, the trailing-RV *vol-forecast*
baseline above — two different things both called "baseline," disambiguated
by name throughout this document from here on: **trading baseline** vs.
**vol-forecast baseline**). The existing options backtest (Round 2: 37.6%
win rate 7-day, 39.5% win rate 30-45 day,
`agents/OPTIONS_BACKTEST_RESULTS.md`) is precisely "buy premium on every
qualifying technicals+regime signal, no vol-edge filter at all." This
strand's job is to show whether adding the vol-edge/valuation layer beats
that number, on the same underlying signal stream and window, not to
construct a new trading baseline from scratch.

**Forecast-vs-realized logging**, so calibration can be checked later
without a re-run: for every signal evaluated (traded or not), log
**both** engines' forecasts (`garch_forecast_rv`, `baseline_forecast_rv`),
`forecast_sigma_horizon` for whichever drove the trade signal,
`market_implied_vol`, `edge`, and the ACTUAL realized vol that played out
over `[signal_date, signal_date + horizon]` once that window has passed
— computed as the annualized standard deviation of SPY's own daily log
returns over exactly that forward window, using only data through
`signal_date + horizon` (this measurement happens strictly after the
fact, for evaluation only; it is never available to, or used by, the
decision made at `signal_date` itself). This doesn't gate Task 6's
headline results, but it's the raw material for a future, separate "was
my vol forecast actually any good" check.

**The GARCH ablation — required, not optional, and reported honestly
either way.** Task 6 answers two separate questions, using the
forecast-vs-realized log above:
1. **Forecast accuracy:** across every signal, which engine's forecast
   was closer to the realized vol that actually happened? Reported as
   each engine's mean absolute error (and root-mean-squared error)
   against the logged realized-vol figure, plus a simple win-count
   (how often GARCH's forecast was closer than the baseline's, out of
   the total).
2. **Trade P&L:** running Level 1-2's decision logic with GARCH driving
   `forecast_RV` versus running the identical logic with the baseline
   driving it instead (two parallel decision streams over the same
   signal set, same window, same cost model — the only thing that
   differs is which vol forecast feeds the edge calculation) — win rate,
   Wilson CI, and total P&L for each.

**If GARCH doesn't beat the baseline on accuracy, P&L, or both, Task 6's
writeup says so as plainly as `agents/OPTIONS_BACKTEST_RESULTS.md`'s
Round 2 said the original 64% win rate was a bull-regime artifact** —
this design does not pre-decide that GARCH is worth its added complexity
(a new dependency, a rolling-refit loop, real fitting time) before the
ablation actually says so.

## Backtest window

Pre-committed, stated here before any data is fetched or any result is
seen: **reuse the existing options backtest's Round 2 extended window**
(2024-05-01 → 2026-06-10, confirmed to contain a real crash/down-trend
period — the 2025-04-02→04-07 selloff and its reversal — not just a bull
stretch) rather than choosing a new window, which would risk unconsciously
picking a range that flatters this strategy specifically. Same signals
(technicals+regime, unchanged) will fire on the same dates as Round 2
already documented; this strand adds the vol-edge/valuation filter on
top of that already-generated, already-verified signal stream.

## Token/cost estimate

Two genuinely different kinds of cost here, worth separating rather than
folding into one number — GARCH refitting is local computation, not
agent-mediated data fetching, and conflating them would overstate what
GARCH actually adds:

**Agent-mediated token cost** (unchanged in kind from the prior version
of this estimate — this is the real one, since every fetch round-trips
through the conversation): grounded in Round 2's own real call counts
(`agents/OPTIONS_BACKTEST_RESULTS.md`), not a guess — that pass needed
roughly 94 signals' worth of single-leg option-instrument and
option-historicals lookups (~150-200 MCP/Polygon calls total across
contract resolution and bar fetches). This strand adds: one-time
VIX/VIX9D/VIX3M CSV fetches (three plain HTTP GETs, effectively free, no
MCP involved), plus a second leg's instrument+historicals lookup on
whichever subset of the ~94 signals land on the credit-spread side
(rich-premium case) — likely on the order of 1.3-1.8x Round 2's original
call volume, so roughly **150-250 additional agent-mediated fetches**
across the full pre-committed window. Same interactive drive-and-pass-
raw-JSON pattern as Round 2 (no MCP call happens inside any module code),
likely spanning multiple sessions/background jobs given Polygon's 5
req/min pacing if Robinhood's `get_option_historicals` is unavailable
that day (as it was for part of Round 2). GARCH itself adds **zero**
tokens here — the rolling refit never touches an MCP tool or the
conversation, it's pure `arch`-library computation over data already
fetched once.

**GARCH's own compute cost** (local CPU, not tokens, not gated by any
rate limit): ~94 signals × up to 2 tracks × one refit each ≈ 100-200
individual GARCH(1,1) fits over the full backtest, each on a ~2-year
trailing slice (~500 daily observations, see "Decisions locked in").
`arch`'s GARCH(1,1) fits in roughly tens to a few hundred milliseconds
on that data size — the whole backtest's GARCH refitting totals on the
order of **low tens of seconds of CPU time**, negligible next to the
token-cost side above and not something that needs pacing or background
jobs the way the MCP fetches do.

## New files

- `backtest/vol_forecast.py` — both Level 0 engines: the GARCH(1,1)
  rolling-refit forecaster (via `arch`) and the trailing-window
  vol-forecast baseline, sharing one interface so Task 6 can run either
  interchangeably through the same decision logic. The no-lookahead
  refit discipline described above lives here, in one place, not
  duplicated per track.
- `backtest/vix_data.py` — parses the already-fetched CBOE CSV text (one
  parser, reused for VIX/VIX9D/VIX3M — same shape) into a clean,
  point-in-time-safe `{date: {open, high, low, close}}`, truncated the
  same no-lookahead way `backtest/data.py`'s bar functions already are.
  Agent-mediated in spirit (the module doesn't fetch anything itself, a
  driving script/session does the HTTP GET and passes the raw text in) —
  though notably the fetch itself needs no MCP session or API key, unlike
  every other agent-mediated parser in this project. (Built in Task 3.)
- `backtest/vol_edge_signal.py` — Level 1's IV-edge calculation
  (`iv_edge()`, `premium_signal()`), the horizon-matched market-implied-vol
  lookup (`market_implied_vol()`, picking VIX9D/VIX/VIX3M by closest
  nominal maturity to the signal's ACTUAL resolved days-to-expiration, not
  just its nominal 7/30/45-day label), and `vol_edge_decision()`, which
  combines that with the UNCHANGED
  `options_engine.technicals_only_decision()` to produce a structure/side
  decision. Not named as a separate file in Task 1's original list — added
  here since Level 1's combination logic turned out to be a real,
  self-contained unit once actually built, not something that belonged
  bolted onto `options_engine.py`. (Built in Task 3.)
- `backtest/options_valuation.py` — the Black-Scholes-with-my-own-sigma
  pricing function (`black_scholes_price()`, r=0, stdlib `math.erf` for
  the normal CDF, no new dependency), and the edge calculation built on
  it (`single_leg_edge()` for the debit structure, `spread_model_value()`
  / `spread_edge()` for the credit spread). Genuinely new; nothing in
  this repo priced an option before this. (Built in Task 4.)
- An addition to `backtest/options_data.py` — `select_spread_strikes()`,
  the credit spread's 2-leg strike selection (sold leg reuses
  `select_contract()`'s existing ATM logic unchanged; the protective
  leg is the closest listed strike clearing a stated 1% minimum width,
  never a narrower substitute). Not a new file — this genuinely belongs
  alongside `select_contract()`, the file that already owns contract
  selection. (Built in Task 4.)
- `backtest/options_spread_engine.py` — the two-leg credit-spread
  SIMULATION (`simulate_spread_trade()`: day-by-day fill walk, matched
  by date across both legs, stop/target/expiration), parallel to but
  never modifying `options_engine.simulate_option_trade()`. Stop/target
  apply to the SPREAD's net value (sold leg close minus bought leg
  close), using the exact same `pnl_pct` sign convention the debit
  engine already uses, so `config.OPTIONS_STOP_LOSS_PCT`/
  `OPTIONS_TAKE_PROFIT_PCT` apply unchanged to both structures. Verified
  against a real fetched SPY 615/605 put spread (2026-05-15 expiration,
  real daily closes 2026-04-20→05-01): both legs decayed steadily in the
  real market, producing a real positive P&L, not a synthetic
  best-case fixture. (Built in Task 5.)
- An addition to `backtest/options_metrics.py` — the per-regime/per-side
  breakdown function, and the GARCH-vs-vol-forecast-baseline ablation
  reporting (forecast-error comparison plus the two parallel P&L runs).

## Known limitations, stated plainly

- GARCH(1,1) is a genuinely new dependency (`arch`, not currently used
  anywhere in this project) and a genuinely new failure surface — a
  rolling refit at ~100-200 decision-date/track combinations could hit a
  convergence failure or a degenerate fit on some slice; Task 2 needs a
  documented fallback (skip that signal, logged, never a silent
  fabricated forecast — same fail-safe convention as everywhere else in
  this project) rather than assuming every fit succeeds cleanly.
- The market-implied-vol benchmark is VIX/VIX9D/VIX3M, not per-contract
  ATM IV, for this backtest strand specifically — a real, data-driven
  constraint (ATM IV isn't available historically), not a stylistic
  choice; worth remembering if this is ever compared against a live
  ATM-IV-based version later.
- The credit spread's cost model widens each leg independently using the
  same entry-day-range heuristic `estimate_haircut_pct()` already uses —
  not a joint two-leg spread-cost model, which real market microstructure
  would price somewhat differently (spreads on multi-leg orders aren't
  simply the sum of each leg's independent spread). Stated here, not
  hidden.
- Reuses Round 2's exact signal stream and window — this backtest cannot
  show whether a *different* technicals+regime signal (or a purely
  vol-driven entry with no directional gate at all) would do better; that
  would be a legitimate, separate follow-up experiment, out of scope here.
- SPY vol-selling is a well-known, heavily arbitraged trade — a real
  average edge in this backtest is still consistent with severe tail risk
  in an untested future regime. The multi-regime window is meant to
  surface this, not eliminate the risk of it.
- GARCH(1,1) is itself a simplification (constant long-run variance,
  symmetric response to positive/negative shocks) — asymmetric variants
  (GJR-GARCH, EGARCH, which react more to down-moves than up-moves,
  arguably a better fit for equity index vol) are a natural follow-up,
  explicitly deferred — YAGNI until GARCH(1,1) itself has proven it beats
  the simple baseline.

## Decisions locked in

1. **Vol engine: GARCH(1,1) via `arch`, rolling-refit at every decision
   date on a trailing window, run against a trailing-realized-vol
   vol-forecast baseline** — neither is assumed to win; Task 6's ablation
   decides which one drives the actual trading signal.
2. **Rolling window, not expanding, fixed at ~2 years (~504 trading days)
   of trailing daily returns per refit** — a deliberate choice, not a
   coin flip: GARCH's entire reason for existing here is capturing
   *current* volatility clustering, and an expanding window would dilute
   that signal with a decade of increasingly stale history by the end of
   the backtest window. Confirmed real SPY data reaches back to at least
   2015 (see "Data feasibility"), so even the window's earliest decision
   date has a full ~2-year trailing slice available, never a truncated one.
3. **Realized-vol evaluation metric (for the ablation and for
   calibration logging): annualized standard deviation of SPY's own
   daily log returns over the forward window
   `[signal_date, signal_date + horizon]`**, computed strictly after that
   window has closed — the yardstick both engines' forecasts are judged
   against.
4. **Market-implied-vol source: VIX/VIX9D/VIX3M via CBOE's free public
   CSVs**, not Polygon (blocked) or ATM IV (unavailable historically).
5. **Structure set: exactly two** — single-leg long call/put (debit,
   cheap premium) and a two-leg vertical credit spread (rich premium,
   defined-risk). No iron condors, no calendars, no naked shorts.
6. **Directional tilt: `agents.technicals` + `agents.regime`, unchanged,
   zero forking.**
7. **Backtest window: Round 2's existing extended window
   (2024-05-01 → 2026-06-10)**, chosen before seeing any new results.
8. **Trading baseline: the existing Round 2 options backtest results**,
   not a newly-run ablation — distinct from the vol-forecast baseline in
   decision 1 above.
9. **Valuation: Black-Scholes with my own forecast sigma** (from
   whichever engine wins the ablation), compared against the real
   historical market premium.

No open questions remain — ready for review.
