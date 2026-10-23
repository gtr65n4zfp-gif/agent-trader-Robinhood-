# Market risk-off switch ("safety switch"): backtest results

This is the follow-up hypothesis from `agents/MOMENTUM_RESULTS.md`.
Momentum's one robust property was smaller drawdowns, so this tests that
property directly as a **market-wide seatbelt**: hold a basket, but step the
whole thing aside into cash when the overall market's trend turns down.

**Verdict: the most promising result so far, but not wired into the live
system yet.**

- **Over 24 years of SPY (2002–2026), the switch clearly passes.** It
  roughly cut the worst drawdown from −52% to −21% and raised the Sharpe from
  0.50 to 0.64. It did so mainly by sitting out most of the 2008 crisis.
- **Over the last 9 years alone, the main version fails in every universe.**
  That window has no 2008-style slow bear market, and in 2022 the switch got
  out too late.
- **Every test passed the same way.** Each pass comes from the long, slow
  bear markets (2002 and 2008). The evidence is real but rests on very few
  events, so promoting it is a decision for the owner (see "Decision").

## Pre-registered design (fixed before any result was seen)

- **Signal:** SPY's 12-1 time-series momentum, exactly the signal in
  `strategies/time_series_momentum.py`, on monthly bars and read from SPY's
  history up to that month only.
- **Two variants, no more:**
  - **`bearish_only` (PRIMARY):** step aside only when SPY's trend is clearly
    down.
  - **`not_bullish` (stricter sensitivity check):** step aside whenever the
    trend isn't clearly up.
- **What it protects:** a fixed equal-weight basket from
  `backtest/universes.py`, rebalanced monthly. The benchmark is that same
  basket held all the time.
- **Cash** earns an approximate 3-month T-bill rate, and Sharpe is excess
  return over it. Every switch pays 5 bps on the whole portfolio.
- **PASS = a smaller max drawdown AND a Sharpe at least as good,** over the
  full period, per universe. Windows where both sides lose money are judged
  on return and drawdown, not Sharpe (see `STRATEGY_LIBRARY.md`).
- **Code:** `backtest/risk_switch_backtest.py`, run as
  `python -m backtest.risk_switch_backtest <universe> <raw files...>`.
  The self-test (`--self-test`) proves there's no lookahead, that the switch
  steps aside in a synthetic bear market, and that the benchmark is
  unaffected.

## Test 1: the last 9 years (decisions Aug 2017 – Aug 2026), three universes

These use the same data as the momentum tests.

| Universe | Variant | Switch return | Switch Sharpe | Switch max DD | Hold return | Hold Sharpe | Hold max DD | Invested | PASS |
|---|---|---|---|---|---|---|---|---|---|
| watchlist  | bearish_only | +679.0% | 1.10 | −25.5% | +882.3% | 1.15 | −30.7% | 89% | **no** |
| watchlist  | not_bullish  | +546.0% | 1.04 | −21.1% | +882.3% | 1.15 | −30.7% | 81% | **no** |
| top30_2015 | bearish_only | +147.7% | 0.61 | −19.7% | +186.3% | 0.68 | −21.8% | 89% | **no** |
| top30_2015 | not_bullish  | +161.1% | 0.69 | −19.7% | +186.3% | 0.68 | −21.8% | 81% | yes, narrowly |
| multiasset | bearish_only | +68.7%  | 0.35 | −17.4% | +82.9%  | 0.40 | −18.1% | 89% | **no** |
| multiasset | not_bullish  | +82.1%  | 0.45 | −17.4% | +82.9%  | 0.40 | −18.1% | 81% | yes, narrowly |

**Why the primary version fails here: it got 2022 backwards.** The 12-month
signal only turned bearish at the end of **June 2022**, after most of the
fall. The switch then sat in cash through the recovery, missing +10% to +17%
in the stock baskets. It lost *more* than holding in 2022 in every universe
(top30_2015: −13.4% vs −6.1%).

The stricter variant's narrow passes (Sharpe 0.69 vs 0.68) are within noise.
They lean heavily on a single well-timed month out (Dec 2018). Since it is
the secondary, pre-registered variant, choosing it after seeing these
results would be cherry-picking, so it isn't treated as a win.

## Test 2: 24 years of the US market (decisions Apr 2002 – Aug 2026), SPY

A crash-protection tool can't be judged on a window with only one bear
market, so the same rules were run unchanged on SPY's full Robinhood
history. That adds the 2008 crisis and the tail of the dot-com bust. Most of
2002–2016 had never been tested, so this is largely out-of-sample.

| Variant | Switch return | Switch Sharpe | Switch max DD | Hold return | Hold Sharpe | Hold max DD | Invested | Switches | PASS |
|---|---|---|---|---|---|---|---|---|---|
| bearish_only | **+722.2%** | **0.64** | **−20.6%** | +606.7% | 0.50 | −52.2% | 83% | 15 | **YES** |
| not_bullish  | +602.2% | 0.62 | −19.9% | +606.7% | 0.50 | −52.2% | 73% | 37 | **YES** |

