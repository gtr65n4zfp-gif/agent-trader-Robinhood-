"""
Judge — the strategy gate of the trade council (see agents/COUNCIL_DESIGN.md).

The arbiter that turns a strategy signal into a trade decision. It takes a
Technicals view (agents.technicals.build_view()) — already reduced to
{stance, confidence, reasons} — and, optionally, the regime filter's
reading, and weighs them into a single buy/sell/hold. It never touches raw
market data directly, and it never places an order: the Risk vetoer remains
the separate, final gate at order time inside PaperBroker
(execution/paper_broker.py). The Judge only decides; PaperBroker.buy()/
.sell() is what actually executes, and that call still runs the full risk
gate regardless of what the Judge said.

NO-TRADE IS THE DEFAULT. A buy/sell only fires when the strategy signal is
directional (bullish/bearish) AND clears CONFIDENCE_THRESHOLD, AND (if
supplied) the regime filter (agents/regime.py) doesn't object. A neutral
stance, confidence below the bar, or a non-tradeable regime all fall
through to a HOLD. Overtrading is a bigger threat than a missed trade — the
edge is discipline, not any one signal's cleverness.

The regime filter can only TIGHTEN this gate, never loosen it — it's
checked first, and a non-tradeable regime forces HOLD outright regardless
of what the strategy signal says. The market risk-off switch
(agents/market_switch.py) is checked even before that, because it's
market-wide: a risk-off or unknown market forces HOLD for every symbol. There's no path where a favorable regime
overrides an otherwise-disqualifying signal; "tradeable" just means "no
objection from price conditions," not "go."

HISTORY: the gate used to be conjunctive across two domain-isolated seats
(a SEC Fundamentals seat AND Technicals had to agree). The SEC/fundamentals
track was retired when the project refocused on systematic, backtestable
strategies (see README and COUNCIL_DESIGN.md). The gate is now built to run
one validated strategy signal at a time; adding a second independent
strategy (e.g. the forecast model in strategies/) back into a conjunctive
gate is a deliberate future step, taken only once that strategy has cleared
its own backtest — not assumed here.

decide() and baseline_decide() are pure functions — no PaperBroker calls,
no trade_log writes. Logging a HOLD (so "chose not to trade" is visible in
the audit trail) and logging the baseline are the calling script's job,
same as PaperBroker (not risk_vetoer.review()) is what writes veto records.
"""

CONFIDENCE_THRESHOLD = 0.5  # the strategy signal must clear this to trade

# Real position sizing is the Risk vetoer's job at order time — it's the
# one with volatility/sector/drawdown awareness. This is just "how many
# shares to propose," a placeholder the caller can override.
DEFAULT_QUANTITY = 1


def decide(technicals: dict, regime: dict | None = None, quantity: float = DEFAULT_QUANTITY,
           market: dict | None = None) -> dict:
    """
    Weigh a strategy signal (plus an optional regime read) into a decision.

    technicals: agents.technicals.build_view() output — the strategy signal.
    regime: optional agents.regime.regime_stance() output. Omit to skip the
    regime gate entirely (no objection, same as a tradeable regime). A
    non-tradeable regime forces HOLD outright — see the module docstring:
    this can only tighten the gate, never loosen it. If supplied, it must be
    for the same symbol as the signal.
    market: optional agents.market_switch.evaluate() output. Omit to skip
    the market gate. A market that isn't tradeable (risk_off, or unknown
    because its data was unusable) forces HOLD outright, checked before
    everything else.

    Returns {action: buy/sell/hold, symbol, target_quantity, confidence,
    rationale, seat_inputs}.
    """
    symbol = technicals["symbol"]
    if regime is not None and regime["symbol"] != symbol:
        raise ValueError(f"seat symbol mismatch: regime={regime['symbol']!r} vs {symbol!r}")

    seat_inputs = {"technicals": technicals}
    if regime is not None:
        seat_inputs["regime"] = regime
    if market is not None:
        seat_inputs["market"] = market

    # Market switch first — market-wide, so it outranks any one symbol's
    # regime or signal. Like the regime gate, it can only force a HOLD.
    if market is not None and not market["tradeable"]:
        return {
            "seat": "judge",
            "action": "hold",
            "symbol": symbol,
            "target_quantity": 0,
            "confidence": 0.0,
            "rationale": f"No-trade is the default: market risk switch — {market['state']}: {market['reason']}",
            "seat_inputs": seat_inputs,
        }

    # Regime gate first — cheapest, most decisive block, and it must never
    # be overridden by what the strategy signal says.
    if regime is not None and not regime["tradeable"]:
        return {
            "seat": "judge",
            "action": "hold",
            "symbol": symbol,
            "target_quantity": 0,
            "confidence": 0.0,
            "rationale": f"No-trade is the default: regime filter — {regime['state']}: {regime['reason']}",
            "seat_inputs": seat_inputs,
        }

    stance, conf = technicals["stance"], technicals["confidence"]

    directional = stance in ("bullish", "bearish")
    confident = conf >= CONFIDENCE_THRESHOLD

    if not (directional and confident):
        reason_bits = []
        if not directional:
            reason_bits.append(f"signal {stance} — no directional edge")
        if not confident:
            reason_bits.append(f"confidence {conf} below {CONFIDENCE_THRESHOLD}")
        return {
            "seat": "judge",
            "action": "hold",
            "symbol": symbol,
            "target_quantity": 0,
            "confidence": round(conf, 4),
            "rationale": "No-trade is the default: " + "; ".join(reason_bits),
            "seat_inputs": seat_inputs,
        }

    action = "buy" if stance == "bullish" else "sell"

    return {
        "seat": "judge",
        "action": action,
        "symbol": symbol,
        "target_quantity": quantity,
        "confidence": round(conf, 4),
        "rationale": (
            f"Strategy signal {stance} (confidence {conf}) — clears the "
            f"{CONFIDENCE_THRESHOLD} threshold; regime not objecting."
        ),
        "seat_inputs": seat_inputs,
    }


