# SPY Options Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pure, testable backtest that replays the council's real historical SPY signals as simulated options trades (real historical option prices, not modeled), producing win-rate/P&L evidence before anything touches live or paper trading.

**Architecture:** Three layers of pure, synthetic-data-testable Python (contract selection, trade simulation, metrics aggregation) plus one thin orchestration module that consumes already-fetched raw MCP JSON rather than calling any MCP tool itself — mirrors `backtest/data.py`'s existing "this module never calls Robinhood itself" pattern. No live MCP call is made by any code in this plan; a real historical run is a separate, later, interactive step (same split `agents/BACKTEST_DESIGN.md` already uses between its deterministic engine and its interactive Fundamentals pre-pass).

**Tech Stack:** Python 3.14 stdlib only (no new dependencies). This project has no pytest — every module is self-tested via its own `if __name__ == "__main__":` block with assertions and printed PASS/FAIL, run directly (`python3 -m backtest.options_data` etc.). Follow that exact convention, not pytest.

## Global Constraints

- Never call `place_option_order` or any other order-placing MCP tool anywhere in this plan — this is a backtest only (see `agents/OPTIONS_BACKTEST_DESIGN.md`'s Scope section).
- Never modify `agents/judge.py` — the technicals-only decision logic for SPY is a new, separate function, not a change to the existing conjunctive gate.
- Every new numeric constant goes in `execution/config.py`, next to the existing exit thresholds, with a comment explaining why it's separate from the equity constants (see `agents/OPTIONS_BACKTEST_DESIGN.md`'s "Exit and cost modeling" sections for the exact reasoning to reuse).
- Cost haircut: exactly 3% round-trip (`OPTIONS_ROUNDTRIP_HAIRCUT_PCT = 0.03`), applied as half against the trader on entry and half on exit — locked in the spec, not a free parameter.
- Every function that can't find usable data (no contract, no bars, missing entry bar) returns `None` and the caller skips — never guesses, never substitutes a default. Same fail-safe convention `automation/run_pass.py` already uses throughout this codebase.

---

### Task 1: Contract selection and MCP response parsing

**Files:**
- Create: `backtest/options_data.py`
- Test: inline `if __name__ == "__main__":` block in the same file (this project's established convention — see `backtest/data.py`'s own self-test block for the pattern to match)

**Interfaces:**
- Consumes: nothing from other tasks — this is the foundational module.
- Produces: `parse_option_instruments(raw: dict) -> list[dict]`, `parse_option_bars(raw: dict, instrument_id: str) -> list[dict]`, `nearest_expiration(target_date: str, available_expirations: list[str]) -> str | None`, `select_contract(spot: float, side: str, instruments: list[dict]) -> dict | None`. Tasks 2 and 4 import all four.

- [ ] **Step 1: Write `backtest/options_data.py` with `parse_option_instruments` and its self-test**

```python
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
```

- [ ] **Step 2: Run it to verify the self-test passes**

Run: `python3 -m backtest.options_data`
Expected:
```
Testing parse_option_instruments...
PASS — parsed 2 instruments with strike as float.

Testing parse_option_instruments with an empty response...
PASS — empty instrument list returns [].
```

- [ ] **Step 3: Add `parse_option_bars` and its self-test**

Add to `backtest/options_data.py`, after `parse_option_instruments`:

```python
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
```

Add to the `if __name__ == "__main__":` block, after the existing tests:

```python
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
```

- [ ] **Step 4: Run it to verify all tests pass**

Run: `python3 -m backtest.options_data`
Expected: the two prior PASS lines, plus:
```
Testing parse_option_bars...
PASS — parsed and sorted 2 bars chronologically (input was out of order).

Testing parse_option_bars with an unknown instrument_id...
PASS — raised clearly: not-a-real-id: no results in get_option_historicals response.
```

- [ ] **Step 5: Add `nearest_expiration` and its self-test**

Add to `backtest/options_data.py`:

```python
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
```

Add to the self-test block:

