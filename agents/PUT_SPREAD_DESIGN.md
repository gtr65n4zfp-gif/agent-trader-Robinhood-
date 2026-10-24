# SPY bull put spread: pre-registered test design

**Status: PRE-REGISTERED.** This document and
`backtest/run_put_spread_backtest.py` were committed BEFORE any option
price for this test was fetched. Results go in
`agents/PUT_SPREAD_RESULTS.md`. Nothing below may change after
results are seen.

## Why this test

`agents/SPY_OPTIONS_VOL_EDGE_RESULTS.md` failed for a structural reason:
- its flat-vol valuation gate read the market's normal put skew as
  "edge";
- so it only ever sold at-the-money *bear call* spreads, into a rising
  market.

This test fixes both problems:
- **Direction.** It sells *bull put* spreads, *further out of the
  money*.
- **Gate.** It gates on the short put's **own** implied vol (skew
  included) instead of one VIX number.

It is a new test, not a retune of the old one.

**Account note.** The owner's Robinhood accounts are options Level 2 as
of 2026-10-24. Spreads need Level 3 (and a margin account). So this test
also reports the **Level 2 equivalent**: the same short put, cash-secured,
with no protective leg.

## Rules (all fixed in code constants)

| Rule | Value |
|---|---|
| Underlying | SPY, 1 contract per entry |
| Entry schedule | First trading day of every week, 2022-01-03 → 2026-06-08 (232 weeks), at that day's close |
| Expiration | `options_data.select_liquid_expiration(entry, 30)`: the Friday 30–40 days out, monthly preferred |
| Short put | Highest $5-multiple strike whose Black-Scholes put delta is ≤ 0.20 in size, with that day's VIX as sigma (≈4–8% below spot) |
| Long (protective) put | Short strike − $5 |
| Skew-aware gate | Back out the short put's implied vol from its own entry close. Trade only if that IV − GARCH(1,1) forecast ≥ 0.02 (2 vol points, `vol_edge_signal.MIN_EDGE_PCT`) |
| Safety switch | The armed `agents/market_switch.py` (`bearish_only`, completed months). No new entries when risk-off |
| Take profit | Close when 50% of the credit is kept |
| Stop loss | Close when the loss equals the credit (spread worth 2× what was received) |
| Otherwise | Held to expiration (last real bar, else intrinsic value at SPY's close) |
| Costs | `options_data.estimate_haircut_pct()` per leg from its entry bar (3% floor, 15% cap), applied at entry and exit |
| Tradeable check | Both legs need a real (non-interpolated) daily bar on the entry date; otherwise skip, never substitute |

**Contract lookups.** Contracts are looked up by exact
(expiration, strike). An empty lookup (an expiration on a market
holiday) is a skip.

**Data.** All prices come from Robinhood's MCP tools:
- SPY daily bars from 2019-06 (GARCH needs 504 trading days of
  warm-up);
- VIX daily bars from 2021-06;
- expired SPY option contracts and their daily bars.

## Streams

The headline is fixed in advance. All four are reported.

| Stream | Gate | Switch |
|---|---|---|
| **HEADLINE: `gated_with_switch`** | yes | yes |
| `gated_no_switch` | yes | no |
| `ungated_with_switch` | no | yes |
| `ungated_no_switch` (plain weekly put selling) | no | no |

Each stream is reported two ways:
- **as the spread** (needs Level 3);
- **as a cash-secured put** (Level 2). Its collateral is the full
  strike × 100: about $35,000–$73,000 per contract in this window.

## Pass criteria (headline spread stream)

The stream passes only if **all three** hold over the full window:

1. **Profitable:** total P&L > 0 after costs.
2. **Consistent:** ≥ 65% of months with an exit are positive. This is
   the owner's goal: steady income, not big wins.
3. **Worth the risk:** the Sharpe of monthly P&L on peak capital at risk
   is ≥ SPY buy-and-hold's Sharpe over the same months.
   - Both are excess over the T-bill rate.
   - The strategy's idle collateral is assumed to earn T-bill.

Also reported:
- average $ per week, against the owner's $50–100/week target;
- worst trade and worst month;
- max drawdown;
- P&L by year (2022 is the bear-market stress test);
- peak capital at risk.

## Known limitations, stated up front

- **Fills at the entry day's close,** with an estimated bid/ask cost.
  Robinhood has no historical quotes.
- **Settlement is simplified.** SPY options are American-style and
  physically settled. This simulation ignores early assignment and
  settles at intrinsic value.
- **P&L is realized,** counted in the month of exit. Open positions
  aren't marked to market at month end.
- **One underlying, one ~4.5-year window,** with one real bear market
  (2022) and the April 2025 crash.
