"""
Automation demo — proves run_pass() end-to-end, with zero real-money risk
and zero real paper-order risk (AUTOMATION_DRY_RUN stays True throughout).

Three separate, deterministic passes:
  1. A realistic mixed bundle: a held position that hits take-profit (the
     exit sweep), a confident-bullish entry, a neutral-signal hold, and
     a regime-blocked sit-out — proving exit-sweep-then-entries ordering,
     dry-run logging without execution, regime tagging, and the summary
     + round-trip stats.
  2. The SAME bundle, but `now` falls on a weekend — proving the
     market-hours guard produces a whole-pass no-op.
  3. The SAME bundle, plus two deliberately bad entries (a stale quote, a
     quote that never traded) — proving the per-symbol data sanity check
     skips just those symbols without disturbing the rest of the pass.
  4. The market risk-off switch turns RISK-OFF (SPY in a year-long slide):
     a held position with no price trigger exits via "market_risk_off",
     and every new buy is blocked as a "market_sitout".
  5. The market data is MISSING: the switch is "unknown", which blocks
     every new buy but must NOT force an exit (no fire sale on a glitch).

Passes 1-3 run with a RISK-ON market (SPY steadily rising), so the switch
doesn't interfere with what they prove.

Uses an ISOLATED paper account (logs/demo_automation_portfolio.json, via
PaperBroker's portfolio_path override), same reasoning as
agents/demo_exits.py and agents/demo_regime.py: deterministic, doesn't
depend on whatever state the shared paper account happens to be in.
Constructed prices/indicators throughout, not live quotes — the point
here is proving the automation logic, not live-data integration (already
proven elsewhere).

Run it as: python -m automation.demo_run_pass
"""

import os
from datetime import datetime, timezone

from execution import config, trade_log
from execution.paper_broker import PaperBroker
from .run_pass import run_pass

_DEMO_PORTFOLIO_PATH = os.path.join(config.LOG_DIR, "demo_automation_portfolio.json")

IN_HOURS_NOW = datetime(2026, 7, 15, 14, 30, 0, tzinfo=timezone.utc)   # Wed 10:30 ET
WEEKEND_NOW = datetime(2026, 7, 18, 14, 30, 0, tzinfo=timezone.utc)    # Sat 10:30 ET


def _quote(symbol: str, price: float, timestamp: str, has_traded: bool = True) -> dict:
    return {"data": {"results": [{"quote": {
        "symbol": symbol, "last_trade_price": str(price), "last_non_reg_trade_price": str(price),
        "venue_last_trade_time": timestamp, "venue_last_non_reg_trade_time": timestamp,
        "has_traded": has_traded, "state": "active",
    }}]}}


def _indicator(symbol: str, itype: str, period: int, value: float) -> dict:
    return {"data": {"symbol": symbol, "interval": "day",
                      "indicators": [{"type": itype, "params": {"period": period},
                                      "series": [{"begins_at": "2026-07-15T00:00:00Z", "value": value}]}]}}


def _fundamentals(symbol: str, sector: str = "Electronic Technology") -> dict:
    return {"data": {"results": [{"symbol": symbol, "sector": sector}]}}


def _build_symbol_bundle(symbol: str, price: float, ema: float, rsi: float, regime_ema: float,
                          atr_pct: float, timestamp: str = "2026-07-15T14:25:00Z",
                          has_traded: bool = True) -> dict:
    return {
        "quote": _quote(symbol, price, timestamp, has_traded=has_traded),
        "atr": _indicator(symbol, "atr", 14, atr_pct * price),
        "rsi": _indicator(symbol, "rsi", 14, rsi),
        "ema": _indicator(symbol, "ema", 9, ema),
        "regime_ema": _indicator(symbol, "ema", config.REGIME_EMA_LOOKBACK_DAYS, regime_ema),
        "robinhood_fundamentals": _fundamentals(symbol),
    }


