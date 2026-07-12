"""
automation/run_pass.py — one full, unattended council pass: exit sweep
first (manage what we hold), then entries (regime -> seats -> judge,
no-trade default) across config.WATCHLIST. This is the ONLY entrypoint a
scheduled routine calls — see agents/AUTOMATION_DESIGN.md for the wake
cadence and what the routine does before calling in (fetching the bundle).

Like every other data-touching module in this project, this function
fetches nothing itself — agent-mediated by the same architecture as
agents/demo_council.py: the calling agent (a scheduled Claude Code
routine, per AUTOMATION_DESIGN.md — never a headless daemon, since
Robinhood's MCP can't run without an authenticated session) has already
made the MCP calls and formed each symbol's Fundamentals verdict.

FAIL-SAFE BY DESIGN — hard rules, see AUTOMATION_DESIGN.md for the full
rationale:
  1. config.assert_paper_mode() first — aborts the whole pass if live
     trading is somehow armed. Never bypassed.
  2. Market-hours guard (config.market_is_open()) — outside US equity
     regular hours, the whole pass is a logged no-op; nothing else runs.
  3. Per-symbol data sanity check — a symbol whose data fails to parse or
     is stale (config.MAX_QUOTE_AGE_MINUTES) is skipped, individually,
     without touching that symbol's position (if any) or blocking other
     symbols. Never trade, never even evaluate, on bad or missing data.
  4. config.AUTOMATION_DRY_RUN (default True) — every entry and exit
     decision is still made and logged in full, but PaperBroker.buy()/
     .sell() is never called. Flip to False only deliberately (see that
     constant's comment in execution/config.py).
  5. When execution IS armed, every order still routes through
     PaperBroker, which still runs the Risk vetoer — automation adds no
     path around it.
"""

from datetime import datetime, timezone

from agents import exits, judge, regime, technicals
from execution import config, robinhood, trade_log
from execution.paper_broker import PaperBroker, TradeError
from execution.robinhood import RobinhoodDataError

BUNDLE_HELP = """\
bundle must be {symbol: {...}} for every symbol to evaluate (normally
config.WATCHLIST), each value shaped exactly like agents.demo_council's
per-symbol bundle:
    "quote", "atr", "rsi", "ema", "regime_ema", "robinhood_fundamentals":
        raw MCP responses (see agents/demo_council.py's docstring for the
        exact call shape each one needs)
    "fundamentals_verdict":
        agents.fundamentals_seat.form_verdict() output, formed by the
        calling agent from build_brief(symbol)
"""


def _extract_symbol_data(symbol: str, sym_bundle: dict) -> dict | None:
    """
    Parse one symbol's raw MCP bundle into the values the pipeline needs.
    Returns None — never raises — if anything fails to parse or the quote
    is stale. This is where FAIL-SAFE rule 3 actually lives: bad or
    missing data for one symbol can never reach the seats or the broker.
    The caller logs a skip and moves on to the next symbol.
    """
    try:
        price = robinhood.get_quote(symbol, sym_bundle["quote"])
        age_minutes = robinhood.get_quote_age_minutes(symbol, sym_bundle["quote"])
        if age_minutes > config.MAX_QUOTE_AGE_MINUTES:
            return None
        atr_pct = robinhood.get_atr_pct(symbol, price, sym_bundle["atr"])
        rsi = robinhood.get_rsi(symbol, sym_bundle["rsi"])
        ema = robinhood.get_ema(symbol, sym_bundle["ema"])
        regime_ema = robinhood.get_regime_ema(symbol, sym_bundle["regime_ema"])
        sector_map = robinhood.get_sectors([symbol], sym_bundle["robinhood_fundamentals"])
        fundamentals_verdict = sym_bundle["fundamentals_verdict"]
    except (RobinhoodDataError, KeyError, ValueError, TypeError):
        return None

    return {
        "price": price, "atr_pct": atr_pct, "rsi": rsi, "ema": ema,
        "regime_ema": regime_ema, "sector_map": sector_map,
        "fundamentals_verdict": fundamentals_verdict,
        "age_minutes": round(age_minutes, 1),
    }


