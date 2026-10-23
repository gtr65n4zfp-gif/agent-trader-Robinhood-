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

### Test universes (every strategy, all three)

Defined once in `backtest/universes.py`, so every result says exactly which
symbols it covered:

- **`watchlist`**: the live watchlist plus SPY. It's what we trade, but it's
  a survivorship-biased test on its own.
- **`top30_2015`**: the ~30 largest US companies at year-end 2014. They're
  chosen by a rule fixed at the start of the test, so later losers are
  included.
- **`multiasset`**: 19 ETFs across stocks, bonds, gold, commodities, real
  estate, and sectors. This is the momentum literature's own setting.

A strategy is judged on all three, never the watchlist alone. Strategy #1
taught that the hard way: the watchlist alone made momentum look much worse
than it is. Every universe is also run with idle cash at approximate T-bill
rates (`--cash-tbill`), because a strategy that sits in cash shouldn't be
scored as if cash earns nothing.

**Use Sharpe only for windows where returns are positive.** For a window
where both sides lose money, Sharpe misranks them: the calmer loser gets the
more negative number. Judge those windows on return and drawdown.

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

- **Status.** Built and tested on real data from the Robinhood MCP: 109
  months of split-adjusted monthly bars, long-only, with costs, on all three
  test universes, and with idle cash both at 0% and at approximate T-bill
  rates. Full write-up in `agents/MOMENTUM_RESULTS.md`. **Not promoted.**
  - **On fair universes it roughly ties buy-and-hold on risk.** Portfolio
    Sharpe was 0.66 vs 0.68 on `top30_2015` and 0.35 vs 0.40 on
    `multiasset` with T-bill cash.
  - **Drawdowns were about 30% smaller** in every universe.
  - **Total return was about half** in every universe.
  - **There's no timing edge.** Buy calls were right no more often than
    simply holding.
  - **On the survivorship-biased `watchlist` it's clearly worse** (0.84 vs
    1.15).
  - **It narrowly fails the "beat buy-and-hold on Sharpe" check everywhere.**
    Its robust strength is drawdown control (it clearly won 2022), which
    points to a possible future *market risk-off overlay*. That's a separate
    hypothesis that needs its own test.
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

## Overlays (gates, not strategies)

An overlay doesn't pick trades. It decides when the whole book should
step aside, which makes it a candidate extension of `agents/regime.py`.
That module can only tighten: it blocks new buys and can trigger exits.

### Market risk-off switch ("safety switch") — *TESTED: promising, awaiting owner decision*

- **Rule:** once a month, step the whole portfolio into cash when SPY's own
  12-1 momentum is clearly down. Everything was pre-registered: the signal,
  two variants, and the pass criteria.
- **Status:** full write-up in `agents/RISK_SWITCH_RESULTS.md`.
  - **Over 24 years of SPY (2002–2026), it PASSES.** Max drawdown fell from
    −52% to −21%, and Sharpe rose from 0.50 to 0.64. That holds with
    dividends included.
  - **The pass comes from the slow bear markets.** It sat out March 2008 to
    September 2009 (−13.7% vs −43.5% in the crisis window) and the tail of
    the dot-com bust.
  - **Over the last 9 years alone, the primary version fails everywhere.**
    It caught 2022 too late, and that window has no 2008-style crash.
  - **The evidence rests on about two events,** so arming it is the owner's
    call.
- **Code:** `backtest/risk_switch_backtest.py`, which reuses the momentum
  signal unchanged.

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
2. **Universe size.** #2, #4, #5 want a real cross-section, and the 8-symbol
   watchlist is too thin to judge them. Partly addressed: `top30_2015` (31
   names) and `multiasset` (19 ETFs) in `backtest/universes.py` are usable
   cross-sections today. A truly broad, survivorship-free universe (hundreds
   of names, including delisted ones) still needs a bulk data source and
   pairs with Milestone 6.
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
