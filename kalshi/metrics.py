"""
Backtest metrics — reported the way the repo's go-live gate demands: never a bare
win rate, always with its confidence interval, always net of fees.
"""

import math
from dataclasses import dataclass

from . import config


@dataclass
class BacktestResult:
    days: int
    bets_placed: int
    bets_settled: int
    wins: int
    starting_bankroll: float
    ending_bankroll: float
    total_fees: float
    max_drawdown: float
    equity_curve: list[float]

    # --- derived, all net of fees -----------------------------------------
    @property
    def total_return(self) -> float:
        if self.starting_bankroll <= 0:
            return 0.0
        return self.ending_bankroll / self.starting_bankroll - 1.0

    @property
    def annualized_return(self) -> float:
        """Compound the realized per-day growth out to 365 days. Honest about
        direction: a losing backtest annualizes to a (large) negative number,
        not a hidden one."""
        if self.days <= 0 or self.starting_bankroll <= 0 or self.ending_bankroll <= 0:
            return -1.0 if self.ending_bankroll <= 0 else 0.0
        daily_growth = (self.ending_bankroll / self.starting_bankroll) ** (1.0 / self.days)
        return daily_growth ** 365 - 1.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.bets_settled if self.bets_settled else 0.0

    def win_rate_ci(self, z: float = 1.96) -> tuple[float, float]:
        """Wilson score interval for the win rate — the honest one for small n,
        unlike the naive normal approximation."""
        n = self.bets_settled
        if n == 0:
            return (0.0, 0.0)
        p = self.win_rate
        denom = 1 + z * z / n
        center = (p + z * z / (2 * n)) / denom
        half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
        return (max(0.0, center - half), min(1.0, center + half))

    @property
    def fee_drag_pct(self) -> float:
        """Fees paid as a percentage of starting bankroll — the headwind."""
        if self.starting_bankroll <= 0:
            return 0.0
        return self.total_fees / self.starting_bankroll * 100.0

    def beats_risk_free(self) -> bool:
        return self.annualized_return > config.RISK_FREE_ANNUAL

    def meets_target(self) -> bool:
        return self.annualized_return >= config.TARGET_ANNUAL_RETURN

    def report(self) -> str:
        lo, hi = self.win_rate_ci()
        lines = [
            f"  days simulated .......... {self.days}",
            f"  bets placed ............. {self.bets_placed}",
            f"  bets settled ............ {self.bets_settled}",
            f"  win rate ................ {self.win_rate*100:.1f}%  "
            f"(95% CI {lo*100:.1f}%–{hi*100:.1f}%)",
            f"  starting bankroll ....... ${self.starting_bankroll:,.2f}",
            f"  ending bankroll ......... ${self.ending_bankroll:,.2f}",
            f"  total return ............ {self.total_return*100:+.2f}%",
            f"  annualized return ....... {self.annualized_return*100:+.2f}%",
            f"  fees paid ............... ${self.total_fees:,.2f} "
            f"({self.fee_drag_pct:.2f}% of start)",
            f"  max drawdown ............ {self.max_drawdown*100:.2f}%",
            f"  target ({config.TARGET_ANNUAL_RETURN*100:.0f}%/yr) met ...... "
            f"{'YES' if self.meets_target() else 'no'}",
            f"  beats risk-free ({config.RISK_FREE_ANNUAL*100:.1f}%) .. "
            f"{'YES' if self.beats_risk_free() else 'no'}",
        ]
        return "\n".join(lines)
