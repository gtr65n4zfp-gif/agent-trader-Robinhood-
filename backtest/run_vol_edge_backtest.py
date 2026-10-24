"""
backtest/run_vol_edge_backtest.py -- the Task 6 driver for the SPY
vol-edge strand (agents/SPY_OPTIONS_DESIGN.md), rebuilt to finish the run
that agents/SPY_OPTIONS_VOL_EDGE_RESULTS.md left at 5 of 8 data chunks.
The original orchestration was never committed, only its components.
This file wires those components together UNCHANGED: vol_forecast,
vol_edge_signal, options_valuation, options_data, options_engine,
options_spread_engine, and options_metrics.

Agent-mediated like every backtest module here: it never calls an MCP
tool. The calling agent fetches raw Robinhood MCP responses and saves them
under CACHE_DIR; this script reads them. Three stages, run in order:

    python -m backtest.run_vol_edge_backtest plan   # -> which option chains to fetch
    python -m backtest.run_vol_edge_backtest legs   # -> which contracts' bars to fetch
    python -m backtest.run_vol_edge_backtest run    # -> results

SUBSTITUTIONS vs. the original design, forced by data access in this
environment and fixed before any option data was fetched:
1. Market-implied vol comes from Robinhood's VIX index history
   (get_index_historicals), not CBOE's CSVs, which are network-blocked
   here. Robinhood carries VIX but not VIX9D, so only the 30-45 DAY TRACK
   runs. For its 30-45 day DTE, the design's own closest-maturity rule
   picks VIX anyway, so that track is exactly as designed. The 7-day
   track (designed against VIX9D) is not run, rather than run on a
   mismatched benchmark.
2. Point-in-time listing check: Polygon's reference endpoint is blocked
   here. A leg counts as genuinely tradeable on the signal date only if
   its Robinhood bar history has a REAL (not interpolated) daily bar ON
   that date. A contract that actually traded that day existed that day.
   Otherwise the candidate is skipped, never substituted.

PRE-REGISTERED here, because the original run didn't record them:
- Signal stream: every day 2024-08-02..2026-06-10 where the unchanged
  technicals+regime decision (options_engine.technicals_only_decision)
  says buy or sell. This reproduces Round 2's documented 94-signal
  stream exactly.
- Vol-forecast baseline (Engine B) lookback for this track: 63 trading
  days (one quarter). The design only says "longer than the 7-day
  track's" (20).
- Valuation gate: a candidate trades only if its pricing edge, per
  options_valuation, is strictly positive.
- Headline stream: whichever engine wins the full-data forecast-accuracy
  ablation (lower MAE), per the design. Both streams are always
  reported.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

from agents import market_switch
from agents import regime as agents_regime
from agents import technicals as agents_technicals
from execution import config

from . import data as backtest_data
from . import options_data, options_engine, options_metrics, options_spread_engine, options_valuation
from . import run_momentum_backtest as rmb
from . import vol_edge_signal, vol_forecast

CACHE_DIR = os.path.join(config.LOG_DIR, "backtests", "_shared_cache")
VE_DIR = os.path.join(CACHE_DIR, "vol_edge")
SPY_DAILY = os.path.join(CACHE_DIR, "spy_daily_2021-06_2026-07.json")
VIX_DAILY = os.path.join(CACHE_DIR, "vix_daily_2024-01_2026-07.json")
SPY_MONTHLY_LONG = os.path.join(CACHE_DIR, "long_spy_sectors_1993.json")
PLAN_OUT = os.path.join(VE_DIR, "plan.json")
LEGS_OUT = os.path.join(VE_DIR, "legs.json")
RESULTS_OUT = os.path.join(config.LOG_DIR, "backtests", "vol_edge_results.json")

SIGNAL_FROM, SIGNAL_TO = "2024-08-02", "2026-06-10"
TRACK_HORIZON_DAYS = 30            # the 30-45 day track's target, same as automation/run_options_pass.py
BASELINE_LOOKBACK_DAYS = 63        # pre-registered, see module docstring
STREAMS = ("garch", "baseline")


# --- data loading ---------------------------------------------------------

def load_spy_bars() -> list[dict]:
    with open(SPY_DAILY) as f:
        return backtest_data.parse_bars("SPY", json.load(f))


def load_vix_bars() -> list[dict]:
    """Robinhood index bars -> vix_data's {date, open, high, low, close}
    shape (gap-fill bars dropped), so vix_data.value_as_of() and
    vol_edge_signal.market_implied_vol() work on it unchanged."""
    with open(VIX_DAILY) as f:
        raw = json.load(f)
    bars = [b for b in raw["data"]["results"][0]["bars"] if not b.get("interpolated")]
    out = [{"date": b["begins_at"][:10], "open": float(b["open_value"]), "high": float(b["high_value"]),
            "low": float(b["low_value"]), "close": float(b["close_value"])} for b in bars]
    out.sort(key=lambda b: b["date"])
    return out


def _month_index(ym: str) -> int:
    return int(ym[:4]) * 12 + int(ym[5:7]) - 1


def market_switch_states() -> dict[str, str]:
    """{"YYYY-MM": switch state for decisions made in that month}, from
    SPY's completed monthly closes before that month, using the SAME
    agents.market_switch code the live bot runs (bearish_only)."""
    raw = rmb.load_raw([SPY_MONTHLY_LONG])
    bars, _ = rmb.clean_bars("SPY", raw["SPY"])
    states = {}
    for i in range(1, len(bars) + 1):
        month = bars[i]["date"][:7] if i < len(bars) else None
        view = market_switch.market_view([b["close"] for b in bars[:i]], "SPY")
        if month is None or view is None:
            continue
        on = market_switch.is_risk_on(view["stance"], config.MARKET_SWITCH_VARIANT)
        states[month] = "risk_on" if on else "risk_off"
    return states


def _realized_vol(spy_bars: list[dict], after: str, through: str) -> float | None:
    """Annualized stdev of SPY daily log returns over (after, through] --
    computed strictly after the fact, for the ablation only."""
    window = [b for b in spy_bars if after < b["date"] <= through]
    prev = backtest_data.bars_through(spy_bars, after)
    if not prev or len(window) < 2:
        return None
    seq = [prev[-1]] + window
    rets = [r["log_return"] for r in vol_forecast.daily_log_returns(seq)]
    return vol_forecast._annualized_stdev(rets)


# --- stage 1: plan ----------------------------------------------------------

def plan() -> dict:
    spy, vix = load_spy_bars(), load_vix_bars()
    closes = {b["date"]: b["close"] for b in spy}
    rows, needs = [], set()
    for b in spy:
        d = b["date"]
        if not (SIGNAL_FROM <= d <= SIGNAL_TO):
            continue
        ind = backtest_data.technicals_as_of("SPY", d, spy, config.REGIME_EMA_LOOKBACK_DAYS)
        if ind is None:
            continue
        tech = agents_technicals.build_view("SPY", ind["price"], ema=ind["ema"], rsi=ind["rsi"], atr_pct=ind["atr_pct"])
        reg = agents_regime.regime_stance("SPY", ind["price"], ema=ind["regime_ema"], atr_pct=ind["atr_pct"])
        tilt = options_engine.technicals_only_decision(tech, reg)
        if tilt["action"] == "hold":
            continue  # not a signal

        exp = options_data.select_liquid_expiration(d, TRACK_HORIZON_DAYS)
        dte = (datetime.strptime(exp, "%Y-%m-%d") - datetime.strptime(d, "%Y-%m-%d")).days
        implied, which = vol_edge_signal.market_implied_vol(vix, [], [], d, dte)
        row = {
            "date": d, "spot": ind["price"], "side": tilt["action"], "regime_state": reg["state"],
            "expiration": exp, "dte_calendar": dte, "dte_trading": vol_forecast.trading_days_in_horizon(dte),
            "implied_vol": implied, "implied_index": which,
            "garch_rv": vol_forecast.garch_forecast_annualized_vol(spy, d, dte),
            "baseline_rv": vol_forecast.baseline_forecast_annualized_vol(spy, d, BASELINE_LOOKBACK_DAYS),
            "realized_vol": _realized_vol(spy, d, exp),
            "spot_at_expiration": closes.get(exp),
            "decisions": {},
        }
        for stream in STREAMS:
            fc = row[f"{stream}_rv"]
            edge = vol_edge_signal.iv_edge(fc, implied) if (fc is not None and implied is not None) else None
            dec = vol_edge_signal.vol_edge_decision(tech, reg, edge)
            row["decisions"][stream] = dec
            if dec["action"] == "trade":
                needs.add((exp, dec["option_type"]))
        rows.append(row)

    os.makedirs(VE_DIR, exist_ok=True)
    out = {"rows": rows, "instrument_fetches": sorted(list(n) for n in needs)}
    with open(PLAN_OUT, "w") as f:
        json.dump(out, f, indent=2)
    trades = {s: sum(1 for r in rows if r["decisions"][s]["action"] == "trade") for s in STREAMS}
    structs = {s: sum(1 for r in rows if r["decisions"][s].get("structure") == "credit") for s in STREAMS}
    print(f"signals: {len(rows)} ({rows[0]['date']}..{rows[-1]['date']}); implied index used: "
          f"{sorted({r['implied_index'] for r in rows})}")
    print(f"decisions to trade: {trades}  (of which credit spreads: {structs})")
    print(f"GARCH forecasts unavailable: {sum(1 for r in rows if r['garch_rv'] is None)}; "
          f"spot_at_expiration missing: {sum(1 for r in rows if r['spot_at_expiration'] is None)}")
    print(f"option chains to fetch (expiration, type): {len(needs)}")
    for e, t in sorted(needs):
        print(f"  {e} {t}")
    print(f"\nwrote {PLAN_OUT}")
    return out


# --- stage 2: legs ----------------------------------------------------------

INSTRUMENTS_MANIFEST = os.path.join(VE_DIR, "instruments_manifest.txt")


def _load_manifest() -> dict[tuple[str, str], list[dict]]:
    """{(expiration, type): [instrument]} from the targeted-lookup manifest.

    Full SPY chains are hundreds of strikes per expiration, too large to
    fetch inline, so the calling agent looked up ONLY the strikes the
    unchanged selectors can pick: per candidate, the nearest strike
    (round(spot), tie -> lower) and the 1%-away protective strike
    (floor(k - 0.01*spot) for puts, ceil(k + 0.01*spot) for calls). One line
    per lookup: "YYYY-MM-DD type strike id". The id is NONE when Robinhood
    has no such contract (e.g. an expiration that fell on a market
    holiday); those are dropped, so the selector skips rather than
    substitutes."""
    out: dict[tuple[str, str], list[dict]] = {}
    if not os.path.exists(INSTRUMENTS_MANIFEST):
        return out
    with open(INSTRUMENTS_MANIFEST) as f:
        for line in f:
            if not line.strip():
                continue
            exp, otype, strike, iid = line.split()
            chain = out.setdefault((exp, otype), [])
            if iid != "NONE":
                chain.append({"id": iid, "strike": float(strike), "type": otype, "expiration_date": exp})
    return out


def load_instruments(expiration: str, option_type: str) -> list[dict] | None:
    """The looked-up contracts for one (expiration, type). None if no
    lookup was made for it yet; [] if every lookup came back empty."""
    return _load_manifest().get((expiration, option_type))


def _resolve_legs(row: dict, decision: dict) -> dict | None:
    """The contract(s) a trade decision maps to, via the UNCHANGED
    options_data selectors. None if the chain isn't fetched or no valid
    strike exists (skip, never substitute)."""
    chain = load_instruments(row["expiration"], decision["option_type"])
    if not chain:
        return None
    if decision["structure"] == "debit":
        side = "buy" if decision["option_type"] == "call" else "sell"
        c = options_data.select_contract(row["spot"], side, chain)
        return None if c is None else {"legs": [c]}
    pair = options_data.select_spread_strikes(row["spot"], decision["option_type"], chain)
    return None if pair is None else {"legs": list(pair)}


def legs() -> dict:
    with open(PLAN_OUT) as f:
        p = json.load(f)
    missing_chains = [(e, t) for e, t in p["instrument_fetches"] if load_instruments(e, t) is None]
    if missing_chains:
        print(f"{len(missing_chains)} option chains not fetched yet: {missing_chains}")
        return {}
    by_exp: dict[str, dict] = {}
    unresolved = 0
    for row in p["rows"]:
        for stream in STREAMS:
            dec = row["decisions"][stream]
            if dec["action"] != "trade":
                continue
            res = _resolve_legs(row, dec)
            if res is None:
                unresolved += 1
                continue
            slot = by_exp.setdefault(row["expiration"], {"start": row["date"], "ids": {}})
            slot["start"] = min(slot["start"], row["date"])
            for leg in res["legs"]:
                slot["ids"][leg["id"]] = leg["strike"]
    with open(LEGS_OUT, "w") as f:
        json.dump(by_exp, f, indent=2)
    total = sum(len(v["ids"]) for v in by_exp.values())
    print(f"unique contracts needed: {total} across {len(by_exp)} expirations; "
          f"trade decisions with no valid strike: {unresolved}")
    for exp, v in sorted(by_exp.items()):
        ids = sorted(v["ids"])
        print(f"  {exp}: {len(ids)} contracts, bars from {v['start']} -> {exp}")
    print(f"\nwrote {LEGS_OUT}")
    return by_exp


# --- stage 3: run -----------------------------------------------------------

def _bars_file(expiration: str, batch: int) -> str:
    return os.path.join(VE_DIR, f"bars_{expiration}_b{batch}.json")


def load_all_bars() -> dict[str, list[dict]]:
    """{instrument_id: real daily bars}, from every saved historicals file.
    Gap-fill (interpolated) bars are dropped here, so a "real bar on the
    signal date" check is meaningful."""
    out: dict[str, list[dict]] = {}
    for name in sorted(os.listdir(VE_DIR)):
        if not name.startswith("bars_"):
            continue
        with open(os.path.join(VE_DIR, name)) as f:
            raw = json.load(f)
        for r in raw.get("data", {}).get("results", []):
            iid = r["instrument_id"]
            clean = {"data": {"results": [{**r, "bars": [b for b in r.get("bars", []) if not b.get("interpolated")]}]}}
            out[iid] = options_data.parse_option_bars(clean, iid)
    return out


