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
# than this reference. A "normal" stock's ATR runs ~2% of its price per day;
# something several times that (meme stocks, small caps) gets a smaller slot
# for the same dollar risk. MIN_VOL_SCALAR floors how far it can shrink so a
# volatile name still gets some room rather than an effectively-zero cap.
TARGET_DAILY_VOL_PCT: float = 0.02
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

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
