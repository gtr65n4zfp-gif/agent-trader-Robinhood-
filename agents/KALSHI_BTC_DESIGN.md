# Kalshi Bitcoin — multi-timeframe systematic strand (design / sketch)

Blueprint only — no implementation yet. This is the outline for a new,
fully isolated strand: systematically trading **Kalshi Bitcoin event
contracts** across multiple horizons, structured like a small systematic
desk but honestly scoped to run from one machine, paper-first.

It reuses the repo's spine (regime filter, risk vetoer, paper broker with
honest fills, isolated account + dry-run flag, a go-live gate that is a
real bar and not a vibe), and adds the one thing this venue needs that
equities/options didn't: a **calibrated probability engine**, because a
Kalshi contract price *is* a probability and the whole edge is estimating
that probability better than the crowd.

## Guiding principle

> A Kalshi binary is priced 0–100¢ = the market's implied probability the
> event happens. Our only job is to estimate that probability better than
> the price, and to trade **only** when our estimate diverges from the
> price by more than fees + our own uncertainty. The edge is calibration
> and cost discipline, not conviction and not speed. No-trade is still the
> default.

## Honest framing (read this first)

"Big trading firm style, from my computer" is the right *structure* but the
wrong *edge* if taken literally. We will not win a latency race against a
real market-making desk, and Kalshi's crypto books are thin. What a solo on
a laptop can realistically have:

- A **better-calibrated terminal-price distribution** for BTC at each
  horizon than a retail-heavy order book prices in — especially on the
  far-from-money strikes retail systematically misprices.
- **Ruthless fee and risk discipline** — modeling Kalshi's fee from the
  first line of code, and refusing negative-EV-after-fees trades that look
  like "value" gross.

So we borrow the desk's *organization* (data → fair value → signal → risk →
execution → monitoring, each layer built and proven before the next) and
its *humility* (calibrate before you size, size small, one bet is one bet),
not the fantasy of out-trading professionals on speed. Everything below
serves that framing.

## What we're actually trading

Kalshi lists BTC markets as event contracts — "will BTC be **above $X** at
time **T**", plus range brackets ("BTC between $X and $Y"). They exist at
several horizons (intraday/hourly, daily, and longer). Each contract:

- Pays **$1.00** if it settles YES, $0.00 if NO. Price in cents = implied P.
- Settles against a **specific reference index at a specific time** — not
  "the BTC price" in the abstract. We must forecast, and later reconcile
  against, the *same* settlement reference Kalshi uses, or we're carrying
  silent basis risk between our model and the payout.
- Costs a **trading fee** on each fill. Kalshi's fee is roughly
  `fee ≈ ceil(0.07 × contracts × price × (1 − price))` (price in dollars),
  i.e. maximal at 50¢ and small near the wings. On a binary, this eats a
  large fraction of a thin edge, so it is a first-class input to the signal
  layer, never an afterthought.

> **To verify before any code (Milestone 0):** exact current BTC product
> lineup and horizons, the precise fee formula and any caps, the exact
> settlement reference/index and cut times, API auth + rate limits, and how
> much historical contract/settlement data is actually retrievable for
> backtest. These are the load-bearing unknowns; the rest of this design is
> stable regardless of how they resolve.

## Why multiple timeframes (they are different problems, one engine)

The same fair-value engine is instantiated per horizon, but each horizon is
a genuinely different forecasting problem — which is exactly why running
several is diversifying rather than redundant *if* the correlation is
budgeted (see Risk):

- **Intraday / hourly** — terminal distribution is narrow and dominated by
  current spot + very-short-horizon volatility and microstructure. Edge is
  fast, accurate short-horizon vol estimation and catching retail
  mispricing of far-OTM hourly strikes. Most opportunities, smallest edges,
  fees bite hardest.
- **Daily** — overnight/session vol, weekend effects. The sweet spot for a
  laptop: enough time to compute and place a considered order, distribution
  wide enough that model quality matters more than reaction speed.
- **Weekly / monthly** — drift assumptions and fat tails start to matter;
  volatility term structure matters; fewer, larger, slower bets.

