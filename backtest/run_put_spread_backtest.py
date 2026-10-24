"""
backtest/run_put_spread_backtest.py -- the pre-registered SPY bull put
spread test (agents/PUT_SPREAD_DESIGN.md). Every rule below was fixed and
committed BEFORE any option price was fetched.

The follow-up to agents/SPY_OPTIONS_VOL_EDGE_RESULTS.md, whose flat-vol
valuation gate read the market's put skew as "edge" and so only ever
sold bear call spreads. This test instead:
- sells further-OTM puts on a fixed weekly schedule;
- gates each trade on the SHORT PUT'S OWN implied vol (backed out of its
  real market price, skew included) against the GARCH forecast, rather
  than on one VIX number.

Agent-mediated like every backtest module here: it never calls an MCP
tool. The calling agent saves raw Robinhood MCP responses under PS_DIR,
and this script reads them. Three stages, run in order:

    python -m backtest.run_put_spread_backtest plan   # -> which contracts to look up
    python -m backtest.run_put_spread_backtest legs   # -> which contracts' bars to fetch
    python -m backtest.run_put_spread_backtest run    # -> results
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime

from execution import config

from . import data as backtest_data
from . import options_data, options_spread_engine, options_valuation
from . import run_momentum_backtest as rmb
from . import run_vol_edge_backtest as rve
from . import vol_edge_signal, vol_forecast

CACHE_DIR = rve.CACHE_DIR
PS_DIR = os.path.join(CACHE_DIR, "put_spread")
SPY_DAILY = os.path.join(CACHE_DIR, "spy_daily_2019-06_2026-07.json")
VIX_DAILY = os.path.join(CACHE_DIR, "vix_daily_2021-06_2026-07.json")
PLAN_OUT = os.path.join(PS_DIR, "plan.json")
MANIFEST = os.path.join(PS_DIR, "instruments_manifest.txt")
RESULTS_OUT = os.path.join(config.LOG_DIR, "backtests", "put_spread_results.json")

# --- PRE-REGISTERED RULES (agents/PUT_SPREAD_DESIGN.md) ----------------------
ENTRY_FROM, ENTRY_TO = "2022-01-03", "2026-06-12"  # first trading day of every week
HORIZON_DAYS = 30            # options_data.select_liquid_expiration: Friday 30-40 days out, monthly preferred
SHORT_DELTA = 0.20           # short put: highest $5 strike with |delta| <= 0.20 (VIX as sigma)
STRIKE_STEP = 5
WIDTH = 5                    # long (protective) put = short strike - $5
MIN_EDGE = vol_edge_signal.MIN_EDGE_PCT   # gate: short put's own IV - GARCH forecast >= 2 vol points
TAKE_PROFIT_PCT = 0.50       # close at 50% of the credit kept
STOP_LOSS_PCT = 1.00         # close when the loss equals the credit (spread worth 2x credit)
PASS_POSITIVE_MONTH_FRAC = 0.65
HEADLINE = "gated_with_switch"
VARIANTS = {                 # name -> (needs gate pass, needs switch risk-on)
    "gated_with_switch": (True, True),
    "gated_no_switch": (True, False),
    "ungated_with_switch": (False, True),
    "ungated_no_switch": (False, False),
}
MAX_EXPS_PER_LOOKUP = 8
# POST-HOC only (chosen after the pre-registered run): a realistic SPY fill,
# a fixed $0.02 off mid per leg per side, instead of the %-of-price haircut.
REALISTIC_COST_PER_LEG = 0.02


# --- data loading ------------------------------------------------------------

def load_spy_bars() -> list[dict]:
    with open(SPY_DAILY) as f:
        return backtest_data.parse_bars("SPY", json.load(f))


def load_vix_bars() -> list[dict]:
    with open(VIX_DAILY) as f:
        raw = json.load(f)
    bars = [b for b in raw["data"]["results"][0]["bars"] if not b.get("interpolated")]
    return sorted(({"date": b["begins_at"][:10], "close": float(b["close_value"])} for b in bars),
                  key=lambda b: b["date"])


def load_manifest() -> dict[tuple[str, float], str | None]:
    """{(expiration, strike): instrument id, or None if Robinhood has no
    such contract}. Same line format as the vol-edge manifest."""
    out: dict[tuple[str, float], str | None] = {}
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as f:
            for line in f:
                if line.strip():
                    exp, _otype, strike, iid = line.split()
                    out[(exp, float(strike))] = None if iid == "NONE" else iid
    return out


def load_all_bars() -> dict[str, list[dict]]:
    """{instrument_id: real daily bars}; gap-fill bars dropped."""
    out: dict[str, list[dict]] = {}
    for name in sorted(os.listdir(PS_DIR)):
        if not name.startswith("bars_"):
            continue
        with open(os.path.join(PS_DIR, name)) as f:
            raw = json.load(f)
        for r in raw.get("data", {}).get("results", []):
            clean = {"data": {"results": [{**r, "bars": [b for b in r.get("bars", []) if not b.get("interpolated")]}]}}
            out[r["instrument_id"]] = options_data.parse_option_bars(clean, r["instrument_id"])
    return out


# --- pricing helpers -----------------------------------------------------------

def put_delta(spot: float, strike: float, trading_days: int, sigma: float) -> float:
    T = trading_days / 252
    d1 = (math.log(spot / strike) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
    return options_valuation._norm_cdf(d1) - 1.0


def pick_short_strike(spot: float, trading_days: int, sigma: float) -> int:
    k = math.floor(spot / STRIKE_STEP) * STRIKE_STEP
    while k > STRIKE_STEP and abs(put_delta(spot, k, trading_days, sigma)) > SHORT_DELTA:
        k -= STRIKE_STEP
    return k


def implied_vol(price: float, spot: float, strike: float, trading_days: int) -> float | None:
    """The put's own implied vol from its market price (bisection on the
    same r=0 Black-Scholes used everywhere here). None if the price is
    outside what any vol in [1%, 300%] can produce."""
    lo, hi = 0.01, 3.0
    f = lambda s: options_valuation.black_scholes_price(spot, strike, trading_days, s, "put") - price
    if trading_days <= 0 or f(lo) > 0 or f(hi) < 0:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


# --- stage 1: plan -------------------------------------------------------------

def _first_trading_day_each_week(spy: list[dict]) -> list[dict]:
    seen, out = set(), []
    for b in spy:
        if ENTRY_FROM <= b["date"] <= ENTRY_TO:
            week = datetime.strptime(b["date"], "%Y-%m-%d").isocalendar()[:2]
            if week not in seen:
                seen.add(week)
                out.append(b)
    return out


def plan() -> dict:
    spy, vix = load_spy_bars(), load_vix_bars()
    switch = rve.market_switch_states()
    rows = []
    for b in _first_trading_day_each_week(spy):
        d, spot = b["date"], b["close"]
        exp = options_data.select_liquid_expiration(d, HORIZON_DAYS)
        dte = (datetime.strptime(exp, "%Y-%m-%d") - datetime.strptime(d, "%Y-%m-%d")).days
        tdays = vol_forecast.trading_days_in_horizon(dte)
        vix_now, _ = vol_edge_signal.market_implied_vol(vix, [], [], d, dte)
        if vix_now is None:
            continue
        short = pick_short_strike(spot, tdays, vix_now)
        at_exp = backtest_data.bars_through(spy, exp)
        rows.append({
            "date": d, "spot": spot, "vix": vix_now, "expiration": exp, "dte_calendar": dte,
            "dte_trading": tdays, "short_strike": short, "long_strike": short - WIDTH,
            "garch_rv": vol_forecast.garch_forecast_annualized_vol(spy, d, dte),
            "market_switch": switch.get(d[:7], "unknown"),
            "spot_at_expiration": at_exp[-1]["close"] if at_exp else None,
        })

    by_strike: dict[int, set[str]] = {}
    for r in rows:
        for k in (r["short_strike"], r["long_strike"]):
            by_strike.setdefault(k, set()).add(r["expiration"])
    lookups = []
    for k in sorted(by_strike):
        exps = sorted(by_strike[k])
        for i in range(0, len(exps), MAX_EXPS_PER_LOOKUP):
            lookups.append({"strike": k, "exps": exps[i:i + MAX_EXPS_PER_LOOKUP]})

    os.makedirs(PS_DIR, exist_ok=True)
    out = {"rows": rows, "lookups": lookups}
    with open(PLAN_OUT, "w") as f:
        json.dump(out, f, indent=2)
    pairs = sum(len(v) for v in by_strike.values())
    print(f"entry weeks: {len(rows)} ({rows[0]['date']}..{rows[-1]['date']}); "
          f"GARCH unavailable: {sum(1 for r in rows if r['garch_rv'] is None)}; "
          f"switch risk-off weeks: {sum(1 for r in rows if r['market_switch'] == 'risk_off')}")
    print(f"unique (expiration, strike) contracts: {pairs}; lookups: {len(lookups)}")
    print(f"wrote {PLAN_OUT}")
    return out


# --- stage 2: legs -------------------------------------------------------------

def legs() -> list[list]:
    with open(PLAN_OUT) as f:
        p = json.load(f)
    man = load_manifest()
    missing = [(e, k) for lk in p["lookups"] for e in lk["exps"] for k in [float(lk["strike"])] if (e, k) not in man]
    if missing:
        print(f"{len(missing)} contracts not looked up yet, e.g. {missing[:5]}")
        return []
    spans: dict[str, list] = {}
    for r in p["rows"]:
        for k in (r["short_strike"], r["long_strike"]):
            iid = man[(r["expiration"], float(k))]
            if iid:
                s = spans.setdefault(iid, [r["date"], r["expiration"]])
                s[0] = min(s[0], r["date"])
    ordered = sorted(spans.items(), key=lambda kv: (kv[1][1], kv[0]))
    batches = [ordered[i:i + 10] for i in range(0, len(ordered), 10)]
    out = [[min(s[0] for _, s in b), max(s[1] for _, s in b), [iid for iid, _ in b]] for b in batches]
    with open(os.path.join(PS_DIR, "bar_batches.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(f"contracts with bars to fetch: {len(ordered)} in {len(out)} batches; "
          f"no such contract: {sum(1 for v in man.values() if v is None)}")
    return out


# --- stage 3: run --------------------------------------------------------------

def _simulate(row: dict, man: dict, bars_by_id: dict,
              fixed_cost_per_leg: float | None = None) -> tuple[dict | None, str]:
    """One weekly entry -> (trade with BOTH the spread and the Level 2
    cash-secured-put result, "") or (None, skip_reason). fixed_cost_per_leg
    replaces the pre-registered %-haircut with a fixed $ per leg per side
    (post-hoc cost sensitivity only)."""
    d = row["date"]
    ids = [man.get((row["expiration"], float(k))) for k in (row["short_strike"], row["long_strike"])]
    if not all(ids):
        return None, "no such contract (holiday expiration)"
    entry, after = [], []
    for iid in ids:
        bars = bars_by_id.get(iid)
        if bars is None:
            return None, "contract bars not fetched"
        on_d = next((b for b in bars if b["date"] == d), None)
        if on_d is None:
            return None, "no real bar on the entry date (not genuinely tradeable then)"
        entry.append(on_d)
        after.append([b for b in bars if b["date"] > d])
    floor = config.OPTIONS_ROUNDTRIP_HAIRCUT_PCT
    if fixed_cost_per_leg is None:
        h_short, h_long = (options_data.estimate_haircut_pct(e, floor) for e in entry)
    else:  # the engine's haircut is round-trip %, half charged per fill
        h_short, h_long = (min(1.0, 2 * fixed_cost_per_leg / e["close"]) if e["close"] > 0 else 0.0
                           for e in entry)
    mult = config.OPTIONS_CONTRACT_MULTIPLIER

    spread = options_spread_engine.simulate_spread_trade(
        entry[0]["close"], entry[1]["close"], after[0], after[1], "put",
        row["short_strike"], row["long_strike"], row["expiration"], row["spot_at_expiration"],
        STOP_LOSS_PCT, TAKE_PROFIT_PCT, h_short, h_long)
    if spread is None:
        return None, "non-positive net credit after costs"
    # Level 2 version: the same short put, cash-secured, no protective leg
    # (a zero-priced, zero-strike "long leg" reuses the same engine exactly).
    csp = options_spread_engine.simulate_spread_trade(
        entry[0]["close"], 0.0, after[0], [{"date": b["date"], "close": 0.0} for b in after[0]], "put",
        row["short_strike"], 0.0, row["expiration"], row["spot_at_expiration"],
        STOP_LOSS_PCT, TAKE_PROFIT_PCT, h_short, 0.0)

    iv = implied_vol(entry[0]["close"], row["spot"], row["short_strike"], row["dte_trading"])
    edge = None if (iv is None or row["garch_rv"] is None) else iv - row["garch_rv"]
    return {
        "date": d, "expiration": row["expiration"], "spot": row["spot"], "vix": row["vix"],
        "strikes": [row["short_strike"], row["long_strike"]], "short_iv": iv, "garch_rv": row["garch_rv"],
        "edge": edge, "gate_pass": edge is not None and edge >= MIN_EDGE,
        "switch_on": row["market_switch"] == "risk_on",
        "net_entry_credit": spread["net_entry_credit"], "exit_reason": spread["exit_reason"],
        "exit_date": spread["exit_date"], "realized_pnl": round(spread["realized_pnl"] * mult, 2),
        "max_loss": round((WIDTH - spread["net_entry_credit"]) * mult, 2),
        "csp": None if csp is None else {
            "exit_reason": csp["exit_reason"], "exit_date": csp["exit_date"],
            "realized_pnl": round(csp["realized_pnl"] * mult, 2),
            "collateral": round(row["short_strike"] * mult, 2),
        },
    }, ""


def _month_range(first: str, last: str) -> list[str]:
    y, m = int(first[:4]), int(first[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _sharpe(monthly_excess: list[float]) -> float | None:
    if len(monthly_excess) < 3:
        return None
    mu = sum(monthly_excess) / len(monthly_excess)
    sd = math.sqrt(sum((x - mu) ** 2 for x in monthly_excess) / (len(monthly_excess) - 1))
    return None if sd == 0 else round(mu / sd * math.sqrt(12), 3)


def _spy_benchmark(spy: list[dict], months: list[str]) -> dict:
    """SPY buy-and-hold over the same months: monthly excess return over
    the T-bill rate, from month-end closes."""
    month_end = {}
    for b in spy:
        month_end[b["date"][:7]] = b["close"]
    prev_month = _month_range("2000-01", months[0])[-2]
    closes = [month_end[prev_month]] + [month_end[m] for m in months]
    rets = [closes[i + 1] / closes[i] - 1 for i in range(len(months))]
    excess = [r - rmb.TBILL_ANNUAL[int(m[:4])] / 12 for r, m in zip(rets, months)]
    peak, dd, level = 1.0, 0.0, 1.0
    for r in rets:
        level *= 1 + r
        peak = max(peak, level)
        dd = min(dd, level / peak - 1)
    return {"total_return": round(closes[-1] / closes[0] - 1, 4), "sharpe": _sharpe(excess),
            "max_drawdown": round(dd, 4)}


def _evaluate(trades: list[dict], key: str, n_weeks: int, months: list[str]) -> dict:
    """Ladder metrics for one stream. key = "spread" or "csp"."""
    def pnl(t):
        return t["realized_pnl"] if key == "spread" else t["csp"]["realized_pnl"]

    def exit_date(t):
        return t["exit_date"] if key == "spread" else t["csp"]["exit_date"]

    def capital(t):
        return t["max_loss"] if key == "spread" else t["csp"]["collateral"]

    trades = [t for t in trades if key == "spread" or t["csp"] is not None]
    if not trades:
        return {"count": 0}
    events = sorted([(t["date"], capital(t)) for t in trades] + [(exit_date(t), -capital(t)) for t in trades],
                    key=lambda e: (e[0], e[1]))  # same-day exits free capital before entries
    cur = peak_cap = 0.0
    for _, c in events:
        cur += c
        peak_cap = max(peak_cap, cur)
    by_month = {m: 0.0 for m in months}
    for t in trades:
        by_month[exit_date(t)[:7]] = by_month.get(exit_date(t)[:7], 0.0) + pnl(t)
    cum = high = max_dd = 0.0
    for m in sorted(by_month):
        cum += by_month[m]
        high = max(high, cum)
        max_dd = min(max_dd, cum - high)
    with_exits = [m for m in by_month if any(exit_date(t)[:7] == m for t in trades)]
    pos = sum(1 for m in with_exits if by_month[m] > 0)
    total = sum(pnl(t) for t in trades)
    wins = [pnl(t) for t in trades if pnl(t) > 0]
    losses = [pnl(t) for t in trades if pnl(t) <= 0]
    by_year: dict[str, float] = {}
    for t in trades:
        by_year[exit_date(t)[:4]] = by_year.get(exit_date(t)[:4], 0.0) + pnl(t)
    reasons: dict[str, int] = {}
    for t in trades:
        r = t["exit_reason"] if key == "spread" else t["csp"]["exit_reason"]
        reasons[r] = reasons.get(r, 0) + 1
    sharpe = _sharpe([by_month[m] / peak_cap for m in sorted(by_month)]) if peak_cap else None
    return {
        "count": len(trades), "win_rate": round(len(wins) / len(trades), 4),
        "total_pnl": round(total, 2), "avg_pnl": round(total / len(trades), 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "worst_trade": round(min(pnl(t) for t in trades), 2), "exit_reasons": reasons,
        "avg_per_week": round(total / n_weeks, 2),
        "peak_capital": round(peak_cap, 2),
        "return_on_peak_capital": round(total / peak_cap, 4) if peak_cap else None,
        "max_drawdown": round(max_dd, 2), "sharpe": sharpe,
        "months_with_exits": len(with_exits), "positive_months": pos,
        "positive_month_frac": round(pos / len(with_exits), 4) if with_exits else None,
        "worst_month": round(min(by_month.values()), 2), "best_month": round(max(by_month.values()), 2),
        "by_year": {y: round(v, 2) for y, v in sorted(by_year.items())},
        "by_month": {m: round(v, 2) for m, v in sorted(by_month.items())},
    }


def run() -> dict:
    with open(PLAN_OUT) as f:
        p = json.load(f)
    man, bars_by_id, spy = load_manifest(), load_all_bars(), load_spy_bars()
    trades, skips = [], {}
    for row in p["rows"]:
        t, why = _simulate(row, man, bars_by_id)
        if t is None:
            skips[why] = skips.get(why, 0) + 1
        else:
            trades.append(t)
    n_weeks = len(p["rows"])
    months = _month_range(p["rows"][0]["date"][:7], max(t["exit_date"] for t in trades)[:7])
    spy_bm = _spy_benchmark(spy, months)
    out = {"entry_weeks": n_weeks, "skips": skips, "spy_buy_and_hold": spy_bm, "variants": {}}
    print(f"entry weeks {n_weeks}; simulated {len(trades)}; skips {skips}")
    print(f"SPY buy-and-hold {months[0]}..{months[-1]}: return {spy_bm['total_return']:+.1%}, "
          f"Sharpe {spy_bm['sharpe']}, max drawdown {spy_bm['max_drawdown']:.1%}\n")
    for name, (need_gate, need_switch) in VARIANTS.items():
        sel = [t for t in trades if (t["gate_pass"] or not need_gate) and (t["switch_on"] or not need_switch)]
        res = {"spread": _evaluate(sel, "spread", n_weeks, months), "csp": _evaluate(sel, "csp", n_weeks, months)}
        s = res["spread"]
        res["pass"] = bool(s.get("count") and s["total_pnl"] > 0
                           and (s["positive_month_frac"] or 0) >= PASS_POSITIVE_MONTH_FRAC
                           and s["sharpe"] is not None and spy_bm["sharpe"] is not None
                           and s["sharpe"] >= spy_bm["sharpe"])
        out["variants"][name] = res
        tag = "HEADLINE " if name == HEADLINE else ""
        print(f"{tag}[{name}] PASS={res['pass']}")
        for k in ("spread", "csp"):
            x = res[k]
            if not x.get("count"):
                print(f"  {k}: no trades")
                continue
            print(f"  {k}: n={x['count']} win {x['win_rate']:.0%} total ${x['total_pnl']:,.0f} "
                  f"(${x['avg_per_week']}/week) worst trade ${x['worst_trade']:,.0f} | peak capital "
                  f"${x['peak_capital']:,.0f} -> {x['return_on_peak_capital']:+.1%} | Sharpe {x['sharpe']} | "
                  f"positive months {x['positive_months']}/{x['months_with_exits']} worst month "
                  f"${x['worst_month']:,.0f} | max DD ${x['max_drawdown']:,.0f} | by year {x['by_year']}")
        print()
    print("POST-HOC cost sensitivity (NOT pre-registered): fixed "
          f"${REALISTIC_COST_PER_LEG:.2f} per leg per side instead of the %-of-price haircut")
    real = [t for row in p["rows"] for t in [_simulate(row, man, bars_by_id, REALISTIC_COST_PER_LEG)[0]] if t]
    out["post_hoc_realistic_costs"] = {}
    for name, (need_gate, need_switch) in VARIANTS.items():
        sel = [t for t in real if (t["gate_pass"] or not need_gate) and (t["switch_on"] or not need_switch)]
        res = {"spread": _evaluate(sel, "spread", n_weeks, months), "csp": _evaluate(sel, "csp", n_weeks, months)}
        out["post_hoc_realistic_costs"][name] = res
        for k in ("spread", "csp"):
            x = res[k]
            print(f"  [{name}] {k}: n={x['count']} win {x['win_rate']:.0%} total ${x['total_pnl']:,.0f} "
                  f"(${x['avg_per_week']}/week) | peak capital ${x['peak_capital']:,.0f} -> "
                  f"{x['return_on_peak_capital']:+.1%} | Sharpe {x['sharpe']} | positive months "
                  f"{x['positive_months']}/{x['months_with_exits']} worst month ${x['worst_month']:,.0f} | "
                  f"max DD ${x['max_drawdown']:,.0f} | by year {x['by_year']}")
    print()
    out["trades"] = trades
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