def _spy_monthly(rising: bool) -> dict:
    """A raw monthly get_equity_historicals response for SPY: 49 bars ending
    with IN_HOURS_NOW's month (July 2026, still in progress, so the switch
    ignores it). Steady rise -> risk_on; a rise then a year-long slide ->
    risk_off."""
    if rising:
        closes = [300 * 1.01 ** i for i in range(49)]
    else:
        closes = [300 * 1.01 ** i for i in range(35)] + [300 * 1.01 ** 34 * 0.97 ** i for i in range(1, 15)]
    y, m, bars = 2022, 7, []
    for c in closes:                       # Jul 2022 .. Jul 2026
        bars.append({"begins_at": f"{y:04d}-{m:02d}-01T00:00:00Z", "close_price": f"{c:.2f}", "volume": 1000})
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    return {"data": {"results": [{"symbol": "SPY", "interval": "month", "bars": bars}]}}


MARKET_RISK_ON = _spy_monthly(rising=True)
MARKET_RISK_OFF = _spy_monthly(rising=False)


def _build_bundle() -> dict:
    ref = config.TARGET_DAILY_VOL_PCT
    return {
        # JPM: pre-held below (opened via a seed buy, not via run_pass), this
        # pass's price is set to clear TAKE_PROFIT_PCT -- proves the exit sweep.
        "JPM": _build_symbol_bundle(
            "JPM", price=180.09, ema=175.0, rsi=50.0, regime_ema=178.0, atr_pct=ref,
        ),
        # AAPL: confident bullish signal (price above EMA, RSI oversold),
        # tradeable regime -> Judge says buy.
        "AAPL": _build_symbol_bundle(
            "AAPL", price=210.0, ema=200.0, rsi=25.0, regime_ema=195.0, atr_pct=ref,
        ),
        # MSFT: neutral strategy signal (price inside the EMA band, RSI in the
        # neutral range) -> plain hold. Regime is deliberately tradeable here
        # so the hold is attributable to the signal having no edge, not the
        # regime filter.
        "MSFT": _build_symbol_bundle(
            "MSFT", price=300.0, ema=300.4, rsi=50.0, regime_ema=290.0, atr_pct=ref,
        ),
        # GOOGL: strongly bullish signal, but regime is low_vol_ranging ->
        # forced sit-out, proving the filter tightens regardless of the signal.
        "GOOGL": _build_symbol_bundle(
            "GOOGL", price=140.0, ema=134.0, rsi=20.0, regime_ema=139.9, atr_pct=ref * 0.3,
        ),
    }


def _seed_jpm_position(broker: PaperBroker) -> None:
    """Open JPM's pre-existing position directly (not via run_pass) so the
    main proof starts with something to exit. Works around
    MAX_TRADES_PER_DAY reading the SHARED trade_log (see
    agents/demo_exits.py for the same pattern, fully explained)."""
    original_cap = config.MAX_TRADES_PER_DAY
    trades_today = trade_log.count_trades_today()
    if trades_today >= original_cap:
        config.MAX_TRADES_PER_DAY = trades_today + 1
    try:
        trade = broker.buy("JPM", 1, 150.0, reason="demo_run_pass: seed a held position for the exit sweep")
    finally:
        config.MAX_TRADES_PER_DAY = original_cap
    print(f"Seeded JPM position (not via run_pass): {trade}")


