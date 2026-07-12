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
- [~] Milestone 2: SEC research agent
      - [x] Data client: ticker->CIK, recent filings, financial facts (`research/sec_client.py`)
      - [ ] Handle inconsistent XBRL tags (e.g. revenue reported under different tags)
      - [ ] Report layer: use Claude to turn a filing into a plain-English + structured report
- [ ] Milestone 3: The council (trade review agents)
- [ ] Milestone 4: Backtest / track paper P&L over time
- [ ] Milestone 5: (only if profitable) tiny real-money pilot

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
