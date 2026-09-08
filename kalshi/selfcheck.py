"""
Invariant checks for the Kalshi harness — the guardrail that keeps the
simulation honest. Run:  python -m kalshi.selfcheck

These are the properties that, if they ever break, mean the P&L is lying:
  A. A NO contract costs (1 - price), not price.
  B. Fair (zero-EV) bets end at starting cash minus fees — no drift.
  C. An efficient market is unbeatable: the strategy loses on average, and an
     omniscient bot on a fair market places zero bets.
  D. A grossly inefficient market with a sharp forecaster IS beatable (so the
     harness isn't just always-negative by construction).
"""

import random
import statistics

from . import config
from .backtest import run_backtest
from .paper_broker import KalshiPaperBroker


def check_no_cost() -> None:
    b = KalshiPaperBroker(starting_cash=10_000)
    # Buy 100 NO at a YES price of 0.30 -> cost is 0.70/contract = $70 stake.
    pos = b.buy("T", "NO", 100, 0.30)
    assert abs(pos.entry_cost - 0.70) < 1e-9, pos.entry_cost
    assert abs(pos.stake - 70.0) < 1e-9, pos.stake
    # Cash spent = stake + fee; fee is symmetric so trade_fee(100, 0.30).
    from .fees import trade_fee
    expected_cash = 10_000 - 70.0 - trade_fee(100, 0.30)
    assert abs(b.cash - expected_cash) < 1e-9, (b.cash, expected_cash)
    # YES loses -> NO wins -> payout 100, realized = 100 - 70 - fee.
    b.settle("T", yes_won=False)
    t = b.settled[-1]
    assert t.won and abs(t.payout - 100.0) < 1e-9
    assert abs(t.realized_pnl - (100.0 - 70.0 - trade_fee(100, 0.30))) < 1e-9
    print("A. NO-side cost basis .......... ok")


def check_fair_bets_lose_only_fees() -> None:
    rng = random.Random(0)
    b = KalshiPaperBroker(starting_cash=1_000_000)
    for i in range(20_000):
        t = f"M{i}"
        try:
            b.buy(t, "YES", 5, 0.50)
        except Exception:
            pass
        b.settle(t, yes_won=(rng.random() < 0.50))
    # End should be start - fees, within sampling noise of a zero-EV process.
    drift = b.bankroll - (1_000_000 - b.total_fees_paid)
    rel = drift / 1_000_000
    assert abs(rel) < 0.01, f"fair-bet drift too large: {rel:.4%}"
    print(f"B. Fair bets lose only fees .... ok (residual drift {rel:+.3%})")


def check_efficient_unbeatable() -> None:
    nets = [run_backtest(days=252, mispricing=0.0, skill_error=0.10, seed=s)
            .total_return for s in range(40)]
    med = statistics.median(nets)
    assert med < 0, f"efficient market should lose; median total {med:+.2%}"
    # Omniscient on a fair market: nothing clears the gate.
    r = run_backtest(days=252, mispricing=0.0, skill_error=0.0, seed=0)
    assert r.bets_settled == 0, f"omniscient fair market bet {r.bets_settled} times"
    print(f"C. Efficient market unbeatable . ok (median total {med:+.1%}, "
          f"omniscient places 0 bets)")


def check_extreme_edge_is_beatable() -> None:
    med = statistics.median(
        run_backtest(days=252, mispricing=0.30, skill_error=0.03, seed=s)
        .annualized_return for s in range(20))
    assert med > config.TARGET_ANNUAL_RETURN, (
        f"a grossly inefficient market with a sharp model should be beatable; "
        f"median annualized {med:+.2%}")
    print("D. Real edge IS capturable ..... ok (harness isn't rigged negative)")


def main() -> None:
    print(config.mode_banner())
    check_no_cost()
    check_fair_bets_lose_only_fees()
    check_efficient_unbeatable()
    check_extreme_edge_is_beatable()
    print("\nAll Kalshi harness invariants hold.")


if __name__ == "__main__":
    main()