def run_demo() -> None:
    print(config.mode_banner())
    assert config.AUTOMATION_DRY_RUN, "this demo only runs meaningfully with AUTOMATION_DRY_RUN=True"

    if os.path.exists(_DEMO_PORTFOLIO_PATH):
        os.remove(_DEMO_PORTFOLIO_PATH)
    broker = PaperBroker(portfolio_path=_DEMO_PORTFOLIO_PATH)
    _seed_jpm_position(broker)

    bundle = _build_bundle()

    # --- 1. Main dry-run proof -------------------------------------------
    print("\n" + "=" * 70)
    print("1. Full pass, in market hours, DRY-RUN")
    print("=" * 70)
    before_stats = trade_log.round_trip_stats()
    before_positions = dict(broker.positions)
    summary = run_pass(bundle, now=IN_HOURS_NOW, broker=broker, market_bundle=MARKET_RISK_ON)

    print(f"market_open={summary['market_open']}  dry_run={summary['dry_run']}")
    print(f"\nExits (sweep ran FIRST): {summary['exits']}")
    print(f"\nEntries (ran AFTER the sweep): {summary['entries']}")
    print(f"\nHolds: {summary['holds']}")
    print(f"Regime sit-outs: {summary['regime_sitouts']}")
    print(f"\nRound-trip stats: {summary['round_trip_stats']}")

    assert summary["market_open"] is True
    assert summary["dry_run"] is True
    assert summary["exits"][0]["symbol"] == "JPM" and summary["exits"][0]["path"] == "take_profit"
    assert summary["exits"][0]["executed"] is False, "dry-run must never execute an exit"
    assert all(r["symbol"] == "JPM" for r in summary["exits"]), "only the held symbol should appear in exits"
    assert {e["symbol"] for e in summary["entries"]} == {"AAPL"}
    assert summary["entries"][0]["action"] == "buy" and summary["entries"][0]["executed"] is False, \
        "dry-run must never execute an entry"
    assert summary["holds"] == ["MSFT"]
    assert summary["regime_sitouts"] == ["GOOGL"]
    assert summary["market"] == "risk_on" and summary["market_sitouts"] == [], \
        "a risk-on market must not interfere"
    assert all(r.get("regime_state") for r in summary["exits"] + summary["entries"]), \
        "every logged decision must carry its regime state"
    assert broker.positions == before_positions, "dry-run must never change what's actually held"
    assert trade_log.round_trip_stats() == before_stats, "dry-run must never move the round-trip counter"
    print("\n-> Exit sweep ran before entries; every decision was logged and NONE was executed; "
          "regime state is tagged on every record; broker state and round-trips are untouched.")

    # --- 2. Market-hours guard proof --------------------------------------
    print("\n" + "=" * 70)
    print("2. Same bundle, but `now` is a Saturday -> market-hours guard")
    print("=" * 70)
    before_stats = trade_log.round_trip_stats()
    weekend_summary = run_pass(bundle, now=WEEKEND_NOW, broker=broker)
    print(f"Summary: {weekend_summary}")
    assert weekend_summary["market_open"] is False
    assert weekend_summary["exits"] == [] and weekend_summary["entries"] == []
    assert "round_trip_stats" not in weekend_summary, "a market-closed pass shouldn't even reach that far"
    assert trade_log.round_trip_stats() == before_stats
    print("-> Whole-pass no-op, logged as action=\"automation_noop\" — nothing was evaluated, nothing changed.")

    # --- 3. Bad-data fail-safe proof ---------------------------------------
    print("\n" + "=" * 70)
    print("3. Same bundle, plus a stale quote and an unparseable one")
    print("=" * 70)
    bad_bundle = dict(bundle)
    # WMT: well-formed, but its own venue timestamp is 5 days old.
    bad_bundle["WMT"] = _build_symbol_bundle(
        "WMT", price=90.0, ema=88.0, rsi=40.0, regime_ema=89.0, atr_pct=config.TARGET_DAILY_VOL_PCT,
        timestamp="2026-07-10T14:00:00Z",  # days before IN_HOURS_NOW
    )
    # XOM: fresh timestamp, but has_traded=False -> fails to parse at all.
    bad_bundle["XOM"] = _build_symbol_bundle(
        "XOM", price=115.0, ema=112.0, rsi=45.0, regime_ema=113.0, atr_pct=config.TARGET_DAILY_VOL_PCT,
        has_traded=False,
    )

    before_stats = trade_log.round_trip_stats()
    bad_summary = run_pass(bad_bundle, now=IN_HOURS_NOW, broker=broker, market_bundle=MARKET_RISK_ON)
    print(f"Symbols skipped: {bad_summary['symbols_skipped']}")
    print(f"Entries that still went through fine: {[e['symbol'] for e in bad_summary['entries']]}")

    assert set(bad_summary["symbols_skipped"]) == {"WMT", "XOM"}
    assert "WMT" not in {e["symbol"] for e in bad_summary["entries"]}
    assert "XOM" not in {e["symbol"] for e in bad_summary["entries"]}
    # AAPL/JPM/MSFT/GOOGL behave exactly as pass 1 despite the two bad symbols
    # sitting right next to them in the same bundle.
    assert {e["symbol"] for e in bad_summary["entries"]} == {"AAPL"}
    print("-> WMT (stale) and XOM (never traded) were skipped and logged (action=\"automation_skip\") — "
          "never evaluated, never traded — while every other symbol in the same pass processed normally.")

    # --- 4. Market switch RISK-OFF ---------------------------------------
    print("\n" + "=" * 70)
    print("4. Market switch RISK-OFF (SPY in a year-long slide)")
    print("=" * 70)
    calm_bundle = dict(bundle)
    # JPM back near its entry price: no stop-loss, no take-profit -- so if it
    # exits, only the market switch can be why.
    calm_bundle["JPM"] = _build_symbol_bundle(
        "JPM", price=151.0, ema=150.0, rsi=50.0, regime_ema=148.0, atr_pct=config.TARGET_DAILY_VOL_PCT,
    )
    before_stats, before_positions = trade_log.round_trip_stats(), dict(broker.positions)
    off = run_pass(calm_bundle, now=IN_HOURS_NOW, broker=broker, market_bundle=MARKET_RISK_OFF)
    print(f"market={off['market']}  exits={[(e['symbol'], e['path']) for e in off['exits']]}")
    print(f"entries={off['entries']}  market_sitouts={off['market_sitouts']}  holds={off['holds']}")
    assert off["market"] == "risk_off", off
    assert [(e["symbol"], e["path"]) for e in off["exits"]] == [("JPM", "market_risk_off")], off["exits"]
    assert off["exits"][0]["executed"] is False, "dry-run must never execute an exit"
    assert off["entries"] == [], "risk-off must block every new buy"
    assert sorted(off["market_sitouts"]) == ["AAPL", "GOOGL", "MSFT"], off["market_sitouts"]
    assert off["holds"] == [] and off["regime_sitouts"] == [], "the market gate is checked first"
    assert broker.positions == before_positions and trade_log.round_trip_stats() == before_stats
    print("-> JPM (no price trigger) would exit via market_risk_off; AAPL's confident buy, MSFT and "
          "GOOGL were all blocked as market_sitout. Nothing executed (DRY-RUN).")

    # --- 5. Market data MISSING -> unknown ----------------------------------
    print("\n" + "=" * 70)
    print("5. Market data MISSING -> switch 'unknown'")
    print("=" * 70)
    unk = run_pass(calm_bundle, now=IN_HOURS_NOW, broker=broker, market_bundle=None)
    print(f"market={unk['market']}  exits={unk['exits']}  entries={unk['entries']}  "
          f"market_sitouts={unk['market_sitouts']}")
    assert unk["market"] == "unknown", unk
    assert unk["exits"] == [], "missing market data must NOT force an exit"
    assert unk["entries"] == [] and sorted(unk["market_sitouts"]) == ["AAPL", "GOOGL", "MSFT"], unk
    print("-> With no usable market data: every new buy blocked, JPM left alone. "
          "When in doubt, the pass does less, not more.")

    print(f"\nFinal round-trip stats (unchanged all demo — everything above was DRY-RUN): "
          f"{trade_log.round_trip_stats()}")


if __name__ == "__main__":
    run_demo()
