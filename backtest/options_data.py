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
