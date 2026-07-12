# Automation design (Milestone 3 -> 4)

The scheduled council routine — the last piece separating "a proven
decision pipeline" from "an actual paper track record." See
COUNCIL_DESIGN.md for the council itself; this document only covers
running it unattended.

## Guiding principle

> Autonomy is the highest-risk surface in this project. Every rule below
> exists to make a bad or missing signal produce NOTHING, not a wrong
> trade. When in doubt, the pass does less, not more.

## Why a scheduled Claude Code routine, not a cron daemon

Robinhood's MCP connection is OAuth-gated and only reachable through an
authenticated agent session (see `execution/robinhood.py`'s module
docstring) — there's no standalone Python client for it. A headless cron
job has no MCP session to call. A **scheduled Claude Code routine** does:
it wakes on a cadence, opens its own session with its own live MCP
access, fetches data, and calls this project's code — the same
agent-mediated pattern every other data-touching module already uses,
just on a timer instead of a human prompt.

## Watchlist

AAPL, MSFT, GOOGL, JPM, JNJ, WMT, XOM, CAT — 8 liquid large-caps spanning
tech, financials, healthcare, consumer staples, energy, and industrials.
Adjustable (`execution/config.py`'s `WATCHLIST`), not a permanent choice
— chosen for liquidity and sector spread, not conviction on any of them.

## Cadence

Once per US trading day, mid-morning ET (~10:00) — after the open's
first-minutes volatility, comfortably inside regular hours so the
market-hours guard reads open on an on-time wake. Set in the scheduled
routine's own cron expression (see "The routine" below), not in code.

## The per-pass sequence

Implemented in `automation/run_pass.py`, called once per wake with a
pre-fetched bundle (agent-mediated — this function fetches nothing
itself):

1. **`config.assert_paper_mode()`.** Hard stop for the whole pass if this
   ever raises. Non-negotiable, first line, no exceptions.
2. **Market-hours guard** (`config.market_is_open()`). Outside US equity
   regular hours, the whole pass is a logged no-op
   (`action="automation_noop"`) and nothing else runs. Doesn't know about
   market holidays on its own — see the fallback below.
3. **Per-symbol data sanity check.** Each symbol's bundle is parsed
   independently; a symbol whose data fails to parse (bad/missing MCP
   response) or is stale (its quote's own venue timestamp is older than
   `config.MAX_QUOTE_AGE_MINUTES`) is skipped — logged
   (`action="automation_skip"`), never traded, never even evaluated by
   the seats. This is also the practical fallback for market holidays:
   the hours guard alone can't detect them, but a holiday's "latest"
   quote is from the prior session and fails the staleness check.
4. **Exit sweep first.** Every OPEN position with fresh data this pass is
   checked against all four exit paths (`agents/exits.py`) before
   anything new opens — manage what we hold before adding to it. A held
   symbol with no fresh data this pass (outside the watchlist, or it
   failed the sanity check) is left alone, not force-closed.
5. **Then entries.** For every watchlist symbol not already held: regime
   -> Fundamentals + Technicals -> Judge, no-trade-by-default. The
   regime-blind single-model baseline is logged unconditionally either
   way (the ablation hook), same as every manual run.
6. **Route or log, per `config.AUTOMATION_DRY_RUN`.** True (the shipped
   default): every entry/exit decision is fully evaluated and logged
   (`action="dry_run_entry"`/`"dry_run_exit"`) but `PaperBroker.buy()`/
   `.sell()` is never called — nothing executes. False: real paper
   orders execute, still through the full Risk vetoer gate — automation
   adds no path around any existing breaker.
7. **Regime tagging.** Every logged decision — trade, hold, sit-out, or
   skip — carries `regime_state` at decision time, so the go-live gate's
   >=3-regime-coverage requirement is verifiable directly from
   `trade_log` later, not reconstructed after the fact.
8. **Run summary.** What the pass saw (symbols evaluated / skipped), what
   it did or would have done (entries, exits, holds, sit-outs), and
   current `trade_log.round_trip_stats()`.

## Known limitation carried into automation

Sector concentration checks only cover the symbol being traded, not every
other held position (same gap `agents/demo_council.py` already
documents) — real, not new here. More importantly for automation
specifically: **the shared paper account currently holds positions
outside this watchlist** (from earlier manual testing) that this pass has
no price data for. `PaperBroker`'s risk checks value an unpriced position
at $0 (documented behavior of `account()`), which can understate total
equity and falsely trip the drawdown/daily-loss breakers — the same bug
class found and fixed during `agents/demo_exits.py`'s development. It
cannot re-trigger while `AUTOMATION_DRY_RUN=True` (nothing executes), but
it needs resolving — close those positions out, or extend the watchlist
to cover them — before execution is ever armed for real.

## The routine

Defined via the `schedule` skill: a scheduled Claude Code routine
("Agent Trader — Daily Council Pass," weekdays at 15:00 UTC — mid-
morning ET year-round, chosen to stay inside regular hours across the
DST shift) whose prompt, on each wake, tells the agent to:

0. Cheap pre-check, no MCP calls: `config.market_is_open()` in plain
   Python. If closed, call `run_pass({})` directly (it short-circuits on
   the market-hours guard before ever touching the bundle) to still log
   the `automation_noop` entry, and stop there — no point spending
   Robinhood/SEC calls on a day nothing will trade.
1. Confirm the Robinhood MCP session is authenticated (re-auth if not).
2. For every symbol in `config.WATCHLIST`: call `get_equity_quotes`,
   `get_equity_technical_indicators` (type=atr/rsi/ema, and a second call
   at `period=config.REGIME_EMA_LOOKBACK_DAYS` for the regime EMA), and
   `get_equity_fundamentals`; pull SEC data and form each symbol's
   Fundamentals verdict (`agents.fundamentals_seat.form_verdict()`) the
   same way a manual run does.
3. Assemble the bundle in the shape `automation/run_pass.py` expects.
4. Call `run_pass(bundle)`.
5. Report the run summary.

Still DRY-RUN. Arming execution is a one-line, deliberate edit to
`execution/config.py` (`AUTOMATION_DRY_RUN = False`) — see that
constant's comment — never a default.
