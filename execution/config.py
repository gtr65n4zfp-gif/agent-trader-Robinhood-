"""
Execution configuration and the paper-trading safety switch.

The single most important rule of this whole project lives here:
    NOTHING places a real-money order unless PAPER_MODE is explicitly turned off.

PAPER_MODE is True by default. Turning it off requires setting an environment
variable to an exact, deliberate value — you can't flip it by accident, and you
can't flip it just by editing a stray boolean somewhere.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

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

# Exit thresholds (agents/exits.py) — policy choices, not derived from
# data, same caveat as MIN_VOL_SCALAR above: reasonable starting points,
# not yet backtested against this project's own track record (that needs
# Milestone 4's history to exist first). Unlike the risk breakers, these
# apply to CLOSING a position, not opening one — per the design, exits
# are never blocked by the exposure-reducing breakers (drawdown/sector/
# daily-loss), so a position must always be closeable.
STOP_LOSS_PCT: float = 0.08     # close if price falls this far below average entry price
TAKE_PROFIT_PCT: float = 0.15   # close if price rises this far above average entry price

# Conviction-drop threshold: if a fresh Judge re-evaluation of a held
# symbol no longer supports holding it (action isn't "buy", or its
# confidence has fallen below this), close regardless of price — the
# thesis that justified opening the position no longer holds. This reuses
# agents.judge.CONFIDENCE_THRESHOLD's scale (0-1) but is a separate,
# independently-tunable number: entry and exit conviction bars don't have
# to be the same value just because they're both "confidence."
CONVICTION_DROP_THRESHOLD: float = 0.5

# Fill modeling: a real order never fills at the exact quoted price —
# slippage from the bid/ask spread, and (for some brokers) a commission.
# Applying a small, deliberate haircut here keeps paper P&L honest rather
# than assuming impossible instant, costless fills. This is a blunt,
# symmetric approximation (same bps against the trader on every fill, buy
# or sell) — not a real market-impact model, and not calibrated against
# data the way TARGET_DAILY_VOL_PCT is.
SLIPPAGE_BPS: float = 5.0    # 5 basis points = 0.05% of price, against the trader on every fill
FLAT_FEE_USD: float = 0.0    # Robinhood equities are commission-free; kept explicit rather than
                              # silently assumed, in case a future broker/asset class isn't

# Regime filter (agents/regime.py) — the "windshield": lets the council sit
# out conditions where systems tend to bleed slowly rather than lose fast
# (low-volatility, range-bound chop with no directional edge to capture).
# Rule-based only, two axes, combined into a small set of named states.
#
# Volatility band edges are multiples of TARGET_DAILY_VOL_PCT above — the
# same calibrated large-cap median used for position sizing — rather than
# new magic numbers: "low"/"high" are relative to what a typical liquid
# large-cap actually does, not an arbitrary guess.
REGIME_LOW_VOL_MULTIPLIER: float = 0.5   # ATR% below 0.5x the calibrated median = "low" volatility
REGIME_HIGH_VOL_MULTIPLIER: float = 2.0  # ATR% above 2.0x the calibrated median = "high" volatility

# Trend band: price within this % of its EMA counts as "sideways". Wider
# than agents.technicals's own _TREND_BAND_PCT (0.5%), which is tuned for
# "which way is momentum leaning right now" — this asks a coarser question,
# "is there a real trend to trade at all, or just noise," so it needs more
# room to avoid calling ordinary chop a trend. Policy choice, not derived
# from data.
REGIME_TREND_BAND_PCT: float = 0.02   # within 2% of EMA = sideways/ranging

# Enforced, not just guidance: execution/robinhood.py's get_regime_ema()
# validates that the EMA response handed to it actually reports this
# period, so the regime filter and agents.technicals's short EMA can never
# be silently fed the same reading again — they're required to be genuinely
# distinct lookbacks, the way domain isolation between them was always
# supposed to work. A longer period here means a smoother, less noisy
# trend read appropriate to classifying the regime rather than immediate
# momentum.
REGIME_EMA_LOOKBACK_DAYS: int = 20

# --- Automation (automation/run_pass.py) ------------------------------------
# The watchlist a scheduled pass evaluates every cadence. Adjustable — this
# is a starting set of liquid large-caps spanning sectors (tech, financials,
# healthcare, consumer staples, energy, industrials), not a permanent choice.
# See agents/AUTOMATION_DESIGN.md.
WATCHLIST: list[str] = ["AAPL", "MSFT", "GOOGL", "JPM", "JNJ", "NBIS", "NVDA", "TSLA"]

# Intended cadence: once per US trading day, mid-morning ET (~10:00) —
# comfortably inside regular hours so market_is_open() below reads True on
# an on-time wake. Set in the scheduled routine's own cron expression, not
# enforced here.

# US equity regular trading hours (9:30-16:00 America/New_York), weekdays
# only. Deliberately does NOT know about market holidays — no holiday
# calendar dependency (see agents/AUTOMATION_DESIGN.md). A holiday still
# reads as "open" here; MAX_QUOTE_AGE_MINUTES below is the fallback that
# catches it in practice, since a holiday's "latest" quote is from the
# prior session.
MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE = 9, 30
MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE = 16, 0


def market_is_open(now: datetime | None = None) -> bool:
    """
    The MARKET-HOURS GUARD for automation/run_pass.py: US equity regular
    trading hours on a weekday. Outside this window a pass is a logged
    no-op — quotes are stale when markets are closed, and a stale price
    must never drive a trade. now: for testing; defaults to the current
    time.
    """
    now = (now or datetime.now(ZoneInfo("America/New_York"))).astimezone(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    open_time = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
    close_time = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    return open_time <= now <= close_time


# How old a live quote's own venue timestamp can be before automation
# treats it as stale and skips that symbol for the pass — the core of the
# FAIL-SAFE rule: bad or missing data must halt that symbol, never feed
# the seats or PaperBroker. Generous relative to the once-daily cadence
# above (a normal on-time fetch is seconds old); this exists to catch
# genuinely wrong data, like a market-holiday quote from days earlier.
MAX_QUOTE_AGE_MINUTES: float = 30.0

# Arm/disarm switch for automation execution. True (shipped default):
# every pass still runs the full pipeline and logs every decision
# (action="dry_run_entry"/"dry_run_exit"), but PaperBroker.buy()/.sell()
# is never called — nothing is actually placed. False: real paper orders
# execute, still gated by every existing breaker (risk vetoer, drawdown,
# sector, daily caps — automation adds no path around any of them).
# Flip deliberately, one line, only when actually ready:
#     config.AUTOMATION_DRY_RUN = False
# Never the default; never flipped as a side effect of something else.
AUTOMATION_DRY_RUN: bool = True

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
