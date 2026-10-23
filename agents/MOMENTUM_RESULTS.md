# Time-series momentum: backtest results

Strategy #1 from `STRATEGY_LIBRARY.md`, tested on real data across three
universes. **Verdict: not promoted to the live gate.** On a fair test, with a
broad universe and cash earning interest, long-only momentum is roughly
*risk-equivalent* to buy-and-hold. It has consistently smaller drawdowns, about
half the total return, and no timing edge. It narrowly fails the gate's
"beat buy-and-hold on Sharpe" check in every universe. Its one robust property,
drawdown reduction, makes it a candidate *risk overlay*. That is a separate
hypothesis that needs its own test.

## What was tested

- **Signal:** `strategies/time_series_momentum.py`, the classic "12-1" rule.
  It takes the trailing 12-month return and skips the most recent month. The
  neutral band and confidence are scaled to each symbol's own typical trend
  size.
- **Long-only.** `execution/paper_broker.py` can't short, so "bullish" means
  hold it and anything else means cash. The literature's long-short version
  is kept in the results JSON for reference only.
- **Data:** real split-adjusted monthly bars from the Robinhood MCP
  (`get_equity_historicals`), Jan 2015 – Sep 2026. That gives 109 scored
  decision months per symbol (Aug 2017 – Aug 2026), after 12 months of
  lookback and 20 more to estimate the typical trend size.
- **Costs:** 5 bps per side on every position change (`config.SLIPPAGE_BPS`).
- **Two scores:** each symbol on its own, and an **equal-weight portfolio**
  across the universe, rebalanced monthly. The portfolio is the headline,
  because that's how the momentum literature evaluates the strategy.
- **Code:** `backtest/momentum_backtest.py` (harness),
  `backtest/run_momentum_backtest.py` (driver), and `backtest/universes.py`
  (the three symbol lists and how each was chosen). Signals are
  lookahead-safe through `backtest/data.py`'s `bars_through()`.
- **Reproduce:** fetch monthly bars for a universe and save the raw MCP JSON,
  then run `python -m backtest.run_momentum_backtest <universe> <files...>
  [--cash-tbill]`. Raw caches and result JSON live under the git-ignored
  `logs/backtests/`; the numbers below are the record.

## The three universes

| Universe | What it is | Why |
|---|---|---|
| `watchlist` | The live watchlist plus SPY (8 scored; NBIS excluded, see below) | What the system actually trades |
| `top30_2015` | ~30 largest US companies at **year-end 2014**: AAPL, XOM, MSFT, BRK.B, GOOGL, JNJ, WFC, GE, WMT, JPM, PG, CVX, PFE, META, VZ, ORCL, KO, BAC, INTC, T, GILD, MRK, AMZN, DIS, IBM, V, PEP, CMCSA, HD, CSCO, C | Chosen by a rule fixed at the **start** of the test, not by hindsight, so later disappointments (GE, IBM, INTC, XOM, T, VZ, C...) are included |
| `multiasset` | 19 ETFs: US/intl/EM stocks, Treasuries, corporate and high-yield bonds, gold, commodities, real estate, 9 US sectors | The momentum literature's own setting: a diversified cross-asset basket |

Limits of these universes (also in `backtest/universes.py`):

- The 2015 ranking comes from general knowledge, so the order near #30 is
  approximate.
- It isn't fully survivorship-free: no mega-cap delisted, and delisted
  tickers can't be fetched anyway.

## Data checks

- **All 50 symbols** had a full 141 months, no gap-fill bars, and no
  zero-volume bars, except NBIS.
- **NBIS (ex-Yandex) is excluded.** It was frozen at $18.94 on zero volume for
  31 months during its 2022–24 halt. The driver drops everything through the
  last zero-volume bar, which left 24 months: too few for a 12-month signal.
- **No splits or spin-offs show up as fake crashes.** NVDA starts at $0.48
  (fully split-adjusted). Known spin-off months show no artificial drop: GE
  +23.6% (Jan 2023, GE HealthCare) and +14.3% (Apr 2024, Vernova), T +5.7%,
  PFE +20.4%, MRK +2.5%. Robinhood's history appears adjusted for spin-offs
  as well as splits.