def run_pass(bundle: dict[str, dict], now: datetime | None = None, broker: PaperBroker | None = None) -> dict:
    """
    Execute one full automation pass. See the module docstring for the
    fail-safe rules and BUNDLE_HELP for the bundle's shape.

    now: for testing determinism; defaults to the current time.
    broker: for testing against an isolated account (see
    PaperBroker.__init__'s docstring — same reasoning as
    agents/demo_exits.py and agents/demo_regime.py). The real scheduled
    routine omits this and gets the one shared paper account.

    Returns a run summary: {started_at, dry_run, market_open,
    symbols_skipped, exits, entries, holds, regime_sitouts,
    round_trip_stats}.
    """
    now = now or datetime.now(timezone.utc)
    summary = {
        "started_at": now.isoformat(),
        "dry_run": config.AUTOMATION_DRY_RUN,
        "market_open": None,
        "symbols_skipped": {},
        "exits": [],
        "entries": [],
        "holds": [],
        "regime_sitouts": [],
    }

    # Rule 1 — hard stop, first line, no exceptions.
    config.assert_paper_mode()

    # Rule 2 — market-hours guard. Whole-pass no-op, not per-symbol: every
    # quote is equally stale outside regular hours.
    if not config.market_is_open(now):
        summary["market_open"] = False
        trade_log.record(
            "automation_noop", "*", 0, None, paper=True,
            reason="market-hours guard: outside US equity regular trading hours",
            extra={"checked_at": now.isoformat()},
        )
        return summary
    summary["market_open"] = True

    broker = broker or PaperBroker()

    # Rule 3 — per-symbol data sanity check, before anything touches the
    # seats or the broker.
    parsed: dict[str, dict] = {}
    for symbol, sym_bundle in bundle.items():
        data = _extract_symbol_data(symbol, sym_bundle)
        if data is None:
            summary["symbols_skipped"][symbol] = "bad or stale data"
            trade_log.record(
                "automation_skip", symbol, 0, None, paper=True,
                reason="data sanity check failed — skipped, never evaluated or traded",
                extra={},
            )
            continue
        parsed[symbol] = data

    # Regime + Technicals views, computed once per symbol with fresh data
    # and reused for both the exit sweep (held positions) and entries
    # (unheld symbols) below.
    views: dict[str, dict] = {}
    for symbol, data in parsed.items():
        views[symbol] = {
            "regime": regime.regime_stance(symbol, data["price"], ema=data["regime_ema"], atr_pct=data["atr_pct"]),
            "technicals": technicals.build_view(symbol, data["price"], ema=data["ema"], rsi=data["rsi"],
                                                 atr_pct=data["atr_pct"]),
            "fundamentals": data["fundamentals_verdict"],
        }

    known_prices = {sym: d["price"] for sym, d in parsed.items()}

    # --- (b) EXIT SWEEP FIRST — manage what we hold before opening anything new
    for symbol, shares in list(broker.positions.items()):
        if symbol not in parsed:
            continue  # held, but no fresh data this pass — leave alone, not a skip
        entry_price = broker.cost_basis.get(symbol)
        if entry_price is None:
            continue  # pre-existing position with no cost basis on record
        v = views[symbol]
        signal = exits.evaluate_exits(
            entry_price, parsed[symbol]["price"],
            fundamentals=v["fundamentals"], technicals=v["technicals"], regime=v["regime"],
        )
        if signal is None:
            continue

        record = {"symbol": symbol, "path": signal["path"], "reason": signal["reason"],
                  "regime_state": v["regime"]["state"]}
        if config.AUTOMATION_DRY_RUN:
            trade_log.record(
                "dry_run_exit", symbol, shares, parsed[symbol]["price"], paper=True,
                reason=f"[DRY RUN] would exit: {signal['path']} — {signal['reason']}",
                extra={"path": signal["path"], "regime_state": v["regime"]["state"]},
            )
            record["executed"] = False
        else:
            trades = exits.close_position(
                broker, symbol, shares, parsed[symbol]["price"],
                reason=f"exit: {signal['path']} — {signal['reason']}",
            )
            realized_pnl = round(sum(t["realized_pnl"] or 0 for t in trades), 2)
            trade_log.record(
                "exit", symbol, shares, parsed[symbol]["price"], paper=True,
                reason=signal["reason"],
                extra={"path": signal["path"], "realized_pnl": realized_pnl, "regime_state": v["regime"]["state"]},
            )
            record["executed"] = True
            record["realized_pnl"] = realized_pnl
        summary["exits"].append(record)

    # --- (c) THEN ENTRIES — regime -> seats -> judge, no-trade-by-default
    for symbol, data in parsed.items():
        if symbol in broker.positions:
            continue  # already held — the exit sweep above owns it this pass
        v = views[symbol]
        decision = judge.decide(v["fundamentals"], v["technicals"], regime=v["regime"])
        # Baseline stays regime-blind on purpose (see agents/judge.py) —
        # logged unconditionally, same as every manual run's ablation hook.
        baseline = judge.baseline_decide(v["fundamentals"], v["technicals"])
        trade_log.record(
            "baseline", symbol, baseline["target_quantity"], data["price"], paper=True,
            reason=baseline["rationale"],
            extra={"seat": "judge_baseline", "baseline_action": baseline["action"],
                   "confidence": baseline["confidence"], "regime_state": v["regime"]["state"]},
        )

        if decision["action"] == "hold":
            is_regime_sitout = not v["regime"]["tradeable"]
            trade_log.record(
                "regime_sitout" if is_regime_sitout else "hold", symbol, 0, data["price"], paper=True,
                reason=decision["rationale"],
                extra={"seat": "judge", "confidence": decision["confidence"], "regime_state": v["regime"]["state"]},
            )
            (summary["regime_sitouts"] if is_regime_sitout else summary["holds"]).append(symbol)
            continue

        record = {"symbol": symbol, "action": decision["action"], "regime_state": v["regime"]["state"]}
        if config.AUTOMATION_DRY_RUN:
            trade_log.record(
                "dry_run_entry", symbol, decision["target_quantity"], data["price"], paper=True,
                reason=f"[DRY RUN] would {decision['action']}: {decision['rationale']}",
                extra={"seat": "judge", "confidence": decision["confidence"], "regime_state": v["regime"]["state"]},
            )
            record["executed"] = False
            summary["entries"].append(record)
            continue

        reason = f"Judge: {decision['rationale']}"
        try:
            if decision["action"] == "buy":
                trade = broker.buy(
                    symbol, decision["target_quantity"], data["price"], reason=reason,
                    prices=known_prices, atr_pct=data["atr_pct"], sector_map=data["sector_map"],
                )
            else:
                trade = broker.sell(
                    symbol, decision["target_quantity"], data["price"], reason=reason, prices=known_prices,
                )
            record["executed"] = True
            record["trade"] = trade
        except TradeError as e:
            # Same property proven manually in agents/demo_council.py: the
            # Judge doesn't get the last word. Automation is no exception.
            record["executed"] = False
            record["vetoed"] = str(e)
        summary["entries"].append(record)

    summary["round_trip_stats"] = trade_log.round_trip_stats()
    return summary