def _simulate(row: dict, stream: str, dec: dict, bars_by_id: dict,
              stop_loss_pct: float | None = None, take_profit_pct: float | None = None,
              valuation_gate: bool = True) -> tuple[dict | None, str]:
    """One candidate -> (trade, "") or (None, skip_reason). Stop/target
    default to the pre-registered config values; the overrides exist only
    for the post-hoc exit-rule diagnostics in run()."""
    stop = config.OPTIONS_STOP_LOSS_PCT if stop_loss_pct is None else stop_loss_pct
    target = config.OPTIONS_TAKE_PROFIT_PCT if take_profit_pct is None else take_profit_pct
    res = _resolve_legs(row, dec)
    if res is None:
        return None, "no valid strike in the fetched chain"
    d, sigma = row["date"], row[f"{stream}_rv"]
    leg_bars, entry = [], []
    for leg in res["legs"]:
        bars = bars_by_id.get(leg["id"])
        if bars is None:
            return None, "contract bars not fetched"
        on_d = next((b for b in bars if b["date"] == d), None)
        if on_d is None:
            return None, "no real bar on the signal date (not genuinely tradeable then)"
        leg_bars.append([b for b in bars if b["date"] > d])
        entry.append(on_d)
    floor = config.OPTIONS_ROUNDTRIP_HAIRCUT_PCT
    common = {"date": d, "stream": stream, "structure": dec["structure"], "direction": dec["direction"],
              "option_type": dec["option_type"], "side": row["side"], "regime_state": row["regime_state"],
              "expiration": row["expiration"], "vol_edge": dec["edge"]}

    if dec["structure"] == "debit":
        leg = res["legs"][0]
        model = options_valuation.black_scholes_price(row["spot"], leg["strike"], row["dte_trading"], sigma,
                                                      dec["option_type"])
        edge = options_valuation.single_leg_edge(model, entry[0]["close"], "debit")
        if edge <= 0:
            return None, "valuation edge not positive"
        if row["spot_at_expiration"] is None:
            return None, "no SPY close on expiration date"
        sim = options_engine.simulate_option_trade(
            entry[0]["close"], leg_bars[0], "buy" if dec["option_type"] == "call" else "sell", leg["strike"],
            row["expiration"], row["spot_at_expiration"], stop, target,
            options_data.estimate_haircut_pct(entry[0], floor))
        mult = config.OPTIONS_CONTRACT_MULTIPLIER
        return {**common, **sim, "strikes": [leg["strike"]], "valuation_edge": round(edge, 4),
                "realized_pnl": round(sim["realized_pnl"] * mult, 2),
                "max_loss": round(entry[0]["close"] * mult, 2)}, ""

    sold, bought = res["legs"]
    model_net = options_valuation.spread_model_value(row["spot"], sold["strike"], bought["strike"],
                                                      row["dte_trading"], sigma, dec["option_type"])
    market_net = entry[0]["close"] - entry[1]["close"]
    edge = options_valuation.spread_edge(model_net, market_net)
    if valuation_gate and edge <= 0:
        return None, "valuation edge not positive"
    if row["spot_at_expiration"] is None:
        return None, "no SPY close on expiration date"
    sim = options_spread_engine.simulate_spread_trade(
        entry[0]["close"], entry[1]["close"], leg_bars[0], leg_bars[1], dec["option_type"],
        sold["strike"], bought["strike"], row["expiration"], row["spot_at_expiration"],
        stop, target,
        options_data.estimate_haircut_pct(entry[0], floor), options_data.estimate_haircut_pct(entry[1], floor))
    if sim is None:
        return None, "non-positive net credit after costs"
    mult = config.OPTIONS_CONTRACT_MULTIPLIER
    width = abs(sold["strike"] - bought["strike"])
    return {**common, **sim, "strikes": [sold["strike"], bought["strike"]], "valuation_edge": round(edge, 4),
            "realized_pnl": round(sim["realized_pnl"] * mult, 2),
            "max_loss": round((width - sim["net_entry_credit"]) * mult, 2)}, ""


