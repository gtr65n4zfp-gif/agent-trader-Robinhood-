# M0 recon findings — Kalshi Bitcoin (read-only, no code)

Answers the open questions from `kalshi/DESIGN.md`'s M0 milestone, from
Kalshi's public API docs and third-party guides (WebFetch to
`docs.kalshi.com` was blocked by the network egress proxy in this session —
the exact fee multiplier below needs a direct doc check, or a hit against
`GET /account/api-limits`/a real order preview, before Layer 5/6 code is
written). Everything else here is corroborated across multiple sources.

## 1. Product lineup and horizons

Six cadences, all on the same underlying, confirming the "one engine,
horizon-parameterized" design:

| Ticker family | Cadence |
|---|---|
| `KXBTC15M` | 15-minute |
| `KXBTCD` (hourly variant) | hourly |
| — | daily |
| — | weekly |
| — | monthly |
| — | yearly |

**Revision to the design:** Kalshi also now lists **`BTCPERP`** — a
CFTC-regulated Bitcoin **perpetual futures** contract (margin-based,
launched June 2026), not an event contract. This is a structurally
different instrument (continuous P&L, funding rate, margin — not a
0/$1 binary) and is **out of scope for this strand as designed**. Worth a
one-line callout in `DESIGN.md` so a future reader doesn't conflate it with
the event-contract lineup this design targets, but not a reason to change
the plan now.

**Confirms:** the 15-minute market is the real stress-test of the fee model
— narrowest distribution, most fee-sensitive, exactly where the design
already predicted "hourly edges may be structurally too small net of fee"
(open question #1). Worth checking 15-min *and* hourly, not just hourly, in
M2.

## 2. Fee formula

Confirmed structurally: **`fee = ceil(0.07 × P × (1−P) × 100) / 100`
per contract for takers**, roughly **1/4 of that for makers** (i.e. passive
fills are meaningfully cheaper — reinforces the design's "passive-first"
execution rule in Layer 7). Parabolic, peaks at 50¢, near-zero at the wings
— exactly as `DESIGN.md` assumed.

**Revision — needs direct verification before Layer 5/6 are coded:**
multiple sources note the 0.07 multiplier is the *general* rate and that
"premium markets like Crypto" may carry a **higher** multiplier. If BTC
specifically uses a higher multiplier than 0.07, the EV-net-of-fee math in
`fair_value.py`/`signal.py` needs that real number, not the general default
— using the wrong constant would systematically overstate edge on every
contract. **Action:** pull the exact per-category fee schedule from
`docs.kalshi.com` (or `GET /exchange/schedule`-style endpoint / an order
preview call) as the first step of M1, before `kalshi/config.py`'s
`kalshi_fee()` is written with a hardcoded constant.

## 3. Settlement reference — fully resolved

All BTC contracts across all six cadences settle against **the same
reference regardless of horizon**: a **60-second average of the CF
Benchmarks Bitcoin Real-Time Index (BRTI)**, sampled once per second over
the final minute before the window closes.

This fully answers open question #3. It's a well-defined, continuously
published index (CF Benchmarks is the same house that indexes CME's BTC
futures), so:
- `reference_data.py`'s job is now concrete: track/replicate a 60-second
  trailing average of BRTI (or a very tight proxy for it), not "BTC price"
  in the abstract.
- Basis risk is bounded and estimable: it's specifically "our spot proxy
  vs. CF Benchmarks BRTI's 60-second trailing average," not an unbounded
  unknown. Worth quantifying this gap empirically in M1 (pull our feed and
  a BRTI-equivalent side by side over a settlement window) before trusting
  the fair-value engine's output.

## 4. API auth and rate limits

- **Auth:** RSA-PSS request signing — `KALSHI-ACCESS-KEY`,
  `KALSHI-ACCESS-TIMESTAMP`, `KALSHI-ACCESS-SIGNATURE` headers, key pair
  generated from the Kalshi dashboard (Settings → API). Scopes are
  `["read", "write"]`, defaulting to full access if omitted — **M1-M3
  should request a `read`-only key**, matching the design's "read-only
  first" rule for `market_data.py`, and only add a `write`-scoped key when
  Layer 7's paper broker needs to place real (still paper-mode-gated)
  orders later.
- **Rate limits:** ~10 req/sec per key, tiered token-bucket budgets beyond
  that; `GET /account/api-limits` reports the actual tier. Fine for a
  single-symbol, few-horizon strand polling on a schedule — no redesign
  needed, but `market_data.py` should read and respect that endpoint rather
  than hardcoding an assumed limit.

## 5. Historical data availability — resolved, real backtest is feasible

`GET /historical/markets/{ticker}/candlesticks` — periods of 1/60/1440
minutes (minute/hour/day), up to 100 tickers per request, up to 10,000
candlesticks per response. This directly answers open question #2:
**there is enough retrievable history to build a real M2 backtest**, not
just a thin one — daily and hourly granularity are both natively available,
which lines up with the design's proposed "daily first" sequencing in M2.

## Net effect on the design

No architectural changes. Confirms: product lineup, one-engine-many-horizons
approach, fee shape (parabolic, maker discount), passive-first execution
rationale, and that a real backtest is buildable. Revises: (a) note `BTCPERP`
exists but is out of scope, (b) do not hardcode the 0.07 fee multiplier for
BTC without checking whether crypto carries a higher one — this is now the
single highest-priority verification before any Layer 5/6 code is written,
since it directly changes which strikes/horizons are ever +EV. Resolves
open questions #2 and #3 outright; open question #1 (shortest horizon worth
trading after fees) still needs the real fee number to answer and stays a
question for M2's calibration harness, not M0.

## Next step

M1: build `market_data.py` against a **read-scoped** key, pull one live book
plus a `candlesticks` pull for a settled market, and hand-reconcile a single
settlement against a manually-fetched BRTI-equivalent print — before writing
a line of `fair_value.py`. Also resolve the fee-multiplier question as part
of that same pass (it's a read-only lookup, not a trading action).
