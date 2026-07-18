"""
backtest/options_spread_engine.py — Level 2/Task 5's two-leg
credit-spread simulation (see agents/SPY_OPTIONS_DESIGN.md): the
day-by-day fill walk for the credit structure, parallel to
options_engine.simulate_option_trade() (which remains completely
unchanged and still owns the debit structure's simulation — this module
adds the NEW two-leg surface, it doesn't touch the existing one).

Stop-loss/take-profit apply to the SPREAD'S NET VALUE, not either leg
independently — see simulate_spread_trade()'s own docstring for the
exact sign convention, which mirrors simulate_option_trade()'s own
pnl_pct semantics so the SAME config.OPTIONS_STOP_LOSS_PCT /
OPTIONS_TAKE_PROFIT_PCT constants apply unchanged to both structures.
"""

from __future__ import annotations


def simulate_spread_trade(
    sold_entry_close: float,
    bought_entry_close: float,
    sold_bars_after_entry: list[dict],
    bought_bars_after_entry: list[dict],
    option_type: str,
    sold_strike: float,
    bought_strike: float,
    expiration_date: str,
    spot_at_expiration: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    sold_haircut_pct: float,
    bought_haircut_pct: float,
) -> dict | None:
    """
    Simulate one 2-leg vertical credit spread forward from entry,
    walking sold_bars_after_entry / bought_bars_after_entry
    (options_data.parse_option_bars() output for each leg, sorted
    chronologically, every bar strictly after the entry date) day by day
    IN LOCKSTEP, matched by date — a day either leg is missing a bar for
    is skipped entirely (never fabricated for the other leg from a
    mismatched date).

    Entry: SELLING the sold leg (haircut reduces what we receive) and
    BUYING the bought leg (haircut increases what we pay):
        net_entry_credit = sold_entry_close*(1 - sold_haircut/2)
                          - bought_entry_close*(1 + bought_haircut/2)
    Returns None if net_entry_credit <= 0 — a degenerate/inverted spread
    (options_data.select_spread_strikes()'s own ATM-vs-further-OTM
    convention should always produce a positive net credit in a
    realistic market; a non-positive one signals bad input, not a trade
    worth simulating).

    Sign convention for stop/target (matches
    options_engine.simulate_option_trade()'s own pnl_pct semantics,
    where positive = profit for the position actually held). Each day:
        net_value_now = sold_leg_close - bought_leg_close  (mark to
                         market, no haircut — haircut only applies at an
                         ACTUAL fill, entry or exit, same precedent as
                         simulate_option_trade())
        pnl_pct = (net_entry_credit - net_value_now) / net_entry_credit
    net_value_now FALLING (the spread getting cheaper to close) is a WIN
    for the credit seller — pnl_pct rises. net_value_now RISING is a
    LOSS — pnl_pct falls. Stop-loss triggers at pnl_pct <= -stop_loss_pct,
    take-profit at pnl_pct >= take_profit_pct — the SAME
    config.OPTIONS_STOP_LOSS_PCT / OPTIONS_TAKE_PROFIT_PCT constants the
    debit engine already uses, computed against the spread's net value
    instead of one leg's price.

    Exit fill (whichever triggers first: stop-loss, take-profit, or
    expiration): BUYING BACK the sold leg (haircut increases cost) and
    SELLING the bought leg (haircut reduces proceeds):
        net_exit_cost = sold_leg_close*(1 + sold_haircut/2)
                       - bought_leg_close*(1 - bought_haircut/2)
    realized_pnl = net_entry_credit - net_exit_cost (per-share; the
    caller scales by config.OPTIONS_CONTRACT_MULTIPLIER for per-contract
    dollars — not applied here, matching simulate_option_trade()'s own
    docstring precedent, where that scaling happens one layer up).

    At expiration with no trigger: exits at the last matched day's close
    if it reaches expiration_date, otherwise falls back to intrinsic
    value for BOTH legs at spot_at_expiration (cash settlement, no
    haircut on settlement — same precedent as simulate_option_trade()).
    """
    net_entry_credit = (
        sold_entry_close * (1 - sold_haircut_pct / 2)
        - bought_entry_close * (1 + bought_haircut_pct / 2)
    )
    if net_entry_credit <= 0:
        return None

    bought_by_date = {b["date"]: b["close"] for b in bought_bars_after_entry}
    matched = [
        (b["date"], b["close"], bought_by_date[b["date"]])
        for b in sold_bars_after_entry
        if b["date"] in bought_by_date
    ]

    def _exit_fill(sold_close: float, bought_close: float) -> float:
        return sold_close * (1 + sold_haircut_pct / 2) - bought_close * (1 - bought_haircut_pct / 2)

    for date, sold_close, bought_close in matched:
        net_value_now = sold_close - bought_close
        pnl_pct = (net_entry_credit - net_value_now) / net_entry_credit

        if pnl_pct <= -stop_loss_pct:
            net_exit_cost = _exit_fill(sold_close, bought_close)
            return {
                "net_entry_credit": round(net_entry_credit, 4), "net_exit_cost": round(net_exit_cost, 4),
                "exit_reason": "stop_loss", "exit_date": date,
                "realized_pnl": round(net_entry_credit - net_exit_cost, 4),
            }
        if pnl_pct >= take_profit_pct:
            net_exit_cost = _exit_fill(sold_close, bought_close)
            return {
                "net_entry_credit": round(net_entry_credit, 4), "net_exit_cost": round(net_exit_cost, 4),
                "exit_reason": "take_profit", "exit_date": date,
                "realized_pnl": round(net_entry_credit - net_exit_cost, 4),
            }

    last = matched[-1] if matched else None
    if last is not None and last[0] >= expiration_date:
        date, sold_close, bought_close = last
        net_exit_cost = _exit_fill(sold_close, bought_close)
        return {
            "net_entry_credit": round(net_entry_credit, 4), "net_exit_cost": round(net_exit_cost, 4),
            "exit_reason": "expiration_last_bar", "exit_date": date,
            "realized_pnl": round(net_entry_credit - net_exit_cost, 4),
        }

    if option_type == "put":
        sold_intrinsic = max(0.0, sold_strike - spot_at_expiration)
        bought_intrinsic = max(0.0, bought_strike - spot_at_expiration)
    else:
        sold_intrinsic = max(0.0, spot_at_expiration - sold_strike)
        bought_intrinsic = max(0.0, spot_at_expiration - bought_strike)
    net_exit_cost = sold_intrinsic - bought_intrinsic  # cash settlement, no haircut
    return {
        "net_entry_credit": round(net_entry_credit, 4), "net_exit_cost": round(net_exit_cost, 4),
        "exit_reason": "expiration_intrinsic", "exit_date": expiration_date,
        "realized_pnl": round(net_entry_credit - net_exit_cost, 4),
    }


