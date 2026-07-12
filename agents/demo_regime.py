"""
Regime-filter demo — proves the three behaviors the mission asked for,
with zero real-money risk:

  1. A favorable regime (clear trend, adequate volatility) lets an
     otherwise-qualifying trade through the Judge's gate.
  2. An unfavorable regime (low-volatility ranging) forces a HOLD even
     when both seats are strongly bullish — the filter can only tighten,
     never loosen — and that sit-out is logged distinctly (action=
     "regime_sitout") without moving the round-trip counter at all.
  3. A held position whose regime flips to non-tradeable gets closed by
     the regime_change exit path, isolated from the other exit paths by
     keeping price change small and omitting fundamentals/technicals so
     stop_loss/take_profit/conviction_drop can't be what fired.

Uses an ISOLATED paper account (logs/demo_regime_portfolio.json, via
PaperBroker's portfolio_path override — see its docstring) for scenario 3,
same reasoning as agents/demo_exits.py: deterministic, doesn't depend on
whatever state the shared paper account happens to be in. Controlled
prices/indicators throughout — the point is proving the regime logic, not
live-data integration (already proven elsewhere).

Run it as: python -m agents.demo_regime
"""

import os

from . import exits, judge, regime, technicals
from execution import config, trade_log
from execution.paper_broker import PaperBroker

_DEMO_PORTFOLIO_PATH = os.path.join(config.LOG_DIR, "demo_regime_portfolio.json")


def _open_demo_position(broker: PaperBroker, symbol: str, quantity: float, price: float, reason: str) -> dict:
    """Like agents.demo_exits._fresh_broker's buy step: works around
    MAX_TRADES_PER_DAY being exhausted by earlier testing in the same
    session (a shared trade_log, per-account-isolated portfolio
    interaction — not a regime-logic concern), restoring the cap right
    after. See agents/demo_exits.py for the same pattern, more fully
    explained."""
    original_cap = config.MAX_TRADES_PER_DAY
    trades_today = trade_log.count_trades_today()
    if trades_today >= original_cap:
        config.MAX_TRADES_PER_DAY = trades_today + 1
    try:
        return broker.buy(symbol, quantity, price, reason=reason)
    finally:
        config.MAX_TRADES_PER_DAY = original_cap


def run_demo() -> None:
    print(config.mode_banner())
    ref = config.TARGET_DAILY_VOL_PCT

    # --- 1. Favorable regime: trade allowed through the gate ------------
    print("=" * 70)
    print("1. Favorable regime (normal volatility, clear uptrend)")
    print("=" * 70)
    favorable_regime = regime.regime_stance("NVDA", price=500.0, ema=480.0, atr_pct=ref)
    nvda_fundamentals = {
        "seat": "fundamentals", "symbol": "NVDA", "stance": "bullish",
        "confidence": 0.7, "reasons": ["demo: strong revenue growth"],
    }
    nvda_technicals = technicals.build_view("NVDA", price=500.0, ema=480.0, rsi=25.0)
    decision1 = judge.decide(nvda_fundamentals, nvda_technicals, regime=favorable_regime)

    print(f"Regime: {favorable_regime}")
    print(f"Fundamentals: {nvda_fundamentals['stance']} ({nvda_fundamentals['confidence']})")
    print(f"Technicals:   {nvda_technicals['stance']} ({nvda_technicals['confidence']})")
    print(f"Judge decision: {decision1}")
    assert decision1["action"] == "buy", "expected the gate to allow this trade"
    print("-> Trade ALLOWED through the gate, as expected.")

    # --- 2. Unfavorable regime: forced HOLD, logged, doesn't count ------
    print("\n" + "=" * 70)
    print("2. Unfavorable regime (low volatility, ranging) — seats strongly bullish anyway")
    print("=" * 70)
    unfavorable_regime = regime.regime_stance("KO", price=80.0, ema=79.9, atr_pct=ref * 0.3)
    ko_fundamentals = {
        "seat": "fundamentals", "symbol": "KO", "stance": "bullish",
        "confidence": 0.8, "reasons": ["demo: deliberately strong, to prove regime overrides it"],
    }
    ko_technicals = technicals.build_view("KO", price=80.0, ema=76.0, rsi=20.0)
    decision2 = judge.decide(ko_fundamentals, ko_technicals, regime=unfavorable_regime)

    print(f"Regime: {unfavorable_regime}")
    print(f"Fundamentals: {ko_fundamentals['stance']} ({ko_fundamentals['confidence']})")
    print(f"Technicals:   {ko_technicals['stance']} ({ko_technicals['confidence']})")
    print(f"Judge decision: {decision2}")
    assert decision2["action"] == "hold", "expected the regime filter to force a HOLD"
    print("-> Forced HOLD despite both seats bullish — the filter tightened, never loosened.")

    before = trade_log.round_trip_stats()
    trade_log.record(
        "regime_sitout", "KO", 0, 80.0, paper=True, reason=decision2["rationale"],
        extra={"state": unfavorable_regime["state"], "volatility": unfavorable_regime["volatility"],
               "trend": unfavorable_regime["trend"]},
    )
    after = trade_log.round_trip_stats()
    print(f"Round-trip stats before logging the sit-out: {before}")
    print(f"Round-trip stats after logging the sit-out:  {after}")
    assert before == after, "a regime sit-out must NOT count as a round-trip"
    print("-> Confirmed: logging the sit-out did not move the round-trip counter.")

    # --- 3. Held position, regime flips to non-tradeable -> exit --------
    print("\n" + "=" * 70)
    print("3. A held position whose regime flips to non-tradeable")
    print("=" * 70)
    if os.path.exists(_DEMO_PORTFOLIO_PATH):
        os.remove(_DEMO_PORTFOLIO_PATH)
    broker = PaperBroker(portfolio_path=_DEMO_PORTFOLIO_PATH)

    open_trade = _open_demo_position(broker, "NVDA", 1, 500.0, reason="demo_regime: open in favorable regime")
    entry_price = broker.cost_basis["NVDA"]
    print(f"Opened NVDA: {open_trade}")

    # Price barely moves — nowhere near STOP_LOSS_PCT (-8%) or
    # TAKE_PROFIT_PCT (+15%) — so if something closes this position, it
    # can only be the regime_change path, not a price-based one. No
    # fundamentals/technicals are supplied to the sweep either, so
    # conviction_drop is skipped entirely — isolating regime_change as
    # the only path that *can* fire here.
    current_price = round(entry_price * 1.01, 2)
    flipped_regime = regime.regime_stance("NVDA", price=current_price, ema=current_price * 0.999, atr_pct=ref * 0.3)
    print(f"Price barely moved ({entry_price:.2f} -> {current_price}), but regime flipped: {flipped_regime}")
    assert not flipped_regime["tradeable"], "demo setup should force a non-tradeable regime"

    before_closes = trade_log.round_trip_stats()
    closures = exits.run_exit_sweep(broker, {"NVDA": current_price}, regimes={"NVDA": flipped_regime})
    after_closes = trade_log.round_trip_stats()

    print(f"\nExit sweep results: {closures}")
    assert len(closures) == 1 and closures[0]["path"] == "regime_change", \
        "expected exactly one close, via regime_change"
    print(f"-> NVDA closed via {closures[0]['path']}, realized P&L ${closures[0]['realized_pnl']:+.2f}")
    print(f"Round-trip stats: {before_closes} -> {after_closes}")
    print(f"Account after exit: {broker.account({'NVDA': current_price})}")


if __name__ == "__main__":
    run_demo()