```python
    print("\nTesting nearest_expiration...")
    expirations = ["2026-01-09", "2026-01-16", "2026-01-23", "2026-01-02"]
    assert nearest_expiration("2026-01-10", expirations) == "2026-01-16"
    print("PASS — picked the earliest listed date on or after the target (never less holding time).")

    assert nearest_expiration("2026-01-16", expirations) == "2026-01-16"
    print("PASS — exact match on the target date itself returns that date.")

    assert nearest_expiration("2026-02-01", expirations) is None
    print("PASS — no expiration on or after target returns None, not a guess.")
```

- [ ] **Step 6: Run it to verify all tests pass**

Run: `python3 -m backtest.options_data`
Expected: all six prior PASS lines, plus three more for `nearest_expiration`.

- [ ] **Step 7: Add `select_contract` and its self-test**

Add to `backtest/options_data.py`:

```python
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
```

(Note: that last `if __name__ == "__main__":` line replaces the existing one earlier in the file from Step 1 — move the whole self-test block that already exists to after this new function, don't duplicate the `if __name__` line. The file should have exactly one `if __name__ == "__main__":` block at the very end, after all four functions.)

Add to the self-test block:

```python
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
```

- [ ] **Step 8: Run it to verify all tests pass**

Run: `python3 -m backtest.options_data`
Expected: all nine prior PASS lines, plus three more for `select_contract` (12 total).

- [ ] **Step 9: Commit**

```bash
git add backtest/options_data.py
git commit -m "Add options_data.py: MCP response parsing and ATM contract selection for the SPY options backtest"
```

---

### Task 2: Options-specific config constants and trade simulation

**Files:**
- Modify: `execution/config.py` (append new constants; existing values unchanged)
- Create: `backtest/options_engine.py`
- Test: inline `if __name__ == "__main__":` block in `backtest/options_engine.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly (this task's functions are self-contained), but Task 4 will pass Task 1's `select_contract`/`parse_option_bars` output into this task's `simulate_option_trade`.
- Produces: `execution.config.OPTIONS_STOP_LOSS_PCT`, `OPTIONS_TAKE_PROFIT_PCT`, `OPTIONS_ROUNDTRIP_HAIRCUT_PCT`, `OPTIONS_CONTRACT_MULTIPLIER` (floats). `backtest.options_engine.technicals_only_decision(technicals: dict, regime: dict, quantity: float = 1) -> dict` and `backtest.options_engine.simulate_option_trade(entry_close: float, bars_after_entry: list[dict], side: str, strike: float, expiration_date: str, spot_at_expiration: float, stop_loss_pct: float, take_profit_pct: float, haircut_pct: float) -> dict`. Task 4 imports both.

- [ ] **Step 1: Add the new constants to `execution/config.py`**

Add at the end of the file, after the existing `AUTOMATION_DRY_RUN` block and before `LOG_DIR`:

```python
# --- Options backtest (backtest/options_engine.py) --------------------------
# See agents/OPTIONS_BACKTEST_DESIGN.md. Separate from STOP_LOSS_PCT/
# TAKE_PROFIT_PCT above: options move far more than the underlying share
# price, so reusing the equity thresholds directly on premium would be a
# real modeling error, not just imprecision — same reasoning
# CONVICTION_DROP_THRESHOLD's own comment already gives for keeping
# entry/exit conviction bars independently tunable. Policy choices, not
# derived from data, same caveat class as MIN_VOL_SCALAR above.
OPTIONS_STOP_LOSS_PCT: float = 0.50     # close if premium falls 50% from entry
OPTIONS_TAKE_PROFIT_PCT: float = 1.00   # close if premium doubles from entry

# Robinhood's option historicals carry no bid/ask or volume (confirmed
# directly — see agents/OPTIONS_BACKTEST_DESIGN.md's "Data feasibility"),
# so there's no real spread to derive a cost model from. 3% round-trip is
# a deliberate, stated approximation of a realistic ATM SPY spread — SPY
# options are the most liquid options market that exists, so this isn't
# arbitrary conservatism. Applied as half against the trader on entry,
# half on exit.
OPTIONS_ROUNDTRIP_HAIRCUT_PCT: float = 0.03

# Standard US equity/ETF option contract size — one contract controls 100
# shares, so premium P&L per contract is (exit - entry) * this.
OPTIONS_CONTRACT_MULTIPLIER: float = 100.0
```

- [ ] **Step 2: Verify config.py still imports cleanly**

Run: `python3 -c "from execution import config; print(config.OPTIONS_STOP_LOSS_PCT, config.OPTIONS_TAKE_PROFIT_PCT, config.OPTIONS_ROUNDTRIP_HAIRCUT_PCT, config.OPTIONS_CONTRACT_MULTIPLIER)"`
Expected: `0.5 1.0 0.03 100.0`

- [ ] **Step 3: Write `backtest/options_engine.py` with `technicals_only_decision` and its self-test**

```python
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
```

- [ ] **Step 4: Run it to verify the self-test passes**

Run: `python3 -m backtest.options_engine`
Expected:
```
Testing technicals_only_decision...
PASS — confident bullish technicals + tradeable regime -> buy.
PASS — non-tradeable regime forces hold regardless of technicals.
PASS — technicals below confidence threshold -> hold, not a weak buy.
```

- [ ] **Step 5: Add `simulate_option_trade` and its self-test**

Add to `backtest/options_engine.py`, after `technicals_only_decision` and before the `if __name__` block:

```python
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
    realized_pnl is per ONE contract, in dollars (already multiplied by
    config.OPTIONS_CONTRACT_MULTIPLIER-equivalent — caller passes that
    multiplier's actual value baked into how it reads this result, or see
    run_options_backtest.py for the exact usage).
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
```

Add to the self-test block, after the `technicals_only_decision` tests:

```python
    print("\nTesting simulate_option_trade — stop-loss path...")
    bars_falling = [
        {"date": "2026-01-06", "open": 5.0, "high": 5.2, "low": 4.5, "close": 4.8},
        {"date": "2026-01-07", "open": 4.8, "high": 4.9, "low": 3.0, "close": 3.2},
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
```

- [ ] **Step 6: Run it to verify all tests pass**

Run: `python3 -m backtest.options_engine`
Expected: the three `technicals_only_decision` PASS lines, plus four more for `simulate_option_trade` (7 total), each printing its result dict.

- [ ] **Step 7: Commit**

```bash
git add execution/config.py backtest/options_engine.py
git commit -m "Add options-specific config thresholds and trade simulation (stop/target/expiration)"
```

---

### Task 3: Metrics aggregation

**Files:**
- Create: `backtest/options_metrics.py`
- Test: inline `if __name__ == "__main__":` block in the same file

**Interfaces:**
- Consumes: `backtest.metrics.wilson_ci` (existing, unchanged).
- Produces: `backtest.options_metrics.summarize_option_trades(trades: list[dict]) -> dict`. Task 4 imports this.

- [ ] **Step 1: Write `backtest/options_metrics.py` with its self-test**

```python
"""
backtest/options_metrics.py — aggregate reporting over simulated option
trades (backtest/options_engine.py's simulate_option_trade() output).

Reuses backtest.metrics.wilson_ci() rather than reimplementing a
confidence interval calculation — same reasoning applies here as there:
a bare win rate with no interval is misleading at the trade counts a
single-symbol backtest will realistically produce.
"""

from __future__ import annotations

from . import metrics as equity_metrics


def summarize_option_trades(trades: list[dict]) -> dict:
    """
    trades: a list of simulate_option_trade() outputs. Returns count,
    total realized P&L, win rate, and its 95% Wilson score confidence
    interval — same field names as backtest.metrics.account_summary()'s
    win-rate fields, so options results read consistently next to the
    equity backtest's own numbers.
    """
    n = len(trades)
    wins = sum(1 for t in trades if t["realized_pnl"] > 0)
    total_pnl = round(sum(t["realized_pnl"] for t in trades), 2)
    win_rate = round(wins / n, 4) if n > 0 else None
    return {
        "count": n,
        "wins": wins,
        "losses": n - wins,
        "total_realized_pnl": total_pnl,
        "win_rate": win_rate,
        "win_rate_ci_95": equity_metrics.wilson_ci(wins, n),
    }


if __name__ == "__main__":
    print("Testing summarize_option_trades...")
    trades = [
        {"realized_pnl": 3.5},
        {"realized_pnl": -1.2},
        {"realized_pnl": 2.0},
        {"realized_pnl": -0.8},
    ]
    summary = summarize_option_trades(trades)
    assert summary["count"] == 4
    assert summary["wins"] == 2
    assert summary["losses"] == 2
    assert summary["total_realized_pnl"] == 3.5, summary
    assert summary["win_rate"] == 0.5, summary
    assert summary["win_rate_ci_95"] is not None
    print(f"PASS — 4 trades, 2 wins, total P&L $3.50: {summary}")

    print("\nTesting summarize_option_trades with no trades...")
    empty_summary = summarize_option_trades([])
    assert empty_summary["count"] == 0
    assert empty_summary["win_rate"] is None
    assert empty_summary["win_rate_ci_95"] is None
    print(f"PASS — empty trade list returns None win rate, not a divide-by-zero: {empty_summary}")
```

- [ ] **Step 2: Run it to verify all tests pass**

Run: `python3 -m backtest.options_metrics`
Expected:
```
Testing summarize_option_trades...
PASS — 4 trades, 2 wins, total P&L $3.50: {...}

Testing summarize_option_trades with no trades...
PASS — empty trade list returns None win rate, not a divide-by-zero: {...}
```

- [ ] **Step 3: Commit**

```bash
git add backtest/options_metrics.py
git commit -m "Add options_metrics.py: win-rate and P&L aggregation reusing wilson_ci"
```

---

### Task 4: Orchestration over a pre-fetched signal manifest

**Files:**
- Create: `backtest/run_options_backtest.py`
- Test: inline `if __name__ == "__main__":` block in the same file

**Interfaces:**
- Consumes: `options_data.parse_option_instruments`, `options_data.select_contract`, `options_data.parse_option_bars` (Task 1); `options_engine.simulate_option_trade` (Task 2); `options_metrics.summarize_option_trades` (Task 3); `execution.config.OPTIONS_STOP_LOSS_PCT`/`OPTIONS_TAKE_PROFIT_PCT`/`OPTIONS_ROUNDTRIP_HAIRCUT_PCT`.
- Produces: `run_one_signal(signal: dict) -> dict | None`, `run_backtest(signals: list[dict]) -> dict`. This is the final module in this plan — nothing downstream consumes it yet (a real historical run, using real fetched signals, is future work, not part of this plan).

- [ ] **Step 1: Write `backtest/run_options_backtest.py` with `run_one_signal` and its self-test**

```python
"""
backtest/run_options_backtest.py — ties options_data + options_engine +
options_metrics together over a pre-fetched run manifest.

Agent-mediated, same reasoning as backtest/data.py's parse_bars(): the
actual MCP tool calls (get_option_instruments, get_option_historicals)
happen in whatever interactive session drives a real historical run —
this module only ever consumes their already-fetched raw JSON, passed in
via each signal's raw_instruments/raw_historicals fields. Nothing in this
module calls an MCP tool, and nothing in this module calls any order-
placing tool. See agents/OPTIONS_BACKTEST_DESIGN.md.
"""

from __future__ import annotations

from execution import config

from . import options_data, options_engine, options_metrics


def run_one_signal(signal: dict) -> dict | None:
    """
    signal: {
        "date": "YYYY-MM-DD",             # entry/signal date
        "side": "buy" | "sell",           # from technicals_only_decision()'s action
        "spot": float,                    # SPY close on `date`
        "expiration_date": "YYYY-MM-DD",  # already resolved via options_data.nearest_expiration()
        "spot_at_expiration": float,      # SPY close on expiration_date
        "raw_instruments": dict,          # get_option_instruments() raw response for expiration_date
        "raw_historicals": dict,          # get_option_historicals() raw response for the selected contract
    }

    Returns simulate_option_trade() output with realized_pnl scaled to
    per-contract dollars (config.OPTIONS_CONTRACT_MULTIPLIER applied here,
    not inside simulate_option_trade() — see that function's docstring),
    or None if no usable contract or bar data was found — the caller
    skips this signal, same fail-safe convention as everywhere else in
    this project. Never guesses a
    substitute contract or a fabricated price.

    NOTE on options_data.parse_option_bars(): it raises ValueError if
    contract["id"] isn't found in signal["raw_historicals"]'s results —
    deliberately, matching backtest/data.py's parse_bars() precedent, so a
    genuine caller-wiring bug (fetching the wrong contract's historicals)
    fails loudly rather than silently. But a contract Robinhood genuinely
    has zero historical bars for (common for thin/never-traded strikes) is
    NOT a bug — it's a normal "skip this signal" case per this plan's
    Global Constraints. This function is the seam where that distinction
    gets drawn: catch the exception here, at the orchestration layer,
    rather than changing parse_option_bars() itself (resolved as a Task 1
    reviewer finding — see the plan's task-1-report.md if it still exists).
    """
    instruments = options_data.parse_option_instruments(signal["raw_instruments"])
    contract = options_data.select_contract(signal["spot"], signal["side"], instruments)
    if contract is None:
        return None

    try:
        bars = options_data.parse_option_bars(signal["raw_historicals"], contract["id"])
    except ValueError:
        return None
    if not bars:
        return None

    entry_bar = next((b for b in bars if b["date"] == signal["date"]), None)
    if entry_bar is None:
        return None

    bars_after_entry = [b for b in bars if b["date"] > signal["date"]]

    trade = options_engine.simulate_option_trade(
        entry_close=entry_bar["close"],
        bars_after_entry=bars_after_entry,
        side=signal["side"],
        strike=contract["strike"],
        expiration_date=signal["expiration_date"],
        spot_at_expiration=signal["spot_at_expiration"],
        stop_loss_pct=config.OPTIONS_STOP_LOSS_PCT,
        take_profit_pct=config.OPTIONS_TAKE_PROFIT_PCT,
        haircut_pct=config.OPTIONS_ROUNDTRIP_HAIRCUT_PCT,
    )
    # simulate_option_trade() returns realized_pnl as a raw PER-SHARE
    # premium delta (see its own docstring) — this is the seam where that
    # gets scaled to actual per-contract dollars. entry_fill/exit_fill stay
    # unscaled (they're genuinely per-share quoted prices; only P&L is a
    # settled dollar amount).
    trade["realized_pnl"] = round(trade["realized_pnl"] * config.OPTIONS_CONTRACT_MULTIPLIER, 2)
    return trade


if __name__ == "__main__":
    print("Testing run_one_signal — end to end with a fabricated signal...")
    signal = {
        "date": "2026-01-06",
        "side": "buy",
        "spot": 618.5,
        "expiration_date": "2026-01-16",
        "spot_at_expiration": 615.0,
        "raw_instruments": {
            "data": {"instruments": [
                {"id": "call-620", "strike_price": "620.0000", "type": "call",
                 "expiration_date": "2026-01-16"},
                {"id": "put-620", "strike_price": "620.0000", "type": "put",
                 "expiration_date": "2026-01-16"},
            ]}
        },
        "raw_historicals": {
            "data": {"results": [
                {"instrument_id": "call-620", "bars": [
                    {"begins_at": "2026-01-06T00:00:00Z", "open_price": "6.0", "high_price": "6.5",
                     "low_price": "5.8", "close_price": "6.0"},
                    {"begins_at": "2026-01-07T00:00:00Z", "open_price": "6.0", "high_price": "6.3",
                     "low_price": "2.5", "close_price": "2.7"},
                ]}
            ]}
        },
    }
    result = run_one_signal(signal)
    assert result is not None
    assert result["exit_reason"] == "stop_loss", result
    print(f"PASS — full pipeline wired correctly, stopped out: {result}")

    print("\nTesting run_one_signal — no matching contract type (skip, not crash)...")
    signal_no_put = dict(signal, side="sell")
    signal_no_put["raw_instruments"] = {
        "data": {"instruments": [
            {"id": "call-620", "strike_price": "620.0000", "type": "call",
             "expiration_date": "2026-01-16"},
        ]}
    }
    assert run_one_signal(signal_no_put) is None
    print("PASS — bearish signal with no put available returns None, not a crash or a substitute.")

    print("\nTesting run_one_signal — entry date missing from bars (skip, not crash)...")
    signal_missing_entry = dict(signal, date="2026-01-05")
    assert run_one_signal(signal_missing_entry) is None
    print("PASS — no bar on the entry date returns None, never fabricates an entry price.")

    print("\nTesting run_one_signal — contract has zero historical bars anywhere (skip, not crash)...")
    signal_no_historicals = dict(signal)
    signal_no_historicals["raw_historicals"] = {
        "data": {"results": [{"instrument_id": "some-other-contract-id", "bars": []}]}
    }
    assert run_one_signal(signal_no_historicals) is None
    print("PASS — options_data.parse_option_bars()'s ValueError (instrument_id not in results) "
          "is caught here and treated as a skip, not an unhandled crash.")
```

- [ ] **Step 2: Run it to verify all tests pass**

Run: `python3 -m backtest.run_options_backtest`
Expected:
```
Testing run_one_signal — end to end with a fabricated signal...
PASS — full pipeline wired correctly, stopped out: {...}

Testing run_one_signal — no matching contract type (skip, not crash)...
PASS — bearish signal with no put available returns None, not a crash or a substitute.

Testing run_one_signal — entry date missing from bars (skip, not crash)...
PASS — no bar on the entry date returns None, never fabricates an entry price.

Testing run_one_signal — contract has zero historical bars anywhere (skip, not crash)...
PASS — options_data.parse_option_bars()'s ValueError (instrument_id not in results) is caught here and treated as a skip, not an unhandled crash.
```

- [ ] **Step 3: Add `run_backtest` and its self-test**

Add to `backtest/run_options_backtest.py`, after `run_one_signal` and before the `if __name__` block:

```python
def run_backtest(signals: list[dict]) -> dict:
    """
    Runs every signal through run_one_signal(), collects whatever trades
    were actually produced (skipped signals excluded, never guessed), and
    reports options_metrics.summarize_option_trades() over them.
    """
    trades = [t for t in (run_one_signal(s) for s in signals) if t is not None]
    return {
        "signals_total": len(signals),
        "signals_skipped": len(signals) - len(trades),
        "trades": trades,
        "summary": options_metrics.summarize_option_trades(trades),
    }
```

Add to the self-test block, after the existing `run_one_signal` tests:

```python
    print("\nTesting run_backtest — mix of a usable and an unusable signal...")
    report = run_backtest([signal, signal_no_put, signal_missing_entry])
    assert report["signals_total"] == 3
    assert report["signals_skipped"] == 2
    assert report["summary"]["count"] == 1
    assert report["summary"]["losses"] == 1
    print(f"PASS — 1 of 3 signals produced a trade, 2 skipped and excluded from the summary: {report['summary']}")
```

- [ ] **Step 4: Run it to verify all tests pass**

Run: `python3 -m backtest.run_options_backtest`
Expected: the four prior PASS lines, plus one more confirming `run_backtest`'s aggregation (5 total).

- [ ] **Step 5: Commit**

```bash
git add backtest/run_options_backtest.py
git commit -m "Add run_options_backtest.py: orchestrates a pre-fetched signal manifest into a P&L report"
```

---

## What this plan does NOT cover (by design)

A real historical run — actually calling `get_option_instruments`/`get_option_historicals` for real SPY council signals over the confirmed ~1-year window, assembling the manifest `run_backtest()` expects, and reading the resulting report — is a separate, later, interactive step, same as `agents/BACKTEST_DESIGN.md`'s own Fundamentals pre-pass is documented but not code. Every module built in this plan is fully tested with synthetic data and ready for that step once it happens.
