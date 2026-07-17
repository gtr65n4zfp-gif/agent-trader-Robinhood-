# Live SPY options paper-trading layer — design

Blueprint only — no implementation yet. Companion to
`agents/OPTIONS_BACKTEST_DESIGN.md` / `agents/OPTIONS_BACKTEST_RESULTS.md`
and `docs/superpowers/specs/2026-07-16-forecast-seat-design.md` (both
same underlying research, same session). This is the step both of those
explicitly deferred: a live (paper-only) options-trading pass for SPY.

## Guiding principle

> Forward-test what the backtest already showed, honestly. The corrected,
> extended options backtest found no proven edge (sub-50% win rate once
> bull-market bias was removed). That doesn't block building this — paper
> trading an unproven strategy to keep gathering real evidence is exactly
> what this project's paper-first principle is for. It means going in
> with the right expectation: this is evidence-gathering forward-testing
> of something we already have real reason to be skeptical of, not
> deployment of a validated edge. Real money is nowhere near this.

## Scope

**In scope:** a fully isolated, paper-only daily automation pass trading
SPY options on the technicals+regime-only strategy already backtested
this session (`backtest.options_engine.technicals_only_decision()`,
unchanged), running **two independent horizon tracks off the same daily
signal — 7-day and 30-45-day — each with its own one-open-position-at-a-time
slot** (so up to 2 concurrent positions total, never 2 in the same
track). Added deliberately to grow toward the go-live gate's ≥30-trade
threshold faster, without loosening the signal itself — same confidence
bar, same regime filter, just a second independent expiration track off
signals that already qualify.

**Explicitly out of scope:** any real order (`place_option_order` is
never called), any change to the equity watchlist's account/risk/state,
the forecast-seat model (it failed its own promotion gate — there is no
validated model to plug in, see
`docs/superpowers/specs/2026-07-17-forecast-seat-results.md`), a
conviction-drop exit path, more than one position per horizon track, and
any symbol other than SPY.

## Decision logic: Technicals + Regime only

