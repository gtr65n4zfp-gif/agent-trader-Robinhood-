# Time-series momentum — backtest results

Strategy #1 from `STRATEGY_LIBRARY.md`, tested on real data. **Verdict: it
does NOT clear the promotion gate on this watchlist and is not promoted to
the live gate.** The process did its job: a famous strategy got rejected
before any money was involved.

## What was tested

- **Signal:** `strategies/time_series_momentum.py`, the classic "12-1" rule.
  The signal is the trailing 12-month return, skipping the most recent month.
  Each symbol's trend size is measured against its own typical trend, which
  sets both the neutral band and the confidence.
- **How it trades here: long-only.** `execution/paper_broker.py` can't short,
  so a bullish month means hold the stock and anything else means sit in cash.
  The literature's long-short version is in the results JSON for reference
  only, because this system can't trade it.
- **Data:** real monthly bars from the Robinhood MCP (`get_equity_historicals`,
  split-adjusted), Jan 2015 – Sep 2026, for the watchlist plus SPY. There were
  109 scored decision months per symbol (Aug 2017 – Aug 2026). Before that, 12
  months were needed to build the lookback and 20 more to estimate the typical
  trend size.
- **Costs:** 5 bps per side on every position change (`config.SLIPPAGE_BPS`,
  the same haircut PaperBroker applies).
- **Harness / driver:** `backtest/momentum_backtest.py`,
  `backtest/run_momentum_backtest.py`. Signals are lookahead-safe through
  `backtest/data.py`'s `bars_through()`. Monthly bars with a 1-month horizon
  mean the scored windows don't overlap.
- **Reproduce:** fetch the same monthly bars and save the raw JSON, then run
  `python -m backtest.run_momentum_backtest <path>`. The raw cache and the full
  results JSON live under `logs/backtests/` and are git-ignored by convention.
  The numbers below are the record.

## Results — full period (Aug 2017 – Aug 2026)

"In-mkt" is the share of months the strategy actually held the stock. Ret is
total compounded return, Shp is annualized Sharpe (with the risk-free rate
taken as 0), and DD is max drawdown.

| Symbol | In-mkt | Strategy ret | Strategy Sharpe | Strategy DD | Buy-hold ret | Buy-hold Sharpe | Buy-hold DD | Beats B&H (Sharpe) | Shallower DD |
|---|---|---|---|---|---|---|---|---|---|
| AAPL  | 78% | +144.5%  | 0.51  | −39.6% | +711.8%  | 0.97 | −30.7% | no | no  |
| MSFT  | 77% | +180.7%  | 0.72  | −35.4% | +585.6%  | 1.01 | −31.0% | no | no  |
| GOOGL | 76% | +272.2%  | 0.70  | −24.2% | +620.0%  | 0.92 | −40.4% | no | yes |
| JPM   | 67% | +91.4%   | 0.50  | −35.4% | +263.8%  | 0.71 | −38.5% | no | yes |
| JNJ   | 62% | −30.7%   | −0.22 | −57.3% | +99.9%   | 0.52 | −19.9% | no | no  |
| NVDA  | 75% | +1719.8% | 1.02  | −43.2% | +5288.9% | 1.19 | −62.8% | no | yes |
| TSLA  | 61% | +419.9%  | 0.57  | −50.0% | +1394.6% | 0.75 | −67.7% | no | yes |
| SPY   | 81% | +161.4%  | 0.83  | −19.9% | +208.0%  | 0.85 | −24.8% | no | yes |

**NBIS was excluded.** As Yandex, it was halted from Mar 2022 to Sep 2024 at a
frozen $18.94 on zero volume. The driver drops everything through the last
zero-volume bar, which left 24 continuous months. That's too few for a
12-month signal, so NBIS is excluded rather than scored on fake prices.

**Pooled across the 8 symbols:**
- **"Buy" calls were right 56.0% of the time** (95% CI 52.1–59.8%). The base
  rate of up-months was **59.8%**, so buying blind would have done at least as
  well. The signal shows no timing edge.
