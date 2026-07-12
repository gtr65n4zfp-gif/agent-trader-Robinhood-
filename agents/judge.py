"""
Judge — fourth seat of the trade council (see agents/COUNCIL_DESIGN.md).

The only seat that sees other seats' *outputs*, never their raw inputs:
it takes a Fundamentals verdict (agents.fundamentals_seat.form_verdict())
and a Technicals view (agents.technicals.build_view()) — each already
reduced to {stance, confidence, reasons} — and weighs them into a single
trade decision. It never touches SEC data or market data directly, and it
never places an order: the Risk vetoer remains the separate, final gate
at order time inside PaperBroker (execution/paper_broker.py). The Judge
only decides; PaperBroker.buy()/.sell() is what actually executes, and
that call still runs the full risk gate regardless of what the Judge said.

NO-TRADE IS THE DEFAULT — a conjunctive gate, not a vote or an average. A
buy/sell decision only fires when Fundamentals and Technicals agree on
direction AND both clear CONFIDENCE_THRESHOLD, AND (if supplied) the
regime filter (agents/regime.py) doesn't object. Any disagreement, either
seat below threshold, a neutral stance from either seat, or a non-
tradeable regime all fall through to a HOLD. Overtrading is a bigger
threat than a missed trade.

The regime filter can only TIGHTEN this gate, never loosen it — it's
checked first, and a non-tradeable regime forces HOLD outright regardless
of what Fundamentals/Technicals say. There's no path where a favorable
regime overrides an otherwise-disqualifying seat disagreement; "tradeable"
just means "no objection from price conditions," not "go."

decide() and baseline_decide() are pure functions — no PaperBroker calls,
no trade_log writes. Logging a HOLD (so "chose not to trade" is visible
in the audit trail) and logging the baseline are the calling script's
job, same as PaperBroker (not risk_vetoer.review()) is what writes veto
records to trade_log.
"""

CONFIDENCE_THRESHOLD = 0.5  # each seat must clear this AND agree on direction

# Real position sizing is the Risk vetoer's job at order time — it's the
# one with volatility/sector/drawdown awareness. This is just "how many
# shares to propose," a placeholder the caller can override.
DEFAULT_QUANTITY = 1


def decide(
    fundamentals: dict, technicals: dict, regime: dict | None = None, quantity: float = DEFAULT_QUANTITY
) -> dict:
    """
    Weigh two seat outputs into a single decision.

    fundamentals: agents.fundamentals_seat.form_verdict() output.
    technicals: agents.technicals.build_view() output.
    regime: optional agents.regime.regime_stance() output. Omit to skip
    the regime gate entirely (no objection, same as a tradeable regime).
    A non-tradeable regime forces HOLD outright — see the module
    docstring: this can only tighten the gate, never loosen it.
    All supplied seat outputs must be for the same symbol.

    Returns {action: buy/sell/hold, symbol, target_quantity, confidence,
    rationale, seat_inputs}.
    """
    if fundamentals["symbol"] != technicals["symbol"]:
        raise ValueError(
            f"seat symbol mismatch: fundamentals={fundamentals['symbol']!r} "
            f"technicals={technicals['symbol']!r}"
        )
    symbol = fundamentals["symbol"]
    if regime is not None and regime["symbol"] != symbol:
        raise ValueError(f"seat symbol mismatch: regime={regime['symbol']!r} vs {symbol!r}")

    seat_inputs = {"fundamentals": fundamentals, "technicals": technicals}
    if regime is not None:
        seat_inputs["regime"] = regime

    # Regime gate first — cheapest, most decisive block, and it must never
    # be overridden by what the other seats say.
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

    f_stance, f_conf = fundamentals["stance"], fundamentals["confidence"]
    t_stance, t_conf = technicals["stance"], technicals["confidence"]

    aligned = f_stance == t_stance and f_stance in ("bullish", "bearish")
    both_confident = f_conf >= CONFIDENCE_THRESHOLD and t_conf >= CONFIDENCE_THRESHOLD

    if not (aligned and both_confident):
        reason_bits = []
        if not aligned:
            reason_bits.append(f"fundamentals={f_stance} vs technicals={t_stance} — not aligned")
        if not both_confident:
            reason_bits.append(
                f"confidence below {CONFIDENCE_THRESHOLD}: fundamentals={f_conf}, technicals={t_conf}"
            )
        return {
            "seat": "judge",
            "action": "hold",
            "symbol": symbol,
            "target_quantity": 0,
            "confidence": round(min(f_conf, t_conf), 4),
            "rationale": "No-trade is the default: " + "; ".join(reason_bits),
            "seat_inputs": seat_inputs,
        }

    action = "buy" if f_stance == "bullish" else "sell"
    confidence = round(min(f_conf, t_conf), 4)  # weakest link, not an average

    return {
        "seat": "judge",
        "action": action,
        "symbol": symbol,
        "target_quantity": quantity,
        "confidence": confidence,
        "rationale": (
            f"Fundamentals and technicals both {f_stance} "
            f"(confidence {f_conf}/{t_conf}) — clears the {CONFIDENCE_THRESHOLD} threshold."
        ),
        "seat_inputs": seat_inputs,
    }