- **Unverified 2026 moves:** INTC +114% (Apr 2026), ORCL +39.9% then −35.1%
  (May–Jun 2026), GOOGL +33.8% (Apr 2026), CSCO +31.6% (May 2026). These are
  beyond what could be cross-checked. They were left as-is, since the
  strategy and buy-and-hold both see the same prices.
- **Prices only, no dividends,** on both sides. That understates buy-and-hold
  slightly more, because it holds every month.

## Cash interest

The first watchlist run credited idle cash with 0%. For a strategy that sits
in cash 30–40% of the time, that matters: real 3-month T-bills paid about
5% a year in 2023–24. Each universe was therefore run twice. The second run
(`--cash-tbill`) credits cash months with the year's approximate average
3-month T-bill rate, and computes Sharpe on excess return over that rate.

The rates (`TBILL_ANNUAL` in the driver) are **approximate, from general
knowledge**, not fetched data. They can't come from the price feed: T-bill
ETFs like BIL pay their yield as dividends, so their price is flat. 2025–26
are the least certain. This makes the run a sensitivity check, not a precise
figure.

## Headline: equal-weight portfolio, full period (Aug 2017 – Aug 2026)

| Universe | Cash | Strategy return | Strategy Sharpe | Strategy max DD | Buy-hold return | Buy-hold Sharpe | Buy-hold max DD | Time in market |
|---|---|---|---|---|---|---|---|---|
| watchlist  | 0%     | +284.2% | 0.94 | −20.3% | +882.3% | **1.27** | −30.7% | 72% |
| watchlist  | T-bill | +314.2% | 0.84 | −20.2% | +882.3% | **1.15** | −30.7% | 72% |
| top30_2015 | 0%     | +86.0%  | 0.84 | **−15.7%** | +186.3% | 0.85 | −21.8% | 59% |
| top30_2015 | T-bill | +104.4% | 0.66 | **−15.7%** | +186.3% | 0.68 | −21.8% | 59% |
| multiasset | 0%     | +40.0%  | 0.56 | **−12.8%** | +82.9%  | 0.61 | −18.1% | 58% |
| multiasset | T-bill | +55.1%  | 0.35 | **−12.8%** | +82.9%  | 0.40 | −18.1% | 58% |

How to read it:

- **On the watchlist, momentum is clearly worse.** That's the
  survivorship-biased case.
- **On the two fair universes, the Sharpe gap is tiny** (0.66 vs 0.68; 0.35
  vs 0.40). That's well within the noise of nine years of monthly data, so it
  should be read as a draw, not a win for either side.
- **The portfolio drawdown is about 30% smaller in every universe.**
- **Total return is about half** in every universe.

## Timing: does a "buy" call beat always holding?

Pooled across symbols, buy calls were right about as often as simply holding
would have been. There's no timing edge.

| Universe | Buy calls | Right | 95% CI | Base up-month rate |
|---|---|---|---|---|
| watchlist  | 629   | 56.0% | 52.1–59.8% | 59.8% |
| top30_2015 | 2,002 | 54.5% | 52.3–56.6% | 54.7% |
| multiasset | 1,209 | 56.8% | 54.0–59.6% | 56.1% |

## Which symbols it beat

With T-bill cash, it beat buy-and-hold on Sharpe for these symbols:

- **top30_2015 (7/31):** XOM, GE, WMT, PFE, ORCL, T, AMZN. Mostly names that
  went through long slides, where getting out helped.
- **multiasset (4/19):** EEM, TLT, IEF, LQD. Emerging markets plus the three
  bond funds, which it got out of before the 2022 rate shock. Holding TLT
  lost −39.3%; the strategy lost −4.0%.

Shallower drawdown than buy-and-hold: 21/31 and 14/19 symbols.

## By market period (equal-weight portfolio, T-bill cash)

