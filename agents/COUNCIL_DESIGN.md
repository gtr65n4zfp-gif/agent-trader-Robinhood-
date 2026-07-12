# Council design (Milestone 3)

Blueprint only — no implementation yet. This describes the shape of the
multi-agent trade-review system that sits between "the execution layer can
place a paper trade" and "a paper trade actually gets placed."

## Guiding principle

> No-trade is the default. The council doesn't exist to greenlight trades —
> it exists to only let a trade through when several independent,
> domain-isolated signals agree it's justified. The edge is discipline (not
> overtrading), not any single seat's cleverness.

## Seats

Each seat is **domain-isolated** — it only ever sees its own data category.
All seats share the same base model, so isolating *inputs* (not just prompts)
is the main defense against correlated errors: if every seat can see
everything, they tend to converge on the same mistake for the same reason.

- **Fundamentals seat** — sees only data from `research/sec_client.py`
  (filings, financial facts). Opines on long-term thesis: is this company
  fundamentally sound, cheap, or expensive. Never sees price or technical data.
- **Technicals/price seat** — sees only data from `execution/robinhood.py`
  (live quotes, price history, indicators). Opines on short-term setup,
  momentum, entry timing. Never sees fundamentals.
- **Risk vetoer** — sees only the proposed order size against the hard
  limits in `execution/config.py` (`MAX_POSITION_PCT`, `MAX_TRADE_USD`) and
  current account state. Pure veto power: can reject a trade on size or
  exposure grounds regardless of what the other seats say. Cannot originate
  a trade, only kill one.
- **Judge** — the only seat that sees the other seats' *outputs* (not their
  raw inputs). Weighs Fundamentals + Technicals + Risk vetoer into a single
  decision (buy / sell / hold / sit-out) plus a confidence score. Every judge
  decision is logged with its reasoning, alongside what each seat said.

## No-trade is the default (conjunctive gate)

A trade only fires if Fundamentals + Technicals + Risk vetoer + the regime
filter (below) **all** clear the bar — a conjunction (AND), not a vote (OR)
or an average. One seat objecting is enough to sit out. This is deliberate:
overtrading is a bigger threat to paper (and eventually real) P&L than any
single missed opportunity.

## Regime / volatility filter

Built (`agents/regime.py`). A simple, explainable filter, not a seat with a
vote: if the market — or the symbol — is in a low-volatility,
choppy/range-bound state, force a sit-out regardless of what the seats would
otherwise say. Most signal, fundamental or technical, is noisiest and least
reliable in choppy conditions; better to do nothing than trade a false signal.

Two axes, rule-based only (no HMM, no ML, no clustering):
volatility (low/normal/high, relative to `execution/config.py`'s calibrated
`TARGET_DAILY_VOL_PCT`) and trend (up/down/sideways, price vs. its EMA over
a wider band than the Technicals seat uses). Combined into named states —
`low_vol_ranging` and `ranging` are NOT tradeable; `low_vol_trend`,
`trending`, and `volatile_trend` are.

Can only TIGHTEN, never loosen: wired into `agents/judge.py`'s entry gate
as an additional condition (a non-tradeable regime forces HOLD outright,
checked first, never overridden by the seats), and into the exit engine
as the `regime_change` path (`agents/exits.py`) — a held position whose
regime flips to non-tradeable gets closed. A regime-caused sit-out is
logged distinctly (`action="regime_sitout"`) and, being a non-fill, never
counts toward the round-trip go-live counter.

## Exit logic — a first-class concern

Entries get most of the attention; exits are just as much a council concern,
with multiple independent paths so no single failure mode holds a position
open too long:

- **Stop-loss** — hard, mechanical, not overridable by the judge.
- **Take-profit** — target hit, gains locked in.
- **Regime change** — the regime filter flips against the open position.
- **Conviction drop** — a later council run re-evaluates and confidence falls
  below the entry threshold, even with no fixed price trigger hit.

Unlike the entry gate, exits are disjunctive: any one of these is enough to
close a position (OR, not AND) — once a position is open, the priority is
capital preservation, not confirmation.

## Ablation / baseline hook

Every council decision also records what a **single model**, given all
inputs at once (fundamentals + technicals + risk + regime, no seat
isolation, no conjunctive gate), would have decided in the same moment. This
shadow decision is logged alongside the real council decision but never
acted on. Purpose: once there's enough paper-trade history, compare the two
decision logs to test whether the multi-agent structure actually adds value
over a plain single-model filter — or whether it's just theater.

## Everything still routes through the existing plumbing

The council decides; it never executes directly.

- A buy/sell decision still goes through `PaperBroker`
  (`execution/paper_broker.py`) — same risk caps, same persistence.
- Every decision, trade, and its reasoning still goes through
  `trade_log.record()` (`execution/trade_log.py`) — including sit-outs and
  vetoes, so "chose not to trade" is as visible in the log as "traded."
- Real order placement remains blocked by `assert_paper_mode()` /
  `AGENT_TRADER_LIVE` — the council has no path around it.

## Open questions (for implementation time)

- Exact interface contract per seat: inputs in, decision + confidence +
  reasoning out.
- How regime is computed concretely (e.g. realized volatility over N days,
  ADX, etc.) — needs to be picked and justified, not just plausible-sounding.
- Council cadence: how often it convenes per symbol — ties to Milestone 1's
  still-missing automated decision loop.
