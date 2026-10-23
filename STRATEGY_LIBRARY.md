# Strategy library — roadmap

The build order for `strategies/`: the concrete signals this project will add,
one at a time, each proven on its own before the next. This is the "strategy
library" the project was reshaped around (see README's "History" and
`agents/COUNCIL_DESIGN.md`) — a direct answer to "what strategies do we
actually build, and in what order."

Blueprint only — **none of these is built yet** except where noted. The
sources are well-known SSRN/academic strategy papers (links at the bottom);
they are **starting points to validate, not turnkey alpha** — every one is
famous and therefore partly arbitraged away. SSRN fame is a reason to backtest
carefully, not a reason to trust the result.

## Guiding principle (inherited, unchanged)

> A strategy earns its place in the live gate only by beating its own baseline
> in backtest, across ≥3 distinct regimes, net of fees and slippage. No real
> money until the go-live gate (README) is met. Discipline is the edge; a
> famous signal with no measured edge on *our* universe is not a strategy, it's
> a citation.

## How a strategy plugs in

Every strategy is a **domain-isolated signal** that emits the same shape
`agents/technicals.py` already returns — `{seat, symbol, stance, confidence,
reasons}` — so the existing machinery consumes it unchanged:

- **Gate:** `agents/judge.py` (regime gate + confidence bar), with
  `agents/regime.py` as the risk-on/off filter and `agents/risk_vetoer.py` /
  `execution/paper_broker.py` as the final size/exposure veto.
- **Backtest:** `backtest/engine.py` (three isolated accounts: strategy,
  no-gate baseline, buy-and-hold) + `backtest/metrics.py` (win rate with
  Wilson CI, per-regime breakdown). Point-in-time data via
  `backtest/data.py`, proven lookahead-safe by `backtest/prove_no_lookahead.py`.
- **Promotion:** a candidate lives in `strategies/` and is validated
  standalone (the forecast model's `backtest/forecast_backtest.py` is the
  template). It joins the live gate only after clearing the promotion gate
  below. A second live strategy turns `judge.decide` back into a **conjunctive**
  gate — two independent signals must agree (the machinery is already kept for
  this).

### Per-strategy promotion gate (what "proven" means)

1. **Beats its own no-gate baseline** (`judge.baseline_decide`) on held-out
   data — not just positive, but better than naively trading the raw signal.
2. **Beats buy-and-hold** on risk-adjusted return (Sharpe), net of modeled
   fees/slippage, over the same window.
3. **Holds across ≥3 regimes** (trending up / down / choppy) — reusing the
   backtest's existing `regime_state` tagging, not a single lucky window.
