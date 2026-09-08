"""
End-to-end proof + the feasibility answer.

Run:  python -m kalshi.demo_backtest

It demonstrates, in order:
  1. The fee reality (why frequent 50c betting is a fee machine).
  2. An EFFICIENT market — the bot correctly LOSES to fees (control). If this
     ever showed a profit, the harness would be lying.
  3. A REALISTICALLY mispriced market with a realistically imperfect forecaster
     — the honest "can we actually make money" case.
  4. A frontier sweep: for what combinations of market inefficiency and forecast
     skill does 5%/yr become reachable? That is the real answer to
     "is it possible?"

Every figure is a MEDIAN across many random seeds. A single backtest of a few
hundred bets is mostly noise; one lucky seed proves nothing, which is exactly
what the go-live gate (KALSHI_DESIGN.md) is built to stop us forgetting.
"""

import statistics

from . import config, fees
from .backtest import run_backtest

SEEDS = range(40)


def _median_ann(mispricing: float, skill_error: float, days: int = 252) -> float:
    return statistics.median(
        run_backtest(days=days, mispricing=mispricing, skill_error=skill_error,
                     seed=s).annualized_return * 100
        for s in SEEDS)


def _profit_fraction(mispricing: float, skill_error: float, days: int = 252):
    nets = [run_backtest(days=days, mispricing=mispricing, skill_error=skill_error,
                         seed=s).total_return for s in SEEDS]
    return sum(1 for n in nets if n > 0), len(nets), statistics.median(nets) * 100


def _rule(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def main() -> None:
    print(config.mode_banner())

    _rule("1. THE FEE REALITY (kalshi.fees)")
    print("Kalshi charges ceil(0.07 * contracts * price * (1-price)) per order.")
    print(f"{'price':>6} {'fee/contract':>13} {'fee %stake':>11} "
          f"{'break-even edge (hold)':>24}")
    for p in (0.50, 0.60, 0.75, 0.90, 0.95):
        print(f"{p:>6.2f} {'$'+format(fees.trade_fee(1,p),'.2f'):>13} "
              f"{fees.fee_as_fraction_of_stake(p)*100:>10.2f}% "
              f"{fees.break_even_edge_cents(p):>22.1f}c")
    print("\nTo net a profit you must know the true probability better than the "
          "quoted\nprice by MORE than the break-even edge, on average, after "
          "sizing and variance.")

    _rule("2. CONTROL — EFFICIENT MARKET (must lose to fees)")
    w, n, med = _profit_fraction(mispricing=0.0, skill_error=0.10)
    print("Market price == true probability (no edge exists). Good forecaster "
          "(0.10 RMS).")
    print(f"  median annualized return : {_median_ann(0.0, 0.10):+.1f}%")
    print(f"  seeds that finished green : {w}/{n}")
    print("A fair market cannot be beaten; fees guarantee a loss. A profit here "
          "= a bug.")
    print("(Omniscient on a fair market simply sits out — 0 bets, 0% — because "
          "no price\never clears the edge gate.)")

    _rule("3. HONEST CASE — REALISTIC MISPRICING + REALISTIC FORECASTER")
    print("Favorite-longshot bias of 0.08 (a few cents — about what real markets "
          "show)\nand a genuinely good model (0.10 RMS error). This is the "
          "realistic best case.")
    w, n, med = _profit_fraction(mispricing=0.08, skill_error=0.10)
    print(f"  median annualized return : {_median_ann(0.08, 0.10):+.1f}%")
    print(f"  seeds that finished green : {w}/{n}")
    print("Takeaway: with realistic inputs it hovers around break-even to a loss "
          "— the\nfees and forecast noise eat the thin edge. This is the sober "
          "answer.")

    _rule("4. FEASIBILITY FRONTIER — when is 5%/yr reachable?")
    print("Rows = market inefficiency (favorite-longshot strength; real markets "
          "~0.05-0.10).")
    print("Cols = forecast skill (RMS error; lower = sharper; 0.08 is already "
          "very good).")
    print("Cell = MEDIAN annualized return over 40 seeds, net of fees. "
          "'*' = clears 5%.\n")
    skills = [0.12, 0.10, 0.08, 0.05, 0.03]
    misps = [0.00, 0.05, 0.10, 0.20, 0.30]
    print("  misp\\skill " + "".join(f"{s:>10.2f}" for s in skills))
    for mp in misps:
        cells = []
        for se in skills:
            med = _median_ann(mp, se)
            disp = ">1e4" if abs(med) >= 1e4 else f"{med:+.1f}"
            cells.append(f"{disp:>9}" + ("*" if med >= 5 else " "))
        print(f"  {mp:>7.2f}   " + "".join(cells))

    print("\nHow to read it:")
    print(" - The whole top region (efficient / mildly mispriced markets with "
          "realistic\n   skill) is negative. 'Bet every day' lives here. It "
          "bleeds.")
    print(" - 5%/yr only appears with LARGE persistent mispricing AND a sharp "
          "model — a\n   corner of the space real Kalshi markets rarely occupy.")
    print(" - '>1e4' cells are fantasy inputs (near-omniscient forecasts of an "
          "absurdly\n   inefficient market): mathematically real, practically "
          "meaningless.")
    print("\nBottom line: it is POSSIBLE only if you actually own an edge that "
          "lands in\nthe bottom-right. Whether yours does is an empirical "
          "question the paper track\nrecord must answer before real money — see "
          "the go-live gate in KALSHI_DESIGN.md.")


if __name__ == "__main__":
    main()
