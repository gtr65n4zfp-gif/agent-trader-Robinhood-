# Agent Trader

A **strategy-driven** trading system that connects to Robinhood (via their MCP
server) and makes **paper trades first**, with the goal of graduating to small
real-money trades only after a strategy proves itself.

## Guiding principle

> No real money until a strategy is provably profitable in paper trading over a
> meaningful stretch of time. Every strategy is built and backtested on its own
> before it's allowed anywhere near the live loop. The edge is discipline (only
> trading when a validated signal has a real edge, and not overtrading), not any
> single signal's cleverness.

## What this is (and what it deliberately isn't)

This is a **library of systematic trading strategies** — each one a defined,
rule-based or model-based signal that is individually backtested and run behind
a shared risk-and-regime gate. It is **not** a discretionary "is this a good
company" engine: the project started with an SEC-fundamentals research agent and
a two-seat (fundamentals + technicals) council, and that fundamentals track has
been **retired** in favor of strategies that can be measured against historical
data. (See the git history and the "History" note below if you're looking for it.)

## The layers (built in this order)

1. **Execution layer** (`execution/`) — talks to the Robinhood MCP. Reads the
   account, places *paper* trades, enforces every risk cap. This is the
   foundation; nothing else works without it.
2. **Strategies** (`strategies/`, `agents/technicals.py`) — self-contained,
   individually backtestable signals. Each is domain-isolated to the market data
   it needs and emits a uniform `{stance, confidence, reasons}` view. Today's
   library: the rule-based **Technicals** signal (live) and a quant **forecast
   model** (in backtest validation). The build order for what comes next is in
   `STRATEGY_LIBRARY.md`.
3. **The gate** (`agents/`) — the regime filter, the Judge (the strategy
   arbiter), and the Risk vetoer decide whether a strategy's signal actually
   becomes a trade. No-trade is the default.

## Current status

- [~] Milestone 1: Paper-trading harness
      - [x] Paper-mode safety switch — live trading blocked by default (`execution/config.py`)
      - [x] Paper broker — simulated cash/positions/P&L, risk caps (`execution/paper_broker.py`)
      - [x] Trade logging with reasoning (`execution/trade_log.py`)
      - [x] Robinhood MCP authenticated; read-only wrapper for live prices + real
            account, agent-mediated (`execution/robinhood.py`)
      - [x] Proved live quote -> paper buy end-to-end, zero real-money risk
            (`execution/demo_live_paper.py`)
      - [x] Wire the council (Milestone 3) into an automated decision loop —
            a scheduled cloud routine now runs the full council on a
            weekday cadence, no manual trigger required (see Milestone 3's
            automation entry below)
      - [ ] Real order placement stays behind `assert_paper_mode()` and the
            `AGENT_TRADER_LIVE` unlock phrase until profitability is proven
- [x] Milestone 2: Strategy library
      - [x] Technicals strategy — rule-based price/EMA/RSI momentum /
            mean-reversion signal, the live library's first entry
            (`agents/technicals.py`)
      - [x] Time-series momentum (trend-following) — built and backtested on
            real Robinhood monthly data. **It failed the promotion gate** (0/8
            symbols beat buy-and-hold on Sharpe), so it is not in the live
            gate. Its only edge was drawdown control in the 2022 bear market
            (`strategies/time_series_momentum.py`, `agents/MOMENTUM_RESULTS.md`)
      - [x] Forecast strategy (quant) — plain OLS return model over
            momentum/mean-reversion/volatility features, in backtest
            validation, not yet in the live gate (`strategies/forecast_model.py`,
            `agents/forecast_seat.py`, `backtest/forecast_backtest.py`)
      - [~] RETIRED: the SEC-fundamentals research agent and the two-seat
            (fundamentals + technicals) council it fed. Dropped when the
            project refocused on backtestable strategies — a "is this a
            good business" discretionary thesis can't be measured against
            historical price data the way a signal can. Removed:
            `research/sec_client.py`, `research/report.py`,
            `agents/fundamentals_seat.py` (see git history).
- [x] Milestone 3: The gate (regime + judge + risk vetoer) — see `agents/COUNCIL_DESIGN.md` for the blueprint
      - [x] Risk vetoer — trade-size, volatility-scaled position, sector,
            drawdown, and daily-loss/frequency caps; structurally enforced
            inside `PaperBroker` (`agents/risk_vetoer.py`)
      - [x] Judge — the strategy gate (no-trade-by-default): a confident,
            directional signal behind the regime filter becomes buy/sell,
            everything else holds; never executes directly (`agents/judge.py`)
      - [x] Ablation/baseline hook — a no-gate baseline (raw signal, no
            regime filter, no confidence threshold) logged alongside every
            real decision, never acted on
      - [x] End-to-end proof: Technicals -> Judge -> PaperBroker
            (Risk vetoer still the final word) -> trade_log, verified live
            against real AAPL data for both a buy attempt (correctly vetoed
            on an already-over-concentrated account) and a hold
            (`agents/demo_council.py`)
      - [x] Exit logic — stop-loss, take-profit, and conviction-drop (a fresh
            Judge re-decision on the strategy signal that no longer supports
            holding), disjunctive (first to fire wins); regime-change left as
            an explicit seam, not built (`agents/exits.py`)
      - [x] Cost-basis tracking, honest fills (slippage modeled on every
            fill so paper P&L isn't a fantasy of free perfect execution),
            and realized P&L on every close (`execution/paper_broker.py`)
      - [x] Round-trip accounting — the correct Milestone 5 go-live counter
            (a close realizing P&L, not an open) (`execution/trade_log.py`)
      - [x] End-to-end proof: all three exit paths fired deterministically
            against a real (isolated) paper account — stop-loss, take-
            profit, and conviction-drop each closed a position with
            correctly recorded realized P&L (`agents/demo_exits.py`)
      - [x] Regime filter — rule-based, two axes (volatility relative to the
            calibrated `TARGET_DAILY_VOL_PCT`, trend vs. EMA), five named
            states; can only tighten the entry gate (never loosen it) and
            adds the `regime_change` exit path. A sit-out is logged
            distinctly (`regime_sitout`) and never counts as a round-trip
            (`agents/regime.py`)
      - [x] End-to-end proof: a favorable regime let a trade through, an
            unfavorable one forced a HOLD despite a strongly bullish signal
            (and didn't move the round-trip counter), and a regime
            flip closed a held position via `regime_change` — all
            deterministic, verified live (`agents/demo_regime.py`)
      - [x] Fixed: regime and Technicals were deriving trend from the same
            EMA reading, making `REGIME_EMA_LOOKBACK_DAYS` a setting that
            did nothing and silently breaking domain isolation between the
            two. `execution/robinhood.py`'s `get_regime_ema()` now fetches
            and validates a genuinely distinct, longer-period EMA — proven
            live with a constructed case where the two signals actually
            disagree on trend direction from the same price
      - [x] Automation entrypoint — `automation/run_pass.py`: exit sweep
            first, then entries, across a watchlist; fail-safe by design
            (`assert_paper_mode()` first, market-hours guard, per-symbol
            data sanity check that skips bad/stale data rather than
            trading on it); ships `AUTOMATION_DRY_RUN=True` — every
            decision is logged with its regime state, nothing executes
            until deliberately armed (`execution/config.py`,
            `agents/AUTOMATION_DESIGN.md`)
      - [x] End-to-end proof: exit-sweep-then-entries ordering, dry-run
            logging with zero execution, regime tagging on every record,
            and both fail-safes (market-hours no-op, per-symbol bad-data
            skip) all verified deterministically
            (`automation/demo_run_pass.py`)
      - [x] Wire `run_pass.py` to an actual live cadence — a scheduled
            Claude Code cloud routine ("Agent Trader — Daily Council
            Pass") runs weekdays at 15:00 UTC (mid-morning ET, chosen to
            stay inside regular market hours across the DST shift).
            `logs/trades.jsonl` and `logs/paper_portfolio.json` are now
            tracked in git (everything else in `logs/` stays ignored) —
            since every cloud invocation starts from a fresh checkout,
            the routine commits+pushes those two files after each pass
            that changes them; git is the persistence layer between
            runs. Reset to a clean baseline first (the pre-automation
            files were full of manual testing data — archived locally,
            not deleted, as `logs/archive_pre_automation_*`) so the
            go-live gate's round-trip count starts genuinely at zero.
            Validated live: the first triggered run correctly detected
            markets closed (a Sunday), no-op'd cleanly, and made no
            commit — proving repo access, the Robinhood MCP connector,
            and the market-hours fail-safe all work end to end in the
            actual cloud environment. The first full weekday pass (real
            entries/exits/holds, still `AUTOMATION_DRY_RUN`) runs
            automatically next.
- [ ] Milestone 4: Backtest / track paper P&L over time
- [ ] Milestone 5: Real-money pilot — blocked until the go-live gate below is met
- [ ] Milestone 6: Universe expansion — a screening/universe-selection funnel so
      a strategy can look at a whole market instead of a hand-picked watchlist.
      Deliberately deferred: the current one-symbol-at-a-time, agent-mediated MCP
      path can't scan thousands of tickers, so this needs a *bulk, programmatic*
      market-data source (not the interactive MCP) feeding a cheap quant screen
      that narrows the universe to a shortlist the existing strategies then analyze.
      Not to be built until a strategy has proven an edge on the watchlist —
      widening intake before the engine is validated is premature. The crux
      decision is the bulk data source; design before code.

## Parking lot (deferred ideas)

Things worth doing eventually, deliberately NOT built now — no strategy has yet
shown it beats its own no-gate baseline in backtest, so new surface area (new
strategies, new data sources) is on hold until that's resolved. Revisit after
the live dry-run has real days behind it and the win-rate/entry-quality question
is understood.

- **A second independent strategy in the live gate** — the quant forecast model
  (`strategies/forecast_model.py`) is the obvious candidate: once it clears its
  own backtest (beating the naive-drift baseline on held-out data), promote it
  alongside Technicals so the gate becomes conjunctive again — two independent
  strategies that must agree. Blocked on that promotion gate, not plumbing.
- **Correlation filter** — a more general version of the existing
  `MAX_SECTOR_PCT` cap: block piling into another position that's highly
  correlated with existing holdings even when they don't share a sector.
  Minor refinement, not an urgent gap.
- **Daily status digest** — a morning "here's what today's pass sees" and
  evening "here's how the paper account did" summary. Would have surfaced
  the earlier cloud-routine Robinhood-unavailable failure same-day instead
  of silently. Pairs naturally with the automation observability work
  already in `automation/run_scheduled.sh`.

## Go-live gate (Milestone 5)

"Provably profitable" isn't a vibe check. Real money doesn't get touched until
*all* of these hold, checked against the paper trade log:

- **≥30 closed paper trades.** Below that, the confidence interval on the win
  rate is too wide to mean anything — an 8/10 win rate has a roughly 44–97%
  confidence interval, which is statistically indistinguishable from a coin
  flip. n=7-10 doesn't tell you anything.
- **Performance holds across ≥3 distinct market regimes** (e.g. trending up,
  trending down, choppy/range-bound) — not just one favorable window. Good
  numbers from a single trending period are regime alignment, not system
  quality.
- **Win rate reported with its confidence interval**, never a bare percentage.
- **Total P&L including fees/slippage**, not gross.

Until every box above is checked, real trading stays blocked by
`assert_paper_mode()` and the `AGENT_TRADER_LIVE` unlock phrase in
`execution/config.py` — no exceptions, no manual overrides.

## Layout

```
agent-trader/
├── agents/       # the gate: regime filter, judge, risk vetoer, exits
├── strategies/   # the strategy library (signal models)
├── backtest/     # historical replay + per-strategy validation
├── execution/    # Robinhood MCP connection + paper trading
├── automation/   # the scheduled unattended pass
├── config/       # settings, .env (never committed)
└── logs/         # trade logs, decisions, P&L history
```

## History

This project began as an SEC-fundamentals research agent feeding a two-seat
council (a Fundamentals seat debating long-term thesis vs. a Technicals seat on
short-term setup). That fundamentals track was **retired** when the project
refocused on systematic, backtestable strategies: a discretionary "good
business?" judgment can't be scored against historical price data the way a
signal can, and the edge we care about is a measurable one. The council
machinery (domain isolation, no-trade-by-default, the ablation baseline) carried
over; it now gates one validated strategy signal at a time, built to add more
back into a conjunctive gate as each clears its own backtest. The removed code
lives in git history.

## Safety rules (non-negotiable while we build)

- Paper/simulated mode is the default. Real trading is behind an explicit,
  hard-to-flip switch.
- Secrets (API keys, tokens) live in `config/.env`, which is git-ignored and
  never committed.
- Every trade decision gets logged with the reasoning behind it.
