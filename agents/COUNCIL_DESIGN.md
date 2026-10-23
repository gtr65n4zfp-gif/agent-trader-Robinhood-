# Gate design (formerly "the council")

How a strategy signal becomes a trade. This describes the shape of the
decision machinery that sits between "the execution layer can place a paper
trade" and "a paper trade actually gets placed."

> **Naming note.** This file and much of the code still say "council" and
> "seat" for historical reasons — the system began as a multi-seat council
> where a SEC **Fundamentals seat** and a **Technicals seat** had to agree.
> The fundamentals track was retired when the project refocused on
> systematic, backtestable strategies (see README's "History"). What remains
> is a **strategy gate**: the regime filter + the Judge + the Risk vetoer,
> deciding whether one validated strategy signal becomes a trade. The
> conjunctive-gate machinery is kept, ready for a second independent strategy
> to be promoted back in once it clears its own backtest.

## Guiding principle

> No-trade is the default. The gate doesn't exist to greenlight trades — it
> exists to only let a trade through when a validated signal has a real,
> confident edge and price conditions don't argue against it. The edge is
> discipline (not overtrading), not any single signal's cleverness.

## The strategy signal(s)

A strategy is **domain-isolated** — it only ever sees the market-data
category it needs, and emits a uniform `{stance, confidence, reasons}` view
the gate can consume without knowing how it was produced. Isolating *inputs*
(not just prompts) is the main defense against correlated errors: if every
signal can see everything, they tend to converge on the same mistake for the
same reason. This is why, when a second strategy is promoted into a
conjunctive gate, it must be genuinely independent — a different data view,
not the same inputs relabeled.

- **Technicals strategy** (`agents/technicals.py`, live) — sees only parsed
  market data from `execution/robinhood.py` (price, ATR, RSI, EMA). A
  rule-based momentum / mean-reversion signal: price vs. its trend line, RSI
  overbought/oversold. Mechanical and fully reproducible, same as the Risk
  vetoer's checks.
- **Forecast strategy** (`strategies/forecast_model.py` +
  `agents/forecast_seat.py`, in backtest validation) — a plain OLS return
  model over momentum/mean-reversion/volatility features. The next candidate
  for the live gate; promoted only once it beats its naive-drift baseline on
  held-out data (`backtest/forecast_backtest.py`).

## The gate

- **Market risk-off switch** (`agents/market_switch.py`, armed). It's a
  market-wide extension of the regime filter, checked before everything
  else, and it can only tighten.
  - **RISK-OFF** (SPY's 12-1 trend clearly down): it forces HOLD on every
    symbol and exits holdings via the `market_risk_off` path.
  - **UNKNOWN** (unusable market data): it blocks new buys but forces no
    exits.
  - Evidence and caveats: `agents/RISK_SWITCH_RESULTS.md`.
- **Regime filter** — see below. Checked next, per symbol; can only tighten.
- **Judge** (`agents/judge.py`) — the arbiter. It sees the strategy
  signal's output (not its raw inputs) plus the regime read, and weighs them
  into a single decision (buy / sell / hold) with a confidence score. Every
  decision is logged with its reasoning.
- **Risk vetoer** (`agents/risk_vetoer.py`) — sees only the proposed order
  size against the hard limits in `execution/config.py` and current account
  state. Pure veto power, enforced structurally inside `PaperBroker`: it can
  reject a trade on size/exposure grounds regardless of what the signal says,
  and it can never originate a trade.

## No-trade is the default

A trade fires only if the strategy signal is **directional** (bullish or
bearish) AND clears `CONFIDENCE_THRESHOLD`, AND the regime filter doesn't
object, AND the Risk vetoer doesn't veto at order time. A neutral stance,
confidence below the bar, a non-tradeable regime, or a veto all fall through
to no trade. Overtrading is a bigger threat to paper (and eventually real)
P&L than any single missed opportunity.

**When a second strategy is added**, this becomes a conjunction again: both
signals must agree on direction AND both clear the bar — an AND, not a vote
or an average. One signal objecting is enough to sit out.

## Regime / volatility filter

Built (`agents/regime.py`). A simple, explainable filter, not a strategy with
a vote: if the symbol is in a low-volatility, choppy/range-bound state, force
a sit-out regardless of what the signal would otherwise say. Most signal is
noisiest and least reliable in choppy conditions; better to do nothing than
trade a false signal.

Two axes, rule-based only (no HMM, no ML, no clustering): volatility
(low/normal/high, relative to `execution/config.py`'s calibrated
`TARGET_DAILY_VOL_PCT`) and trend (up/down/sideways, price vs. its EMA over a
wider band than the Technicals strategy uses). Combined into named states —
`low_vol_ranging` and `ranging` are NOT tradeable; `low_vol_trend`,
`trending`, and `volatile_trend` are.

Can only TIGHTEN, never loosen: wired into `agents/judge.py`'s entry gate as
an additional condition (a non-tradeable regime forces HOLD outright, checked
first, never overridden by the signal), and into the exit engine as the
`regime_change` path (`agents/exits.py`) — a held position whose regime flips
to non-tradeable gets closed. A regime-caused sit-out is logged distinctly
(`action="regime_sitout"`) and, being a non-fill, never counts toward the
round-trip go-live counter.

## Exit logic — a first-class concern

Entries get most of the attention; exits are just as much a gate concern,
with multiple independent paths so no single failure mode holds a position
open too long:

- **Stop-loss** — hard, mechanical, not overridable by the judge.
- **Take-profit** — target hit, gains locked in.
- **Market risk-off** — the market switch reads definitively RISK-OFF.
  Checked right after the price-based paths. An "unknown" market never
  fires it.
- **Regime change** — the regime filter flips against the open position.
- **Conviction drop** — a later re-decision on the strategy signal re-evaluates
  and no longer supports holding (not a confident buy any more), even with no
  fixed price trigger hit. Deliberately regime-blind — regime_change is the
  separate, higher-priority path for that.

Unlike the entry gate, exits are disjunctive: any one of these is enough to
close a position (OR, not AND) — once a position is open, the priority is
capital preservation, not confirmation.

## Ablation / baseline hook

Every decision also records what trading the **raw signal with no discipline**
would do — no regime gate, no confidence threshold, just follow the signal's
direction whenever it has one. This shadow decision is logged alongside the
real decision but never acted on. Purpose: once there's enough paper-trade
history, compare the two decision logs to test whether the regime gate and the
confidence bar actually add value — or whether the discipline is just theater.

## Everything still routes through the existing plumbing

The gate decides; it never executes directly.

- A buy/sell decision still goes through `PaperBroker`
  (`execution/paper_broker.py`) — same risk caps, same persistence.
- Every decision, trade, and its reasoning still goes through
  `trade_log.record()` (`execution/trade_log.py`) — including sit-outs and
  vetoes, so "chose not to trade" is as visible in the log as "traded."
- Real order placement remains blocked by `assert_paper_mode()` /
  `AGENT_TRADER_LIVE` — the gate has no path around it.

## Open questions (for implementation time)

- Promotion criteria for a second strategy into a conjunctive gate: exactly
  what a candidate must clear in backtest (beat its own naive baseline, over
  how many regimes) before it earns a vote.
- How regime is computed concretely is settled (`agents/regime.py`); whether
  its bands want recalibrating once there's a real paper track record is open.
- Gate cadence: how often it convenes per symbol — currently once per
  weekday via the scheduled automation pass (`agents/AUTOMATION_DESIGN.md`).