One codebase, one `fair_value(horizon)` function, horizon-specific vol and
drift parameters. Do **not** build a separate strategy per horizon — build
one probability engine and parameterize it.

## The desk, laptop edition (the layers, built in this order)

Mirrors the existing repo discipline: each layer is built and *proven on
its own* before the next is added.

1. **Reference-data layer** — BTC spot + OHLCV history. Robinhood MCP
   (`get_crypto_quotes`, `get_crypto_historicals`) is the zero-new-dependency
   start, but sub-daily granularity for the hourly horizon likely needs a
   real crypto OHLCV feed (a public exchange REST endpoint). Must ultimately
   track the **same reference Kalshi settles on**.
2. **Kalshi market-data layer (read-only first)** — contract metadata,
   order books, and (where available) historical settlements via the Kalshi
   API. Read-only, no auth-to-trade, until the engine below is proven.
3. **Fair-value / probability engine** — the heart. Forecast BTC's terminal
   distribution at horizon `T`, then integrate it to `P(contract settles
   YES)`. Vol model: EWMA / GARCH(1,1) realized vol (we already have
   `backtest/vol_forecast.py` — reuse it), optionally cross-checked against
   options-implied vol (e.g. Deribit) as a sanity band. Map to probability
   via lognormal closed form for a first cut, Monte-Carlo for range brackets
   and fat-tailed horizons.
4. **Regime filter** — reuse the repo's regime concept (`agents/regime.py`):
   a BTC vol/trend classifier that (a) *sets the distribution width* the
   engine uses and (b) *gates* trading. Can only **tighten**, never loosen —
   during regime transitions and blow-off vol, the model is least reliable,
   so sit out. A regime sit-out is logged distinctly and never counts as a
   round-trip, exactly like the equity strand.
5. **Signal / edge layer** — compare fair value to Kalshi's book (use the
   ask you'd actually pay, not the mid), compute **expected value net of the
   Kalshi fee**, and fire only when `edge > threshold` *and* our calibration
   history says we're trustworthy at that probability band. Conjunctive gate,
   no-trade default.
6. **Risk layer (vetoer)** — the one that keeps a laptop desk alive:
   - Fractional-Kelly sizing, hard-capped well below full Kelly.
   - Per-market and per-horizon exposure caps.
   - **Aggregate BTC-directional budget** — see below; this is the single
     most important risk rule for this strand.
   - Daily-loss limit and trade-frequency cap, mirroring `risk_vetoer.py`.
   Pure veto: can kill a trade on exposure grounds regardless of edge.
7. **Execution layer** — fee-aware limit orders into the Kalshi book,
   passive-first (post inside the spread) with an aggressive fallback rule.
   **Paper broker first** (`kalshi_paper_broker`), isolated account, own
   trade log, own dry-run flag — same pattern as `options_paper_broker.py`.
8. **Backtest / calibration layer** — the go-live gate's evidence.
   Reliability diagrams and Brier score (are our 70% forecasts right ~70% of
   the time?), plus PnL **net of fees**. A directional lucky streak can show
   profit while being badly miscalibrated — calibration is the honest metric
   here, PnL alone is not.
9. **Monitoring / automation** — same cadence pattern as the existing
   council: a scheduled pass that plans then executes, dry-run by default,
   commits its logs as the persistence layer between cloud runs.

## The correlation rule (do not skip)

Hourly-YES-above, daily-YES-above, and weekly-YES-above BTC are **not three
independent bets** — they are one directional BTC view expressed three
times. The fastest way to blow up this strand is to see three "edges", size
each independently, and wake up with 3× the BTC exposure you thought you
had. So:

- The risk vetoer maintains an **aggregate signed BTC exposure** across all
  open Kalshi positions (and, ideally, nets against any BTC held in the
  equity/crypto side of the account).
- New positions are sized against the *remaining* budget, not from zero.
- Opposing-direction contracts across horizons partially offset and are
  allowed to; same-direction stacking is what the budget throttles.

## Fees and settlement basis are inputs, not footnotes

Two model inputs that retail ignores and a desk never does:

- **Fee model** — `execution/config.py` gets a `kalshi_fee(price, contracts)`
  used by *both* the signal layer (EV net of fee) and the paper broker
  (honest fills). A trade that's +edge gross and −EV after fee must be
  rejected, and the log must show both numbers.
- **Settlement basis** — forecast and settle against Kalshi's actual
  reference index/time. If our spot feed differs from Kalshi's settlement
  source, that gap is modeled as basis risk, not assumed to be zero.

## Isolation and safety (non-negotiable, same as every strand)

- **Paper only** until proven. No task places a real Kalshi order until the
  go-live gate below is met and a hard switch is deliberately flipped —
  mirror `assert_paper_mode()` / the `AGENT_TRADER_LIVE` unlock pattern with
  a Kalshi-specific equivalent.
- **Fully isolated account** — own cash pool, own portfolio file
  (`logs/kalshi_paper_portfolio.json`), own trade log
  (`logs/kalshi_trades.jsonl`), own dry-run flag. Never touches the equity
  or options accounts.
- Kalshi API keys live in `config/.env` (git-ignored), never committed.
- Every decision logged with reasoning, its regime state, fair-value P, book
  price, edge gross, fee, and edge net.

## Go-live gate (Kalshi strand)

Same spirit as Milestone 5's equity gate, plus the calibration bar this
venue demands. Real Kalshi money is blocked until **all** hold, measured on
the paper log:

- **≥ 30 closed/settled paper contracts** — below that the win-rate CI is
  too wide to mean anything.
- **Calibration proof**, not just PnL: a reliability diagram whose buckets
  land in-band and a **Brier score that beats the naive "trade the price"
  baseline** — because a profitable-looking directional streak can be
  badly calibrated and is not evidence of edge.
- **Performance holds across ≥ 3 distinct BTC vol/trend regimes**, not one
  favorable trending window.
- **Total PnL including Kalshi fees**, never gross, reported with the
  win-rate confidence interval.

## Milestones (proposed)

- **M0 — Recon (read-only).** Verify the load-bearing unknowns above: BTC
  product lineup/horizons, fee formula + caps, settlement reference/cut
  times, API auth + rate limits, historical-data availability for backtest.
  No trading code. Output: a short findings note that confirms or revises
  this design's assumptions.
- **M1 — Data.** Reference BTC feed (right granularity, right settlement
  basis) + Kalshi read-only market-data client. Proven by pulling a live
  book and reconciling one settled market by hand.
- **M2 — Probability engine + calibration harness (single horizon).** Daily
  first. Reuse `backtest/vol_forecast.py`. Prove calibration on history
  before it ever sizes a trade.
- **M3 — Multi-horizon + regime filter.** Parameterize the engine per
  horizon; wire the BTC regime classifier as a width-setter and gate.
- **M4 — Paper broker + fee model + risk vetoer + signal→paper execution.**
  Isolated account, dry-run default, aggregate-BTC-exposure budget live.
- **M5 — Forward paper + backtest across regimes.** Accumulate toward the
  go-live gate; report calibration + net PnL, not gross.
- **M6 — Tiny real-money pilot.** Only after the gate, behind the hard
  switch, smallest viable size.

## Open questions to resolve in M0

1. Exact BTC product lineup and the shortest horizon actually worth trading
   after fees (hourly edges may be structurally too small net of fee).
2. How much historical Kalshi settlement/price data is retrievable — this
   determines whether M2's backtest is real or thin, and may force a
   longer forward-paper period to compensate.
3. Settlement reference: which index, which cut time, and how far our spot
   feed drifts from it.
4. Whether Robinhood MCP crypto granularity suffices, or a dedicated crypto
   OHLCV feed is required from M1 (likely yes for anything sub-daily).
5. Fee cap specifics — they change which strikes/horizons are ever +EV.

---

*This is a sketch to align on shape and sequencing, deliberately at
blueprint altitude (matching `COUNCIL_DESIGN.md` and `SPY_OPTIONS_DESIGN.md`).
Nothing here is built yet. The next concrete step is M0 recon, which is
read-only and cheap, and either confirms these assumptions or tells us where
the design has to bend before a single trade module is written.*
