# Kalshi baseball bot — design (blueprint)

Blueprint only — **no implementation yet**. This is the same "design before
code" discipline every other track in this repo starts with (see
`agents/COUNCIL_DESIGN.md`, `agents/AUTOMATION_DESIGN.md`). It answers a
feasibility question — *can we build a Kalshi bot that trades MLB game
outcomes using baseball statistics?* — before a line of trading code exists.

The answer is **yes, it's buildable, and it maps cleanly onto the machinery
this repo already has** — but only if we are honest up front about (1) where
the edge would actually come from, (2) why some obvious stats (WAR) are the
wrong tool, and (3) two hard environmental constraints that block a naïve
"just call the APIs" approach.

## Guiding principle (inherited, unchanged)

> No real money until the system is provably profitable in paper trading
> over a meaningful stretch of time. No-bet is the default. The edge is
> discipline (only betting when our model disagrees with the market by more
> than the fees), not any single stat's cleverness.

This is a **prediction/event market**, not equities, so the go-live gate is
restated below in the vocabulary that actually measures a probabilistic
forecaster (calibration, Brier score, closing-line value) rather than raw
win rate.

---

## Two constraints to resolve before this can run live

These are stated first because they gate everything else. Neither blocks
*offline* development (modeling, backtesting) — both block *live* operation
in the current cloud environment.

### 1. Network policy blocks the data + exchange hosts

The cloud environment's outbound proxy currently **denies** the hosts this
bot needs (verified 2026-09-20 — all returned a 403 `connect_rejected` at
the gateway):