def _one_at_a_time(trades: list[dict]) -> list[dict]:
    """What a single account holding ONE position at a time could have
    taken: chronologically, a trade is taken only if the previous taken
    trade has already exited."""
    taken, busy_until = [], ""
    for t in sorted(trades, key=lambda t: t["date"]):
        if t["date"] > busy_until:
            taken.append(t)
            busy_until = t["exit_date"]
    return taken


def _monthly(trades: list[dict]) -> dict:
    """P&L by EXIT month -- how consistent the income actually was."""
    by: dict[str, float] = {}
    for t in trades:
        by[t["exit_date"][:7]] = by.get(t["exit_date"][:7], 0.0) + t["realized_pnl"]
    if not by:
        return {"months_with_exits": 0}
    vals = list(by.values())
    return {
        "months_with_exits": len(vals),
        "positive_months": sum(1 for v in vals if v > 0),
        "worst_month": round(min(vals), 2), "best_month": round(max(vals), 2),
        "avg_per_month_with_exits": round(sum(vals) / len(vals), 2),
        "by_month": {k: round(v, 2) for k, v in sorted(by.items())},
    }


def _summary(trades: list[dict]) -> dict:
    s = options_metrics.summarize_option_trades(trades)
    if trades:
        s["avg_pnl"] = round(s["total_realized_pnl"] / len(trades), 2)
        s["worst_trade"] = round(min(t["realized_pnl"] for t in trades), 2)
        s["best_trade"] = round(max(t["realized_pnl"] for t in trades), 2)
        s["avg_max_loss"] = round(sum(t["max_loss"] for t in trades) / len(trades), 2)
        wins = [t["realized_pnl"] for t in trades if t["realized_pnl"] > 0]
        losses = [t["realized_pnl"] for t in trades if t["realized_pnl"] <= 0]
        s["avg_win"] = round(sum(wins) / len(wins), 2) if wins else None
        s["avg_loss"] = round(sum(losses) / len(losses), 2) if losses else None
        s["exit_reasons"] = {r: sum(1 for t in trades if t["exit_reason"] == r)
                             for r in sorted({t["exit_reason"] for t in trades})}
    return s


