"""
backtest/options_engine.py — SPY-only technicals+regime decision gate,
and pure option-trade simulation over already-fetched daily bars (see
agents/OPTIONS_BACKTEST_DESIGN.md).

technicals_only_decision() is an ISOLATED exception for this backtest —
SPY's Fundamentals leg is confirmed structurally empty (ETF trusts don't
file the 10-K/10-Q reports agents.fundamentals_seat's SEC concepts come
from), so this mirrors agents.judge.decide()'s gate logic with the
Fundamentals requirement dropped. This does NOT modify judge.py, and is
never used for any symbol other than SPY in this experiment.
"""

from __future__ import annotations

from agents import judge


def technicals_only_decision(technicals: dict, regime: dict, quantity: float = 1) -> dict:
    """
    Same shape as judge.decide()'s return value, but the gate only
    requires Technicals to clear judge.CONFIDENCE_THRESHOLD — there is no
    Fundamentals leg to agree with. Regime gate is checked first, exactly
    as judge.decide() does, and can only force a HOLD, never override one
    (same "tighten, never loosen" principle as the live Judge).
    """
    symbol = technicals["symbol"]
    if regime["symbol"] != symbol:
        raise ValueError(f"seat symbol mismatch: regime={regime['symbol']!r} vs {symbol!r}")

    seat_inputs = {"technicals": technicals, "regime": regime}

    if not regime["tradeable"]:
        return {
            "seat": "judge_technicals_only", "action": "hold", "symbol": symbol,
            "target_quantity": 0, "confidence": 0.0,
            "rationale": f"No-trade is the default: regime filter — {regime['state']}: {regime['reason']}",
            "seat_inputs": seat_inputs,
        }

    t_stance, t_conf = technicals["stance"], technicals["confidence"]
    if t_stance not in ("bullish", "bearish") or t_conf < judge.CONFIDENCE_THRESHOLD:
        return {
            "seat": "judge_technicals_only", "action": "hold", "symbol": symbol,
            "target_quantity": 0, "confidence": round(t_conf, 4),
            "rationale": (
                f"No-trade is the default: technicals={t_stance}, confidence "
                f"{t_conf} below {judge.CONFIDENCE_THRESHOLD}"
            ),
            "seat_inputs": seat_inputs,
        }

    action = "buy" if t_stance == "bullish" else "sell"
    return {
        "seat": "judge_technicals_only", "action": action, "symbol": symbol,
        "target_quantity": quantity, "confidence": round(t_conf, 4),
        "rationale": (
            f"Technicals {t_stance} alone (confidence {t_conf}) — clears "
            f"{judge.CONFIDENCE_THRESHOLD}; SPY has no usable Fundamentals leg "
            f"(agents/OPTIONS_BACKTEST_DESIGN.md)."
        ),
        "seat_inputs": seat_inputs,
    }


def simulate_option_trade(
    entry_close: float,
    bars_after_entry: list[dict],
    side: str,
    strike: float,
    expiration_date: str,
    spot_at_expiration: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    haircut_pct: float,
) -> dict:
    """
    Simulate one option trade forward from entry, walking bars_after_entry
    (options_data.parse_option_bars() output, sorted chronologically,
    every bar strictly after the entry date) day by day.

    entry_close: the contract's close price on the signal/entry date.
    side: "buy" (call bought on a bullish signal) or "sell" (put bought
    on a bearish signal) — same convention as options_data.select_contract().
    spot_at_expiration: the UNDERLYING's (SPY's) closing price on
    expiration_date — used only as the intrinsic-value fallback if the
    option's own bars run out before expiration actually arrives.
    haircut_pct: round-trip cost (e.g. 0.03) applied as half against the
    trader on entry and half on exit — see config.OPTIONS_ROUNDTRIP_HAIRCUT_PCT.

    Exit is whichever triggers first: stop-loss, take-profit, or
    expiration (last available bar if it reaches expiration_date,
    otherwise intrinsic value — see agents/OPTIONS_BACKTEST_DESIGN.md's
    "Entry and exit simulation" section).

    Returns {entry_fill, exit_fill, exit_reason, exit_date, realized_pnl}.
    realized_pnl is a raw PER-SHARE premium delta (exit_fill - entry_fill),
    NOT yet multiplied by config.OPTIONS_CONTRACT_MULTIPLIER — the caller
    is responsible for applying that multiplier to get a per-contract
    dollar P&L (one contract = 100 shares of premium), same as how a real
    options quote is priced per share but settles per contract.
    """
    entry_fill = entry_close * (1 + haircut_pct / 2)

    for bar in bars_after_entry:
        change = (bar["close"] - entry_fill) / entry_fill
        if change <= -stop_loss_pct:
            exit_fill = bar["close"] * (1 - haircut_pct / 2)
            return {
                "entry_fill": round(entry_fill, 4), "exit_fill": round(exit_fill, 4),
                "exit_reason": "stop_loss", "exit_date": bar["date"],
                "realized_pnl": round(exit_fill - entry_fill, 4),
            }
        if change >= take_profit_pct:
            exit_fill = bar["close"] * (1 - haircut_pct / 2)
            return {
                "entry_fill": round(entry_fill, 4), "exit_fill": round(exit_fill, 4),
                "exit_reason": "take_profit", "exit_date": bar["date"],
                "realized_pnl": round(exit_fill - entry_fill, 4),
            }

    last_bar = bars_after_entry[-1] if bars_after_entry else None
    if last_bar is not None and last_bar["date"] >= expiration_date:
        exit_fill = last_bar["close"] * (1 - haircut_pct / 2)
        exit_reason = "expiration_last_bar"
        exit_date = last_bar["date"]
    else:
        intrinsic = (
            max(0.0, spot_at_expiration - strike) if side == "buy"
            else max(0.0, strike - spot_at_expiration)
        )
        exit_fill = intrinsic  # cash settlement at expiration, not a market fill — no haircut
        exit_reason = "expiration_intrinsic"
        exit_date = expiration_date

    return {
        "entry_fill": round(entry_fill, 4), "exit_fill": round(exit_fill, 4),
        "exit_reason": exit_reason, "exit_date": exit_date,
        "realized_pnl": round(exit_fill - entry_fill, 4),
    }