- `statsapi.mlb.com` (MLB Stats API — the primary free data source)
- `www.baseball-reference.com` (and by extension pybaseball's B-Ref scrapers)
- `api.elections.kalshi.com` (Kalshi's trading API)

The existing Robinhood system only works because it talks through an **MCP
connector** (routed via Anthropic's MCP proxy, which *is* allowed), not
direct HTTP. There is **no Kalshi MCP connector** available in this session.

**Implication / options:**
- **Offline first (recommended).** Modeling + backtesting need no live
  network — historical data (Retrosheet, cached Statcast/pybaseball pulls)
  can be vendored into the repo or a local cache. Build and validate the
  forecaster entirely offline, exactly as the SPY options backtest was.
- **Live later** requires one of: (a) a network-policy allowlist for the
  three hosts above, or (b) a Kalshi MCP connector, or (c) running the live
  leg outside this cloud env. This is a deliberate decision to make with the
  user, not to route around.

### 2. The edge question is the entire project

Kalshi's MLB contracts price off (and arbitrage against) the Vegas
moneyline, which already embeds essentially all public information — starter,
bullpen, lineup, park, weather. **Beating that price with public stats means
out-forecasting one of the sharpest markets that exists, then still clearing
Kalshi's fees and the bid/ask spread.** This is not a reason not to build it;
it *is* the reason the whole thing stays paper-mode behind the same hard
switch as the equity system, and why the baseline comparison below is not
optional. If our model can't beat the closing line, we have no edge and we
don't bet — full stop.

---

## Why WAR is the wrong headline stat (and what to use instead)

The original ask named **Wins Above Replacement**. WAR is a season-long,
**cumulative value** metric — "how many wins this player was worth vs. a
freely-available replacement, over the whole year." Used as a single-game
predictor it is a category error:

- It is **backward-looking and season-aggregated** — it answers "was this
  player valuable this year," not "who wins tonight."
- It **blends offense, defense, baserunning, and (for pitchers) run
  prevention into one number**, which is exactly the wrong granularity for a
  game where today's *starting pitcher* dominates the outcome.
- Feeding raw WAR into a win-probability model double-counts and washes out
  the game-specific signal that actually moves the line.

**What actually predicts a single MLB game** (the components a real model
uses):

- **Starting pitcher**, *projected* not season-to-date — FIP / xFIP / SIERA,
  and ideally a projection system's rest-of-season number, because ERA is
  noisy over a start or two.
- **Bullpen** strength and recent usage/fatigue (games/pitches last 2-3 days).
- **Team offense** — wOBA / wRC+, park- and opponent-adjusted.
- **Park factors**, **weather** (wind direction/speed, temperature — real
  run-environment effects), **umpire** tendencies.
- **Lineup as actually posted** (rest days, platoon splits), rest/travel.

**Modeling approach** (standard, explainable, not black-box):
- A **log5 / Bradley-Terry** matchup win probability from team strength with
  a **starting-pitcher adjustment**, **or** a team **Elo** rating updated
  game-to-game (the retired FiveThirtyEight MLB Elo is the reference design).
- **Calibrate** the model's probabilities against realized outcomes — a
  forecast that says 60% must win ~60% of the time. Calibration is the
  product here, not point accuracy.

**Where WAR legitimately helps:** as a slow-moving **prior on talent** to
stabilize small in-season samples — a stabilizer/component, never the model.

### Data sources (and their terms)

- **MLB Stats API** (`statsapi.mlb.com`) — free, official, no scraping;
  schedules, live lineups, results. Primary live source.
- **Baseball Savant / Statcast** — pitch-level and expected-stat data.
- **`pybaseball`** — one Python library wrapping FanGraphs, Baseball
  Reference, Retrosheet, and Statcast, **with local caching**. This is the
  polite path.
- **Retrosheet** — gold-standard historical play-by-play for backtesting.
- **Baseball Reference directly:** its terms **discourage scraping** and it
  rate-limits aggressively. Do **not** write a homemade B-Ref scraper —
  go through `pybaseball` (cached) or the MLB Stats API. This is a
  correctness/ToS constraint, not a preference.

---

## How it maps onto this repo's existing machinery

The council architecture transfers almost directly; only the domains change.

| Equity system (existing)                     | Kalshi baseball equivalent                                                                 |
| -------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `research/` (SEC filings)                    | `baseball/` data layer — MLB Stats API / pybaseball, cached; no live-network dependency in tests |
| Fundamentals + Technicals seats              | **Model seat** (our calibrated win prob) + **Market seat** (is the contract mispriced vs. our prob, net of fees?) — domain-isolated as before |
| Regime filter (sit out choppy conditions)    | **Edge gate**: only bet when \|model_prob − implied_prob\| clears fees + a margin; sit out otherwise |
| Risk vetoer + `PaperBroker`                  | Kalshi **paper broker**; **fractional-Kelly** stake sizing with a hard cap; same per-day / drawdown breakers |
| Judge (conjunctive, no-trade default)        | Same — a bet fires only if model edge **and** market seat **and** risk vetoer all clear    |
| Ablation / single-model baseline             | Same — log what a naïve "bet the favorite" and a "bet the Vegas-implied fav" strategy would do, never acted on |
| `assert_paper_mode()` / `AGENT_TRADER_LIVE`  | **Reused unchanged** — real Kalshi orders stay behind the exact same hard switch           |
| Go-live gate (≥30 trades, CI on win rate)    | Restated for a forecaster — see below                                                       |

### Seats (domain-isolated, same principle as the council)

- **Model seat** — sees only baseball data (`baseball/`). Emits a calibrated
  P(home win) and P(away win). Never sees the market price (so it can't be
  anchored by the line).
- **Market seat** — sees only Kalshi order-book data: the contract's
  yes/no prices, spread, and fees. Converts price → implied probability.
  Never sees our model's number.
- **Judge** — the only seat that sees both outputs. Computes edge =
  model_prob − implied_prob, and fires only if it clears the fee-plus-margin
  bar. No-bet is the default.
- **Risk vetoer** — fractional-Kelly stake with a hard `MAX_STAKE_USD` cap,
  per-day bet count and daily-loss breakers, and a per-day exposure cap.
  Pure veto; can only shrink or kill a bet, never originate one.

### Fees and sizing are first-class, not an afterthought

- **Kalshi charges trading fees** (a per-contract fee that scales with
  price); a naïve edge that ignores them is imaginary. The market seat must
  quote implied probability **net of the fee it would actually pay**, and the
  judge's margin must sit on top of that.
- **Stake sizing = fractional Kelly** (e.g. ¼-Kelly) off the *edge*, floored
  and capped — never full Kelly (too aggressive on an uncertain model), never
  flat (ignores edge size). Config lives beside the equity risk caps in
  `execution/config.py`.

---

## Go-live gate (restated for a probabilistic forecaster)

The equity gate ("≥30 closed trades, win rate with CI across ≥3 regimes")
is about a directional strategy. A baseball forecaster needs the metrics that
actually catch a bad probability model *before* money is on it:

- **Calibration** — bucket every forecast; 60%-predicted games must win
  ~60% of the time. A reliability curve, not a single number.
- **Brier score / log loss**, beating two baselines: (a) always bet the home
  team, and (b) **the Vegas/Kalshi closing-implied probability** itself. If
  we can't beat the closing line's own implied prob, we have no edge.
- **Closing-line value (CLV)** — did we get a better price than the market's
  close? Positive CLV over a large sample is the single most trusted
  proof-of-edge in sports markets, more than short-run P&L.
- **≥ a full, pre-registered sample of settled paper bets** (a season slice,
  not a lucky week) — same "n is too small to mean anything" logic as the
  equity gate's ≥30, but sized to game cadence.
- **Net P&L after Kalshi fees and realistic fills**, never gross.

Until all of these hold on the paper log, real Kalshi trading stays blocked
by `assert_paper_mode()` / `AGENT_TRADER_LIVE`, no exceptions — identical to
the equity rule.

---

## Suggested build order (each layer verified before the next)

Mirrors how the equity system was built — foundation first, no live money,
each layer self-contained and provable on its own.

1. **Data layer (`baseball/`)** — MLB Stats API + pybaseball wrapper, with a
   local cache so tests and backtests run **with no live network** (works
   today despite the proxy block). Ingests schedules, starters, results.
2. **Win-probability model** — log5-with-starter or Elo; **calibrated**
   offline against Retrosheet history. This is the real intellectual work.
3. **Backtest harness** — replay historical seasons, score the model on
   Brier/log-loss/calibration vs. the two baselines. Reuses the pattern in
   `backtest/`. **This is where we find out if there's any edge at all** —
   if the model can't beat the closing line here, stop.
4. **Kalshi paper broker + market seat** — model implied prob, fees, spread,
   fractional-Kelly sizing; paper fills only.
5. **The council wiring** — model seat + market seat → judge → risk vetoer →
   paper broker → trade log, with the single-model baseline logged alongside.
6. **Live leg** — only after the network/connector constraint is resolved
   *and* the go-live gate is met.

## Open questions (for implementation time)

- **Model choice** — Elo vs. log5-with-projections. Elo is simpler and
  self-updating; log5 needs a projection input but is more transparent about
  *why* a team is favored. Pick and justify, don't hand-wave.
- **How to resolve the live-network constraint** — allowlist, Kalshi MCP
  connector, or run the live leg elsewhere. A user decision.
- **Which Kalshi markets** — single-game moneyline (most liquid, tightest,
  hardest to beat) vs. series/futures (less efficient, but slower feedback
  and thinner). Start by *measuring* which the model beats in backtest.
- **In-game vs. pre-game** — pre-game only to start (one decision per game);
  live in-game markets are a much harder, latency-sensitive problem, deferred.