def baseline_decide(fundamentals: dict, technicals: dict, quantity: float = DEFAULT_QUANTITY) -> dict:
    """
    The ablation/baseline hook (see COUNCIL_DESIGN.md): what a SINGLE
    model, seeing both seats' outputs at once with no conjunctive gate,
    would decide — the stronger signal wins instead of requiring strict
    agreement. Logged alongside the real decision, never acted on, so we
    can later measure whether the multi-agent structure actually adds
    value over a plain filter or is just theater.
    """
    symbol = fundamentals["symbol"]
    f_stance, f_conf = fundamentals["stance"], fundamentals["confidence"]
    t_stance, t_conf = technicals["stance"], technicals["confidence"]

    if f_stance == t_stance and f_stance in ("bullish", "bearish"):
        stance, confidence = f_stance, round((f_conf + t_conf) / 2, 4)
    else:
        # No agreement required — unlike the real Judge, the stronger
        # (or, on a tie, fundamentals') signal simply wins. This laxness
        # is deliberate: it's exactly what the real Judge is being
        # measured against.
        stance, confidence = (f_stance, f_conf) if f_conf >= t_conf else (t_stance, t_conf)

    action = "buy" if stance == "bullish" else "sell" if stance == "bearish" else "hold"

    return {
        "seat": "judge_baseline",
        "action": action,
        "symbol": symbol,
        "target_quantity": quantity if action != "hold" else 0,
        "confidence": confidence,
        "rationale": (
            "Single-model baseline (no seat isolation, no conjunctive gate) — "
            "for comparison only, never executed."
        ),
        "seat_inputs": {"fundamentals": fundamentals, "technicals": technicals},
    }


if __name__ == "__main__":
    # Self-test with synthetic seat outputs — deterministic, no network needed.
    bullish_fundamentals = {
        "seat": "fundamentals", "symbol": "AAPL", "stance": "bullish",
        "confidence": 0.7, "reasons": ["revenue +16.6% YoY", "strong balance sheet"],
    }
    bullish_technicals = {
        "seat": "technicals", "symbol": "AAPL", "stance": "bullish",
        "confidence": 0.6, "reasons": ["price above EMA", "RSI oversold"],
    }
    bearish_technicals = {
        "seat": "technicals", "symbol": "AAPL", "stance": "bearish",
        "confidence": 0.8, "reasons": ["price below EMA", "RSI overbought"],
    }
    weak_bullish_technicals = {
        "seat": "technicals", "symbol": "AAPL", "stance": "bullish",
        "confidence": 0.2, "reasons": ["one weak signal only"],
    }

    print("Both seats bullish, both confident (should BUY):")
    print(decide(bullish_fundamentals, bullish_technicals))

    print("\nSeats disagree in direction (should HOLD):")
    print(decide(bullish_fundamentals, bearish_technicals))

    print("\nSeats agree but technicals confidence too low (should HOLD):")
    print(decide(bullish_fundamentals, weak_bullish_technicals))

    print("\nBaseline for the disagreement case — no strict gate, picks the stronger signal:")
    print(baseline_decide(bullish_fundamentals, bearish_technicals))

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

    print("\nBoth seats bullish and confident, but regime is non-tradeable "
          "(should HOLD regardless — the gate can only tighten):")
    print(decide(bullish_fundamentals, bullish_technicals, regime=non_tradeable_regime))

    print("\nSame bullish seats, tradeable regime (should BUY, same as with no regime supplied):")
    print(decide(bullish_fundamentals, bullish_technicals, regime=tradeable_regime))
