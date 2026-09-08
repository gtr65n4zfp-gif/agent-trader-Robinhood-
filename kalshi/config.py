"""
Kalshi harness configuration + the paper-mode safety switch.

Reuses the same live-unlock discipline as execution/config.py: nothing places a
real Kalshi order unless an env var is set to an exact, deliberate phrase. The
switch is intentionally separate from the equities one — arming stock trading
must not silently arm event-contract trading too.
"""

import os

# --- The switch ------------------------------------------------------------
# Real Kalshi trading is only enabled when this env var equals this exact phrase.
_LIVE_UNLOCK_PHRASE = "I_UNDERSTAND_THIS_USES_REAL_MONEY_KALSHI"

PAPER_MODE: bool = os.environ.get("KALSHI_TRADER_LIVE") != _LIVE_UNLOCK_PHRASE


def assert_paper_mode() -> None:
    """Call right before any order. Raises unless we're in paper mode, so live
    Kalshi trading can never happen silently."""
    if not PAPER_MODE:
        raise RuntimeError(
            "LIVE KALSHI TRADING IS ARMED. Unset KALSHI_TRADER_LIVE immediately "
            "if this was not deliberate."
        )


def mode_banner() -> str:
    return "📝 KALSHI PAPER MODE (simulated)" if PAPER_MODE else "💸 KALSHI LIVE — REAL MONEY"


# --- Bankroll --------------------------------------------------------------
PAPER_STARTING_CASH: float = float(os.environ.get("KALSHI_PAPER_CASH", "10000"))


# --- Fees ------------------------------------------------------------------
# Kalshi's published general trading fee, charged on the *notional risk* of a
# trade and rounded UP to the next whole cent, PER ORDER:
#
#     fee = ceil( FEE_COEFF * contracts * price * (1 - price) )   [dollars]
#
# where price is the per-contract price in dollars (0..1). The coefficient is
# 0.07 for most markets; a handful (e.g. S&P/Nasdaq range markets) use 0.035.
# We default to 0.07 because it is the conservative (worse-for-us) case — a bot
# that assumes the cheaper schedule and meets the lower one is over-optimistic;
# one that clears the 0.07 hurdle clears both.
#
# Two facts that dominate the whole strategy:
#   1. The fee is maximized at price = 0.50 (coin-flip markets are the most
#      expensive to trade, ~1.75c per contract = 3.5% of a 50c stake).
#   2. It is charged again if you exit before settlement. Holding to settlement
#      (0.00/1.00, no settlement fee) pays the fee ONCE; round-tripping pays
#      it TWICE. Our default strategy holds to settlement for this reason.
FEE_COEFF: float = float(os.environ.get("KALSHI_FEE_COEFF", "0.07"))


# --- Sizing / risk ---------------------------------------------------------
# Fraction of *current bankroll* risked on a single market, as a hard ceiling.
# Binary contracts have fat left tails (a "sure thing" at 95c loses the whole
# stake when the 5% happens), so this stays small. Kelly-style sizing in the
# strategy scales DOWN from this cap; it never exceeds it.
MAX_STAKE_FRACTION: float = float(os.environ.get("KALSHI_MAX_STAKE_FRAC", "0.02"))

# Fraction of the *full* Kelly bet we actually take. Full Kelly is famously
# over-aggressive and assumes your edge estimate is exactly right — which, with
# a modeled probability, it never is. Quarter-Kelly is the standard humility
# haircut.
KELLY_FRACTION: float = float(os.environ.get("KALSHI_KELLY_FRACTION", "0.25"))

# Minimum modeled edge, in cents of price, over and above the break-even fee
# hurdle, before we bet at all. This is the single most important knob: set it
# too low and fees eat every marginal trade. See break_even.py.
MIN_EDGE_MARGIN_CENTS: float = float(os.environ.get("KALSHI_MIN_EDGE_CENTS", "3.0"))

# Daily loss circuit breaker: once the bankroll is down this fraction on the
# day, stop opening new positions until the next day. Settlement of already-open
# positions is never blocked.
MAX_DAILY_LOSS_FRACTION: float = 0.05

# Account-wide drawdown breaker: new bets halt once bankroll is this far below
# its all-time peak. Preserving capital always beats chasing the target.
MAX_DRAWDOWN_FRACTION: float = 0.20


# --- The target (what the user asked for) ----------------------------------
# 5% per year, "comparable to the S&P," pull-out-whenever. Kept here as a named
# constant so break_even.py and the metrics can measure honestly against it
# instead of a vibe. NOTE: ~5% annual, liquid, low-risk is also roughly what
# T-bills / a money-market fund pay right now — the harness reports that
# benchmark alongside its own results on purpose.
TARGET_ANNUAL_RETURN: float = 0.05
RISK_FREE_ANNUAL: float = float(os.environ.get("KALSHI_RISK_FREE", "0.045"))


# --- Paths -----------------------------------------------------------------
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
