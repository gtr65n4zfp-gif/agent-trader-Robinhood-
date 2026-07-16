# SPY options backtest design

Blueprint only — no implementation yet, per instruction. This describes a
harness that replays the EXISTING council's decision logic (unchanged)
against SPY specifically, but simulates the signal as an options trade
instead of a share trade, using real historical SPY option prices. The
question this answers: would buying SPY calls/puts on the council's real
historical signals have been profitable, before any of it touches a live
account, paper or otherwise.

## Guiding principle

> The instrument changes; the trustworthiness bar doesn't. Same
> NO-LOOKAHEAD discipline as `BACKTEST_DESIGN.md` — everything here reuses
> that harness's already-proven signal generation unchanged. The only new
> surface is "given a signal that already happened, what would the options
> trade around it have looked like."

## Scope

**In scope:** a standalone, manually-run backtest producing P&L/win-rate
evidence for directional SPY call/put buying, parameterized by expiration
horizon (so a 7-day and a 30-45-day run are the same code, one parameter
changed).

**Explicitly deferred**, pending what this backtest shows: a live options
paper-trading layer, an options seat/risk model, any automation
integration. Nothing here touches `PaperBroker`, `execution/config.py`'s
live switches, or any `place_option_order` MCP call.

## Data feasibility (verified before writing this, not assumed)

Robinhood's MCP exposes **expired option contracts** via
`get_option_instruments(state="expired")` and real daily OHLC bars for
them via `get_option_historicals`, even for expirations no longer listed
in `get_option_chains()`'s current chain. Confirmed directly: a SPY
$620 call expiring 2025-07-18 (~1 year before this design was written)
returned real daily bars from June 2025 onward. This means the backtest
uses **actual historical option prices**, not a Black-Scholes or other
modeled approximation.

**Real limitations of this data**, to design around rather than discover
later:
- **No volume, no bid/ask** — only OHLC. There's no real spread to model
  costs from (see "Cost modeling" below).
- **Unverified depth** — confirmed back to ~1 year; not yet confirmed
  whether it goes back further. The engine should fetch-and-fail-loud per
  window rather than assume a depth that isn't there.

## Signal source, and a real open question about it

The engine reuses `backtest/data.py` and the existing seat functions
(`agents.regime`, `agents.technicals`, `agents.fundamentals_seat`,
`agents.judge`) completely unchanged, run against SPY — which is NOT
currently in `config.WATCHLIST`, so this is a standalone symbol run, not
a change to the live watchlist.

**Confirmed, not just predicted:** `build_brief("SPY")` resolves to a real
CIK (`0000884394`, SPDR S&P 500 ETF Trust) but every concept comes back
`null` — `Revenues`, `NetIncomeLoss`, `Assets`, `StockholdersEquity` — and
`recent_filings` is empty. An ETF trust doesn't file the 10-K/10-Q reports
those XBRL tags come from, so there's structurally nothing there. The
conjunctive gate (`judge.decide()` requires Fundamentals AND Technicals to
agree) would see a permanently neutral, zero-confidence Fundamentals leg
for SPY — meaning it would never fire, since `aligned` requires
`f_stance in ("bullish", "bearish")`.

**Decided:** this backtest uses `agents.technicals.build_view()` +
`agents.regime.regime_stance()` only for SPY, and calls `judge.decide()`
with Fundamentals omitted from the alignment check (a neutral stub, or a
small `judge`-level allowance for "technicals-only" symbols) — an
explicit, isolated exception for this SPY-only experiment, not a change to
`judge.py`'s behavior for any other symbol or to the live watchlist.

## Contract selection (per historical BUY/SELL signal)

1. SPY's closing price on signal date D — from the existing no-lookahead
   bar data (`backtest/data.py`), unchanged.
