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
      - [ ] Wire the council (Milestone 3) into an automated decision loop —
            still no autonomous/scheduled trading; every trade above is
            triggered manually with an explicit reason
      - [ ] Real order placement stays behind `assert_paper_mode()` and the
            `AGENT_TRADER_LIVE` unlock phrase until profitability is proven
- [x] Milestone 2: SEC research agent
      - [x] Data client: ticker->CIK, recent filings, financial facts (`research/sec_client.py`)
      - [x] Handle inconsistent XBRL tags — tag-candidate fallback + a proper
            YoY (not just sequential) comparison (`agents/fundamentals_seat.py`)
      - [x] Report layer: plain-English + structured company report, agent-
            authored from a structured brief (`research/report.py`)
- [~] Milestone 3: The council (trade review agents) — see `agents/COUNCIL_DESIGN.md` for the blueprint
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
      - [ ] Regime/volatility filter (forced sit-out in choppy conditions)
      - [ ] Exit logic (stop-loss / take-profit / regime-change / conviction-drop)
      - [ ] Automated/scheduled cadence — every council run above is still
            triggered manually
- [ ] Milestone 4: Backtest / track paper P&L over time
- [ ] Milestone 5: Real-money pilot — blocked until the go-live gate below is met

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
