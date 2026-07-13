# Backtest design (Milestone 4)

Blueprint only — no implementation yet, per instruction. This describes a
harness that replays the EXISTING council (unchanged decision logic)
against historical data, so regime-diversity evidence doesn't have to wait
on live paper passes accumulating one trading day at a time.

## Guiding principle

> A naive backtest of an LLM council lies to you. The primary correctness
> property is NO LOOKAHEAD — everything else (metrics, benchmarks, cost)
> is secondary to proving the council only ever sees what it could have
> actually known on each simulated date.

## The cardinal rule, precisely

At simulated date D, every seat may see only data knowable on D.

**Prices/technicals:** EMA/RSI/ATR computed from daily bars up to and
including D only. Robinhood's live `get_equity_technical_indicators` tool
always computes off the *current* moment — it cannot answer "what would
this EMA have read on a past date using only data available then." So the
backtest cannot reuse that tool for historical dates; instead it fetches
raw daily bars (`get_equity_historicals`) once, and the data layer computes
EMA/RSI/ATR itself from a bar series truncated at D, using the same
periods the live system uses (RSI-14, EMA-9 short, EMA-`config.
REGIME_EMA_LOOKBACK_DAYS` (20) regime, ATR-14). These are standard,
well-known formulas — not new judgment calls, just arithmetic the live
system currently delegates to Robinhood's server instead of computing
locally.

**Fundamentals — the subtle trap:** SEC XBRL concept data points carry
both a period `end` date (e.g. a quarter ending 2024-06-30) and a separate
`filed` date (when the filing reporting that period actually became
public, typically 30-45 days after period end). Truncating by `end <= D`
is a real lookahead bug — it would let the council "know" Q2 earnings the
day the quarter closed, weeks before anyone actually filed them. The
correct contract is **`filed <= D`**, exactly as specified. `research/
sec_client.py`'s `get_concept()` currently does NOT return `filed` at all
— see "Required changes" below.

**Timing convention:** the council decides using D's closing price and
fills (if any) execute at that same price — matching what the LIVE system
already does (fetch-quote, decide, fill, all at one timestamp; no
execution-lag modeling exists there either). The backtest stays faithful
to the live system's actual behavior rather than being artificially more
idealized.

## Required changes to existing files (flagging before touching anything)

Everything below is additive and backward-compatible — every new
parameter defaults to current behavior, so no existing caller (live
automation, demo scripts) changes behavior. This is the same pattern
`PaperBroker.__init__`'s `portfolio_path` already established for
isolation; these are the missing pieces needed to extend that same
isolation to the audit trail and to fundamentals point-in-time truncation.
No decision logic changes anywhere.