def run() -> dict:
    with open(PLAN_OUT) as f:
        p = json.load(f)
    bars_by_id = load_all_bars()
    switch = market_switch_states()
    out = {"ablation": options_metrics.compare_forecast_accuracy(p["rows"]), "streams": {}}
    acc = out["ablation"]
    winner = min(STREAMS, key=lambda s: acc[s]["mae"] if acc[s]["mae"] is not None else 9e9)
    out["headline_stream"] = winner
    print(f"Forecast accuracy (full data): GARCH MAE {acc['garch']['mae']} vs baseline MAE {acc['baseline']['mae']}; "
          f"GARCH closer on {acc['win_count']['garch']}/{acc['win_count_total']} -> headline stream: {winner}\n")

    for stream in STREAMS:
        trades, skips = [], {}
        for row in p["rows"]:
            dec = row["decisions"][stream]
            if dec["action"] != "trade":
                continue
            t, why = _simulate(row, stream, dec, bars_by_id)
            if t is None:
                skips[why] = skips.get(why, 0) + 1
                continue
            t["market_switch"] = switch.get(row["date"][:7], "unknown")
            trades.append(t)
        credit = [t for t in trades if t["structure"] == "credit"]
        guarded = [t for t in credit if t["market_switch"] == "risk_on"]
        sole = _one_at_a_time(credit)
        sole_guarded = _one_at_a_time(guarded)
        res = {
            "decisions_to_trade": sum(1 for r in p["rows"] if r["decisions"][stream]["action"] == "trade"),
            "skips": skips,
            "all_trades": _summary(trades),
            "credit_spreads": _summary(credit),
            "debit": _summary([t for t in trades if t["structure"] == "debit"]),
            "credit_by_regime_side": options_metrics.summarize_by_regime_and_side(credit, side_key="direction"),
            "credit_one_at_a_time": {**_summary(sole), "monthly": _monthly(sole)},
            "credit_with_safety_switch": _summary(guarded),
            "credit_one_at_a_time_with_switch": {**_summary(sole_guarded), "monthly": _monthly(sole_guarded)},
            "trades": trades,
        }
        out["streams"][stream] = res
        c, s1 = res["credit_spreads"], res["credit_one_at_a_time"]
        print(f"[{stream}] decisions {res['decisions_to_trade']}, simulated {len(trades)}, skips {skips}")
        print(f"  ALL trades: {res['all_trades'].get('count')} n, win {res['all_trades'].get('win_rate')}, "
              f"P&L ${res['all_trades'].get('total_realized_pnl')}")
        if c.get("count"):
            print(f"  CREDIT SPREADS: n={c['count']} win {c['win_rate']} CI {c['win_rate_ci_95']} total ${c['total_realized_pnl']} "
                  f"avg ${c['avg_pnl']} | avg win ${c['avg_win']} avg loss ${c['avg_loss']} worst ${c['worst_trade']} "
                  f"| avg max-loss/contract ${c['avg_max_loss']} | exits {c['exit_reasons']}")
            m = s1["monthly"]
            print(f"  ONE SPREAD AT A TIME: n={s1['count']} win {s1['win_rate']} total ${s1['total_realized_pnl']} | "
                  f"months w/ exits {m.get('months_with_exits')}, positive {m.get('positive_months')}, "
                  f"worst month ${m.get('worst_month')}, avg ${m.get('avg_per_month_with_exits')}")
            g = res["credit_with_safety_switch"]
            print(f"  WITH SAFETY SWITCH: n={g.get('count')} win {g.get('win_rate')} total ${g.get('total_realized_pnl')} "
                  f"(blocked {c['count'] - (g.get('count') or 0)} spreads)")
        print()

    # POST-HOC diagnostics, chosen AFTER seeing the result above, so they
    # are never a headline: they only show whether the losses come from
    # the exit rule or from the trade itself. Same candidates and costs,
    # headline stream, credit spreads only.
    print("POST-HOC exit-rule diagnostics (credit spreads, headline stream; NOT pre-registered):")
    out["post_hoc_exit_diagnostics"] = {}
    variants = {
        "hold_to_expiration": (1e9, 1e9),
        "tp_50pct_of_credit_stop_1x_credit": (1.0, 0.5),
    }
    # The valuation gate prices both legs at ONE flat sigma, but the market
    # prices the downside put skew: the protective put is dear, so every
    # bull put spread reads "cheap" and every bear call spread "rich". The
    # last variant shows the bull put spreads the gate blocked.
    variants["bull_put_spreads_ungated"] = (None, None)
    for name, (stop, target) in variants.items():
        ungated = name == "bull_put_spreads_ungated"
        trades = []
        for row in p["rows"]:
            dec = row["decisions"][winner]
            if dec["action"] != "trade" or dec["structure"] != "credit":
                continue
            if ungated and dec["option_type"] != "put":
                continue
            t, _ = _simulate(row, winner, dec, bars_by_id, stop_loss_pct=stop, take_profit_pct=target,
                             valuation_gate=not ungated)
            if t is not None:
                trades.append(t)
        sole = _one_at_a_time(trades)
        res = {**_summary(trades), "one_at_a_time": {**_summary(sole), "monthly": _monthly(sole)}}
        out["post_hoc_exit_diagnostics"][name] = res
        m = res["one_at_a_time"]["monthly"]
        print(f"  {name}: n={res.get('count')} win {res.get('win_rate')} total ${res.get('total_realized_pnl')} "
              f"worst ${res.get('worst_trade')} | one at a time: n={res['one_at_a_time'].get('count')} "
              f"total ${res['one_at_a_time'].get('total_realized_pnl')}, positive months "
              f"{m.get('positive_months')}/{m.get('months_with_exits')}")
    print()

    with open(RESULTS_OUT, "w") as f:
        json.dump(out, f, indent=2)
    print(f"Full results: {RESULTS_OUT}")
    return out


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else ""
    if stage == "plan":
        plan()
    elif stage == "legs":
        legs()
    elif stage == "run":
        run()
    else:
        print(__doc__)
        sys.exit(1)
