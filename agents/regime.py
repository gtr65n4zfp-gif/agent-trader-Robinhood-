"""
Regime filter (see agents/COUNCIL_DESIGN.md's "Regime / volatility filter"
section) — the windshield, not another seat with a vote. The single
biggest lesson behind this module: the edge is knowing when NOT to trade.
Systems bleed slowly in low-volatility, range-bound chop with no
directional edge to capture — this exists to recognize that condition and
force a sit-out before it costs anything.

Domain-isolated, price-data only: only ever touches parsed market data
from execution/robinhood.py (price, EMA, ATR). Never sees fundamentals or
seat opinions — same isolation reasoning as agents/technicals.py.

Rule-based only, two axes, combined into a small set of named states:
    volatility: low / normal / high, relative to config.TARGET_DAILY_VOL_PCT
    trend:      up / down / sideways, price vs. its EMA over a wider band
                than agents.technicals uses (see config.REGIME_TREND_BAND_PCT)
No HMM, no ML, no clustering — a two-threshold classifier is the whole
point; anything fancier defeats it.

CORE SAFETY PRINCIPLE — a filter can only TIGHTEN, never LOOSEN: this
module only ever produces a `tradeable` bool that agents/judge.py and
agents/exits.py may use to ADD a reason to hold or ADD a reason to exit.
Nothing here can turn a hold into a trade, relax the risk vetoer, override
an exit, or increase a position size — a tradeable=True regime result
means "no objection," never "go."

regime_stance() takes already-parsed inputs (from execution/robinhood.py's
get_quote/get_regime_ema/get_atr_pct — this module does not call any MCP
tool or fetch anything itself) and reduces them to a compact structured
view: {state, volatility, trend, tradeable, reason}. get_regime_ema() is a
genuinely distinct reading from agents.technicals's get_ema() — a longer
lookback, validated at the data layer (execution.config.REGIME_EMA_LOOKBACK_DAYS)
so the two can never be silently fed the same EMA and mechanically agree.
"""

from execution import config


def _classify_volatility(atr_pct: float) -> str:
    """low/normal/high relative to config.TARGET_DAILY_VOL_PCT — the same
    calibrated large-cap median agents.risk_vetoer uses for position sizing."""
    reference = config.TARGET_DAILY_VOL_PCT
    if atr_pct < reference * config.REGIME_LOW_VOL_MULTIPLIER:
        return "low"
    if atr_pct > reference * config.REGIME_HIGH_VOL_MULTIPLIER:
        return "high"
    return "normal"


def _classify_trend(price: float, ema: float) -> str:
    """up/down/sideways: price vs. its EMA, banded by
    config.REGIME_TREND_BAND_PCT (wider than agents.technicals's own band —
    see that constant's comment for why)."""
    pct_from_ema = (price - ema) / ema
    if pct_from_ema > config.REGIME_TREND_BAND_PCT:
        return "up"
    if pct_from_ema < -config.REGIME_TREND_BAND_PCT:
        return "down"
    return "sideways"


def regime_stance(symbol: str, price: float, ema: float, atr_pct: float) -> dict:
    """
    Classify a symbol's current regime and combine the two axes into a
    named state with a tradeable flag.

    price, ema, atr_pct: already-parsed inputs — see execution.robinhood's
    get_quote/get_regime_ema/get_atr_pct. ema MUST come from
    get_regime_ema(), not get_ema() — a genuinely distinct, longer-period
    reading from what agents.technicals uses (config.REGIME_EMA_LOOKBACK_DAYS),
    validated at the data layer so the two can't be silently swapped. This
    is what lets regime and Technicals actually disagree on trend instead
    of mechanically agreeing because they were fed the same number.

    Named states:
        low_vol_ranging  — low vol + sideways: the specific condition this
                            module exists to catch. NOT tradeable.
        ranging          — normal/high vol + sideways: still no directional
                            edge, same reasoning, less severe. NOT tradeable.
        low_vol_trend    — low vol + a real trend. Tradeable.
        trending         — normal vol + a real trend. The ideal state. Tradeable.
        volatile_trend   — high vol + a real trend. Tradeable — there's a
                            directional edge; agents.risk_vetoer's volatility-
                            scaled position cap already handles the extra risk.
    """
    symbol = symbol.upper()
    volatility = _classify_volatility(atr_pct)
    trend = _classify_trend(price, ema)

    if trend == "sideways":
        state = "low_vol_ranging" if volatility == "low" else "ranging"
        tradeable = False
        reason = (
            f"{volatility} volatility ({atr_pct * 100:.2f}%), price within "
            f"{config.REGIME_TREND_BAND_PCT * 100:.0f}% of its EMA — no "
            f"directional edge, sitting out"
        )
    else:
        state = {"low": "low_vol_trend", "normal": "trending", "high": "volatile_trend"}[volatility]
        tradeable = True
        reason = f"{volatility} volatility ({atr_pct * 100:.2f}%), clear {trend} trend"

    return {
        "seat": "regime",
        "symbol": symbol,
        "state": state,
        "volatility": volatility,
        "trend": trend,
        "tradeable": tradeable,
        "reason": reason,
    }


if __name__ == "__main__":
    # Self-test with synthetic inputs — deterministic, no network needed.
    ref = config.TARGET_DAILY_VOL_PCT

    print("Low vol + sideways -> low_vol_ranging, NOT tradeable (the named worst case):")
    print(regime_stance("KO", price=80.0, ema=79.9, atr_pct=ref * 0.3))

    print("\nNormal vol + sideways -> ranging, NOT tradeable:")
    print(regime_stance("KO", price=80.0, ema=79.9, atr_pct=ref * 1.0))

    print("\nHigh vol + sideways -> ranging, NOT tradeable:")
    print(regime_stance("KO", price=80.0, ema=79.9, atr_pct=ref * 2.5))

    print("\nLow vol + uptrend -> low_vol_trend, tradeable:")
    print(regime_stance("KO", price=84.0, ema=80.0, atr_pct=ref * 0.3))

    print("\nNormal vol + uptrend -> trending, tradeable (the ideal state):")
    print(regime_stance("NVDA", price=500.0, ema=480.0, atr_pct=ref * 1.0))

    print("\nHigh vol + downtrend -> volatile_trend, tradeable:")
    print(regime_stance("MSTR", price=90.0, ema=100.0, atr_pct=ref * 2.5))