1. **`execution/trade_log.py`** — `record()`, `read_all()`,
   `count_trades_today()`, `round_trip_stats()` gain an optional
   `log_path` parameter (default: today's shared `_LOG_PATH`).
   `count_trades_today()` also gains an optional `now` parameter (default:
   real wall-clock), mirroring `config.market_is_open()`'s existing
   testability pattern.
   *Why required:* `trade_log`'s path is currently a hardcoded module
   constant with no override — reusing `PaperBroker` unchanged for a
   backtest would otherwise write every simulated trade straight into the
   live `logs/trades.jsonl`, which is exactly the contamination rule 2
   forbids. (This is also the same gap `demo_exits.py`/`demo_regime.py`
   already worked around with a temporary `MAX_TRADES_PER_DAY` bump hack —
   worth actually fixing now instead of hacking around it again.)

2. **`execution/paper_broker.py`** — `PaperBroker.__init__` gains an
   optional `log_path` parameter (alongside the existing `portfolio_path`),
   threaded into every internal `trade_log` call. `buy()`/`sell()`/
   `_check_risk()` gain an optional `now` parameter (default: real
   wall-clock) for day-rollover bookkeeping (`day_date`/`day_start_equity`)
   and threaded into the `count_trades_today()` call.
   *Why required:* `_check_risk()` currently computes "today" from the
   real wall clock to decide when to reset the daily circuit breakers
   (`MAX_TRADES_PER_DAY`, `MAX_DAILY_LOSS_PCT`). A backtest stepping
   through simulated past dates would otherwise never see these breakers
   roll over correctly — every simulated day would compare against
   *today's* real date, so the daily breakers would silently misbehave
   (this is a correctness bug in replaying "the exact council pipeline,"
   not a lookahead bug, but it would just as surely produce wrong results).

3. **`research/sec_client.py`** — `get_concept()`'s returned dicts gain one
   more key: `filed` (straight from the SEC response's own `filed` field,
   already present in the raw JSON, just not currently extracted).
   *Why required:* this is the only place the actual filing date lives;
   without it, point-in-time truncation has no way to distinguish "known
   on D" from "true as of D but not yet public."

4. **`agents/fundamentals_seat.py`** — `fetch_concept_trend()` and
   `build_brief()` gain an optional `as_of` parameter (a date string).
   When given, points are filtered to `filed <= as_of` before `_trend()`
   runs, and `recent_filings` is filtered to `filing_date <= as_of`
   (already returned by `get_recent_filings()` — no sec_client change
   needed there). `None` (default) means today's live/unfiltered behavior,
   unchanged for every existing caller.

5. **`agents/exits.py`** — `close_position()` gains an optional `now`
   parameter, threaded straight through to every `broker.sell()` call it
   makes. *Why required (caught mid-implementation, not in the original
   pass):* the exit sweep closes positions via `close_position()`, not a
   direct `broker.sell()` call — without this, a backtest's exit fills
   would silently use the real wall clock instead of the simulated date,
   the same day-rollover bug item 2 exists to prevent, just one call
   deeper than originally spotted.

Nothing in `judge.py`, `technicals.py`, `regime.py`, `risk_vetoer.py`, or
`exits.py`'s other (pure) functions changes — they're already pure
functions over already-fetched values with no hidden clock or global
state, so they're reusable completely as-is.

## Results isolation

Every backtest run gets its own directory: `logs/backtests/<run-id>/`,
containing:
- `council_portfolio.json` / `council_trades.jsonl` — the real council's
  simulated account (regime → seats → judge → risk vetoer, exactly as
  live), via `PaperBroker(portfolio_path=..., log_path=...)`.
- `baseline_portfolio.json` / `baseline_trades.jsonl` — the ablation
  account (see "Benchmarks" below).
- `buyhold_portfolio.json` / `buyhold_trades.jsonl` — the naive benchmark.
- `fundamentals_cache.json` — cached Fundamentals verdicts (see "Cost").
- `metrics_report.json` — final numbers (task 4).

`logs/trades.jsonl` and `logs/paper_portfolio.json` (the live audit trail)
are never opened by any backtest code path. `round_trip_stats()` called
with no `log_path` override — i.e. every existing call site, including the
live routine — stays scoped to the live log exactly as today.

**No order tools:** the engine only ever calls `PaperBroker.buy()/sell()`
against isolated backtest accounts. No `mcp__robinhood-trading__place_*`
tool is ever called by anything backtest-related — those tools are for the
live routine only, never invoked here.

## Scope: symbols and windows

Proposing to reuse `config.WATCHLIST` unchanged (same 8 symbols the live
system trades) rather than a different basket — keeps the backtest
evidence directly about the system that's actually running.

Three candidate windows, chosen from genuinely distinct, well-known
market conditions (not guessed):

| Window | Dates | Intended regime |
|---|---|---|
| A | 2022-01-03 → 2022-06-30 | Downtrend — Fed tightening cycle, broad equity selloff, growth/tech hit hardest |
| B | 2023-01-01 → 2023-06-30 | Uptrend — post-2022-bottom recovery, AI-driven mega-cap rally |
| C | 2018-03-01 → 2018-08-31 | Choppy/range-bound — relatively directionless stretch preceding the Q4 2018 selloff |

**Caveat, stated plainly:** these are reasonable starting candidates based
on general market history, not a guarantee — the regime filter's *own*
classification (run against the actually-fetched historical bars) is the
real verification. If window C in particular doesn't actually classify as
`ranging`/`low_vol_ranging` for enough of the watchlist once real data is
in hand, the fallback is swapping in a different choppy stretch (e.g.
2015-08 → 2016-01, or 2011 post-debt-ceiling) — flagged as a live decision
point in task 2/5, not resolved here.

Each window needs ~90 calendar days of bar history *before* its start date
as EMA/RSI/ATR warm-up (regime EMA-20 needs ~3x lookback per `execution/
robinhood.py`'s own comment) — fetched but never evaluated as a "decision
day," purely to seed accurate indicators from day 1 of the actual window.

## Data layer (task 2)

New package: `backtest/`.

- `backtest/data.py`:
  - `fetch_bars(symbol, start, end)` — `get_equity_historicals`, cached to
    disk per symbol (backtest-scoped cache, separate from `sec_cache.json`)
    since historical bars for past dates never change.
  - `ema_series`/`rsi_series`/`atr_series` — standard textbook formulas
    (same ones `agents/technicals.py`'s docstring already calls "textbook
    technical-analysis levels") computed over a bar series.
  - `technicals_as_of(symbol, date_D, bars)` — truncates bars to `<= D`,
    returns `{price, ema, rsi, atr_pct, regime_ema}` — the same shape
    `automation/run_pass.py`'s `_extract_symbol_data()` produces, so the
    downstream council code is identical either way.
  - `fundamentals_as_of(symbol, date_D)` — thin wrapper over
    `fundamentals_seat.build_brief(ticker, as_of=date_D)`.
  - `council_bundle_for(symbol, date_D)` — combines both into one
    council-ready bundle.

## Engine (task 3)

`backtest/engine.py`, structured like `automation/run_pass.py` (exit sweep
before entries) but as a date-stepping loop instead of one wake:

```
for D in trading_days(window):
    for symbol in watchlist:
        bundle = council_bundle_for(symbol, D)
    # exit sweep first (mirrors run_pass.py's own loop, not run_exit_sweep()
    # itself, for the same reason run_pass.py doesn't call it either:
    # logging needs to target the isolated log_path)
    for held symbol: evaluate_exits(...) -> close_position(broker, ..., log via broker's own log_path)
    # then entries
    for unheld symbol: regime -> judge.decide(...) -> broker.buy/sell(..., now=D, log_path=...)
```

Three parallel account tracks per run, all driven off the SAME fetched
data for a fair comparison:
1. **Council** — real `regime.regime_stance` → `technicals.build_view` +
   cached Fundamentals verdict → `judge.decide` (conjunctive gate) →
   `PaperBroker`. Exits: full `evaluate_exits` (all four paths).
2. **Baseline (ablation)** — same seat inputs → `judge.baseline_decide`
   (regime-blind, no conjunctive gate, per its own docstring) →
   `PaperBroker`. Exits: `evaluate_exits` called *without* fundamentals/
   technicals — this already skips `conviction_drop` gracefully (existing
   optional-arg behavior, zero new code) rather than mixing a
   baseline-driven entry with a real-Judge-driven exit check, which would
   muddy the ablation comparison. **Flagging this as a judgment call**,
   not obviously the only right answer — the alternative is letting
   baseline positions use the same conviction_drop check as the real
   council; open to either, this is the cleaner-semantics default.
3. **Buy-and-hold** — chunked `PaperBroker.buy()` calls per symbol (same
   `MAX_TRADE_USD`-sized chunking `agents/exits.py`'s `close_position()`
   already uses) at the window's first trading day (equal cash split),
   held untouched, marked to market at window end via a final sell.
   Routes through the SAME `PaperBroker`/risk vetoer as the other two —
   found during the smoke test: a naive single "invest everything" order
   trips `MAX_TRADE_USD` outright, and even chunked, `MAX_POSITION_PCT`
   (10%) caps how much of the account any one symbol can ever hold. A
   truly fully-invested equal-weight buy-and-hold is therefore not
   achievable under the same rules the council/baseline live by — and
   giving the benchmark an artificial capital-deployment advantage the
   other two accounts could never have would make it a less honest
   comparison, not a more generous one. This benchmark is "buy-and-hold
   within the same risk caps," not literally 100% invested.

## Metrics (task 4)

`backtest/metrics.py`, reading each account's isolated `trades.jsonl`:
- `round_trip_stats()`-style count, `total_realized_pnl` (already nets
  slippage/fees per-fill, same as live).
- **Win rate with a 95% Wilson score confidence interval** — not the naive
  normal approximation, which misbehaves at small n and near 0%/100% (both
  realistic for a ~24-40 trade backtest slice). This is a real
  computation to implement, not currently in this codebase.
- **Per-regime breakdown** — every trade already carries `regime_state`
  (automation's tagging convention, reused here); group realized P&L and
  win rate by that field.
- **Two benchmark comparisons**: council vs. baseline, council vs.
  buy-and-hold — the real question per the brief: *did the council beat
  just holding, and did the multi-agent structure beat a single-model
  shadow?*

## Cost strategy (task 1 requirement)

The only step here that costs LLM tokens at all is forming each
Fundamentals verdict — Technicals, Regime, Judge, and the Risk vetoer are
all pure rule-based Python (per `COUNCIL_DESIGN.md`: "no HMM, no ML, no
clustering"), so the day-by-day engine loop itself is free computationally
once data is fetched; it's a plain script, not an agent session.

**Caching strategy: per (symbol, filing-boundary), not per (symbol,
date).** The Fundamentals *brief* is byte-identical for every date between
two consecutive filings — recomputing a fresh judgment for each of those
days would be both wasteful and circular (same input, should give the same
output). So the actual execution model is two phases:

1. **Interactive pre-pass** (me, reading briefs and forming judgments —
   same "agent-formed, not scored" pattern the live system already uses):
   for each symbol, find every filing-boundary date within the window
   (plus warm-up), call `build_brief(ticker, as_of=boundary_date)`, read
   it, form a verdict, cache it to `fundamentals_cache.json` keyed by
   `(symbol, boundary_date)`.
2. **Deterministic replay**: the engine looks up the most recent
   filing-boundary `<= D` for each `(symbol, D)` and reuses that cached
   verdict — zero further LLM calls.

**Rough estimate for the proposed scope** (8 symbols × 3 windows, ~6
months each): expect roughly 1-3 filing boundaries per symbol per window
(a quarterly 10-Q, maybe an 8-K) → **~24-70 actual Fundamentals LLM calls
total**, each a brief read + short judgment (~2-4k tokens in+out) → **very
roughly 100-250k tokens total** for the entire Fundamentals pass — small
in absolute terms. Contrast: a naive per-day approach (8 symbols × 3
windows × ~125 trading days each) would be **~3,000 calls**, over 40x
more — this is the concrete reason the caching strategy isn't optional.

## No-lookahead proof (task 5)

The actual trustworthiness test: build `council_bundle_for(symbol, D)`
twice — once against a bar/filing source truncated exactly at D, once
against the full, untruncated source (which includes everything after D
too) — and assert the two bundles are byte-identical. If truncating future
data ever changes what's produced for date D, the contract is broken and
nothing downstream is trustworthy. This gets its own small script
(`backtest/prove_no_lookahead.py`) run against a few real (symbol, D)
pairs before the "real" backtest is trusted.

Also: a short end-to-end run over a small window (a few symbols, a few
weeks) showing the full pipeline working, with metrics and both
benchmarks, as a smoke test before committing to the full 8×3-window run.

## Open questions for your input before I start building

1. **Windows** — the three proposed above, or different ones? (Especially
   window C — the choppy one is the hardest to pick blind.)
2. **Baseline's exit logic** — skip `conviction_drop` entirely (my
   leaning, see "Engine" above), or let it reuse the real Judge's
   conviction check too?
3. **Scope size** — 8 symbols × 3 windows × ~6 months as proposed, or
   smaller/larger? This is the main lever on both token cost and
   wall-clock time to run the pre-pass + engine.