Reuses `backtest.options_engine.technicals_only_decision()` unchanged —
the version actually exercised by the real backtest (sub-50% win rate,
not proven, but a complete, tested path), not `spy_forecast_decision()`
(which requires a fitted, promoted forecast model that doesn't exist).

## New files

```
automation/run_options_pass.py     — daily entrypoint: exit sweep, then entries
execution/options_paper_broker.py  — OptionsPaperBroker: contracts, not shares
agents/options_risk_vetoer.py      — dedicated risk gate for options units
automation/demo_run_options_pass.py — end-to-end proof, mirrors demo_run_pass.py
```

**Reused, unchanged:** `agents.regime.regime_stance()`,
`agents.technicals.build_view()`,
`backtest.options_engine.technicals_only_decision()`,
`backtest.options_data.select_liquid_expiration()` / `select_contract()`,
`execution.robinhood` (live indicator fetchers), `execution.trade_log`,
`execution.config.OPTIONS_STOP_LOSS_PCT` /
`OPTIONS_TAKE_PROFIT_PCT` / `OPTIONS_ROUNDTRIP_HAIRCUT_PCT` /
`OPTIONS_CONTRACT_MULTIPLIER` (all already exist from the backtest work).

**Isolation, matching this whole session's pattern:** separate portfolio
file (`logs/options_paper_portfolio.json`), separate trade log
(`logs/options_trades.jsonl`), separate automation entrypoint, separate
dry-run flag. Never touches `logs/paper_portfolio.json` or
`logs/trades.jsonl`.

## Signal generation (live)

Same agent-mediated pattern `automation/run_pass.py` already uses for
equities: the scheduled routine fetches SPY's live quote/EMA/RSI/ATR/
regime-EMA via Robinhood MCP and passes them in (same shape as
`run_pass.py`'s `BUNDLE_HELP`, minus `fundamentals_verdict` — SPY has
none to fetch, per the backtest design's own established finding).

```python
regime     = agents.regime.regime_stance("SPY", price, ema=regime_ema, atr_pct=atr_pct)
technicals = agents.technicals.build_view("SPY", price, ema=ema, rsi=rsi, atr_pct=atr_pct)
decision   = backtest.options_engine.technicals_only_decision(technicals, regime)
```

## Concurrency policy: one open position per horizon track

Before evaluating a new entry for a given track (7-day or 30-45-day),
check whether that track already has a position open. If so, skip entry
evaluation for that track only — the other track and the exit sweep are
unaffected. In practice this means one qualifying signal can open up to
2 positions the same day (one per track), but never a second position in
a track that's already holding one. Both tracks share the same cash pool
and the same risk gate — this is one account with two independent
position slots, not two accounts.

## Contract selection (live)

Simpler than the backtest's — no Polygon dependency for the primary path,
since "listed right now" is exactly what a live active-state query
answers (unlike the backtest's need to verify point-in-time listing on a
past date). Run once per track that needs a new entry (so up to twice on
a day both tracks are flat and the signal qualifies):

1. `expiration = options_data.select_liquid_expiration(today, horizon_days=N)`
   for that track's `N` (7 or 30) — pure calendar math, no API call.
2. Strike guess = `round(spot)`; type = call (bullish) / put (bearish).
3. **Live** `get_option_instruments(chain_symbol="SPY", expiration_dates=expiration, type=option_type, strike_price=..., state="active")`.
4. No match → skip this track's entry, log why, never substitute a
   different strike/expiration.
5. Live quote for the resolved contract (`get_option_quotes`) for the
   entry fill.

**Polygon fallback — diagnostic only, not trade-unblocking.** If
Robinhood's `get_option_instruments` is down (this happened once already
this session, with `get_option_historicals`), fall back to Polygon's
`/v3/reference/options/contracts` to confirm the strike/expiration should
exist. This does NOT unblock the trade: Polygon uses OCC tickers, not
Robinhood instrument UUIDs, and a live fill still needs a Robinhood
instrument ID and a Robinhood live quote. The entry is still skipped that
day either way — the Polygon check only upgrades the skip's logged reason
from an opaque failure to "Robinhood outage, contract confirmed to exist
via Polygon." Never used for quotes/fills — confirmed this session that
the current Polygon plan has no live quote/NBBO entitlement (`403` on
`/v3/quotes`, `/v2/last/nbbo`, `/v3/snapshot/options`).

## Options paper broker (`execution/options_paper_broker.py`)

Mirrors `execution.paper_broker.PaperBroker`'s structure; units are
contracts, and state tracks **two independent optional position slots,
keyed by horizon track** (matching the one-per-track concurrency
policy), sharing one cash balance:

```
logs/options_paper_portfolio.json:
{
  "cash": float,
  "open_positions": {
    "7":  {"contract_id", "strike", "type", "expiration_date",
           "quantity", "entry_fill", "entry_date"} | null,
    "30": {"contract_id", "strike", "type", "expiration_date",
           "quantity", "entry_fill", "entry_date"} | null
  },
  "peak_equity", "day_date", "day_start_equity"
}
```

**Methods**, mirroring `PaperBroker`'s shape, now taking a `track`
parameter (`"7"` or `"30"`) to address the right slot:
- `buy_to_open(track, contract_id, strike, type, expiration_date, quantity, entry_fill, now)`
  — runs `agents.options_risk_vetoer` first (raises `OptionsTradeError`
  and logs a veto if blocked, exactly like the equity broker's
  `_check_risk()`), then deducts cash and opens that track's position.
  Raises if the track already has an open position — the concurrency
  guard is enforced here, not just hoped for by the caller.
- `close_position(track, exit_fill, reason, now)` — credits cash,
  computes realized P&L, logs the round-trip, clears that track's slot.
- `account(current_marks: dict[str, float])` — total value (cash +
  mark-to-market of whichever slots are occupied, one current price per
  occupied track), used by the risk gate's drawdown/daily-loss checks.

## Options risk vetoer (`agents/options_risk_vetoer.py`)

Mirrors `agents.risk_vetoer`'s principles, scaled for options units:

- **Trade-size cap**: `quantity × OPTIONS_CONTRACT_MULTIPLIER × entry_fill ≤ OPTIONS_MAX_TRADE_USD`.
- **Position cap**: same trade's dollar size, as a fraction of current
  account value, `≤ OPTIONS_MAX_POSITION_PCT`.
- **Daily loss breaker**: same drawdown-from-`day_start_equity` concept
  as the equity vetoer, computed from `OptionsPaperBroker.account()`.
- **Frequency cap**: `OPTIONS_MAX_TRADES_PER_DAY = 2` — one qualifying
  signal opening both tracks the same day is expected, legitimate
  behavior, not churn; this cap's real job is still blocking same-day
  re-entry right after a same-day stop-out (which would otherwise let a
  single track open, stop out, and re-open twice in one day).

**Explicitly not ported** from the equity vetoer: sector-concentration
caps (meaningless for a single-symbol account) and ATR-based share-count
sizing (position size here is just "1 contract," not a computed share
count). A blocked trade raises `OptionsTradeError` and logs a `veto`
record to `logs/options_trades.jsonl`.

**New config constants** (`execution/config.py`), values locked in now
rather than left to implementation time:

- `OPTIONS_PAPER_STARTING_CASH = 10000` — matches the equity account's
  own `PAPER_STARTING_CASH` default; no reason for this account to start
  larger or smaller.
- `OPTIONS_MAX_TRADE_USD = 2500` — comfortably clears the highest real
  single-contract cost observed in the backtest ($1,626), with headroom;
  the equity account's $1,000 cap would have rejected several of the
  backtest's real trades outright.
- `OPTIONS_MAX_POSITION_PCT = 0.25` — consistent with the $2,500/$10,000
  ratio above, applied per-trade at the moment it opens. With two tracks
  now able to hold positions simultaneously, total exposure can reach up
  to ~50% of account value (two $2,500 positions against $10,000) — a
  deliberate, stated tradeoff of running two tracks, not an oversight.
  Each individual position is still capped at 25%; nothing here bounds
  the *sum* of both tracks, since they're independent bets by design.
- `OPTIONS_AUTOMATION_DRY_RUN = True` (mirrors `AUTOMATION_DRY_RUN`).

## Automation entrypoint (`automation/run_options_pass.py`)

Same fail-safe skeleton as `run_pass.py`, single symbol:

1. `config.assert_paper_mode()` — abort the whole pass if live trading is
   somehow armed.
2. Market-hours guard — outside regular hours, logged no-op, nothing
   else runs.
3. SPY data-sanity check — bad/stale quote skips the whole pass cleanly.
4. `OPTIONS_AUTOMATION_DRY_RUN` (default `True`) — every decision still
   made and logged in full; `OptionsPaperBroker` methods never actually
   called until deliberately armed.
5. Exit sweep: for each track with an open position, live quote →
   compare vs. entry → close on stop-loss / take-profit /
   expiration-reached. Both tracks checked independently, every pass.
6. Entries: regime → technicals → `technicals_only_decision()` computed
   once (shared by both tracks — it's the same underlying signal); then
   for each track that's currently flat, contract selection for that
   track's horizon → risk gate → open. A track that already holds a
   position is skipped for entries that day regardless of what the
   signal says.

## Fill/cost modeling (live)

Uses **real bid/ask when the live quote has it** — buy fills at ask,
sell fills at bid, the natural real spread against the trader, strictly
more honest than any backtest estimate. Falls back to
`mark_price ± half of OPTIONS_ROUNDTRIP_HAIRCUT_PCT` only if a quote
comes back without bid/ask, logged distinctly so a fallback fill is
never mistaken for a real-spread one.

Exit thresholds reuse `OPTIONS_STOP_LOSS_PCT`/`OPTIONS_TAKE_PROFIT_PCT`
unchanged. Expiration-day handling: if today is the contract's
expiration and neither threshold has fired, close at the live quote —
the live equivalent of the backtest's `expiration_last_bar` path.

## Testing

Matching existing conventions exactly:
- `execution/options_paper_broker.py`, `agents/options_risk_vetoer.py` —
  `if __name__ == "__main__":` self-tests (assert/PASS with an em dash,
  no pytest), same as every other module this session built.
- `automation/run_options_pass.py` — proven via
  `automation/demo_run_options_pass.py`, mirroring the existing
  `automation/demo_run_pass.py` precedent: fabricated but deterministic
  inputs exercising exit-sweep-then-entries ordering across both tracks
  independently, one qualifying signal correctly opening both tracks the
  same day, a track with an open position correctly skipped for entries
  while the other track still opens, both fail-safes (market-hours
  no-op, bad-data skip), dry-run logging with zero execution, and the
  Polygon-fallback diagnostic path.

## Known limitations, stated plainly

- This forward-tests a strategy the backtest already showed has no
  proven edge — deliberate evidence-gathering, not deployment of
  something validated. That's the whole point of doing it in paper.
- One position per horizon track, two tracks total — a deliberate step
  up from the original single-track design specifically to reach the
  go-live gate's ≥30-trade threshold faster, not a claim about optimal
  concurrency. The two tracks are correlated with each other (same
  underlying signal, same day), so they don't count as fully independent
  evidence toward that threshold — closer to 1.5 independent trades than
  2, informally.
- Combined exposure across both tracks can reach ~50% of account value
  even though each position is individually capped at 25% — stated
  plainly above, not hidden.
- No conviction-drop exit (re-running Technicals+Regime daily against an
  open position) — matches the backtest's own "out of scope for v1"
  call, same reasoning.
- Real bid/ask availability depends on what Robinhood's live quote
  actually returns day to day; the haircut fallback is exactly as
  unvalidated as it was in the backtest.
- The Polygon fallback is diagnostic only — it cannot unblock a trade if
  Robinhood's live tools are down, only improve the logged reason for
  that day's skip.

## Decisions locked in

1. Decision logic: `technicals_only_decision()` (Technicals + Regime
   only) — not the forecast-seat wrapper, which has no validated model.
2. Fully isolated automation pass, portfolio file, and trade log —
   never touches the equity watchlist's state.
3. New, dedicated `agents/options_risk_vetoer.py` — not an extension of
   the existing equity risk vetoer.
4. **Two horizon tracks — 7-day and 30-45-day — run concurrently off the
   same daily signal**, each with its own one-position slot, sharing one
   cash pool and one risk gate. Added specifically to grow toward the
   go-live gate's ≥30-trade threshold faster, without loosening the
   signal quality bar itself (same confidence threshold, same regime
   filter — the two levers that were explicitly rejected as ways to
   increase trade count).
5. Daily live quote check for exit monitoring — no forward simulation
   (that's what the backtest already did).
6. One open position per track (so up to 2 concurrent positions total,
   never 2 in the same track).
7. New options-specific constants, values locked in: `OPTIONS_PAPER_STARTING_CASH = 10000`,
   `OPTIONS_MAX_TRADE_USD = 2500`, `OPTIONS_MAX_POSITION_PCT = 0.25`,
   `OPTIONS_MAX_TRADES_PER_DAY = 2` — not reusing the equity account's
   numbers.
8. Real bid/ask fills when available, documented haircut fallback
   otherwise.
9. Polygon used only as a diagnostic fallback for contract-listing
   verification, never for live quotes or fills.

No open questions remain — ready for an implementation plan.