if __name__ == "__main__":
    print("Testing simulate_spread_trade — degenerate entry (non-positive net credit) returns None...")
    bad = simulate_spread_trade(
        sold_entry_close=3.0, bought_entry_close=8.0,  # inverted: bought leg costs MORE than sold leg fetches
        sold_bars_after_entry=[], bought_bars_after_entry=[],
        option_type="put", sold_strike=615.0, bought_strike=605.0,
        expiration_date="2026-01-16", spot_at_expiration=610.0,
        stop_loss_pct=0.50, take_profit_pct=1.00, sold_haircut_pct=0.0, bought_haircut_pct=0.0,
    )
    assert bad is None, bad
    print("PASS — an inverted/degenerate spread (net_entry_credit <= 0) returns None, never simulated.")

    print("\nTesting simulate_spread_trade — stop-loss path (spread's net value rises sharply against the seller)...")
    sold_bars_falling = [
        {"date": "2026-01-06", "close": 8.0},
        {"date": "2026-01-07", "close": 15.0},  # sold leg (near strike) spikes -- bad for the credit seller
    ]
    bought_bars_falling = [
        {"date": "2026-01-06", "close": 3.0},
        {"date": "2026-01-07", "close": 5.0},
    ]
    r1 = simulate_spread_trade(
        sold_entry_close=8.0, bought_entry_close=3.0,
        sold_bars_after_entry=sold_bars_falling, bought_bars_after_entry=bought_bars_falling,
        option_type="put", sold_strike=615.0, bought_strike=605.0,
        expiration_date="2026-01-16", spot_at_expiration=610.0,
        stop_loss_pct=0.50, take_profit_pct=1.00, sold_haircut_pct=0.0, bought_haircut_pct=0.0,
    )
    # net_entry_credit=5.0; day2 net_value_now=15-5=10.0; pnl_pct=(5-10)/5=-1.0, past -0.50
    assert r1["exit_reason"] == "stop_loss" and r1["exit_date"] == "2026-01-07", r1
    assert r1["realized_pnl"] < 0, r1
    print(f"PASS — net value rose sharply against the seller, stopped out on 2026-01-07: {r1}")

    print("\nTesting simulate_spread_trade — take-profit path (both legs decay toward worthless)...")
    sold_bars_decaying = [
        {"date": "2026-01-06", "close": 6.0},
        {"date": "2026-01-07", "close": 0.0},
    ]
    bought_bars_decaying = [
        {"date": "2026-01-06", "close": 2.0},
        {"date": "2026-01-07", "close": 0.0},
    ]
    r2 = simulate_spread_trade(
        sold_entry_close=8.0, bought_entry_close=3.0,
        sold_bars_after_entry=sold_bars_decaying, bought_bars_after_entry=bought_bars_decaying,
        option_type="put", sold_strike=615.0, bought_strike=605.0,
        expiration_date="2026-01-16", spot_at_expiration=610.0,
        stop_loss_pct=0.50, take_profit_pct=1.00, sold_haircut_pct=0.0, bought_haircut_pct=0.0,
    )
    # net_entry_credit=5.0; day2 net_value_now=0-0=0.0; pnl_pct=(5-0)/5=1.0, exactly at +1.00 threshold
    assert r2["exit_reason"] == "take_profit" and r2["exit_date"] == "2026-01-07", r2
    assert r2["realized_pnl"] > 0, r2
    print(f"PASS — both legs decayed to worthless, took profit on 2026-01-07: {r2}")

    print("\nTesting simulate_spread_trade — expiration via last bar (no trigger hit)...")
    sold_bars_flat = [
        {"date": "2026-01-15", "close": 7.5},
        {"date": "2026-01-16", "close": 7.4},
    ]
    bought_bars_flat = [
        {"date": "2026-01-15", "close": 2.7},
        {"date": "2026-01-16", "close": 2.6},
    ]
    r3 = simulate_spread_trade(
        sold_entry_close=8.0, bought_entry_close=3.0,
        sold_bars_after_entry=sold_bars_flat, bought_bars_after_entry=bought_bars_flat,
        option_type="put", sold_strike=615.0, bought_strike=605.0,
        expiration_date="2026-01-16", spot_at_expiration=610.0,
        stop_loss_pct=0.50, take_profit_pct=1.00, sold_haircut_pct=0.0, bought_haircut_pct=0.0,
    )
    assert r3["exit_reason"] == "expiration_last_bar" and r3["exit_date"] == "2026-01-16", r3
    print(f"PASS — neither stop nor target hit, exited at last matched bar on expiration date: {r3}")

    print("\nTesting simulate_spread_trade — expiration via intrinsic value fallback (bars run out early)...")
    sold_bars_thin = [{"date": "2026-01-10", "close": 6.0}]
    bought_bars_thin = [{"date": "2026-01-10", "close": 2.0}]
    r4 = simulate_spread_trade(
        sold_entry_close=8.0, bought_entry_close=3.0,
        sold_bars_after_entry=sold_bars_thin, bought_bars_after_entry=bought_bars_thin,
        option_type="put", sold_strike=615.0, bought_strike=605.0,
        expiration_date="2026-01-16", spot_at_expiration=612.0,  # between the two strikes
        stop_loss_pct=0.50, take_profit_pct=1.00, sold_haircut_pct=0.0, bought_haircut_pct=0.0,
    )
    assert r4["exit_reason"] == "expiration_intrinsic" and r4["exit_date"] == "2026-01-16", r4
    # sold (615 put) intrinsic = max(0, 615-612) = 3.0; bought (605 put) intrinsic = max(0, 605-612) = 0.0
    assert r4["net_exit_cost"] == 3.0, r4
    print(f"PASS — bars stopped 6 days early, fell back to intrinsic value for both legs: {r4}")

    print("\nTesting simulate_spread_trade — a day only ONE leg has a bar for is skipped, not fabricated...")
    sold_bars_gap = [
        {"date": "2026-01-06", "close": 7.9},
        {"date": "2026-01-07", "close": 7.8},  # bought leg has no bar this date -- must be skipped, not paired with a stale bought price
        {"date": "2026-01-08", "close": 7.6},
    ]
    bought_bars_gap = [
        {"date": "2026-01-06", "close": 2.9},
        {"date": "2026-01-08", "close": 2.6},
    ]
    r5 = simulate_spread_trade(
        sold_entry_close=8.0, bought_entry_close=3.0,
        sold_bars_after_entry=sold_bars_gap, bought_bars_after_entry=bought_bars_gap,
        option_type="put", sold_strike=615.0, bought_strike=605.0,
        expiration_date="2026-01-08", spot_at_expiration=610.0,
        stop_loss_pct=0.50, take_profit_pct=1.00, sold_haircut_pct=0.0, bought_haircut_pct=0.0,
    )
    assert r5["exit_reason"] == "expiration_last_bar" and r5["exit_date"] == "2026-01-08", r5
    print(f"PASS — 2026-01-07 (bought leg missing) was skipped entirely, matched straight through to 01-08: {r5}")

    # --- Real SPY option data from here on ----------------------------------
    print("\nTesting simulate_spread_trade on REAL SPY put spread data (615/605 puts, 2026-05-15 expiration)...")
    # Fetched directly this session via get_option_instruments + get_option_historicals:
    # SPY 615P (sold, nearer strike) and SPY 605P (bought, protective), both real
    # daily closes 2026-04-20 through 2026-05-01. Both legs decayed steadily
    # (SPY apparently held above 615 the whole window) -- a real, developing
    # win for this bull put spread, not a synthetic fixture.
    real_sold_bars = [
        {"date": "2026-04-21", "close": 0.77}, {"date": "2026-04-22", "close": 0.63},
        {"date": "2026-04-23", "close": 0.62}, {"date": "2026-04-24", "close": 0.43},
        {"date": "2026-04-27", "close": 0.31}, {"date": "2026-04-28", "close": 0.28},
        {"date": "2026-04-29", "close": 0.33}, {"date": "2026-04-30", "close": 0.16},
        {"date": "2026-05-01", "close": 0.13},
    ]
    real_bought_bars = [
        {"date": "2026-04-21", "close": 0.65}, {"date": "2026-04-22", "close": 0.52},
        {"date": "2026-04-23", "close": 0.51}, {"date": "2026-04-24", "close": 0.35},
        {"date": "2026-04-27", "close": 0.26}, {"date": "2026-04-28", "close": 0.23},
        {"date": "2026-04-29", "close": 0.27}, {"date": "2026-04-30", "close": 0.14},
        {"date": "2026-05-01", "close": 0.11},
    ]
    real_result = simulate_spread_trade(
        sold_entry_close=0.72, bought_entry_close=0.60,  # real 2026-04-20 closes
        sold_bars_after_entry=real_sold_bars, bought_bars_after_entry=real_bought_bars,
        option_type="put", sold_strike=615.0, bought_strike=605.0,
        expiration_date="2026-05-01",  # treating the last fetched real bar as this smoke test's horizon
        spot_at_expiration=620.0,  # unused unless the intrinsic branch fires
        stop_loss_pct=0.50, take_profit_pct=1.00, sold_haircut_pct=0.03, bought_haircut_pct=0.03,
    )
    assert real_result is not None, "real fetched premiums should never produce a degenerate net credit"
    assert real_result["exit_reason"] in ("expiration_last_bar", "take_profit"), real_result
    assert real_result["realized_pnl"] > 0, real_result  # both legs genuinely decayed in the seller's favor
    print(f"PASS — real SPY 615/605 put spread, real premium decay, positive realized P&L: {real_result}")

    print("\nAll options_spread_engine tests passed.")
