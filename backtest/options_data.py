"""
backtest/options_data.py — parsing and contract-selection logic for the
SPY options backtest (see agents/OPTIONS_BACKTEST_DESIGN.md).

Like backtest/data.py, this module is agent-mediated: every function here
takes an already-fetched raw MCP response as a plain dict — nothing in
this module calls get_option_instruments or get_option_historicals
itself. A real historical run drives those calls from an interactive
session and passes the raw JSON straight through.
"""

from __future__ import annotations

from datetime import datetime, timedelta


def parse_option_instruments(raw: dict) -> list[dict]:
    """
    Parse a get_option_instruments response into a clean list of
    {id, strike, type, expiration_date}. raw is the unmodified JSON the
    MCP tool returns (raw["data"]["instruments"]); this function does not
    call the tool itself.
    """
    out = []
    for inst in raw.get("data", {}).get("instruments", []):
        out.append({
            "id": inst["id"],
            "strike": float(inst["strike_price"]),
            "type": inst["type"],
            "expiration_date": inst["expiration_date"],
        })
    return out


def parse_option_bars(raw: dict, instrument_id: str) -> list[dict]:
    """
    Parse a get_option_historicals response into a clean, chronologically
    sorted list of {date, open, high, low, close} for `instrument_id`.
    date is a plain "YYYY-MM-DD" string (option bars are UTC-midnight-
    labeled daily bars, same convention backtest/data.py's parse_bars()
    already uses for equity bars).

    raw is the unmodified JSON get_option_historicals returns
    (raw["data"]["results"], a list keyed by instrument_id); this
    function does not call the tool itself.
    """
    results = raw.get("data", {}).get("results", [])
    match = next((r for r in results if r.get("instrument_id") == instrument_id), None)
    if match is None:
        raise ValueError(f"{instrument_id}: no results in get_option_historicals response.")

    bars = []
    for b in match.get("bars", []):
        bars.append({
            "date": b["begins_at"][:10],
            "open": float(b["open_price"]),
            "high": float(b["high_price"]),
            "low": float(b["low_price"]),
            "close": float(b["close_price"]),
        })
    bars.sort(key=lambda b: b["date"])
    return bars


def nearest_expiration(target_date: str, available_expirations: list[str]) -> str | None:
    """
    The smallest date in available_expirations that is >= target_date —
    i.e. the earliest listed expiration that gives AT LEAST the intended
    holding period, never less. Both target_date and every entry in
    available_expirations are "YYYY-MM-DD" strings (safe to compare
    directly, same convention backtest/data.py's bars_through() relies
    on). Returns None if nothing qualifies (every available expiration is
    before target_date) or the list is empty.
    """
    candidates = sorted(d for d in available_expirations if d >= target_date)
    return candidates[0] if candidates else None


def _is_third_friday(d: datetime) -> bool:
    """The standard monthly-options expiration convention: the third
    Friday of the month (weekday() == 4, day-of-month in [15, 21])."""
    return d.weekday() == 4 and 15 <= d.day <= 21


