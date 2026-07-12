# Agent Trader

An AI-driven trading system that connects to Robinhood (via their MCP server) and
makes **paper trades first**, with the goal of graduating to small real-money trades
only after it proves itself.

## Guiding principle

> No real money until the system is provably profitable in paper trading over a
> meaningful stretch of time. Every layer is built and verified on its own before
> the next one is added.

## The three layers (built in this order)

1. **Execution layer** (`execution/`) — talks to the Robinhood MCP. Reads the
   account, places *paper* trades. This is the foundation; nothing else works
   without it.
2. **SEC research agent** (`research/`) — pulls official SEC filings, turns them
   into structured data + plain-English reports the other agents can consume.
   Self-contained and useful on its own.
3. **The council** (`agents/`) — multiple agents that debate whether a proposed
   trade is worth it (short-term risk vs long-term thesis) before it's placed.
   Built last, because it needs the other two to have anything to reason about.

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
- [x] Milestone 2: SEC research agent
      - [x] Data client: ticker->CIK, recent filings, financial facts (`research/sec_client.py`)
      - [x] Handle inconsistent XBRL tags — tag-candidate fallback + a proper
            YoY (not just sequential) comparison (`agents/fundamentals_seat.py`)
      - [x] Report layer: plain-English + structured company report, agent-
            authored from a structured brief (`research/report.py`)
- [x] Milestone 3: The council (trade review agents) — see `agents/COUNCIL_DESIGN.md` for the blueprint
      - [x] Risk vetoer seat — trade-size, volatility-scaled position, sector,
            drawdown, and daily-loss/frequency caps; structurally enforced
            inside `PaperBroker` (`agents/risk_vetoer.py`)
      - [x] Fundamentals seat — SEC-only structured brief, judgment stays
            agent-formed by design (`agents/fundamentals_seat.py`)
      - [x] Technicals seat — rule-based price/EMA/RSI stance, domain-isolated
            from fundamentals (`agents/technicals.py`)
      - [x] Judge — conjunctive gate (no-trade-by-default) combining seat
            outputs into buy/sell/hold; never executes directly
            (`agents/judge.py`)
      - [x] Ablation/baseline hook — a non-isolated single-model shadow
            decision logged alongside every real one, never acted on
      - [x] End-to-end proof: Fundamentals + Technicals -> Judge -> PaperBroker
            (Risk vetoer still the final word) -> trade_log, verified live
            against real AAPL data for both a buy attempt (correctly vetoed
            on an already-over-concentrated account) and a hold
            (`agents/demo_council.py`)
      - [x] Exit logic — stop-loss, take-profit, and conviction-drop (a fresh
            Judge re-decision that no longer supports holding), disjunctive
            (first to fire wins); regime-change left as an explicit seam,
            not built (`agents/exits.py`)
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
            unfavorable one forced a HOLD despite both seats being strongly
            bullish (and didn't move the round-trip counter), and a regime
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
      the council can look at a whole market instead of a hand-picked watchlist.
      Deliberately deferred: the current one-symbol-at-a-time, agent-mediated MCP
      path can't scan thousands of tickers, so this needs a *bulk, programmatic*
      market-data source (not the interactive MCP) feeding a cheap quant screen
      that narrows the universe to a shortlist the existing council then analyzes.
      Not to be built until the council has proven an edge on the watchlist —
      widening intake before the engine is validated is premature. The crux
      decision is the bulk data source; design before code.

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
├── agents/       # the council (trade-review agents) — later
├── execution/    # Robinhood MCP connection + paper trading
├── research/     # SEC filings agent — later
├── config/       # settings, .env (never committed)
└── logs/         # trade logs, decisions, P&L history
```

## Safety rules (non-negotiable while we build)

- Paper/simulated mode is the default. Real trading is behind an explicit,
  hard-to-flip switch.
- Secrets (API keys, tokens) live in `config/.env`, which is git-ignored and
  never committed.
- Every trade decision gets logged with the reasoning behind it.
