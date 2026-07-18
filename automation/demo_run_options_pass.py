"""
automation/demo_run_options_pass.py -- end-to-end proof of
automation.run_options_pass's two-phase flow, mirroring
automation/demo_run_pass.py's own role for the equity pass.

Fabricated but deterministic inputs throughout -- this drives both
phases exactly the way a real scheduled routine would: call
plan_options_pass(), inspect what it says is needed, fetch (here,
fabricate) that live data, then call execute_options_pass(). Every
scenario runs against its own isolated OptionsPaperBroker (a tempfile
portfolio/log path) so nothing here ever touches
logs/options_paper_portfolio.json or logs/options_trades.jsonl.
"""

import os
import tempfile
from datetime import datetime, timezone

from automation.run_options_pass import execute_options_pass, plan_options_pass
from execution import config
from execution.options_paper_broker import OptionsPaperBroker


def _spy_bundle(price: float, ema: float, rsi: float, regime_ema: float, atr_pct: float,
                 now: datetime) -> dict:
    """
    A fabricated but well-formed bundle -- shaped EXACTLY like the real
    get_equity_quotes / get_equity_technical_indicators responses
    execution.robinhood's parsers actually expect (verified against
    execution/robinhood.py's _extract_price() and
    _extract_indicator_value() directly, not guessed), so this demo
    exercises the real parsing path end-to-end rather than a shortcut.

    now is used as this quote's trade timestamp, matching whatever `now`
    the calling scenario passes to plan_options_pass() -- get_quote_age_
    minutes() computes staleness against that same `now`, so a fixed
    historical test time and a "just traded" timestamp agree with each
    other regardless of the real wall-clock time this demo actually runs.

    atr_pct is supplied directly as a fraction for fixture simplicity --
    get_atr_pct() divides the raw dollar ATR by price, so the raw
    indicator value threaded through here is atr_pct * price.

    regime_ema's indicator carries params.period matching
    config.REGIME_EMA_LOOKBACK_DAYS -- get_regime_ema() validates this
    exact field and raises if it's missing or wrong (see
    execution/robinhood.py's own docstring for why: it's what keeps
    Technicals's short EMA and the regime filter's EMA structurally
    unable to be swapped for each other).
    """
    now_iso = now.isoformat()
    return {
        "quote": {"data": {"results": [{"quote": {
            "symbol": "SPY", "has_traded": True, "state": "active",
            "last_trade_price": str(price), "venue_last_trade_time": now_iso,
        }}]}},
        "atr": {"data": {"indicators": [
            {"type": "atr", "series": [{"value": str(atr_pct * price)}]},
        ]}},
        "rsi": {"data": {"indicators": [
            {"type": "rsi", "series": [{"value": str(rsi)}]},
        ]}},
        "ema": {"data": {"indicators": [
            {"type": "ema", "series": [{"value": str(ema)}]},
        ]}},
        "regime_ema": {"data": {"indicators": [
            {"type": "ema", "params": {"period": config.REGIME_EMA_LOOKBACK_DAYS},
             "series": [{"value": str(regime_ema)}]},
        ]}},
    }


def _instruments_response(contract_id: str, strike: float, option_type: str, expiration: str) -> dict:
    return {"data": {"instruments": [
        {"id": contract_id, "strike_price": f"{strike:.4f}", "type": option_type, "expiration_date": expiration},
    ]}}


def _quote_response(contract_id: str, mark: float, bid: float | None = None, ask: float | None = None) -> dict:
    """Shaped exactly like a real get_option_quotes response -- verified
    2026-07-17 against a live SPY contract: instrument_id and every price
    field are nested under "quote", same convention get_equity_quotes
    uses (see _spy_bundle()'s own docstring above). An earlier version of
    this fixture used a flat shape that happened to match
    parse_option_quote()'s original bug rather than catching it."""
    return {"data": {"results": [
        {"quote": {"instrument_id": contract_id, "mark_price": str(mark),
                    "bid_price": str(bid) if bid is not None else None,
                    "ask_price": str(ask) if ask is not None else None}},
    ]}}


