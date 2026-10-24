# SPY bull put spread: results

The rules were pre-registered in `agents/PUT_SPREAD_DESIGN.md` and committed (be3ba20) before any option
price was fetched. Code: `backtest/run_put_spread_backtest.py` (`plan` / `legs` / `run`). Full output:
`logs/backtests/put_spread_results.json`.

**Verdict: FAIL. Do not promote, and not a reason to apply for options Level 3.**

The pre-registered headline lost money:
- 137 spreads, 26% won, −$7,849.
- Positive in 7 of 44 months.
- It failed all three pass criteria.

A post-hoc check with realistic trading costs still loses in every version: −$2,000 to −$2,500, positive in
only about 40% of months. The Level 2 version (cash-secured puts) makes a little money with realistic costs.
But it needs about $256,000 of cash tied up to earn about $39 a week, while simply holding SPY returned 57%
over the same window.

## Data

All data is real Robinhood data:
- **Entries:** 232 weekly entries, 2022-01-03 → 2026-06-08. This covers the whole 2022 bear market
  (SPY −25% at the worst point) and the April 2025 crash.
- **Contracts:** 440 looked up by exact strike and expiration.
  - 420 exist, and each one's daily bars were fetched.
  - Each contract's OCC symbol was checked against the strike and expiration requested.
  - The 20 that don't exist all expire on market holidays: Good Fridays 2022–26, 2025-07-04, 2026-06-19
    and 2026-07-03. Those entries are skipped, never substituted.
- **Simulated:** 214 entries. 11 were skipped (holiday expiration) and 7 had no credit left after costs.
- **SPY buy-and-hold, same months:** +57.2%, Sharpe 0.456, max drawdown −24.8%.

## Pre-registered results (the official answer)

| Stream | Trades | Win rate | Total P&L | Per week | Positive months | Sharpe | PASS |
|---|---|---|---|---|---|---|---|
| **HEADLINE: gated + safety switch** | 137 | 26% | **−$7,849** | −$33.83 | 7 / 44 | −3.25 | **no** |
| gated, no switch | 171 | 30% | −$9,257 | −$39.90 | 8 / 53 | −3.93 | no |
| ungated + switch | 172 | 30% | −$8,659 | −$37.32 | 7 / 45 | −3.43 | no |
| ungated, no switch (plain weekly put selling) | 214 | 35% | −$10,005 | −$43.12 | 8 / 54 | −4.05 | no |

**Every year lost money.** Headline by year:

| 2022 | 2023 | 2024 | 2025 | 2026 (to June) |
|---|---|---|---|---|
| −$2,449 | −$737 | −$1,508 | −$1,981 | −$1,174 |

**Neither filter helped.** The skew-aware gate passed 171 of 214 entries. The short put's own implied vol
(median 18.5%) almost always beat the GARCH forecast (median 14.2%). The safety switch kept out of 43 weeks,
mostly in 2022. Each filter removed some losers and some winners, and neither changed the outcome.

### Why it lost so badly: the cost model, mostly

The project's shared cost estimate, `options_data.estimate_haircut_pct()`, charges a percent of each leg's
price, based on the day's price range (3% floor, 15% cap).

**It's far too expensive for SPY spreads.**
- The median per-leg charge was 8.4%.
- That consumed **40% of the typical credit**: $55 at mid became $33 after costs.
- Real SPY options trade a cent or two off mid.

**It also distorts the stop-loss.**
- The stop compares the cost-reduced credit to the spread's mid price.
- So a new position starts already about two-thirds of the way to its stop.
- The median trade lasted 3 days, and 113 of 214 ended within 3 days.

This was a pre-registered choice, reused unchanged from the earlier options tests, so the official result
stands. But it means the headline mostly measures the cost assumption.

## Post-hoc cost check (chosen after seeing the result; never a headline)

Same entries and rules, with a realistic fill: **$0.02 off mid per leg, each way.**

| Stream | Spread trades | Spread win rate | Spread total | Per week | Positive months |
|---|---|---|---|---|---|
| gated + switch | 143 | 59% | −$2,332 | −$10.05 | 17 / 44 |
| gated, no switch | 177 | 62% | −$2,070 | −$8.92 | 22 / 53 |
| ungated + switch | 179 | 61% | −$2,456 | −$10.59 | 19 / 45 |
| ungated, no switch | 221 | 64% | −$2,007 | −$8.65 | 25 / 54 |

**Still a loss, and it's structural.** With these exits, the average win is about $30 (half the credit) and
the average loss about $80. Breaking even needs roughly 73% winners; the spreads won 59–64%. A $5-wide
spread about 5% below SPY collects too little for the losses it takes when SPY drops quickly. That happened
in 2022, in April 2025 and in several sharp dips.

## The Level 2 version: cash-secured puts

The owner's accounts are options Level 2, which can't hold spreads. Level 2 can sell the same put
**cash-secured**, with no protective leg, but that means holding the full strike in cash: about $35,000–
$73,000 per contract in this window. Weekly entries overlap, so the ladder needs much more.

| | Trades | Win rate | Total P&L | Per week | Cash needed (peak) | Return on that cash, 4.5 yrs | Worst trade | Worst month | Positive months |
|---|---|---|---|---|---|---|---|---|---|
| Pre-registered costs, headline | 137 | 69% | −$3,042 | −$13.11 | $189,000 | −1.6% | −$945 | −$1,929 | 26 / 44 |
| Pre-registered costs, ungated, no switch | 214 | 75% | +$1,874 | +$8.08 | $196,000 | +1.0% | −$945 | −$1,929 | 33 / 54 |
| *Post-hoc* realistic costs, headline | 143 | 72% | +$2,334 | +$10.06 | $256,500 | +0.9% | −$909 | −$1,710 | 27 / 44 |
| *Post-hoc* realistic costs, ungated, no switch | 221 | 77% | +$9,147 | +$39.43 | $256,500 | +3.6% | −$909 | −$1,710 | 37 / 54 |

The best case is a post-hoc cherry-pick. Even that version:
- **earned $39 a week on $256,000 of cash**, about 0.8% a year above T-bills;
- would have made about $146,000 over the same window in SPY instead.

It isn't a path to $50–100 a week for a small account.

## Plain read

- **Selling SPY put spreads on a schedule didn't make money here.** That held under the official cost model
  and under realistic costs, in all four versions, and in nearly every year.
- **Level 3 isn't worth applying for on this evidence.** The trade it unlocks lost money in this test.
- **The Level 2 alternative is out of reach.** Cash-secured SPY puts need tens of thousands of dollars per
  contract for a tiny return.

## Known limitations

- **Fills** are at the entry day's close plus a cost estimate. There is no historical bid/ask.
- **Settlement is simplified.** SPY options are American-style and physically settled; this simulation
  ignores early assignment and settles at intrinsic value.
- **P&L is realized,** booked in the month of exit, with no month-end mark-to-market.
- **Sample:** one underlying, one ~4.5-year window, one strike rule ($5 wide, 0.20 delta) and one exit rule.
  Other widths, deltas and exits weren't tried. Trying many would be data mining on the same window.
- **Shared cost model (affects the earlier options tests too).** The %-of-price haircut overstates costs on
  SPY multi-leg trades. The vol-edge result (`agents/SPY_OPTIONS_VOL_EDGE_RESULTS.md`) still stands: its
  trades lost on direction under every exit rule. Any future options test should use a fixed per-contract
  cost instead.