def liquid_expirations_between(start_date: str, end_date: str) -> list[str]:
    """
    Every Friday (inclusive) in [start_date, end_date] as "YYYY-MM-DD"
    strings, sorted — the set of genuinely liquid SPY expirations. This
    deliberately EXCLUDES Monday/Wednesday weeklies: those trade so thin
    at a multi-week lead time that a naive "nearest available expiration"
    snap (this backtest's original v1 approach) would pick one that
    barely has any real trading history yet on the signal date, which is
    exactly what caused most of the 30-45 day horizon's skips in the
    first run (see agents/OPTIONS_BACKTEST_RESULTS.md). The monthly,
    third-Friday expiration is a Friday too, so it's already included
    here — select_liquid_expiration() is what prefers it specifically.
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    # Fridays repeat every 7 days — walk forward from the first Friday
    # on or after start_date.
    offset = (4 - start.weekday()) % 7
    d = start + timedelta(days=offset)
    out = []
    while d <= end:
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=7)
    return out


def select_liquid_expiration(signal_date: str, horizon_days: int, search_window_days: int = 10) -> str | None:
    """
    Pick the expiration to target `horizon_days` out from `signal_date`,
    restricted to liquid Friday expirations (see
    liquid_expirations_between()'s docstring for why). Within the
    qualifying window [target, target + search_window_days], the monthly
    (third-Friday) expiration is preferred if one falls in range — it's
    the most liquid, highest-open-interest contract available — otherwise
    the earliest weekly Friday >= target is used. Mirrors
    nearest_expiration()'s own "earliest that gives AT LEAST the intended
    holding period, never less" principle, just restricted to a liquid
    candidate set instead of every listed date.

    Returns None only if no Friday falls in the search window at all
    (shouldn't happen in practice — Fridays recur every 7 days — but kept
    as an explicit non-guess rather than assuming one exists).
    """
    target = (datetime.strptime(signal_date, "%Y-%m-%d") + timedelta(days=horizon_days)).strftime("%Y-%m-%d")
    window_end = (datetime.strptime(target, "%Y-%m-%d") + timedelta(days=search_window_days)).strftime("%Y-%m-%d")
    candidates = liquid_expirations_between(target, window_end)
    if not candidates:
        return None

    monthlies = [c for c in candidates if _is_third_friday(datetime.strptime(c, "%Y-%m-%d"))]
    return monthlies[0] if monthlies else candidates[0]


def verify_listed_as_of(raw_reference: dict) -> bool:
    """
    True if a specific contract was already listed as of a specific date —
    parses an already-fetched Polygon `/v3/reference/options/contracts`
    response (agent-mediated, same convention as this module's other
    parsers: the actual HTTP call is made by whatever session drives a
    real historical run, this function only reads the JSON it returns).
    Call it with that endpoint's `as_of=<signal_date>` param already
    applied to `raw_reference` — Polygon excludes contracts not yet
    listed (or no longer active) as of that date, so a non-empty
    `results` list IS the point-in-time listing proof. This is the check
    that was previously MISSING: selecting a contract by its (retrospective,
    state="expired") existence alone doesn't prove it was tradeable back
    on the actual signal date — using it without this check is a
    lookahead violation.
    """
    return bool(raw_reference.get("results"))


def estimate_haircut_pct(entry_bar: dict, floor_pct: float, vol_multiplier: float = 0.25,
                          ceiling_pct: float = 0.15) -> float:
    """
    A documented ESTIMATE of the round-trip bid-ask cost for one trade,
    used in place of a flat constant — real NBBO/quote data isn't
    available (Polygon's `/v3/quotes`, `/v2/last/nbbo`, and
    `/v3/snapshot/options` all returned 403 Not Authorized on the current
    plan, confirmed directly, not assumed).

    Uses the entry bar's own high-low range as a real, point-in-time
    (not fabricated, not lookahead — it's the entry day's own public
    trading data) signal of that day's trading friction: wider daily
    range implies a wider realistic spread. `floor_pct` keeps this from
    ever going BELOW the flat baseline this project already uses for the
    calmest, most liquid case (config.OPTIONS_ROUNDTRIP_HAIRCUT_PCT) —
    this can only widen the assumption, never tighten it.
    `vol_multiplier` and `ceiling_pct` are stated policy choices (0.25 and
    15%), not fitted to any result — chosen before this pass's backtest
    was re-run, same as every other cost assumption in this project.

    Known simplification: uses only the ENTRY bar's range for the whole
    trade (both entry and exit fills) rather than each leg's own day —
    simulate_option_trade() doesn't know the exit day until it's inside
    the walk-forward loop, and changing that function's shape to plumb a
    per-day callback through wasn't worth the added surface for this
    pass. Documented here, not hidden.
    """
    if entry_bar["close"] <= 0:
        return floor_pct
    day_range_pct = (entry_bar["high"] - entry_bar["low"]) / entry_bar["close"]
    return min(ceiling_pct, max(floor_pct, vol_multiplier * day_range_pct))


def select_contract(spot: float, side: str, instruments: list[dict]) -> dict | None:
    """
    Pick the ATM contract for a signal: nearest listed strike to `spot`,
    among instruments of the type matching `side` ("buy" -> call, "sell"
    -> put — a bearish signal buys a put, this backtest never shorts).

    instruments: parse_option_instruments() output, already filtered to
    ONE expiration date by the caller (this function doesn't check
    expiration_date — see options_engine.py/run_options_backtest.py for
    where that filtering happens).

    Returns the matched instrument dict, or None if no instrument of the
    needed type is present — the caller skips this signal, never guesses
    a fallback type or strike.
    """
    option_type = "call" if side == "buy" else "put"
    candidates = [i for i in instruments if i["type"] == option_type]
    if not candidates:
        return None
    return min(candidates, key=lambda i: abs(i["strike"] - spot))


def select_spread_strikes(spot: float, option_type: str, instruments: list[dict],
                           width_pct: float = 0.01) -> tuple[dict, dict] | None:
    """
    Pick the SOLD (near-ATM) and BOUGHT (protective) strikes for a
    2-leg vertical credit spread (see
    agents/SPY_OPTIONS_DESIGN.md's Level 2 "Structure set"). Reuses
    select_contract() unchanged for the sold leg's ATM selection — this
    function only adds the protective leg's strike.

    instruments: same convention as select_contract() — already filtered
    to ONE expiration date, this function doesn't check expiration_date.
    option_type: "put" for a bull put spread (protective leg strikes
    BELOW the sold strike, further downside OTM) or "call" for a bear
    call spread (protective leg strikes ABOVE the sold strike, further
    upside OTM) — the caller (backtest.vol_edge_signal.vol_edge_decision())
    already resolved which type the tilt+edge combination calls for.

    width_pct: minimum distance from the sold strike, as a fraction of
    spot (default 1%) — a stated POLICY CHOICE, not derived from data or
    fitted to any result, same caveat class as
    vol_edge_signal.MIN_EDGE_PCT and execution/config.py's
    MIN_VOL_SCALAR. The protective leg is the CLOSEST listed strike that
    still clears this minimum width, not the widest available — a
    tighter spread for less protection, not the reverse.

    Returns (sold_instrument, bought_instrument), or None if no sold
    contract exists, or no protective strike clears width_pct — never
    substitutes a narrower width or a different type. Caller skips this
    signal.
    """
    side = "buy" if option_type == "call" else "sell"
    sold = select_contract(spot, side, instruments)
    if sold is None:
        return None

    target_width = width_pct * spot
    candidates = [i for i in instruments if i["type"] == option_type and i["id"] != sold["id"]]
    if option_type == "put":
        far_enough = [i for i in candidates if sold["strike"] - i["strike"] >= target_width]
        bought = max(far_enough, key=lambda i: i["strike"]) if far_enough else None
    else:
        far_enough = [i for i in candidates if i["strike"] - sold["strike"] >= target_width]
        bought = min(far_enough, key=lambda i: i["strike"]) if far_enough else None

    if bought is None:
        return None
    return sold, bought


if __name__ == "__main__":
    print("Testing parse_option_instruments...")
    raw = {
        "data": {
            "instruments": [
                {"id": "aaa", "strike_price": "620.0000", "type": "call",
                 "expiration_date": "2026-01-16", "state": "expired"},
                {"id": "bbb", "strike_price": "625.0000", "type": "call",
                 "expiration_date": "2026-01-16", "state": "expired"},
            ]
        }
    }
    parsed = parse_option_instruments(raw)
    assert parsed == [
        {"id": "aaa", "strike": 620.0, "type": "call", "expiration_date": "2026-01-16"},
        {"id": "bbb", "strike": 625.0, "type": "call", "expiration_date": "2026-01-16"},
    ], parsed
    print("PASS — parsed 2 instruments with strike as float.")

    print("\nTesting parse_option_instruments with an empty response...")
    assert parse_option_instruments({"data": {"instruments": []}}) == []
    print("PASS — empty instrument list returns [].")

    print("\nTesting parse_option_bars...")
    raw_bars = {
        "data": {
            "results": [
                {
                    "instrument_id": "aaa",
                    "bars": [
                        {"begins_at": "2025-12-02T00:00:00Z", "open_price": "66.360000",
                         "high_price": "68.060000", "low_price": "64.100000", "close_price": "65.960000"},
                        {"begins_at": "2025-12-01T00:00:00Z", "open_price": "64.080000",
                         "high_price": "67.410000", "low_price": "63.860000", "close_price": "64.890000"},
                    ],
                }
            ]
        }
    }
    parsed_bars = parse_option_bars(raw_bars, "aaa")
    assert parsed_bars == [
        {"date": "2025-12-01", "open": 64.08, "high": 67.41, "low": 63.86, "close": 64.89},
        {"date": "2025-12-02", "open": 66.36, "high": 68.06, "low": 64.10, "close": 65.96},
    ], parsed_bars
    print("PASS — parsed and sorted 2 bars chronologically (input was out of order).")

    print("\nTesting parse_option_bars with an unknown instrument_id...")
    try:
        parse_option_bars(raw_bars, "not-a-real-id")
        raise AssertionError("should have raised ValueError")
    except ValueError as e:
        print(f"PASS — raised clearly: {e}")

    print("\nTesting nearest_expiration...")
    expirations = ["2026-01-09", "2026-01-16", "2026-01-23", "2026-01-02"]
    assert nearest_expiration("2026-01-10", expirations) == "2026-01-16"
    print("PASS — picked the earliest listed date on or after the target (never less holding time).")

    assert nearest_expiration("2026-01-16", expirations) == "2026-01-16"
    print("PASS — exact match on the target date itself returns that date.")

    assert nearest_expiration("2026-02-01", expirations) is None
    print("PASS — no expiration on or after target returns None, not a guess.")

    print("\nTesting select_contract...")
    instruments = [
        {"id": "call-615", "strike": 615.0, "type": "call", "expiration_date": "2026-01-16"},
        {"id": "call-620", "strike": 620.0, "type": "call", "expiration_date": "2026-01-16"},
        {"id": "call-625", "strike": 625.0, "type": "call", "expiration_date": "2026-01-16"},
        {"id": "put-620", "strike": 620.0, "type": "put", "expiration_date": "2026-01-16"},
    ]
    picked = select_contract(618.5, "buy", instruments)
    assert picked["id"] == "call-620", picked
    print("PASS — bullish signal picked the nearest CALL strike (620, closest to spot 618.5).")

    picked_put = select_contract(618.5, "sell", instruments)
    assert picked_put["id"] == "put-620", picked_put
    print("PASS — bearish signal picked a PUT, not a short call.")

    assert select_contract(618.5, "sell", instruments[:3]) is None
    print("PASS — no put available returns None, not a fallback to a call.")

    print("\nTesting select_spread_strikes — bull put spread (protective strike BELOW sold)...")
    put_instruments = [
        {"id": f"put-{k}", "strike": float(k), "type": "put", "expiration_date": "2026-01-16"}
        for k in range(590, 621, 5)  # 590, 595, ..., 620
    ]
    # spot=618.5 -> sold ATM put is strike 620 (nearest). 1% width of 618.5 = 6.185,
    # so protective must be <= 620 - 6.185 = 613.815 -> nearest strike clearing that is 610.
    spread = select_spread_strikes(618.5, "put", put_instruments, width_pct=0.01)
    assert spread is not None
    sold, bought = spread
    assert sold["strike"] == 620.0, sold
    assert bought["strike"] == 610.0, bought  # closest strike that still clears the 1% width, not 605 or lower
    print(f"PASS — bull put spread: sold {sold['strike']}, protective (bought) {bought['strike']} "
          f"(below sold, closest strike clearing the 1% width).")

    print("\nTesting select_spread_strikes — bear call spread (protective strike ABOVE sold)...")
    call_instruments = [
        {"id": f"call-{k}", "strike": float(k), "type": "call", "expiration_date": "2026-01-16"}
        for k in range(615, 651, 5)  # 615, 620, ..., 650
    ]
    spread_call = select_spread_strikes(618.5, "call", call_instruments, width_pct=0.01)
    assert spread_call is not None
    sold_c, bought_c = spread_call
    assert sold_c["strike"] == 620.0, sold_c
    assert bought_c["strike"] == 630.0, bought_c  # 620 + 6.185 = 626.185 -> nearest clearing strike is 630
    print(f"PASS — bear call spread: sold {sold_c['strike']}, protective (bought) {bought_c['strike']} "
          f"(above sold, closest strike clearing the 1% width).")

    print("\nTesting select_spread_strikes — no protective strike clears the width returns None...")
    thin_instruments = [
        {"id": "put-620", "strike": 620.0, "type": "put", "expiration_date": "2026-01-16"},
        {"id": "put-618", "strike": 618.0, "type": "put", "expiration_date": "2026-01-16"},  # only 2 wide, too close
    ]
    assert select_spread_strikes(618.5, "put", thin_instruments, width_pct=0.01) is None
    print("PASS — no listed strike clears the minimum width, returns None rather than a too-narrow spread.")

    print("\nTesting liquid_expirations_between...")
    fridays = liquid_expirations_between("2026-05-01", "2026-05-31")
    assert fridays == ["2026-05-01", "2026-05-08", "2026-05-15", "2026-05-22", "2026-05-29"], fridays
    print(f"PASS — every Friday in May 2026, no Mon/Wed weeklies: {fridays}")

    print("\nTesting select_liquid_expiration — prefers the monthly third Friday when in range...")
    # 2026-04-09 + 30 days = 2026-05-09; window [2026-05-09, 2026-05-19] contains
    # both 2026-05-15 (the third Friday / monthly) and 2026-05-08 is NOT in range
    # (before the target), so the only weekly candidate is 2026-05-22-adjacent —
    # the monthly at 2026-05-15 should win.
    exp = select_liquid_expiration("2026-04-09", horizon_days=30)
    assert exp == "2026-05-15", exp
    print(f"PASS — monthly (third Friday) preferred over a plain weekly: {exp}")
    assert exp != "2026-05-11", "must never select a Monday weekly"
    print("PASS — never lands on the thin Monday weekly (2026-05-11) that caused the original skips.")

    print("\nTesting select_liquid_expiration — 7-day horizon falls back to the nearest weekly Friday...")
    exp7 = select_liquid_expiration("2026-04-09", horizon_days=7)
    assert datetime.strptime(exp7, "%Y-%m-%d").weekday() == 4, exp7
    assert exp7 >= "2026-04-16", exp7
    print(f"PASS — nearest qualifying weekly Friday, never a Mon/Wed weekly: {exp7}")

    print("\nTesting verify_listed_as_of...")
    assert verify_listed_as_of({"results": [{"ticker": "O:SPY260515C00700000"}]}) is True
    assert verify_listed_as_of({"results": []}) is False
    assert verify_listed_as_of({"status": "OK"}) is False  # no "results" key at all
    print("PASS — listed iff Polygon's as_of-filtered reference response has a non-empty results list.")

    print("\nTesting estimate_haircut_pct...")
    calm_bar = {"open": 6.0, "high": 6.1, "low": 5.9, "close": 6.0}
    calm = estimate_haircut_pct(calm_bar, floor_pct=0.03)
    assert calm == 0.03, calm
    print(f"PASS — a calm day never goes below the existing flat floor: {calm}")

    wild_bar = {"open": 6.0, "high": 13.0, "low": 5.0, "close": 8.0}
    wild = estimate_haircut_pct(wild_bar, floor_pct=0.03)
    assert wild > 0.03, wild  # (13-5)/8 = 1.0 * 0.25 = 0.25, capped at ceiling
    assert wild == 0.15, wild
    print(f"PASS — a wild day widens the estimate above the floor, capped at the ceiling: {wild}")