def baseline_decide(technicals: dict, quantity: float = DEFAULT_QUANTITY) -> dict:
    """
    The ablation/baseline hook (see COUNCIL_DESIGN.md): what acting on the
    raw strategy signal with NO discipline would do — no regime gate and no
    confidence threshold, just follow the signal's direction whenever it has
    one. Logged alongside the real decision, never acted on, so we can later
    measure whether the regime gate + confidence bar actually add value over
    naively trading the signal.
    """
    symbol = technicals["symbol"]
    stance, conf = technicals["stance"], technicals["confidence"]

    action = "buy" if stance == "bullish" else "sell" if stance == "bearish" else "hold"

    return {
        "seat": "judge_baseline",
        "action": action,
        "symbol": symbol,
        "target_quantity": quantity if action != "hold" else 0,
        "confidence": round(conf, 4),
        "rationale": (
            "Baseline: raw strategy signal, no regime gate, no confidence "
            "threshold — for comparison only, never executed."
        ),
        "seat_inputs": {"technicals": technicals},
    }


if __name__ == "__main__":
    # Self-test with synthetic signals — deterministic, no network needed.
    bullish = {
        "seat": "technicals", "symbol": "AAPL", "stance": "bullish",
        "confidence": 0.6, "reasons": ["price above EMA", "RSI oversold"],
    }
    weak_bullish = {
        "seat": "technicals", "symbol": "AAPL", "stance": "bullish",
        "confidence": 0.2, "reasons": ["one weak signal only"],
    }
    neutral = {
        "seat": "technicals", "symbol": "AAPL", "stance": "neutral",
        "confidence": 0.0, "reasons": ["signals disagree"],
    }

    print("Signal bullish and confident (should BUY):")
    print(decide(bullish))

    print("\nSignal bullish but confidence too low (should HOLD):")
    print(decide(weak_bullish))

    print("\nSignal neutral (should HOLD):")
    print(decide(neutral))

    print("\nBaseline for the weak signal — no threshold, follows direction anyway:")
    print(baseline_decide(weak_bullish))

    tradeable_regime = {
        "seat": "regime", "symbol": "AAPL", "state": "trending",
        "volatility": "normal", "trend": "up", "tradeable": True,
        "reason": "normal volatility, clear up trend",
    }
    non_tradeable_regime = {
        "seat": "regime", "symbol": "AAPL", "state": "low_vol_ranging",
        "volatility": "low", "trend": "sideways", "tradeable": False,
        "reason": "low volatility, no directional edge — sitting out",
    }

    print("\nSignal bullish and confident, but regime non-tradeable "
          "(should HOLD regardless — the gate can only tighten):")
    print(decide(bullish, regime=non_tradeable_regime))

    print("\nSame bullish signal, tradeable regime (should BUY, same as no regime supplied):")
    print(decide(bullish, regime=tradeable_regime))

    risk_on = {"seat": "market_switch", "symbol": "SPY", "state": "risk_on", "tradeable": True,
               "reason": "SPY trend bullish -- no objection"}
    risk_off = {"seat": "market_switch", "symbol": "SPY", "state": "risk_off", "tradeable": False,
                "reason": "SPY trend bearish -- RISK-OFF"}
    unknown = {"seat": "market_switch", "symbol": "SPY", "state": "unknown", "tradeable": False,
               "reason": "market data unusable"}
    assert decide(bullish, regime=tradeable_regime, market=risk_on)["action"] == "buy"
    for m in (risk_off, unknown):
        d = decide(bullish, regime=tradeable_regime, market=m)
        assert d["action"] == "hold" and "market risk switch" in d["rationale"], d
    print("\nMarket switch: risk_on -> BUY allowed; risk_off and unknown -> HOLD despite a "
          "confident signal in a tradeable regime -- PASS")
