"""
Kalshi event-contract trading harness.

A separate world from the equities `execution/` + `agents/` council: Kalshi
markets are *binary* — every contract settles at $1.00 (YES resolves true) or
$0.00 (it doesn't). That changes everything downstream (pricing, fees,
settlement, sizing), so it gets its own package rather than being bolted onto
the share-based PaperBroker.

Same non-negotiable discipline as the rest of the repo, though:
  - paper/simulated first, real money behind a hard switch,
  - honest fills and honest fees (Kalshi's actual fee formula, not a fantasy),
  - and a go-live gate that must be met on a real paper track record before a
    single real dollar is at risk.

Read kalshi/KALSHI_DESIGN.md before touching this — it explains *why* a naive
"bet every day" strategy loses to fees, and what a positive-EV strategy would
have to look like.
"""
