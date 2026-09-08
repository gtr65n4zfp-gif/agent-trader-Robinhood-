# Kalshi auto-trading harness — design & feasibility

**The question:** *Can a bot bet on Kalshi every day and earn ~5%/year,
compounding like an S&P 500 investment, staying positive so you can pull out
whenever?*

**The honest answer:** *Not the way the question is framed.* Kalshi is not the
S&P, "bet every day" is the losing instinct, and ~5%/yr liquid already exists at
near-zero risk (T-bills). It **is** possible to profit on Kalshi, but only if you
own a **real, measurable forecasting edge that beats the fees** — and this
harness exists to find out whether you do **before** any real money is at risk.

---

## 1. Why Kalshi ≠ the S&P 500

| | S&P 500 | Kalshi |
|---|---|---|
| What you own | Productive companies | A binary claim (settles $1 or $0) |
| Expected drift | **Positive** (equity risk premium, ~10%/yr) | **Zero, minus fees** (roughly zero-sum between traders) |
| Does holding help? | Yes — time compounds real earnings | No — there's nothing to compound into |
| Edge required to profit | None (just own the index) | A genuine predictive edge, every time |

"Compound interest like the S&P" assumes a built-in upward drift. Kalshi has
none. Holding longer doesn't grow your money; it just exposes you to more
zero-EV, fee-taxed bets.

## 2. Why "bet every day" is the worst part of the plan

Kalshi's trading fee (see `fees.py`, verified against Kalshi's published
numbers):

```
fee = ceil( 0.07 × contracts × price × (1 − price) )   dollars, per order
```

- Maximised at **price = 0.50**: ~1.75¢/contract = **~3.5% of a 50¢ stake**, per trade.
- Charged **again** if you exit before settlement. Holding to resolution pays it
  once; churning pays it twice. (Our strategy holds to settlement.)

The target, 5%/yr, is ≈ **0.019%/day**. Deploy even 10% of bankroll daily at a
~3% round-trip fee and you eat ~0.3%/day of pure fee headwind — roughly
**110%/yr** you'd have to overcome before earning a cent. Frequent binary betting
maximises fee bleed and volatility drag with no drift to offset it. It is close
to the mathematically worst way to try to compound.

## 3. The uncomfortable benchmark

"5%/yr, liquid, stay positive" already describes **T-bills / a money-market
fund** (~4–5% now, essentially no risk). Kalshi adds risk without adding expected
return unless you have an edge. The harness reports the risk-free rate next to
its own results (`metrics.beats_risk_free`) so this comparison is never hidden.
And beware the strategy that *looks* always-positive (repeatedly selling 95¢
near-certainties): it wins small dozens of times, then loses 20× on one surprise
— the opposite of "just stay positive."

## 4. So how *could* it work?

Only with a real, persistent edge that clears the fee hurdle:

- **Selling overpriced tail risk** (favorite-longshot bias — longshots trade
  rich, favorites cheap). Positive EV, but a fat left tail; size tiny.
- **Market-making the spread** on liquid markets. Needs infrastructure.
- **Genuine forecasting edge** on a niche (weather, econ prints) you model better
  than the crowd.

This harness implements the first as its worked example, and — crucially —
measures how much edge you'd need for it to actually pay.

## 5. What's built (all paper / simulated)

```
kalshi/
├── config.py        # paper-mode switch (KALSHI_TRADER_LIVE), fees, sizing caps, target
├── fees.py          # Kalshi's exact fee formula + break-even-edge math (self-checking)
├── paper_broker.py  # binary-contract paper account: per-side cost, settlement, breakers
├── market_sim.py    # HONEST market generator — efficient by default (bot must lose)
├── strategy.py      # fee-aware, EV-gated, fractional-Kelly value bettor
├── backtest.py      # engine + a Forecaster whose skill is an explicit input
├── metrics.py       # net-of-fees results, win rate WITH confidence interval
├── client.py        # read-only live/snapshot market data (for local/real runs)
└── demo_backtest.py # the proof + the feasibility frontier
```

Run the proof:

```
python -m kalshi.fees           # fee formula self-check
python -m kalshi.demo_backtest  # control + honest case + feasibility frontier
```

### Design choices that keep it honest, not flattering
- **The simulator is efficient by default.** Price = true probability, so any
  profit on default settings is a *bug*, not a discovery. The control run
  confirms the bot loses on a fair market; an omniscient bot simply sits out.
- **Fees are real and always charged.** No free, perfect fills.
- **NO contracts cost `(1 − price)`, not `price`.** (An early version got this
  wrong and manufactured phantom profits — see git history. Fixed and guarded.)
- **Every figure is a median across many seeds.** A few hundred bets is mostly
  noise; one lucky seed proves nothing.
- **The forecaster's skill is an explicit input** (RMS error). Swap in your real
  model for deployment; the same fee gate applies.

## 6. The feasibility finding

From `demo_backtest.py` (median annualized return, net of fees, 40 seeds):

- **Efficient market → ~−20%/yr at every skill level.** You cannot beat a fair
  market; fees guarantee the loss.
- **Realistic region** (favorite-longshot bias ~0.05–0.10, forecast RMS ~0.08–0.12,
  i.e. a genuinely good model) → **loses or barely breaks even.**
- **5%/yr appears only** with *large, persistent* mispricing **and** a *sharp*
  model — a corner real Kalshi markets rarely occupy.

**Conclusion:** possible in principle, unlikely with realistic inputs, and
entirely contingent on an edge you have not yet demonstrated. Which is why:

## 7. Go-live gate (no real money until ALL hold)

Mirrors the equities side's discipline. Checked against a real paper track
record (ideally on real resolved markets via `client.py`), never a backtest of
the simulator:

- **≥ 100 settled paper bets** (binaries are high-variance; the win-rate CI is
  too wide below that to mean anything).
- **Positive total P&L *net of fees*** over that record.
- **Beats the risk-free rate** over the same window — clearing 0% isn't the bar;
  clearing T-bills is.
- **Win rate reported with its confidence interval** (`metrics.win_rate_ci`),
  never a bare percentage.
- **Edge holds across distinct market types**, not one lucky category or period.

Until every box is checked, real trading stays blocked by
`config.assert_paper_mode()` and the `KALSHI_TRADER_LIVE` unlock phrase.

## 8. What is deliberately NOT built

- **Live order placement.** Reading markets is harmless; risking money is not.
  It's a separate, later step behind the go-live gate.
- **A real forecasting model.** The `Forecaster` is a simulation stand-in. A real
  edge is the hard 90% of this problem, and inventing a fake one here would be
  the dishonest move.
- **Early-exit / secondary-market trading.** Doubles the fee; the default is
  hold-to-settlement. Revisit only if a spread-capture edge is demonstrated.
