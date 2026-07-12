"""
Technicals seat — third seat of the trade council (see agents/COUNCIL_DESIGN.md).

Domain-isolated: only ever touches parsed market data from
execution/robinhood.py (price, ATR, RSI, EMA). Never sees SEC filings or
financials — that's the Fundamentals seat's job. Opines on short-term
setup and momentum, not long-term thesis.

Unlike agents/fundamentals_seat.py, this seat's stance is rule-based, not
agent-judged: classic technical signals (price vs. its trend line, RSI
overbought/oversold) reduce cleanly to fixed thresholds, the same way the
risk vetoer's checks do — there's no "is this a good business"-style
judgment call here, just "is price above its EMA" and "is momentum
stretched." Keeping it mechanical also keeps it fully reproducible and
auditable, same reasoning as agents/risk_vetoer.py.

build_view() takes already-parsed inputs (from execution/robinhood.py's
get_quote/get_atr_pct/get_rsi/get_ema — this module does not call any MCP
tool or fetch anything itself) and reduces them to a compact structured
view: {stance, confidence, reasons}.
"""

# Thresholds below are textbook technical-analysis levels, not backtested
# against this project's own data — unlike execution/config.py's
# TARGET_DAILY_VOL_PCT, which is calibrated against real market data (see
# that file's comment). Worth calibrating properly once there's a real
# paper track record to check these against (Milestone 4).
_RSI_OVERBOUGHT = 70.0
_RSI_OVERSOLD = 30.0
_TREND_BAND_PCT = 0.005  # within 0.5% of the EMA counts as "no clear trend"

# How many independent signals this seat can currently vote with — used to
# scale confidence so a single signal firing alone can never reach full
# confidence the way two agreeing signals can.
_MAX_SIGNALS = 2


def build_view(
    symbol: str,
    price: float,
    ema: float | None = None,
    rsi: float | None = None,
    atr_pct: float | None = None,
) -> dict:
    """
    Reduce price/EMA/RSI/ATR into a structured technical view.

    symbol, price: required. ema, rsi: each optional — a missing input
    just means one fewer signal contributes to the stance, not a default
    to bullish or bearish. If neither is available, this returns a
    neutral, zero-confidence view rather than guessing from price alone.
    atr_pct: optional, included in `reasons` as volatility context only —
    it has no direction, so it never casts a bullish/bearish vote itself.
    """
    symbol = symbol.upper()
    reasons: list[str] = []
    bullish_votes = 0
    bearish_votes = 0
    total_votes = 0

    if ema is not None and ema > 0:
        total_votes += 1
        pct_from_ema = (price - ema) / ema
        if pct_from_ema > _TREND_BAND_PCT:
            bullish_votes += 1
            reasons.append(f"price {pct_from_ema * 100:+.1f}% above its EMA — uptrend")
        elif pct_from_ema < -_TREND_BAND_PCT:
            bearish_votes += 1
            reasons.append(f"price {pct_from_ema * 100:+.1f}% below its EMA — downtrend")
        else:
            reasons.append(f"price within {_TREND_BAND_PCT * 100:.1f}% of its EMA — no clear trend")

    if rsi is not None:
        total_votes += 1
        if rsi >= _RSI_OVERBOUGHT:
            # Overbought leans toward a pullback, not "chase the momentum."
            bearish_votes += 1
            reasons.append(f"RSI {rsi:.1f} >= {_RSI_OVERBOUGHT:.0f} — overbought")
        elif rsi <= _RSI_OVERSOLD:
            bullish_votes += 1
            reasons.append(f"RSI {rsi:.1f} <= {_RSI_OVERSOLD:.0f} — oversold")
        else:
            reasons.append(f"RSI {rsi:.1f} — neutral range")

    if atr_pct is not None:
        reasons.append(f"ATR {atr_pct * 100:.2f}% of price — volatility context, not directional")

    if total_votes == 0:
        return {
            "seat": "technicals",
            "symbol": symbol,
            "stance": "neutral",
            "confidence": 0.0,
            "reasons": reasons or ["no EMA or RSI data available — nothing to base a stance on"],
        }

    if bullish_votes > bearish_votes:
        stance = "bullish"
    elif bearish_votes > bullish_votes:
        stance = "bearish"
    else:
        stance = "neutral"

    # Confidence rewards agreement, not just one signal firing: two
    # signals agreeing beats one signal alone, and a tie is explicit
    # zero-confidence rather than a coin flip.
    if stance == "neutral":
        confidence = 0.0
    else:
        agreement = abs(bullish_votes - bearish_votes) / total_votes
        coverage = total_votes / _MAX_SIGNALS
        confidence = round(agreement * coverage, 4)

    return {
        "seat": "technicals",
        "symbol": symbol,
        "stance": stance,
        "confidence": confidence,
        "reasons": reasons,
    }


if __name__ == "__main__":
    # Self-test with synthetic inputs — deterministic, no network needed.
    print("Both signals agree bullish (price above EMA, RSI oversold):")
    print(build_view("AAPL", price=210.0, ema=200.0, rsi=25.0, atr_pct=0.02))

    print("\nBoth signals agree bearish (price below EMA, RSI overbought):")
    print(build_view("AAPL", price=190.0, ema=200.0, rsi=75.0))

    print("\nSignals disagree — net neutral, zero confidence:")
    print(build_view("AAPL", price=210.0, ema=200.0, rsi=75.0))

    print("\nOnly one signal available (RSI only) — capped below full confidence:")
    print(build_view("AAPL", price=205.0, rsi=25.0))

    print("\nNo EMA or RSI at all — neutral, zero confidence, not a guess:")
    print(build_view("AAPL", price=205.0))