2. Target expiration = D + N calendar days (N is the run parameter — 7
   for the first pass, 30-45 as the fallback if 7 doesn't work out),
   snapped forward to the nearest date that actually has listed
   contracts.
3. Confirm via `get_option_instruments(expiration_dates=snapped_date,
   state="expired")`. No contracts found → skip and log this signal, same
   fail-safe convention `automation/run_pass.py` already uses for bad
   data — never guess, never substitute a different date silently.
4. Strike: nearest listed strike to D's close (ATM) for v1 — call for a
   bullish signal, put for bearish. (This backtest never shorts — a
   bearish council signal maps to buying a put, matching what a real
   options layer would actually do, not to shorting shares.)
5. Resolve to `instrument_id` via `get_option_instruments(strike_price=...)`.

## Entry and exit simulation

**Entry fill:** the contract's close price on D — mirrors
`BACKTEST_DESIGN.md`'s own timing convention exactly (decide on D's
close, fill at D's close), not a new assumption.

**Exit — whichever triggers first**, walking that contract's own daily
bars forward from D+1:
1. Stop-loss / take-profit on **premium** value — new, separately-tunable
   constants (e.g. `OPTIONS_STOP_LOSS_PCT`, `OPTIONS_TAKE_PROFIT_PCT`),
   not reused from `config.STOP_LOSS_PCT`/`TAKE_PROFIT_PCT`. Options move
   far more than the underlying share price; forcing the same % thresholds
   onto premium would be a real modeling error, not just imprecision —
   same reasoning `CONVICTION_DROP_THRESHOLD`'s own comment already gives
   for keeping entry/exit conviction bars independently tunable.
2. Hard exit at expiration if neither triggers — close at the last
   available bar, or intrinsic value (`max(0, SPY_close - strike)` for a
   call, `max(0, strike - SPY_close)` for a put) if the contract's bars
   run out before the actual expiration date.

**Explicitly out of scope for v1:** a conviction-drop exit (re-running
Fundamentals/Technicals daily against every open position). YAGNI for a
first pass — worth adding only if the simple stop/target/expiry version
shows something worth pursuing further.

## Cost modeling

No bid/ask or volume in the available data, so there's no real spread to
derive costs from. Apply an explicit, deliberate haircut on both entry and
exit fills — a flat % against the trader, **wider than** the equity
engine's `SLIPPAGE_BPS` (5bps), since option spreads run much wider in %
terms, especially away from the money. Stated plainly as an unvalidated
policy choice, same pattern as `config.py`'s own `SLIPPAGE_BPS` comment —
not calibrated against data, a placeholder to revisit once real numbers
exist.

## Output / metrics

Reuses `backtest/metrics.py`'s existing conventions where they apply:
- Per-trade P&L, aggregate total return, win rate with a 95% Wilson score
  confidence interval (small-n safe, same reasoning as the equity
  backtest's own metrics).
- Comparison against a buy-and-hold-SPY-shares baseline over the same
  window, using the equity engine's existing baseline machinery.
- Run twice (N=7, N=30-45) so the two horizons are directly comparable
  from one execution, not two separate one-off scripts.

## New files

- `backtest/options_data.py` — contract selection (expiration snapping,
  strike resolution) and historical option bar fetching, isolated from
  `backtest/data.py`'s equity-only bar/indicator logic.
- `backtest/options_engine.py` — the signal-to-trade-to-exit loop
  described above, consuming the existing council signal stream for SPY.
- `backtest/options_metrics.py` (or an addition to `backtest/metrics.py`
  if the existing functions generalize cleanly) — P&L/win-rate reporting
  for options trades specifically (premium-based, not share-based).

## Known limitations, stated plainly

- Historical option data depth beyond ~1 year is unconfirmed.
- No real bid/ask data means the cost model is a guess, not a
  calibration — same caveat class as `TARGET_DAILY_VOL_PCT` vs.
  `MIN_VOL_SCALAR` in `config.py` (one is derived from data, the other is
  policy).
- SPY's Fundamentals leg is confirmed structurally unusable (see "Signal
  source" above) — the technicals+regime-only fallback is a real
  deviation from how every other symbol is evaluated, worth remembering
  when comparing SPY options results back to the equity backtest's
  numbers, which do use the full three-seat gate.
- ATM strike selection is a v1 default, not a conclusion — different
  moneyness (slightly OTM for cheaper premium/more leverage) is a natural
  follow-up experiment once the basic harness works.

## Open questions for input before building

1. **Cost haircut size** — no data to calibrate from; proposing a
   placeholder (e.g. 3-5% round-trip on premium) unless there's a
   preferred starting number.
2. **How far back to actually pull** — pending confirmation of real data
   depth, how much history is "enough" to trust the win rate's confidence
   interval (a ~20-30 trade sample, per the equity backtest's own
   precedent, is a reasonable floor).