- All directional calls together scored 51.4% accuracy (95% CI 47.9–54.9%),
  which is indistinguishable from a coin flip.
- It beat buy-and-hold on Sharpe for **0 of 8** symbols and had a shallower
  drawdown for **5 of 8**.

## Results — by market period

These are average per-symbol returns over each window of decision months.

| Period | Months | Strategy | Buy-hold | Time in market | Strategy ahead on |
|---|---|---|---|---|---|
| Late-cycle bull + 2018 selloffs | 29 | +20.6%  | +59.5%  | 69% | 0/8 |
| COVID crash + recovery          | 24 | +151.9% | +162.3% | 91% | 0/8 |
| 2022 bear market                | 12 | **−13.0%** | **−18.8%** | 43% | **6/8** |
| 2023–26 bull                    | 44 | +93.7%  | +243.0% | 72% | 0/8 |

This is the classic trend-following shape. In the one sustained bear market
the strategy stepped aside for more than half the months and lost less. In
every rising market it lagged badly, because it sat in cash during dips that
quickly recovered and re-entered late.

## Promotion gate (from `STRATEGY_LIBRARY.md`)

| Check | Result |
|---|---|
| Beats its own baseline (timing accuracy clearly above the always-long base rate) | **FAIL**: 56.0% vs 59.8% |
| Beats buy-and-hold on risk-adjusted return (Sharpe), net of costs | **FAIL**: 0/8 symbols |
| Holds across ≥3 regimes | **FAIL**: ahead in 1 of 4 periods (the bear market) |
| Independence check vs. the incumbent Technicals signal | Not reached, because it failed the gates above |

**Not promoted.** The live gate is unchanged.

## Why, and what this does and doesn't tell us

Be careful about over-reading this. Here's what the test does **not** show:

- **It doesn't refute the momentum literature.** The published edge comes from
  diversified long-short portfolios of futures across bonds, currencies,
  commodities, and equity indexes. This test ran something quite different:
  single stocks, long-only, one at a time.
- **The period and the watchlist are both heavily tilted toward buy-and-hold.**
  2015–2026 was a historic bull run, and the watchlist is made of names that
  are *today's* winners (NVDA, TSLA, AAPL...). That survivorship bias is
  exactly the setting where holding is hardest to beat.
- **Cash was assumed to earn 0%.** Real cash earned meaningful interest in
  2023–2025, which would help a strategy that sits in cash 20–40% of the time.
  It's a small bias that wouldn't come close to closing gaps this large.
- **The pooled confidence intervals are optimistic.** They treat
  symbol-months as independent, but these stocks move together.

Here's what it **does** show:

- As an **entry signal** on this watchlist, 12-1 momentum adds nothing.
  Its buy timing is no better than being always long, and it costs a large
  amount of upside.
- Its one real property is **drawdown control in a sustained decline**.
  It had shallower drawdowns on 5 of 8 symbols, came out ahead in 2022, and on
  SPY specifically it was almost risk-equivalent to holding (Sharpe 0.83 vs
  0.85) with a −19.9% vs −24.8% worst drawdown. That points to a possible
  future role as a **market risk-off overlay** (for example, "stay out of
  equities when SPY's 12-1 trend is negative") rather than a per-stock entry
  signal. That would be a different hypothesis with its own test. It isn't
  something this result licenses on its own.

## Next

- Leave momentum out of the live gate. Keep the code: it's tested, it's
  correct, and it's reusable as a building block. Cross-sectional momentum
  (strategy #2) ranks symbols by this same trailing return.
- Before building strategy #2, note the lesson this run teaches about
  **universe**: an 8-name list of today's mega-cap winners is a poor test bed
  for any strategy whose job is to beat holding them. Cross-sectional
  strategies need a broader, survivorship-aware universe even more than this
  one did (see `STRATEGY_LIBRARY.md` caveat 2).