| Period | top30_2015: strategy | top30_2015: buy-hold | multiasset: strategy | multiasset: buy-hold |
|---|---|---|---|---|
| Late-cycle bull + 2018 selloffs (29 mo) | **+20.9%**, Shp **0.88**, DD −6.4% | +29.2%, Shp 0.74, DD −11.1% | +11.4%, Shp **0.53**, DD −5.9% | +14.6%, Shp 0.47, DD −10.7% |
| COVID crash + recovery (24 mo) | +5.7%, Shp 0.28, DD −15.7% | **+27.3%, Shp 0.72**, DD −19.3% | +7.3%, Shp 0.38, DD −12.8% | **+24.1%, Shp 0.77**, DD −16.9% |
| 2022 bear market (12 mo) | **−3.9%, DD −6.4%** (Shp −0.80) | −6.1%, DD −21.3% (Shp −0.26) | **−0.2%, DD −4.9%** (Shp −0.31) | −4.7%, DD −17.2% (Shp −0.28) |
| 2023–26 bull (44 mo) | +66.3%, Shp **1.23**, DD −4.8% | +85.5%, Shp 1.18, DD −7.6% | +30.0%, Shp 0.44, DD −4.8% | +35.0%, Shp 0.43, DD −8.6% |

**About the 2022 row.** The automated per-window check scored 2022 as a loss
on Sharpe. That's an artifact. When both sides lose money, Sharpe divides a
negative mean by volatility, so the *calmer* loser gets the *more negative*
number. In 2022 the strategy lost less money and had a third to a quarter of
the drawdown. By any sensible standard it won that year.

Read correctly, each fair universe comes out the same way:

- **Late-cycle 2017–19:** the strategy wins on risk.
- **2022:** the strategy wins.
- **2023–26:** a draw.
- **COVID:** the strategy loses clearly. It's the textbook trend-following
  failure in a V-shaped crash: it got out after the drop, missed the rebound,
  and re-entered late.

## Promotion gate (from `STRATEGY_LIBRARY.md`)

| Check | watchlist | top30_2015 | multiasset |
|---|---|---|---|
| Beats its own baseline (timing above the always-long base rate) | FAIL | FAIL (54.5% vs 54.7%) | FAIL (56.8% vs 56.1%, CI overlaps) |
| Beats buy-and-hold on Sharpe, net of costs (portfolio, T-bill cash) | FAIL (0.84 vs 1.15) | FAIL, narrowly (0.66 vs 0.68) | FAIL, narrowly (0.35 vs 0.40) |
| Holds across ≥3 regimes | FAIL | 3 of 4 (with 2022 read correctly) | 3 of 4 (with 2022 read correctly) |

**Not promoted.** The live gate is unchanged. The first two checks fail in
every universe, and they're the ones that say whether momentum improves on
just holding.

## What this means

1. **As an entry signal, momentum adds nothing here.** It has no timing edge
   in any universe, and it gives up about half the return.
2. **The first watchlist test overstated how bad it is.** On a fair universe
   with fair cash, momentum is roughly risk-equivalent to holding, not
   clearly worse. The watchlist of today's mega-cap winners was a test the
   strategy was almost bound to fail.
3. **Drawdown reduction is the robust finding.** The portfolio drawdown was
   about 30% smaller in all three universes, with shallower drawdowns on
   most individual symbols, and the strategy clearly won the 2022 bear
   market. That's consistent with the literature's "crisis alpha"
   description of trend-following.
4. **What this does not show.** It doesn't refute the momentum literature.
   The published edge is long-*short* on diversified futures with
   volatility-scaled sizing. This test was long-only, equal-weight, and
   unleveraged, which is a meaningfully weaker version.

## Next

- **Leave momentum out of the live gate.** Keep the code: it's tested and
  reusable.
- **A specific hypothesis worth testing later is a market risk-off overlay.**
  The idea: keep the normal strategy, but go to cash when a *broad* trend
  signal (SPY's, or a multi-asset basket's) turns negative. The goal would
  be drawdown control, judged on drawdown and Sharpe rather than raw return.
  This is a new hypothesis with its own test; nothing above licenses it on
  its own.
- **Use all three universes for every future strategy.** Strategy #2
  (cross-sectional momentum) needs a broad universe even more, because it
  ranks symbols against each other, so `top30_2015` and `multiasset` are its
  natural test beds.