def _fresh_broker(tmp_dir: str, name: str) -> OptionsPaperBroker:
    return OptionsPaperBroker(
        portfolio_path=os.path.join(tmp_dir, f"{name}_portfolio.json"),
        log_path=os.path.join(tmp_dir, f"{name}_trades.jsonl"),
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        print("=== Scenario 1: market closed -> whole pass is a no-op ===")
        broker = _fresh_broker(tmp, "scenario1")
        closed_time = datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc)  # Saturday
        bundle = _spy_bundle(price=650.0, ema=640.0, rsi=60.0, regime_ema=620.0, atr_pct=0.01, now=closed_time)
        plan = plan_options_pass(bundle, broker, now=closed_time)
        assert plan["no_op"] is True and "market-hours" in plan["no_op_reason"], plan
        summary = execute_options_pass(plan, broker, live_data={}, now=closed_time)
        assert summary["no_op"] is True and summary["exits"] == [] and summary["entries"] == [], summary
        print(f"PASS — market-closed no-op, nothing evaluated or executed: {plan['no_op_reason']}")

        print("\n=== Scenario 2: confident bullish signal opens BOTH tracks the same day ===")
        broker = _fresh_broker(tmp, "scenario2")
        weekday_open_time = datetime(2026, 7, 20, 15, 0, tzinfo=timezone.utc)  # Monday, mid-session
        # Hand-verified against agents/technicals.py and agents/regime.py's
        # actual thresholds, not just assumed to "look bullish":
        #   technicals: pct_from_ema=(650-630)/630=3.17% > _TREND_BAND_PCT(0.5%)
        #     -> 1 bullish vote; RSI=60 is neutral (30-70 band) -> 0 votes but
        #     still counts toward total_votes=2 (_MAX_SIGNALS). confidence =
        #     agreement(0.5) * coverage(2/2=1.0) = 0.5 -- exactly clears
        #     judge.CONFIDENCE_THRESHOLD (the check is strict "<", so 0.5 passes).
        #   regime: atr_pct=0.01 < TARGET_DAILY_VOL_PCT(0.023)*REGIME_LOW_VOL_
        #     MULTIPLIER(0.5)=0.0115 -> "low" volatility. pct_from_regime_ema=
        #     (650-620)/620=4.84% > REGIME_TREND_BAND_PCT(2%) -> "up" trend.
        #     low vol + up trend = "low_vol_trend", tradeable=True.
        bundle = _spy_bundle(price=650.0, ema=630.0, rsi=60.0, regime_ema=620.0, atr_pct=0.01, now=weekday_open_time)
        plan = plan_options_pass(bundle, broker, now=weekday_open_time)
        assert plan["no_op"] is False, plan
        assert plan["decision"]["action"] == "buy", plan["decision"]
        for track in ("7", "30"):
            assert plan["tracks"][track]["entry_lookup"] is not None, plan["tracks"][track]
            assert plan["tracks"][track]["entry_lookup"]["option_type"] == "call"
        print(f"PASS — bullish decision, both tracks flagged for an entry lookup: {plan['decision']['action']}")

        live_data = {
            "entry_instruments": {
                "7": _instruments_response("contract-7d", 650.0, "call", plan["tracks"]["7"]["entry_lookup"]["expiration_date"]),
                "30": _instruments_response("contract-30d", 650.0, "call", plan["tracks"]["30"]["entry_lookup"]["expiration_date"]),
            },
            "entry_quotes": {
                "7": _quote_response("contract-7d", mark=6.0, bid=5.9, ask=6.1),
                "30": _quote_response("contract-30d", mark=12.0, bid=11.8, ask=12.2),
            },
        }
        summary = execute_options_pass(plan, broker, live_data, now=weekday_open_time)
        assert len(summary["entries"]) == 2, summary
        assert all(e["executed"] is False for e in summary["entries"]), summary  # OPTIONS_AUTOMATION_DRY_RUN default True
        print(f"PASS — dry-run logged both entries, executed nothing (default dry-run): {summary['entries']}")

        print("\n=== Scenario 3: a track already holding a position is skipped for entries ===")
        broker = _fresh_broker(tmp, "scenario3")
        broker.buy_to_open("7", "existing-contract", 650.0, "call", "2026-08-01", 1, 6.0, now=weekday_open_time)
        plan = plan_options_pass(bundle, broker, now=weekday_open_time)
        assert plan["tracks"]["7"]["entry_lookup"] is None, plan["tracks"]["7"]  # already held -- no new lookup
        assert plan["tracks"]["7"]["held_contract_id"] == "existing-contract", plan["tracks"]["7"]
        assert plan["tracks"]["30"]["entry_lookup"] is not None, plan["tracks"]["30"]  # still flat -- gets a lookup
        print("PASS — occupied track 7 skipped for entries; flat track 30 still gets an entry lookup.")

        print("\n=== Scenario 4: exit sweep closes a track on stop-loss (armed, not dry-run) ===")
        import execution.config as config_module
        config_module.OPTIONS_AUTOMATION_DRY_RUN = False  # deliberately arm this scenario only
        try:
            broker = _fresh_broker(tmp, "scenario4")
            broker.buy_to_open("7", "losing-contract", 650.0, "call", "2026-07-25", 1, 6.0, now=weekday_open_time)
            plan = plan_options_pass(bundle, broker, now=weekday_open_time)
            assert plan["tracks"]["7"]["held_contract_id"] == "losing-contract", plan["tracks"]["7"]
            live_data = {"exit_quotes": {"7": _quote_response("losing-contract", mark=2.9, bid=2.8, ask=3.0)}}
            # (6.0 - 2.9) / 6.0 = 0.5167 -- past the 0.50 stop-loss threshold
            summary = execute_options_pass(plan, broker, live_data, now=weekday_open_time)
            assert len(summary["exits"]) == 1 and summary["exits"][0]["reason"] == "stop_loss", summary
            assert summary["exits"][0]["executed"] is True, summary
            assert broker.open_positions["7"] is None, broker.open_positions
            print(f"PASS — armed pass actually closed the losing position on stop-loss: {summary['exits'][0]}")
        finally:
            config_module.OPTIONS_AUTOMATION_DRY_RUN = True  # restore -- never leak a global flip between scenarios

        print("\n=== Scenario 5: no listed contract for an entry -> skip, never substitute ===")
        broker = _fresh_broker(tmp, "scenario5")
        plan = plan_options_pass(bundle, broker, now=weekday_open_time)
        live_data = {"entry_instruments": {"7": {"data": {"instruments": []}}, "30": {"data": {"instruments": []}}},
                     "entry_quotes": {}}
        summary = execute_options_pass(plan, broker, live_data, now=weekday_open_time)
        assert summary["entries"] == [], summary  # both tracks skipped, nothing appended
        print("PASS — empty instrument lookup skips the entry cleanly, no fabricated contract.")

    print("\nAll scenarios passed.")
