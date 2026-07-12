"""
Execution configuration and the paper-trading safety switch.

The single most important rule of this whole project lives here:
    NOTHING places a real-money order unless PAPER_MODE is explicitly turned off.

PAPER_MODE is True by default. Turning it off requires setting an environment
variable to an exact, deliberate value — you can't flip it by accident, and you
can't flip it just by editing a stray boolean somewhere.
"""

import os

# --- The switch ------------------------------------------------------------
# Real trading is only enabled when the env var is set to this exact phrase.
# Anything else (unset, empty, "true", "1", typo) keeps us in paper mode.
_LIVE_UNLOCK_PHRASE = "I_UNDERSTAND_THIS_USES_REAL_MONEY"

PAPER_MODE: bool = os.environ.get("AGENT_TRADER_LIVE") != _LIVE_UNLOCK_PHRASE


def assert_paper_mode() -> None:
    """Call this right before any order. Raises if we're NOT in paper mode,
    so live trading can never happen silently."""
    if not PAPER_MODE:
        raise RuntimeError(
            "LIVE TRADING IS ARMED. If this is not what you intended, unset the "
            "AGENT_TRADER_LIVE environment variable immediately."
        )


def mode_banner() -> str:
    return "📝 PAPER MODE (simulated)" if PAPER_MODE else "💸 LIVE MODE — REAL MONEY"


# --- Other settings --------------------------------------------------------
# Simulated starting cash for paper trading.
PAPER_STARTING_CASH: float = float(os.environ.get("PAPER_STARTING_CASH", "10000"))

# Risk guardrails (used later by the council + order checks).
MAX_POSITION_PCT: float = 0.10   # never put more than 10% of the account in one position
MAX_TRADE_USD: float = 1000.0    # hard cap on any single order's dollar size

# Volatility-based position sizing: MAX_POSITION_PCT above is a ceiling, not a
# target — a position's *effective* cap scales down for names more volatile
# than this reference. Something several times more volatile than the
# reference (meme stocks, small caps) gets a smaller slot for the same
# dollar risk. MIN_VOL_SCALAR floors how far it can shrink so a volatile
# name still gets some room rather than an effectively-zero cap.
#
# TARGET_DAILY_VOL_PCT is calibrated, not guessed: median 14-day ATR-as-%-
# of-price across 15 liquid large-caps spanning tech, financials, health
# care, consumer, energy, and industrials (AAPL, MSFT, GOOGL, JPM, V, JNJ,
# UNH, PG, KO, WMT, MCD, XOM, CVX, CAT, DIS), pulled from live Robinhood
# data on 2026-07-11. Range ran 0.90% (XOM) to 4.41% (CAT); median 2.30%,
# mean 2.43%. MIN_VOL_SCALAR (0.25) is still a policy choice, not derived
# from this data — how far we're willing to shrink a slot is a risk
# tolerance, not a market property.
TARGET_DAILY_VOL_PCT: float = 0.023
MIN_VOL_SCALAR: float = 0.25

# Portfolio drawdown circuit breaker: once paper equity has fallen this far
# from its peak, new BUYS halt account-wide until it recovers — independent
# of how good any single trade looks. Sells are never blocked by this;
# capital preservation always wins over a fixed cap.
MAX_DRAWDOWN_PCT: float = 0.15

# Sector concentration cap: MAX_POSITION_PCT limits any one symbol, but says
# nothing about five correlated names in the same sector each individually
# clearing that bar while the portfolio is still one big correlated bet.
# This caps total exposure to any single sector, independent of how it's
# split across symbols.
MAX_SECTOR_PCT: float = 0.25

# Daily circuit breakers — distinct from MAX_DRAWDOWN_PCT above, which is
# measured from the account's all-time peak and can take a long losing
# stretch to trip. These reset every UTC day and catch "several bad trades
# in one session" long before a sustained drawdown would. Like the other
# breakers, only new BUYS are blocked — exits are never rate-limited.
MAX_TRADES_PER_DAY: int = 10
MAX_DAILY_LOSS_PCT: float = 0.05

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