if __name__ == "__main__":
    print("Testing technicals_only_decision...")
    bullish_technicals = {
        "seat": "technicals", "symbol": "SPY", "stance": "bullish",
        "confidence": 0.6, "reasons": ["price above EMA"],
    }
    tradeable_regime = {
        "seat": "regime", "symbol": "SPY", "state": "trending",
        "volatility": "normal", "trend": "up", "tradeable": True,
        "reason": "normal volatility, clear up trend",
    }
    non_tradeable_regime = {
        "seat": "regime", "symbol": "SPY", "state": "ranging",
        "volatility": "normal", "trend": "sideways", "tradeable": False,
        "reason": "no directional edge, sitting out",
    }
    weak_technicals = {
        "seat": "technicals", "symbol": "SPY", "stance": "bullish",
        "confidence": 0.2, "reasons": ["one weak signal only"],
    }

    d1 = technicals_only_decision(bullish_technicals, tradeable_regime)
    assert d1["action"] == "buy" and d1["target_quantity"] == 1, d1
    print("PASS — confident bullish technicals + tradeable regime -> buy.")

    d2 = technicals_only_decision(bullish_technicals, non_tradeable_regime)
    assert d2["action"] == "hold", d2
    print("PASS — non-tradeable regime forces hold regardless of technicals.")

    d3 = technicals_only_decision(weak_technicals, tradeable_regime)
    assert d3["action"] == "hold", d3
    print("PASS — technicals below confidence threshold -> hold, not a weak buy.")

    print("\nTesting simulate_option_trade — stop-loss path...")
    bars_falling = [
        {"date": "2026-01-06", "open": 5.0, "high": 5.2, "low": 4.5, "close": 4.8},
        {"date": "2026-01-07", "open": 4.8, "high": 4.9, "low": 3.0, "close": 3.0},
    ]
    r1 = simulate_option_trade(
        entry_close=6.0, bars_after_entry=bars_falling, side="buy", strike=620.0,
        expiration_date="2026-01-16", spot_at_expiration=615.0,
        stop_loss_pct=0.50, take_profit_pct=1.00, haircut_pct=0.03,
    )
    assert r1["exit_reason"] == "stop_loss" and r1["exit_date"] == "2026-01-07", r1
    assert r1["realized_pnl"] < 0, r1
    print(f"PASS — premium fell >50%, stopped out on 2026-01-07: {r1}")

    print("\nTesting simulate_option_trade — take-profit path...")
    bars_rising = [
        {"date": "2026-01-06", "open": 6.0, "high": 7.0, "low": 5.9, "close": 6.8},
        {"date": "2026-01-07", "open": 6.8, "high": 13.0, "low": 6.7, "close": 12.5},
    ]
    r2 = simulate_option_trade(
        entry_close=6.0, bars_after_entry=bars_rising, side="buy", strike=620.0,
        expiration_date="2026-01-16", spot_at_expiration=630.0,
        stop_loss_pct=0.50, take_profit_pct=1.00, haircut_pct=0.03,
    )
    assert r2["exit_reason"] == "take_profit" and r2["exit_date"] == "2026-01-07", r2
    assert r2["realized_pnl"] > 0, r2
    print(f"PASS — premium doubled, took profit on 2026-01-07: {r2}")

    print("\nTesting simulate_option_trade — expiration via last bar...")
    bars_flat = [
        {"date": "2026-01-15", "open": 6.0, "high": 6.2, "low": 5.8, "close": 6.1},
        {"date": "2026-01-16", "open": 6.1, "high": 6.3, "low": 5.9, "close": 6.0},
    ]
    r3 = simulate_option_trade(
        entry_close=6.0, bars_after_entry=bars_flat, side="buy", strike=620.0,
        expiration_date="2026-01-16", spot_at_expiration=625.0,
        stop_loss_pct=0.50, take_profit_pct=1.00, haircut_pct=0.03,
    )
    assert r3["exit_reason"] == "expiration_last_bar" and r3["exit_date"] == "2026-01-16", r3
    print(f"PASS — neither stop nor target hit, exited at last bar on expiration date: {r3}")

    print("\nTesting simulate_option_trade — expiration via intrinsic value fallback...")
    bars_thin = [
        {"date": "2026-01-10", "open": 6.0, "high": 6.2, "low": 5.8, "close": 6.1},
    ]
    r4 = simulate_option_trade(
        entry_close=6.0, bars_after_entry=bars_thin, side="buy", strike=620.0,
        expiration_date="2026-01-16", spot_at_expiration=628.0,
        stop_loss_pct=0.50, take_profit_pct=1.00, haircut_pct=0.03,
    )
    assert r4["exit_reason"] == "expiration_intrinsic" and r4["exit_date"] == "2026-01-16", r4
    assert r4["exit_fill"] == 8.0, r4  # max(0, 628 - 620) = 8, no haircut on settlement
    print(f"PASS — bars stopped 6 days before expiration, fell back to intrinsic value: {r4}")
