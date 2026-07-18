# Live SPY Options Paper-Trading Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the fully isolated, paper-only live SPY options trading pass described in `docs/superpowers/specs/2026-07-17-live-spy-options-design.md` — two concurrent horizon tracks (7-day, 30-45-day) on the already-backtested technicals+regime-only strategy.

**Architecture:** A dedicated options risk gate (`agents/options_risk_vetoer.py`) feeds a two-slot paper broker (`execution/options_paper_broker.py`, contracts not shares, keyed by horizon track), driven by a two-phase automation entrypoint (`automation/run_options_pass.py`: `plan_options_pass()` computes the signal and names what live data is needed, `execute_options_pass()` takes that plan plus the calling agent's live fetches and does the real work) — proven end-to-end by `automation/demo_run_options_pass.py`.

**Tech Stack:** Python 3, no new dependencies (reuses `agents.regime`, `agents.technicals`, `backtest.options_engine.technicals_only_decision()`, `backtest.options_data`, `execution.robinhood`, `execution.trade_log`, all unchanged).

## Global Constraints

- Paper only. No task in this plan calls `place_option_order` or any other order-placing tool.
- `SPY` only, two horizon tracks (`"7"`, `"30"`), one open position per track — never a second position in an occupied track.
- Decision logic is `backtest.options_engine.technicals_only_decision()`, unchanged — not the forecast-seat wrapper (no validated model exists).
- Fully isolated from the equity watchlist: own portfolio file (`logs/options_paper_portfolio.json`), own trade log (`logs/options_trades.jsonl`), own dry-run flag (`OPTIONS_AUTOMATION_DRY_RUN`, default `True`). Never touches `logs/paper_portfolio.json` or `logs/trades.jsonl`.
- New config constants, values locked in (not implementation-time decisions): `OPTIONS_PAPER_STARTING_CASH = 10000`, `OPTIONS_MAX_TRADE_USD = 2500`, `OPTIONS_MAX_POSITION_PCT = 0.25`, `OPTIONS_MAX_TRADES_PER_DAY = 2`, `OPTIONS_AUTOMATION_DRY_RUN = True`.
- The options risk vetoer reuses `config.MAX_DAILY_LOSS_PCT` (the existing equity constant) for its daily-loss check — no separate options daily-loss constant.
- Closing a position is never blocked by the risk vetoer (capital preservation always wins) — mirrors the equity vetoer's sell-side behavior.
- Real bid/ask preferred for fills when a live quote has it; falls back to `mark_price ± half of OPTIONS_ROUNDTRIP_HAIRCUT_PCT` (existing constant) only when bid/ask is absent — the fallback must be distinctly logged (`used_real_spread: bool`), never silently indistinguishable from a real-spread fill.
- The automation entrypoint is **two pure functions**, not one: `plan_options_pass()` makes no MCP/live calls; `execute_options_pass()` takes the plan plus already-fetched live data. Neither function fetches anything itself.
- Polygon is used only as a diagnostic fallback (logged context on a skip), never for quotes or fills, and never unblocks a trade.
- Every new module gets a `if __name__ == "__main__":` self-test block with `assert`/`print("PASS — ...")` using an em dash (—, U+2014) — matching every existing module's convention. No pytest.

---

### Task 1: Config constants + `.gitignore`

**Files:**
- Modify: `execution/config.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `config.OPTIONS_PAPER_STARTING_CASH`, `config.OPTIONS_MAX_TRADE_USD`, `config.OPTIONS_MAX_POSITION_PCT`, `config.OPTIONS_MAX_TRADES_PER_DAY`, `config.OPTIONS_AUTOMATION_DRY_RUN` — all consumed by Tasks 2 and 3.

- [ ] **Step 1: Confirm the constants don't exist yet**

Run: `cd /Users/ethandungo/agent-trader && python3 -c "from execution import config; config.OPTIONS_PAPER_STARTING_CASH"`
Expected: `AttributeError: module 'execution.config' has no attribute 'OPTIONS_PAPER_STARTING_CASH'`

- [ ] **Step 2: Add the constants**

In `execution/config.py`, immediately after the existing block ending in `OPTIONS_CONTRACT_MULTIPLIER: float = 100.0` (and before the `LOG_DIR = ...` line), insert:

```python

# --- Live SPY options paper-trading pass (automation/run_options_pass.py) --
# See docs/superpowers/specs/2026-07-17-live-spy-options-design.md. A fully
# isolated account -- separate cash pool, separate risk caps from the
# equity watchlist's, sized around real per-contract costs observed in
# the options backtest ($467-$1,626 per contract; the equity account's
# $1,000 MAX_TRADE_USD would have rejected several of those real trades).
OPTIONS_PAPER_STARTING_CASH: float = 10000.0
OPTIONS_MAX_TRADE_USD: float = 2500.0

# Higher than equity's MAX_POSITION_PCT (0.10) deliberately: with at most
# one position per track (two tracks total), this cap mostly matters
# after a drawdown has already shrunk the account -- the trade-size cap
# above is what actually binds most of the time. Two concurrent tracks
# means combined exposure can reach ~50% of account value even with each
# position individually capped at 25% -- a stated tradeoff, not an
# oversight (see the design doc's "Known limitations").
OPTIONS_MAX_POSITION_PCT: float = 0.25

# One qualifying signal legitimately opening both horizon tracks the same
# day is expected behavior, not churn -- this cap's real job is blocking
# same-day re-entry right after a same-day stop-out in a single track.
OPTIONS_MAX_TRADES_PER_DAY: int = 2

# Same pattern as AUTOMATION_DRY_RUN above: every decision still made and
# logged in full, but OptionsPaperBroker methods are never called until
# this is deliberately flipped to False, one line, never as a side effect.
OPTIONS_AUTOMATION_DRY_RUN: bool = True
```

- [ ] **Step 3: Add the new log files to `.gitignore`**

In `.gitignore`, the "Logs and local data" section currently reads:

```
logs/*.log
logs/*.json
!logs/paper_portfolio.json
logs/*.jsonl
!logs/trades.jsonl
logs/backtests/
logs/automation_runs/
*.sqlite
```

Change it to:

```
logs/*.log
logs/*.json
!logs/paper_portfolio.json
!logs/options_paper_portfolio.json
logs/*.jsonl
!logs/trades.jsonl
!logs/options_trades.jsonl
logs/backtests/
logs/automation_runs/
*.sqlite
```

- [ ] **Step 4: Verify the constants are importable and `.gitignore` is correct**

Run: `cd /Users/ethandungo/agent-trader && python3 -c "from execution import config; print(config.OPTIONS_PAPER_STARTING_CASH, config.OPTIONS_MAX_TRADE_USD, config.OPTIONS_MAX_POSITION_PCT, config.OPTIONS_MAX_TRADES_PER_DAY, config.OPTIONS_AUTOMATION_DRY_RUN)"`
Expected: `10000.0 2500.0 0.25 2 True`

Run: `cd /Users/ethandungo/agent-trader && git check-ignore -v logs/options_paper_portfolio.json logs/options_trades.jsonl`
Expected: no output (both paths are explicitly un-ignored by the `!` rules, so `git check-ignore` reports nothing — confirm this by also running `git check-ignore -v logs/some_other_file.json` in the same breath and seeing THAT one report a match, proving the un-ignore rules are specific, not accidentally blanket).

- [ ] **Step 5: Commit**

```bash
cd /Users/ethandungo/agent-trader
git add execution/config.py .gitignore
git commit -m "Add config constants for the live SPY options paper-trading pass

Sized around real per-contract costs from the options backtest
(\$467-\$1,626), isolated from the equity account's own numbers. Also
un-ignores the new options portfolio/trade-log files so they persist
across cloud automation runs, same as the equity ones already do."
```

---

### Task 2: `agents/options_risk_vetoer.py`

**Files:**
- Create: `agents/options_risk_vetoer.py`

**Interfaces:**
- Consumes: `execution.config.OPTIONS_MAX_TRADE_USD`, `OPTIONS_MAX_POSITION_PCT`, `OPTIONS_MAX_TRADES_PER_DAY`, `MAX_DAILY_LOSS_PCT` (existing).
- Produces: `review(action: str, contract_cost: float, account: dict, trades_today: int | None = None, daily_loss_pct: float | None = None) -> dict`, returning `{seat, approved, reason, checks, detail, action, contract_cost}`.

- [ ] **Step 1: Confirm the module doesn't exist yet**

Run: `cd /Users/ethandungo/agent-trader && python3 -c "from agents import options_risk_vetoer"`
Expected: `ModuleNotFoundError: No module named 'agents.options_risk_vetoer'`

- [ ] **Step 2: Create `agents/options_risk_vetoer.py`**

```python
"""
agents/options_risk_vetoer.py -- dedicated risk gate for the live SPY
options pass (see
docs/superpowers/specs/2026-07-17-live-spy-options-design.md).

Mirrors agents.risk_vetoer's principles, scaled for options units
(quantity * OPTIONS_CONTRACT_MULTIPLIER * price, not shares * price) --
a separate module rather than an extension of the equity vetoer, since
this account only ever holds SPY options and doesn't need sector
concentration or ATR-based share-count sizing, both meaningless here.

Same discipline as the equity vetoer: pure veto power, never originates
a trade, never raises for a failed check -- returns approved=False with
a reason. The caller (execution.options_paper_broker.OptionsPaperBroker)
is what raises OptionsTradeError.

Closing a position is never blocked by any of these caps -- capital
preservation always wins, same reasoning as the equity vetoer's sell
side.
"""

from execution import config


def review(
    action: str,
    contract_cost: float,
    account: dict,
    trades_today: int | None = None,
    daily_loss_pct: float | None = None,
) -> dict:
    """
    Check a proposed options trade against the risk caps.

    action: "open" or "close" -- only "open" is ever subject to the
    caps below.
    contract_cost: quantity * config.OPTIONS_CONTRACT_MULTIPLIER *
    entry_fill -- the actual dollar cost of this trade.
    account: an OptionsPaperBroker.account(current_marks) snapshot (or
    an equivalently shaped dict) valued at current marks, so
    total_value is accurate for the position-percentage check.
    trades_today: how many buys/sells have already executed today.
    Optional -- omit to skip the daily trade-count breaker.
    daily_loss_pct: how far current total_value sits below today's
    starting equity (e.g. 0.03 = 3% down since the day began). Optional
    -- omit to skip the daily-loss breaker. Checked against
    config.MAX_DAILY_LOSS_PCT -- the existing equity constant, reused
    here rather than duplicated with a separate options-specific one.

    Returns a decision dict with `approved`, a human-readable `reason`,
    the individual `checks`, and the numbers behind them in `detail`.
    """
    if action not in ("open", "close"):
        raise ValueError(f"action must be 'open' or 'close', got {action!r}")

    detail = {"contract_cost": round(contract_cost, 2)}

    if action == "close":
        return {
            "seat": "options_risk_vetoer", "approved": True,
            "reason": "closing a position is never blocked",
            "checks": {}, "detail": detail,
            "action": action, "contract_cost": round(contract_cost, 2),
        }

    checks = {"within_trade_cap": contract_cost <= config.OPTIONS_MAX_TRADE_USD}
    detail["max_trade_usd"] = config.OPTIONS_MAX_TRADE_USD

    total_value = account.get("total_value", 0)
    position_pct = contract_cost / total_value if total_value > 0 else float("inf")
    checks["within_position_pct"] = position_pct <= config.OPTIONS_MAX_POSITION_PCT
    detail["projected_position_pct"] = round(position_pct, 4)
    detail["max_position_pct"] = config.OPTIONS_MAX_POSITION_PCT

    if trades_today is not None:
        checks["within_daily_trade_limit"] = trades_today < config.OPTIONS_MAX_TRADES_PER_DAY
        detail["trades_today"] = trades_today
        detail["max_trades_per_day"] = config.OPTIONS_MAX_TRADES_PER_DAY

    if daily_loss_pct is not None:
        checks["within_daily_loss_limit"] = daily_loss_pct < config.MAX_DAILY_LOSS_PCT
        detail["daily_loss_pct"] = round(daily_loss_pct, 4)
        detail["max_daily_loss_pct"] = config.MAX_DAILY_LOSS_PCT

    approved = all(checks.values())
    failed = [name for name, ok in checks.items() if not ok]
    reason = "within all risk limits" if approved else f"failed: {', '.join(failed)}"

    return {
        "seat": "options_risk_vetoer", "approved": approved, "reason": reason,
        "checks": checks, "detail": detail,
        "action": action, "contract_cost": round(contract_cost, 2),
    }


if __name__ == "__main__":
    demo_account = {"cash": 8000.0, "positions_value": 2000.0, "total_value": 10000.0}

    print("Testing review -- small open, well within every cap (should pass)...")
    d1 = review("open", 500.0, demo_account, trades_today=0, daily_loss_pct=0.0)
    assert d1["approved"] is True, d1
    print(f"PASS — small open approved: {d1}")

    print("\nTesting review -- open exceeding the trade-cost cap (should fail)...")
    d2 = review("open", 3000.0, demo_account)
    assert d2["approved"] is False and "within_trade_cap" in d2["reason"], d2
    print(f"PASS — over OPTIONS_MAX_TRADE_USD blocked: {d2}")

    print("\nTesting review -- open under the trade cap but over position-pct (should fail)...")
    small_account = {"cash": 900.0, "positions_value": 0.0, "total_value": 900.0}
    d3 = review("open", 300.0, small_account)  # 300/900 = 33% > 25%
    assert d3["approved"] is False and "within_position_pct" in d3["reason"], d3
    print(f"PASS — over OPTIONS_MAX_POSITION_PCT blocked: {d3}")

    print("\nTesting review -- daily trade-count breaker (should fail)...")
    d4 = review("open", 500.0, demo_account, trades_today=2)
    assert d4["approved"] is False and "within_daily_trade_limit" in d4["reason"], d4
    print(f"PASS — trades_today at the OPTIONS_MAX_TRADES_PER_DAY cap blocked: {d4}")

    print("\nTesting review -- daily-loss breaker (should fail)...")
    d5 = review("open", 500.0, demo_account, daily_loss_pct=0.06)  # cap is 0.05
    assert d5["approved"] is False and "within_daily_loss_limit" in d5["reason"], d5
    print(f"PASS — daily_loss_pct over MAX_DAILY_LOSS_PCT blocked: {d5}")

    print("\nTesting review -- closing is never blocked, even far over every cap...")
    d6 = review("close", 999999.0, demo_account, trades_today=999, daily_loss_pct=0.99)
    assert d6["approved"] is True, d6
    print(f"PASS — close approved unconditionally: {d6}")

    print("\nTesting review -- rejects an invalid action...")
    try:
        review("hold", 500.0, demo_account)
        raise AssertionError("should have raised ValueError")
    except ValueError as e:
        print(f"PASS — raised clearly: {e}")
```

- [ ] **Step 3: Run it and verify all PASS**

Run: `cd /Users/ethandungo/agent-trader && python3 -m agents.options_risk_vetoer`
Expected: seven `PASS` lines print, no `AssertionError`, exit code 0.

- [ ] **Step 4: Commit**

```bash
cd /Users/ethandungo/agent-trader
git add agents/options_risk_vetoer.py
git commit -m "Add agents/options_risk_vetoer.py: dedicated risk gate for options units

Mirrors agents.risk_vetoer's principles (trade-cost cap, position-pct
cap, daily trade-count and loss breakers) without the equity-only
sector/ATR-scaling logic that doesn't apply to a single-symbol options
account. Closing a position is never blocked, same as the equity
vetoer's sell side."
```

---

### Task 3: `execution/options_paper_broker.py`

**Files:**
- Create: `execution/options_paper_broker.py`

**Interfaces:**
- Consumes: `agents.options_risk_vetoer.review()`, `execution.trade_log.record()` / `count_trades_today()`, `execution.config.OPTIONS_PAPER_STARTING_CASH` / `OPTIONS_CONTRACT_MULTIPLIER` / `OPTIONS_ROUNDTRIP_HAIRCUT_PCT` / `LOG_DIR`.
- Produces: `OPTIONS_TRADE_LOG_PATH` (module constant), `parse_option_quote(raw_quote: dict, instrument_id: str) -> dict | None`, `entry_fill_from_quote(quote: dict) -> tuple[float, bool]`, `exit_fill_from_quote(quote: dict) -> tuple[float, bool]`, `OptionsTradeError` (exception), `OptionsPaperBroker` class with `open_positions: dict[str, dict | None]` (keys `"7"`, `"30"`), `log_path` (read-only property, resolved never-`None` path — Task 4's `execute_options_pass()` reads this to keep its own direct logging in the same file as the broker's), `account(current_marks=None) -> dict`, `buy_to_open(track, contract_id, strike, option_type, expiration_date, quantity, entry_fill, reason="", current_marks=None, now=None) -> dict`, `close_position(track, exit_fill, reason="", now=None) -> dict`.

- [ ] **Step 1: Confirm the module doesn't exist yet**

Run: `cd /Users/ethandungo/agent-trader && python3 -c "from execution.options_paper_broker import OptionsPaperBroker"`
Expected: `ModuleNotFoundError: No module named 'execution.options_paper_broker'`

- [ ] **Step 2: Create `execution/options_paper_broker.py`**

```python
"""
execution/options_paper_broker.py -- a fully simulated options trading
account for the live SPY options pass (see
docs/superpowers/specs/2026-07-17-live-spy-options-design.md).

Mirrors execution.paper_broker.PaperBroker's structure, but units are
CONTRACTS, not shares, and state tracks two independent, optional
position slots keyed by horizon track ("7", "30") rather than a dict of
symbols -- this account only ever trades SPY, on the one-position-per-
track concurrency policy the design locks in. Never touches real money;
paper only, same as PaperBroker.
"""

import json
import os
from datetime import datetime, timezone

from agents import options_risk_vetoer

from . import config, trade_log

_OPTIONS_PORTFOLIO_PATH = os.path.join(config.LOG_DIR, "options_paper_portfolio.json")

# Public (no leading underscore) and imported by automation/run_options_pass.py's
# execute_options_pass(), via OptionsPaperBroker.log_path below -- NOT via this
# constant directly, so there is exactly one source of truth for "which log
# path is this broker actually using" and no chance of the two drifting apart.
# Deliberately a DIFFERENT file from trade_log's own equity default
# (logs/trades.jsonl) -- unlike PaperBroker, this account must never fall
# through to the equity log just because no override was given.
OPTIONS_TRADE_LOG_PATH = os.path.join(config.LOG_DIR, "options_trades.jsonl")

_TRACKS = ("7", "30")


class OptionsTradeError(Exception):
    """Raised when an order can't be placed (insufficient cash, risk cap,
    or the track is already occupied)."""


def parse_option_quote(raw_quote: dict, instrument_id: str) -> dict | None:
    """
    Parse a get_option_quotes response for one instrument into
    {mark_price, bid_price, ask_price} (bid_price/ask_price are None if
    the live quote didn't include them). Returns None if instrument_id
    isn't in the response's results -- caller skips, never fabricates.

    Field names (mark_price/bid_price/ask_price) follow the snake-case
    plus _price convention already confirmed for strike_price/open_price/
    etc. elsewhere in this project's Robinhood parsing -- verify against
    a real response at first live run and adjust here if the actual
    schema differs, same discipline as every other agent-mediated parser
    in this project.
    """
    results = raw_quote.get("data", {}).get("results", [])
    match = next((r for r in results if r.get("instrument_id") == instrument_id), None)
    if match is None:
        return None
    return {
        "mark_price": float(match["mark_price"]) if match.get("mark_price") is not None else None,
        "bid_price": float(match["bid_price"]) if match.get("bid_price") is not None else None,
        "ask_price": float(match["ask_price"]) if match.get("ask_price") is not None else None,
    }


def entry_fill_from_quote(quote: dict) -> tuple[float, bool]:
    """
    Returns (fill_price, used_real_spread). Prefers the real ask (the
    natural spread against the trader, more honest than any estimate);
    falls back to mark_price widened by half the backtest's documented
    round-trip haircut if ask isn't present in the live quote.
    """
    if quote.get("ask_price") is not None:
        return quote["ask_price"], True
    return quote["mark_price"] * (1 + config.OPTIONS_ROUNDTRIP_HAIRCUT_PCT / 2), False


def exit_fill_from_quote(quote: dict) -> tuple[float, bool]:
    """Same as entry_fill_from_quote(), mirrored for the sell side: real
    bid preferred, mark_price narrowed by half the haircut otherwise."""
    if quote.get("bid_price") is not None:
        return quote["bid_price"], True
    return quote["mark_price"] * (1 - config.OPTIONS_ROUNDTRIP_HAIRCUT_PCT / 2), False


class OptionsPaperBroker:
    def __init__(self, portfolio_path: str | None = None, log_path: str | None = None):
        """See execution.paper_broker.PaperBroker's __init__ docstring
        for why portfolio_path/log_path exist -- same isolation
        reasoning; almost nothing should pass these."""
        self._portfolio_path = portfolio_path or _OPTIONS_PORTFOLIO_PATH
        # `or OPTIONS_TRADE_LOG_PATH`, NOT a bare pass-through like
        # PaperBroker's own `self._log_path = log_path` -- PaperBroker
        # sharing trade_log's built-in default (logs/trades.jsonl) is
        # correct FOR EQUITY, but this account must never fall through to
        # that same equity default when no override is given.
        self._log_path = log_path or OPTIONS_TRADE_LOG_PATH
        self.cash: float = config.OPTIONS_PAPER_STARTING_CASH
        self.open_positions: dict[str, dict | None] = {t: None for t in _TRACKS}
        self.peak_equity: float = config.OPTIONS_PAPER_STARTING_CASH
        self.day_date: str | None = None
        self.day_start_equity: float = config.OPTIONS_PAPER_STARTING_CASH
        self._load()

    @property
    def log_path(self) -> str:
        """The resolved trade-log path this broker actually writes to
        (never None -- __init__ already applied the OPTIONS_TRADE_LOG_PATH
        fallback). automation.run_options_pass.execute_options_pass() reads
        this so its own direct trade_log.record() calls (no-ops, skips,
        dry-run logging) land in the exact same file as this broker's own
        buy/sell/veto records, by construction, not by the caller
        remembering to keep two paths in sync."""
        return self._log_path

    def _load(self) -> None:
        if os.path.exists(self._portfolio_path):
            with open(self._portfolio_path) as f:
                data = json.load(f)
            self.cash = data["cash"]
            self.open_positions = data.get("open_positions", {t: None for t in _TRACKS})
            self.peak_equity = data.get("peak_equity", config.OPTIONS_PAPER_STARTING_CASH)
            self.day_date = data.get("day_date")
            self.day_start_equity = data.get("day_start_equity", config.OPTIONS_PAPER_STARTING_CASH)

    def _save(self) -> None:
        os.makedirs(config.LOG_DIR, exist_ok=True)
        with open(self._portfolio_path, "w") as f:
            json.dump(
                {
                    "cash": self.cash,
                    "open_positions": self.open_positions,
                    "peak_equity": self.peak_equity,
                    "day_date": self.day_date,
                    "day_start_equity": self.day_start_equity,
                },
                f, indent=2,
            )

    def account(self, current_marks: dict[str, float] | None = None) -> dict:
        """current_marks: optional {track: current mark_price} for
        occupied tracks -- falls back to each position's own entry_fill
        (unrealized P&L reads as flat) for any occupied track not in
        current_marks."""
        current_marks = current_marks or {}
        positions_value = 0.0
        for track, pos in self.open_positions.items():
            if pos is None:
                continue
            mark = current_marks.get(track, pos["entry_fill"])
            positions_value += pos["quantity"] * config.OPTIONS_CONTRACT_MULTIPLIER * mark
        return {
            "cash": round(self.cash, 2),
            "open_positions": {t: (dict(p) if p else None) for t, p in self.open_positions.items()},
            "positions_value": round(positions_value, 2),
            "total_value": round(self.cash + positions_value, 2),
            "starting_cash": config.OPTIONS_PAPER_STARTING_CASH,
        }

    def _roll_day_and_check_risk(self, action: str, contract_cost: float,
                                  current_marks: dict[str, float] | None, now: datetime) -> None:
        account = self.account(current_marks)
        if account["total_value"] > self.peak_equity:
            self.peak_equity = account["total_value"]
            self._save()

        today = now.date().isoformat()
        if self.day_date != today:
            self.day_date = today
            self.day_start_equity = account["total_value"]
            self._save()
        daily_loss_pct = (
            (self.day_start_equity - account["total_value"]) / self.day_start_equity
            if self.day_start_equity > 0 else 0.0
        )
        trades_today = trade_log.count_trades_today(log_path=self._log_path, now=now)

        decision = options_risk_vetoer.review(
            action, contract_cost, account,
            trades_today=trades_today, daily_loss_pct=daily_loss_pct,
        )
        if not decision["approved"]:
            trade_log.record(
                "veto", "SPY", 0, None, paper=True, reason=decision["reason"],
                extra={"seat": "options_risk_vetoer", "checks": decision["checks"], "detail": decision["detail"]},
                log_path=self._log_path,
            )
            raise OptionsTradeError(f"Options risk vetoer blocked this trade: {decision['reason']}")

    def buy_to_open(self, track: str, contract_id: str, strike: float, option_type: str,
                     expiration_date: str, quantity: int, entry_fill: float, reason: str = "",
                     current_marks: dict[str, float] | None = None, now: datetime | None = None) -> dict:
        if track not in _TRACKS:
            raise ValueError(f"unknown track {track!r}, expected one of {_TRACKS}")
        if self.open_positions[track] is not None:
            raise OptionsTradeError(f"track {track} already has an open position")

        now = now or datetime.now(timezone.utc)
        contract_cost = quantity * config.OPTIONS_CONTRACT_MULTIPLIER * entry_fill
        self._roll_day_and_check_risk("open", contract_cost, current_marks, now)

        if contract_cost > self.cash:
            raise OptionsTradeError(f"insufficient cash: need {contract_cost:.2f}, have {self.cash:.2f}")

        self.cash -= contract_cost
        position = {
            "contract_id": contract_id, "strike": strike, "type": option_type,
            "expiration_date": expiration_date, "quantity": quantity,
            "entry_fill": entry_fill, "entry_date": now.date().isoformat(),
        }
        self.open_positions[track] = position
        self._save()
        trade_log.record(
            "buy", "SPY", quantity, entry_fill, paper=True, reason=reason,
            extra={"track": track, "option_type": option_type, "strike": strike,
                   "expiration_date": expiration_date, "contract_id": contract_id},
            log_path=self._log_path,
        )
        return dict(position)

    def close_position(self, track: str, exit_fill: float, reason: str = "",
                        now: datetime | None = None) -> dict:
        if track not in _TRACKS:
            raise ValueError(f"unknown track {track!r}, expected one of {_TRACKS}")
        position = self.open_positions[track]
        if position is None:
            raise OptionsTradeError(f"track {track} has no open position to close")

        now = now or datetime.now(timezone.utc)
        proceeds = position["quantity"] * config.OPTIONS_CONTRACT_MULTIPLIER * exit_fill
        cost_basis = position["quantity"] * config.OPTIONS_CONTRACT_MULTIPLIER * position["entry_fill"]
        realized_pnl = round(proceeds - cost_basis, 2)

        self.cash += proceeds
        self.open_positions[track] = None
        self._save()
        trade_log.record(
            "sell", "SPY", position["quantity"], exit_fill, paper=True, reason=reason,
            extra={"track": track, "realized_pnl": realized_pnl, "option_type": position["type"],
                   "strike": position["strike"], "expiration_date": position["expiration_date"],
                   "contract_id": position["contract_id"]},
            log_path=self._log_path,
        )
        return {**position, "exit_fill": exit_fill, "realized_pnl": realized_pnl}


if __name__ == "__main__":
    import tempfile

    print("Testing the default log path is options-specific, never the equity default...")
    assert OPTIONS_TRADE_LOG_PATH.endswith("options_trades.jsonl"), OPTIONS_TRADE_LOG_PATH
    # portfolio_path points at a nonexistent file so __init__'s _load() is a
    # no-op (no read, no write) -- this only exercises log_path resolution.
    no_override = OptionsPaperBroker(portfolio_path=os.path.join(tempfile.gettempdir(), "unused.json"))
    assert no_override.log_path == OPTIONS_TRADE_LOG_PATH, no_override.log_path
    print(f"PASS — a broker built with no log_path override resolves to the options-specific file: {no_override.log_path}")

    print("\nTesting parse_option_quote -- found and not-found cases...")
    raw = {"data": {"results": [{"instrument_id": "abc", "mark_price": "6.50",
                                  "bid_price": "6.40", "ask_price": "6.60"}]}}
    q = parse_option_quote(raw, "abc")
    assert q == {"mark_price": 6.5, "bid_price": 6.4, "ask_price": 6.6}, q
    assert parse_option_quote(raw, "not-there") is None
    print(f"PASS — parsed a matching instrument, None for a non-matching one: {q}")

    print("\nTesting entry_fill_from_quote / exit_fill_from_quote -- real spread preferred...")
    fill, real = entry_fill_from_quote(q)
    assert fill == 6.6 and real is True, (fill, real)
    fill2, real2 = exit_fill_from_quote(q)
    assert fill2 == 6.4 and real2 is True, (fill2, real2)
    print(f"PASS — entry fills at ask (6.6), exit fills at bid (6.4), both flagged real: {fill}, {fill2}")

    print("\nTesting entry_fill_from_quote -- haircut fallback when bid/ask absent...")
    mark_only = {"mark_price": 6.0, "bid_price": None, "ask_price": None}
    fill3, real3 = entry_fill_from_quote(mark_only)
    assert real3 is False, (fill3, real3)
    assert abs(fill3 - 6.09) < 1e-9, fill3  # 6.0 * (1 + 0.03/2) = 6.09
    print(f"PASS — falls back to mark + half the haircut, flagged not-real: {fill3}")

    with tempfile.TemporaryDirectory() as tmp:
        portfolio_path = os.path.join(tmp, "options_portfolio.json")
        log_path = os.path.join(tmp, "options_trades.jsonl")

        print("\nTesting OptionsPaperBroker.buy_to_open -- opens a position, deducts cash...")
        broker = OptionsPaperBroker(portfolio_path=portfolio_path, log_path=log_path)
        start_cash = broker.cash
        pos = broker.buy_to_open("7", "contract-1", 685.0, "call", "2026-08-01", 1, 6.0, reason="test entry")
        assert broker.open_positions["7"] == pos, pos
        assert abs(broker.cash - (start_cash - 600.0)) < 1e-6, broker.cash  # 1 * 100 * 6.0
        print(f"PASS — opened track 7, cash reduced by contract cost: cash={broker.cash}")

        print("\nTesting buy_to_open -- rejects a second position in the same track...")
        try:
            broker.buy_to_open("7", "contract-2", 690.0, "call", "2026-08-01", 1, 5.0)
            raise AssertionError("should have raised OptionsTradeError")
        except OptionsTradeError as e:
            print(f"PASS — raised clearly: {e}")

        print("\nTesting buy_to_open -- a different track (30) can still open...")
        pos30 = broker.buy_to_open("30", "contract-3", 700.0, "put", "2026-09-01", 1, 10.0, reason="test entry 2")
        assert broker.open_positions["30"] == pos30, pos30
        print(f"PASS — track 30 opened independently of track 7: {pos30}")

        print("\nTesting close_position -- credits cash, computes realized_pnl, clears the slot...")
        cash_before_close = broker.cash
        closed = broker.close_position("7", 9.0, reason="test exit")
        assert broker.open_positions["7"] is None, broker.open_positions
        assert closed["realized_pnl"] == 300.0, closed  # (9.0 - 6.0) * 100
        assert abs(broker.cash - (cash_before_close + 900.0)) < 1e-6, broker.cash
        print(f"PASS — track 7 closed, +$300 realized, cash credited: {closed}")

        print("\nTesting close_position -- raises when the track has nothing open...")
        try:
            broker.close_position("7", 9.0)
            raise AssertionError("should have raised OptionsTradeError")
        except OptionsTradeError as e:
            print(f"PASS — raised clearly: {e}")

        print("\nTesting buy_to_open -- vetoed when it exceeds OPTIONS_MAX_TRADE_USD...")
        try:
            # entry_fill * 100 = 3000, over the $2500 cap
            broker.buy_to_open("7", "contract-4", 685.0, "call", "2026-08-01", 1, 30.0)
            raise AssertionError("should have raised OptionsTradeError")
        except OptionsTradeError as e:
            assert "risk vetoer" in str(e).lower(), e
            print(f"PASS — risk vetoer blocked an oversized trade: {e}")

        print("\nTesting persistence -- a fresh broker instance loads the same state from disk...")
        reloaded = OptionsPaperBroker(portfolio_path=portfolio_path, log_path=log_path)
        assert reloaded.open_positions["30"] == pos30, reloaded.open_positions
        assert reloaded.open_positions["7"] is None, reloaded.open_positions
        print(f"PASS — reloaded broker matches: cash={reloaded.cash}, open_positions={reloaded.open_positions}")
```

- [ ] **Step 3: Run it and verify all PASS**

Run: `cd /Users/ethandungo/agent-trader && python3 -m execution.options_paper_broker`
Expected: eleven `PASS` lines print, no `AssertionError`, exit code 0.

- [ ] **Step 4: Commit**

```bash
cd /Users/ethandungo/agent-trader
git add execution/options_paper_broker.py
git commit -m "Add execution/options_paper_broker.py: two-track options paper account

Mirrors PaperBroker's structure for contracts instead of shares -- two
independent position slots keyed by horizon track, sharing one cash
pool and running agents.options_risk_vetoer before every open. Real
bid/ask preferred for fills, documented haircut fallback otherwise,
distinctly flagged either way."
```

---

### Task 4: `automation/run_options_pass.py` (two phases)

**Files:**
- Create: `automation/run_options_pass.py`

**Interfaces:**
- Consumes: `agents.regime.regime_stance()`, `agents.technicals.build_view()`, `backtest.options_engine.technicals_only_decision()`, `backtest.options_data.select_liquid_expiration()` / `parse_option_instruments()` / `select_contract()`, `execution.robinhood.get_quote()` / `get_quote_age_minutes()` / `get_atr_pct()` / `get_rsi()` / `get_ema()` / `get_regime_ema()`, `execution.config.assert_paper_mode()` / `market_is_open()` / `MAX_QUOTE_AGE_MINUTES` / `OPTIONS_AUTOMATION_DRY_RUN` / `OPTIONS_STOP_LOSS_PCT` / `OPTIONS_TAKE_PROFIT_PCT`, `execution.trade_log.record()`, `execution.options_paper_broker.OptionsPaperBroker` / `OptionsTradeError` / `parse_option_quote()` / `entry_fill_from_quote()` / `exit_fill_from_quote()`.
- Produces: `plan_options_pass(bundle: dict, broker: OptionsPaperBroker, now: datetime | None = None) -> dict` (keys: `no_op`, `no_op_reason`, `regime`, `technicals`, `decision`, `spot`, `tracks: {"7": {held_contract_id, entry_lookup}, "30": {...}}`); `execute_options_pass(plan: dict, broker: OptionsPaperBroker, live_data: dict, now: datetime | None = None) -> dict` (keys: `started_at`, `dry_run`, `no_op`, `exits`, `entries`).

- [ ] **Step 1: Confirm the module doesn't exist yet**

Run: `cd /Users/ethandungo/agent-trader && python3 -c "from automation.run_options_pass import plan_options_pass"`
Expected: `ModuleNotFoundError: No module named 'automation.run_options_pass'`

- [ ] **Step 2: Create `automation/run_options_pass.py`**

```python
"""
automation/run_options_pass.py -- the live SPY options pass, two phases
(see docs/superpowers/specs/2026-07-17-live-spy-options-design.md for
the full design and why this is two functions instead of one, unlike
automation/run_pass.py).

Phase A, plan_options_pass(): given SPY's already-fetched indicator
bundle and current broker state, computes the signal and returns a plan
naming exactly what live data Phase B will need -- no MCP calls happen
here.

Phase B, execute_options_pass(): given that plan plus the live data the
calling agent fetched in response to it (quotes for held contracts,
instrument lookups for prospective new ones), does the real exit/entry
work.

Same fail-safe rules as automation/run_pass.py:
  1. config.assert_paper_mode() first.
  2. Market-hours guard -- outside hours, the whole pass is a no-op.
  3. SPY data-sanity check -- bad/stale data no-ops the whole pass (a
     single-symbol account has no "skip this one, evaluate others" case
     the way the multi-symbol equity watchlist does).
  4. config.OPTIONS_AUTOMATION_DRY_RUN (default True) -- every decision
     still made and logged in full, but OptionsPaperBroker methods are
     never called.
  5. When execution IS armed, every order still routes through
     OptionsPaperBroker, which still runs the options risk vetoer --
     automation adds no path around it.
"""

from datetime import datetime, timezone

from agents import regime, technicals
from backtest import options_data, options_engine
from execution import config, robinhood, trade_log
from execution.options_paper_broker import (
    OptionsPaperBroker,
    OptionsTradeError,
    entry_fill_from_quote,
    exit_fill_from_quote,
    parse_option_quote,
)
from execution.robinhood import RobinhoodDataError

_TRACKS = {"7": 7, "30": 30}

BUNDLE_HELP = """\
bundle must be shaped:
    "quote", "atr", "rsi", "ema", "regime_ema":
        raw MCP responses for SPY (same shape run_pass.py's BUNDLE_HELP
        describes for these five keys) -- no fundamentals, no sector;
        SPY has neither.
"""

LIVE_DATA_HELP = """\
live_data must be shaped:
    "exit_quotes": {"7": raw get_option_quotes response | None, "30": ...}
    "entry_instruments": {"7": raw get_option_instruments response | None, "30": ...}
    "entry_quotes": {"7": raw get_option_quotes response | None, "30": ...}
    "polygon_fallback": {"7": raw Polygon reference response | None, "30": ...}
Fetch exactly what plan_options_pass()'s returned plan names under
tracks[track]["held_contract_id"] (exit_quotes) and
tracks[track]["entry_lookup"] (entry_instruments, then entry_quotes for
whichever contract that lookup resolves to). polygon_fallback is
diagnostic only -- only fetch it if entry_instruments came back empty
for a track that had an entry_lookup.
"""


def _extract_spy_data(bundle: dict, now: datetime) -> dict | None:
    """Same parsing/staleness discipline as run_pass.py's
    _extract_symbol_data(), for SPY only. Returns None -- never raises --
    on anything unparseable or stale.

    now is threaded into get_quote_age_minutes() explicitly (run_pass.py's
    own equivalent doesn't do this, relying on both sides defaulting to
    real wall-clock time in production) -- required here because this
    function must also support a fabricated, non-"now" `now` for
    deterministic testing (see automation/demo_run_options_pass.py),
    where the two independently defaulting to real time would silently
    break the staleness check against a fixed test timestamp."""
    try:
        price = robinhood.get_quote("SPY", bundle["quote"])
        age_minutes = robinhood.get_quote_age_minutes("SPY", bundle["quote"], now=now)
        if age_minutes > config.MAX_QUOTE_AGE_MINUTES:
            return None
        atr_pct = robinhood.get_atr_pct("SPY", price, bundle["atr"])
        rsi = robinhood.get_rsi("SPY", bundle["rsi"])
        ema = robinhood.get_ema("SPY", bundle["ema"])
        regime_ema = robinhood.get_regime_ema("SPY", bundle["regime_ema"])
    except (RobinhoodDataError, KeyError, ValueError, TypeError):
        return None
    return {"price": price, "atr_pct": atr_pct, "rsi": rsi, "ema": ema, "regime_ema": regime_ema}


def _empty_tracks() -> dict:
    return {t: {"held_contract_id": None, "entry_lookup": None} for t in _TRACKS}


def plan_options_pass(bundle: dict, broker: OptionsPaperBroker, now: datetime | None = None) -> dict:
    """Phase A -- see module docstring. Makes no MCP calls."""
    now = now or datetime.now(timezone.utc)
    config.assert_paper_mode()

    if not config.market_is_open(now):
        return {
            "no_op": True,
            "no_op_reason": "market-hours guard: outside US equity regular trading hours",
            "regime": None, "technicals": None, "decision": None, "spot": None,
            "tracks": _empty_tracks(),
        }

    data = _extract_spy_data(bundle, now)
    if data is None:
        return {
            "no_op": True,
            "no_op_reason": "SPY data sanity check failed -- skipped, never evaluated or traded",
            "regime": None, "technicals": None, "decision": None, "spot": None,
            "tracks": _empty_tracks(),
        }

    reg = regime.regime_stance("SPY", data["price"], ema=data["regime_ema"], atr_pct=data["atr_pct"])
    tech = technicals.build_view("SPY", data["price"], ema=data["ema"], rsi=data["rsi"], atr_pct=data["atr_pct"])
    decision = options_engine.technicals_only_decision(tech, reg)

    tracks_plan = {}
    for track, horizon_days in _TRACKS.items():
        held = broker.open_positions[track]
        held_contract_id = held["contract_id"] if held is not None else None

        entry_lookup = None
        if held is None and decision["action"] in ("buy", "sell"):
            expiration = options_data.select_liquid_expiration(now.date().isoformat(), horizon_days)
            if expiration is not None:
                entry_lookup = {
                    "expiration_date": expiration,
                    "option_type": "call" if decision["action"] == "buy" else "put",
                    "strike_guess": round(data["price"]),
                }
        tracks_plan[track] = {"held_contract_id": held_contract_id, "entry_lookup": entry_lookup}

    return {
        "no_op": False, "no_op_reason": None,
        "regime": reg, "technicals": tech, "decision": decision, "spot": data["price"],
        "tracks": tracks_plan,
    }


def execute_options_pass(plan: dict, broker: OptionsPaperBroker, live_data: dict,
                          now: datetime | None = None) -> dict:
    """Phase B -- see module docstring and LIVE_DATA_HELP for live_data's shape."""
    now = now or datetime.now(timezone.utc)
    # Read from the broker, not a module-level default computed separately --
    # this is what guarantees these direct trade_log.record() calls always
    # land in the exact same file as the broker's own buy/sell/veto records,
    # never the equity logs/trades.jsonl. See OptionsPaperBroker.log_path's
    # own docstring.
    log_path = broker.log_path
    summary = {
        "started_at": now.isoformat(), "dry_run": config.OPTIONS_AUTOMATION_DRY_RUN,
        "no_op": plan["no_op"], "exits": [], "entries": [],
    }

    if plan["no_op"]:
        trade_log.record(
            "automation_noop", "SPY", 0, None, paper=True, reason=plan["no_op_reason"],
            extra={"checked_at": now.isoformat()}, log_path=log_path,
        )
        return summary

    # --- exit sweep first ----------------------------------------------
    for track in _TRACKS:
        held_id = plan["tracks"][track]["held_contract_id"]
        if held_id is None:
            continue
        position = broker.open_positions[track]
        raw_quote = live_data.get("exit_quotes", {}).get(track)
        if raw_quote is None:
            continue  # no live data supplied this pass -- leave the position alone
        quote = parse_option_quote(raw_quote, held_id)
        if quote is None or quote.get("mark_price") is None:
            continue  # contract genuinely has no live quote right now -- never fabricate

        entry_fill = position["entry_fill"]
        change = (quote["mark_price"] - entry_fill) / entry_fill
        expired = now.date().isoformat() >= position["expiration_date"]

        exit_reason = None
        if change <= -config.OPTIONS_STOP_LOSS_PCT:
            exit_reason = "stop_loss"
        elif change >= config.OPTIONS_TAKE_PROFIT_PCT:
            exit_reason = "take_profit"
        elif expired:
            exit_reason = "expiration"
        if exit_reason is None:
            continue

        exit_fill, used_real_spread = exit_fill_from_quote(quote)
        record = {"track": track, "reason": exit_reason, "used_real_spread": used_real_spread}
        if config.OPTIONS_AUTOMATION_DRY_RUN:
            trade_log.record(
                "dry_run_exit", "SPY", position["quantity"], exit_fill, paper=True,
                reason=f"[DRY RUN] would close track {track}: {exit_reason}",
                extra={"track": track, "used_real_spread": used_real_spread}, log_path=log_path,
            )
            record["executed"] = False
        else:
            closed = broker.close_position(track, exit_fill, reason=f"exit: {exit_reason}", now=now)
            record["executed"] = True
            record["realized_pnl"] = closed["realized_pnl"]
        summary["exits"].append(record)

    # --- entries ---------------------------------------------------------
    for track in _TRACKS:
        lookup = plan["tracks"][track]["entry_lookup"]
        if lookup is None:
            continue  # track already held, or no qualifying signal this pass

        raw_instruments = live_data.get("entry_instruments", {}).get(track)
        instruments = options_data.parse_option_instruments(raw_instruments) if raw_instruments else []
        contract = options_data.select_contract(plan["spot"], plan["decision"]["action"], instruments)
        if contract is None:
            polygon_note = ""
            if live_data.get("polygon_fallback", {}).get(track) is not None:
                polygon_note = " (Robinhood lookup empty; Polygon fallback checked, diagnostic only)"
            trade_log.record(
                "automation_skip", "SPY", 0, None, paper=True,
                reason=f"no listed contract for track {track}'s entry{polygon_note}",
                extra={"track": track}, log_path=log_path,
            )
            continue

        raw_quote = live_data.get("entry_quotes", {}).get(track)
        quote = parse_option_quote(raw_quote, contract["id"]) if raw_quote else None
        if quote is None or quote.get("mark_price") is None:
            trade_log.record(
                "automation_skip", "SPY", 0, None, paper=True,
                reason=f"no live quote for track {track}'s resolved contract -- skipped, not fabricated",
                extra={"track": track, "contract_id": contract["id"]}, log_path=log_path,
            )
            continue

        entry_fill, used_real_spread = entry_fill_from_quote(quote)
        record = {"track": track, "used_real_spread": used_real_spread}
        reason = f"technicals_only_decision: {plan['decision']['rationale']}"
        if config.OPTIONS_AUTOMATION_DRY_RUN:
            trade_log.record(
                "dry_run_entry", "SPY", 1, entry_fill, paper=True,
                reason=f"[DRY RUN] would open track {track}: {reason}",
                extra={"track": track, "option_type": lookup["option_type"], "strike": contract["strike"],
                       "expiration_date": lookup["expiration_date"], "used_real_spread": used_real_spread},
                log_path=log_path,
            )
            record["executed"] = False
        else:
            try:
                opened = broker.buy_to_open(
                    track, contract["id"], contract["strike"], lookup["option_type"],
                    lookup["expiration_date"], 1, entry_fill, reason=reason, now=now,
                )
                record["executed"] = True
                record["position"] = opened
            except OptionsTradeError as e:
                record["executed"] = False
                record["vetoed"] = str(e)
        summary["entries"].append(record)

    return summary
```

- [ ] **Step 3: Run a quick manual smoke check before the demo script proves it end-to-end (Task 5)**

Run: `cd /Users/ethandungo/agent-trader && python3 -c "from automation.run_options_pass import plan_options_pass, execute_options_pass; print('imports OK')"`
Expected: `imports OK`

- [ ] **Step 4: Commit**

```bash
cd /Users/ethandungo/agent-trader
git add automation/run_options_pass.py
git commit -m "Add automation/run_options_pass.py: two-phase live SPY options pass

plan_options_pass() computes the technicals+regime signal and names
exactly what live option data is needed (quotes for held contracts,
instrument lookups for prospective new ones) without making any calls
itself. execute_options_pass() takes that plan plus the calling agent's
live fetches and does the real exit-sweep-then-entries work across both
horizon tracks, dry-run gated same as the equity pass."
```

---

### Task 5: `automation/demo_run_options_pass.py`

**Files:**
- Create: `automation/demo_run_options_pass.py`

**Interfaces:**
- Consumes: `automation.run_options_pass.plan_options_pass()` / `execute_options_pass()`, `execution.options_paper_broker.OptionsPaperBroker`.
- Produces: a runnable demo script (no new importable functions — proof-of-behavior only, matching `automation/demo_run_pass.py`'s own role).

- [ ] **Step 1: Confirm the module doesn't exist yet**

Run: `cd /Users/ethandungo/agent-trader && test -f automation/demo_run_options_pass.py && echo EXISTS || echo MISSING`
Expected: `MISSING`

- [ ] **Step 2: Create `automation/demo_run_options_pass.py`**

```python
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
    return {"data": {"results": [
        {"instrument_id": contract_id, "mark_price": str(mark),
         "bid_price": str(bid) if bid is not None else None,
         "ask_price": str(ask) if ask is not None else None},
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
```

- [ ] **Step 3: Run it and verify all PASS**

Run: `cd /Users/ethandungo/agent-trader && python3 -m automation.demo_run_options_pass`
Expected: six `PASS` lines print (Scenario 2 prints two — one for the plan, one for the dry-run execution — the other four scenarios print one each) plus the final "All scenarios passed.", no `AssertionError`, exit code 0.

- [ ] **Step 4: Run the full test suite one more time to confirm nothing else broke**

Run:
```bash
cd /Users/ethandungo/agent-trader && source .venv/bin/activate && for m in agents.options_risk_vetoer execution.options_paper_broker automation.demo_run_options_pass agents.risk_vetoer execution.paper_broker automation.demo_run_pass backtest.options_data backtest.options_engine backtest.options_metrics backtest.run_options_backtest backtest.forecast_backtest; do echo "=== $m ==="; python3 -m $m > /tmp/plan_verify_$$.txt 2>&1; echo "exit=$?"; tail -3 /tmp/plan_verify_$$.txt; rm -f /tmp/plan_verify_$$.txt; done
```
Expected: every module prints `exit=0` with its final `PASS` line(s) visible, nothing broken.

- [ ] **Step 5: Commit**

```bash
cd /Users/ethandungo/agent-trader
git add automation/demo_run_options_pass.py
git commit -m "Add automation/demo_run_options_pass.py: end-to-end proof

Five scenarios against isolated OptionsPaperBroker instances: market-
hours no-op, a confident signal opening both horizon tracks the same
day, an occupied track correctly skipped for entries while the other
still opens, an armed pass actually closing a losing position on
stop-loss, and an empty instrument lookup skipping an entry cleanly
rather than substituting a contract."
```

---

## What this plan deliberately does NOT do

- Does not call `place_option_order` or any other order-placing tool anywhere.
- Does not change `execution/paper_broker.py`, `agents/risk_vetoer.py`, `automation/run_pass.py`, or any equity-watchlist file.
- Does not wire this into the scheduled cloud routine's actual invocation — that's a deployment decision for later, once this is built and reviewed, not part of this plan.
- Does not resolve the exact Robinhood `get_option_quotes` field names beyond a documented best-effort guess (`mark_price`/`bid_price`/`ask_price`) — verify against a real response at first live run, same discipline as every other agent-mediated parser in this project.
