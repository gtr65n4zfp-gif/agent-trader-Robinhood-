"""
backtest/vix_data.py — parses CBOE's public daily-price CSVs (VIX,
VIX9D, VIX3M — see agents/SPY_OPTIONS_DESIGN.md's "Data feasibility")
into this project's standard bar shape, so
backtest.data.bars_through() truncates them the same no-lookahead way
it truncates SPY's own bars — one choke point, not a second
implementation of point-in-time discipline.

Like every other data-touching module in this project, this module
fetches nothing itself. The CSV is a plain, unauthenticated HTTP GET
(cdn.cboe.com/api/global/us_indices/daily_prices/{VIX,VIX9D,VIX3M}_History.csv)
— genuinely simpler than every other agent-mediated parser here, since it
needs no MCP session and no API key — but the actual fetch still happens
in whatever script or session drives a real run; this module only ever
parses the already-fetched raw text.
"""

from __future__ import annotations

from datetime import datetime


def parse_cboe_csv(raw_csv_text: str) -> list[dict]:
    """
    Parse a CBOE daily-price CSV (`DATE,OPEN,HIGH,LOW,CLOSE`, dates as
    MM/DD/YYYY — confirmed directly against real VIX/VIX9D/VIX3M
    responses, not guessed) into a clean, chronologically sorted list of
    {date, open, high, low, close} — date normalized to "YYYY-MM-DD",
    matching backtest/data.py's parse_bars() convention exactly, so
    backtest_data.bars_through() works on this list unmodified.

    Raises ValueError on a header it doesn't recognize, rather than
    silently misparsing a schema change — same "fail loud on a genuine
    format mismatch" precedent as options_data.parse_option_bars()'s
    unknown-instrument-id case.
    """
    lines = [line for line in raw_csv_text.strip().splitlines() if line.strip()]
    if not lines:
        return []

    header = lines[0].strip().split(",")
    if header != ["DATE", "OPEN", "HIGH", "LOW", "CLOSE"]:
        raise ValueError(f"unrecognized CBOE CSV header: {header!r}")

    out = []
    for line in lines[1:]:
        date_str, open_s, high_s, low_s, close_s = line.strip().split(",")
        date = datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
        out.append({
            "date": date,
            "open": float(open_s), "high": float(high_s),
            "low": float(low_s), "close": float(close_s),
        })
    out.sort(key=lambda b: b["date"])
    return out


def value_as_of(bars: list[dict], as_of: str) -> float | None:
    """
    The latest CLOSE on or before `as_of` — point-in-time, via the same
    backtest_data.bars_through() choke point every other indicator in
    this project goes through. Returns None if no bar exists on or
    before `as_of` (e.g. a date before this index's own inception —
    VIX9D only starts 2011-01-04, VIX3M only starts 2009-09-18) — never
    fabricates a value, caller skips this signal.
    """
    from . import data as backtest_data
    truncated = backtest_data.bars_through(bars, as_of)
    if not truncated:
        return None
    return truncated[-1]["close"]


if __name__ == "__main__":
    print("Testing parse_cboe_csv...")
    raw = "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/1990,17.240000,17.240000,17.240000,17.240000\n01/03/1990,18.190000,18.190000,18.190000,18.190000\n"
    parsed = parse_cboe_csv(raw)
    assert parsed == [
        {"date": "1990-01-02", "open": 17.24, "high": 17.24, "low": 17.24, "close": 17.24},
        {"date": "1990-01-03", "open": 18.19, "high": 18.19, "low": 18.19, "close": 18.19},
    ], parsed
    print(f"PASS — parsed 2 rows, MM/DD/YYYY normalized to YYYY-MM-DD: {parsed}")

    print("\nTesting parse_cboe_csv — out-of-order input still sorts chronologically...")
    raw_unsorted = "DATE,OPEN,HIGH,LOW,CLOSE\n01/03/1990,18.19,18.19,18.19,18.19\n01/02/1990,17.24,17.24,17.24,17.24\n"
    parsed_unsorted = parse_cboe_csv(raw_unsorted)
    assert [b["date"] for b in parsed_unsorted] == ["1990-01-02", "1990-01-03"], parsed_unsorted
    print("PASS — sorted chronologically regardless of input order.")

    print("\nTesting parse_cboe_csv — unrecognized header raises, not silently misparsed...")
    try:
        parse_cboe_csv("DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n01/02/1990,1,1,1,1,1\n")
        raise AssertionError("should have raised ValueError")
    except ValueError as e:
        print(f"PASS — raised clearly: {e}")

    print("\nTesting value_as_of...")
    bars = parse_cboe_csv(raw)
    assert value_as_of(bars, "1990-01-02") == 17.24
    assert value_as_of(bars, "1990-01-15") == 18.19  # no bar that day -- latest ON OR BEFORE
    assert value_as_of(bars, "1989-12-31") is None  # before this series even starts
    print("PASS — latest close on or before as_of; None if nothing exists yet, not a guess.")

    # --- Real CBOE data from here on ---------------------------------------
    SCRATCH = "/private/tmp/claude-501/-Users-ethandungo-agent-trader/f77a7381-786c-45b3-8f03-7b93713c619c/scratchpad"
    print(f"\nTesting parse_cboe_csv on real VIX/VIX9D/VIX3M CSVs...")
    with open(f"{SCRATCH}/VIX_History.csv") as f:
        vix_bars = parse_cboe_csv(f.read())
    with open(f"{SCRATCH}/VIX9D_History.csv") as f:
        vix9d_bars = parse_cboe_csv(f.read())
    with open(f"{SCRATCH}/VIX3M_History.csv") as f:
        vix3m_bars = parse_cboe_csv(f.read())
    assert vix_bars[0]["date"] == "1990-01-02", vix_bars[0]
    assert vix9d_bars[0]["date"] == "2011-01-04", vix9d_bars[0]
    assert vix3m_bars[0]["date"] == "2009-09-18", vix3m_bars[0]
    print(f"PASS — VIX from {vix_bars[0]['date']} ({len(vix_bars)} rows), "
          f"VIX9D from {vix9d_bars[0]['date']} ({len(vix9d_bars)} rows), "
          f"VIX3M from {vix3m_bars[0]['date']} ({len(vix3m_bars)} rows).")

    print("\nTesting value_as_of on the real April 2025 SPY selloff date...")
    vix_at_crash = value_as_of(vix_bars, "2025-04-07")
    vix9d_at_crash = value_as_of(vix9d_bars, "2025-04-07")
    vix_at_calm = value_as_of(vix_bars, "2025-02-03")
    assert vix_at_crash is not None and vix_at_calm is not None
    assert vix_at_crash > vix_at_calm * 1.5, (vix_at_calm, vix_at_crash)  # implied vol should spike hard during a real crash
    print(f"PASS — real VIX close on 2025-02-03 (calm): {vix_at_calm}, on 2025-04-07 (crash bottom): "
          f"{vix_at_crash} (VIX9D same date: {vix9d_at_crash}) — implied vol genuinely spiked, not a flat/garbage read.")

    print("\nAll vix_data tests passed.")
