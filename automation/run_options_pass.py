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