4. **Independence check** before it joins a conjunctive gate: its decisions
   must be meaningfully decorrelated from the incumbent signal(s) — two signals
   that always agree add no diversification (the whole point of "Value and
   Momentum Everywhere" below).

Fail any of these → it stays in `strategies/` as a documented experiment, not
in the live gate. Same bar the whole project runs on.

---

## The build order

Ranked by how cleanly each drops into the existing gate/backtest/paper-broker,
easiest first.

### 1. Time-series momentum (trend-following) — *TESTED: FAILED the gate, not promoted*

- **Status.** Built and tested on real data: 109 months of split-adjusted
  monthly bars from the Robinhood MCP for the watchlist plus SPY, long-only,
  with costs. Full write-up in `agents/MOMENTUM_RESULTS.md`. **It failed every
  promotion check.** It beat buy-and-hold on Sharpe on 0 of 8 symbols. Its buy
  calls were right 56.0% of the time against a 59.8% always-long base rate. It
  came out ahead in only 1 of 4 market periods, the 2022 bear market, where it
  lost −13% against −19% for buy-and-hold. Its one real strength is
  drawdown control, which makes it a possible future *market risk-off
  overlay*. That would be a separate hypothesis with its own test. The code
  stays as a tested building block. It is not in the live gate.
- **Signal.** An instrument's own trailing 12-month return (skipping the most
  recent month — the "12-1" convention) predicts its next-period return:
  positive → long, otherwise → cash (this system can't short). Classic
  trend-following (Moskowitz, Ooi & Pedersen). Self-normalized: the neutral
  band and confidence scale to the symbol's own typical trend magnitude.
- **Data.** Price bars only, from the Robinhood MCP's `get_equity_historicals`.
  The test used monthly bars, the literature's frequency. Signals are
  lookahead-safe through `backtest/data.py`'s `bars_through()`. **No new data
  source.**
- **Why first.** Purely price-based, computes from data we already fetch, and
  the volatility-scaled sizing it wants is already in `execution/config.py`
  (`TARGET_DAILY_VOL_PCT`) and the regime filter. Lowest integration cost.
- **What happened to the catch.** The predicted weakness showed up exactly.
  It bled in V-shaped reversals: in every rising period it sat in cash
  through dips that quickly recovered and re-entered late. One lesson carries
  forward to every later strategy: **this watchlist is a poor test bed.**
  Eight of today's mega-cap winners, measured over a historic bull decade,
  are survivorship-biased toward buy-and-hold.

### 2. Cross-sectional momentum (relative strength)

- **Signal.** Rank the watchlist by trailing return; long the top, short/avoid
  the bottom. Relative, not absolute — different failure mode from #1, which is
  the point of running both.
- **Data.** Daily bars across the whole watchlist at once. Needs the engine to
  score symbols *relative to each other* on a date, a small extension to the
  current one-symbol-at-a-time loop in `backtest/engine.py`.
- **Why second.** Same data as #1, but introduces cross-sectional ranking —
  the first real step toward Milestone 6 (universe selection).
- **Catch.** Needs a real universe to rank; thin on an 8-symbol watchlist.
  Honest about this: it only gets interesting once the universe widens.

### 3. Short-term mean-reversion

- **Signal.** Fade stretched moves: oversold (low RSI, price far below EMA) →
  long; overbought → short, on a days-scale horizon. **Your live Technicals
  signal (`agents/technicals.py`) is already a cousin of this** — this item is
  really "formalize and backtest what we half-have."
- **Data.** Already computed (RSI, EMA, ATR in `backtest/data.py`).
- **Why third.** Least new code — mostly separating the mean-reversion logic
  into its own testable strategy and measuring it honestly rather than
  assuming the current blend works.
- **Catch.** Mean-reversion and trend-following are opposites; they must be
  regime-gated (reversion in chop, trend in trends) or they cancel. This is
  exactly what `agents/regime.py` is for — prove it.

### 4. Low-volatility / betting-against-beta

- **Signal.** Favor low-volatility / low-beta names over high-beta ones on a
  risk-adjusted basis (Frazzini & Pedersen). Cross-sectional, slow-moving.
- **Data.** Trailing volatility (have it) + beta vs. a market proxy (new: a
  rolling regression of symbol returns on an index like SPY — modest).
- **Why fourth.** Conceptually close to the vol-scaling already in the risk
  layer, but as an *entry* signal it's new and needs the beta computation.
- **Catch.** A low-turnover factor — needs a long backtest window to say
  anything; short slices will be noise.

### 5. Value + momentum combo — *the capstone*

- **Signal.** Combine a value measure with momentum; the research result
  (Asness, Moskowitz & Pedersen) is that they're **negatively correlated**, so
  the combination's risk-adjusted return beats either alone.
- **Data.** Momentum (have it, from #1/#2) + a value measure. Value is the hard
  part: it needs fundamentals, which this environment's network policy blocks
  (the very dependency the reshape removed). **Flagged as the crux** — either a
  price-based value proxy, or deferred until a bulk fundamentals source exists
  (ties to Milestone 6's "bulk data source" decision).
- **Why last.** It's the direct demonstration of why the gate is conjunctive:
  two independent, negatively-correlated signals. But it depends on #1 existing
  and on resolving the value-data question — the most blocked, highest-payoff
  item.
- **Catch.** Don't fake the value leg with something that's secretly momentum
  again — the independence check in the promotion gate exists precisely to
  catch that.

---

## Already in flight (not on this list)

- **Forecast model** (`strategies/forecast_model.py`, `agents/forecast_seat.py`)
  — an OLS return model over momentum/mean-reversion/volatility features. It's
  the furthest-along candidate and the first in line for the conjunctive-gate
  promotion, pending its `backtest/forecast_backtest.py` clearing the gate.
- **SPY options vol-edge** (`backtest/options_engine.py`, the vol-edge strand)
  — a separate, already-built options strategy track, not part of this equity
  signal roadmap.

## Honest caveats (read before building any of these)

1. **Crowding.** All five are published and well-known; expect the live edge to
   be a fraction of the paper's. The promotion gate (beat baseline *and*
   buy-and-hold, net of costs, across regimes) is what separates a real
   remaining edge from a citation.
2. **Universe size.** #2, #4, #5 want a real cross-section; the current
   8-symbol watchlist is too thin to judge them. They pair with Milestone 6.
3. **Value data is blocked here.** #5's value leg needs fundamentals, which the
   environment's network policy denies (same block that retired the SEC agent).
   Resolve the data source before committing to it, don't paper over it.
4. **One at a time.** Build, backtest, and prove #1 before starting #2. Adding
   surface area before the previous layer is validated is the exact anti-pattern
   the whole project is built to avoid.

## Sources

- Kakushadze & Serur, *151 Trading Strategies* — [SSRN](https://papers.ssrn.com/abstract=3247865)
  · [arXiv](https://www.arxiv.org/pdf/1912.04492) (note: the Palgrave book
  edition is retracted; the preprint remains).
- Moskowitz, Ooi & Pedersen, *Time Series Momentum* — [SSRN](https://papers.ssrn.com/abstract=2089463).
- Asness, Moskowitz & Pedersen, *Value and Momentum Everywhere* (J. Finance, 2013).
- Frazzini & Pedersen, *Betting Against Beta*.
- Harvey et al., *The Impact of Volatility Targeting* — informs the existing
  regime/vol-scaling layer.