**Robust to dividends.** Prices exclude dividends, which slightly flatters a
strategy that spends time in cash. Crediting holders an approximate 1.9% a
year for every invested month keeps the pass:

- `bearish_only`: Sharpe 0.77 vs 0.63, max drawdown −19.8% vs −51.0%, total
  return +1,110% vs +1,024%.
- `not_bullish`: Sharpe 0.74 vs 0.63, max drawdown −19.6% vs −51.0%.

### By market period (primary variant, SPY)

| Period | Switch | Hold | Invested | What happened |
|---|---|---|---|---|
| Dot-com bust, tail (from Apr 2002) | **+1.5%** | −14.8% (DD −24.2%) | 0% | Already out when the test began; sat out the last leg down |
| 2003–07 bull | +58.6% | +68.3% | 96% | Small cost |
| **2008 financial crisis** | **−13.7% (DD −14.7%)** | **−43.5% (DD −52.2%)** | 28% | **Out from end of Mar 2008 to end of Sep 2009** |
| 2009–16 bull | +105.8% | +160.3% | 89% | Big cost: re-entered late after 2009, plus a few whipsaws |
| Late-cycle 2017–19 | +37.1% | +41.4% | 97% | Small cost |
| COVID crash + recovery | +33.4% | +39.8% | 96% | Too fast for a monthly 12-month signal; out one month (Apr 2020), right into the rebound |
| 2022 bear market | −15.2% | −9.7% | 42% | Out too late (Jun 2022), missed the recovery |
| 2023–26 bull | +85.6% | +87.6% | 93% | About even |

### Every time it stepped aside (primary variant, SPY)

| Out from → to (decision months) | Months | SPY meanwhile | Verdict |
|---|---|---|---|
| Apr 2002 → May 2003 | 14 | −9.5% | Dodged a loss |
| **Mar 2008 → Sep 2009** | **19** | **−21.5%** | **Dodged the crisis** |
| Oct 2011 | 1 | −0.4% | Wash |
| Oct 2015 | 1 | +0.4% | Wash |
| Feb → Mar 2016 | 2 | +6.6% | Missed gains |
| Jan 2019 | 1 | +3.2% | Missed gains |
| Apr 2020 | 1 | +4.8% | Missed gains |
| Jun 2022 → Mar 2023 | 10 | +10.2% | Missed gains (out too late) |

## What it means, in plain terms

- **It's crash insurance for long, slow bear markets.** It pays off big in
  2008- and 2001-style declines, which unfold over many months. Over 24
  years, that more than paid for its costs: a higher Sharpe, the worst
  drawdown cut by more than half, and even a higher total return.
- **It can't help in fast crashes.** A 12-month trend checked once a month
  is too slow for COVID (2020) and was too slow for 2022. Expect it to
  sometimes step aside right at the bottom and miss the rebound.
- **It costs something in most good years.** It lagged in every rising
  period, most of all after 2009.
- **The evidence rests on very few events.** Essentially two slow bear
  markets carry the 24-year result, and one of them is only partly covered.
  Two episodes support the idea; they can't prove it. The recent 9-year
  window, with no 2008-style event, shows exactly what it costs when no
  such crash comes.

## Data notes

- **Robinhood's real SPY data starts September 1999.** Earlier months come
  back as interpolated gap-fill and are dropped. The signal needs about 32
  months of warm-up, so decisions start in April 2002. Most of the 2000–02
  dot-com fall happened before the test window.
- **SPY's history matches well-known levels:** ~$147 at end-1999, ~$82 near
  the 2002 low, ~$153 at the 2007 peak, ~$74 near the 2009 low, ~$322 at
  end-2019.
- **The nine sector SPDRs can't be used for long-history tests.** Their
  Robinhood history has a fake ~−50% month in April 2002 (XLK $21.67 →
  $9.53 while SPY fell ~6%). That's a data discontinuity, not a real move,
  so they're excluded rather than built on. Their 2015+ data, used in the
  multiasset tests, shows no such problem.
- **T-bill rates (1999–2026) are approximate annual averages from general
  knowledge,** not fetched data. See `TBILL_ANNUAL` in
  `backtest/run_momentum_backtest.py`.

## Decision for the owner (not made here)

The switch is a **gate**, not a stock strategy, so it would sit inside
`agents/regime.py` as a market-wide condition. That module is already
designed to only *tighten*: it can block new buys and trigger exits, never
force a trade. Before arming it, three things need deciding:

1. **Whether the evidence is enough.** It passes over 24 years on the one
   long-history basket we have, fails on the recent 9 years in its primary
   form, and rests on about two slow bear markets.
2. **Which variant.** The pre-registered primary, `bearish_only`, has fewer
   switches. The stricter `not_bullish` cuts drawdowns similarly with more
   whipsaw and less return.
3. **Data plumbing.** The live gate would need ~33 months of SPY monthly
   bars from the Robinhood MCP on each pass. That's feasible with the same
   tool used here.

Even if armed, everything stays in paper mode and dry-run, behind the same
go-live gate as every other part of the system.
