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

**Resolved (M1, corroborated but not primary-source-verified):** the 0.07
multiplier is actually the **crypto-specific (higher) rate**, not a general
default — the earlier framing had the direction backwards. Cross-checked two
independent ways: (a) a direct statement that Kalshi's multiplier "ranges
0.07 for most categories, higher for premium categories like Crypto," and
(b) published category peak-fee percentages (peak fee = multiplier × 0.25,
at the 50¢ strike) of **~0.75% sports, ~1.00% politics, ~1.75–1.80%
crypto** — which back out to multipliers of ~0.03, ~0.04, and **~0.07**
respectively. Both angles converge on **0.07 for BTC/crypto**, the number
this design had already guessed, now confirmed for the right reason instead
of the wrong one. `kalshi/config.py`'s `kalshi_fee()` now implements this
(`KALSHI_TAKER_FEE_MULTIPLIER = 0.07`, maker = 1/4 of that), self-tested
against the ~1.75–1.80% peak figure.

**Residual gap:** this is secondary-source corroboration, not a read of
Kalshi's own fee-schedule PDF — `docs.kalshi.com` and `kalshi.com` are both
**blocked by this sandbox's network egress proxy** (confirmed via the
proxy's own status endpoint returning `connect_rejected` for every Kalshi
host tried, not just a timeout). Re-verify against the real PDF or a live
order preview before this strand risks real money; low-priority to revisit
before then since two independent estimates already agree.

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
approach, fee shape (parabolic, maker discount, and now the actual
multiplier), passive-first execution rationale, and that a real backtest is
buildable. Revises: (a) note `BTCPERP` exists but is out of scope, (b) the
0.07 multiplier is BTC/crypto's own (higher) rate, not a shared general
default — corrected in `kalshi/config.py`. Resolves open questions #2 and
#3 outright, and #4 (partially — 0.07 is now load-bearing code, pending
primary-source confirmation). Open question #1 (shortest horizon worth
trading after fees) still needs the real *fair-value edge* distribution to
answer and stays a question for M2's calibration harness, not M0/M1.

## M1 progress (partial — environment-constrained)

Started, not complete. Built and self-tested (all passing, no network
calls):

- **`kalshi/config.py`** — the strand's own paper-mode switch
  (`KALSHI_PAPER_MODE`/`KALSHI_TRADER_LIVE`, distinct from the equity
  strand's), `kalshi_fee()` implementing the confirmed formula, starting
  cash and dry-run flag. Sizing/exposure caps deliberately deferred to M4
  (the risk vetoer), not guessed early.
- **`kalshi/market_data.py`** — read-only client: RSA-PSS request signing
  (`sign_request`/`build_auth_headers`), `get_markets()`, `get_orderbook()`,
  `get_candlesticks()`, and a candlestick normalizer. Self-tests prove the
  signing math is internally consistent (signs and verifies against its own
  keypair) and the request/response shapes are correct against injected
  fake transports — deliberately **not** a claim that Kalshi's real API
  accepts these requests.

**Blocked, not skipped:** this sandbox's network egress proxy denies the
entire `kalshi.com` domain and its API subdomains (confirmed via the
proxy's status endpoint, `connect_rejected` on every host tried — not a
flaky timeout). So the actual M1 exit criterion — pull a real live book,
pull real `candlesticks` for a settled market, and hand-reconcile one
settlement against BRTI — **could not be run from here**. That live
verification pass is the concrete next action, and needs an environment
that can actually reach `kalshi.com`.

## Next step

Run `kalshi/market_data.py` for real (or an equivalent script) from an
environment with live network access to Kalshi: confirm `BASE_URL` (three
different hostnames turned up across sources — `api.elections.kalshi.com`,
`trading-api.kalshi.com`, `external-api.kalshi.com` — pick the one that
actually returns 200s), pull one live order book, pull `candlesticks` for a
recently-settled BTC market, and hand-reconcile that settlement against a
BRTI print from the same window. Also worth a direct primary-source check
of the 0.07 crypto fee multiplier (a live order preview would confirm it
directly) while that access exists — cheap to close out given it's already
load-bearing in `kalshi_fee()`.
