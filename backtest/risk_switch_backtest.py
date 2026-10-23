"""
backtest/risk_switch_backtest.py -- the MARKET RISK-OFF SWITCH test (the
"safety switch"). This is the follow-up hypothesis from
agents/MOMENTUM_RESULTS.md. Results write-up: agents/RISK_SWITCH_RESULTS.md.

THE IDEA, in plain terms: keep holding a basket, but once a month look at
the overall market's trend. If the market is clearly falling, step the whole
portfolio aside into cash until it isn't. It isn't a stock picker. It's a
seatbelt whose only job is to cut big losses.

PRE-REGISTERED, fixed before any result was seen (so the rules can't be
tuned to the answer):
- Market signal: SPY's own 12-1 time-series momentum, the exact signal in
  strategies/time_series_momentum.py on monthly bars (12-bar lookback,
  skip 1), read from SPY's history up to that month only. Since the switch
  was armed, the signal and variant logic live in agents/market_switch.py,
  and this backtest imports them from there, so the tested rule is the live
  rule.
- Two variants, no more:
    bearish_only (PRIMARY): step aside only when SPY's trend is clearly DOWN.
    not_bullish  (stricter sensitivity check): step aside whenever SPY's
                 trend is not clearly UP, so flat counts as risk-off too.
- What it protects: a fixed equal-weight basket of a universe from
  backtest/universes.py, rebalanced monthly. The benchmark is that same
  basket held all the time.
- Cash earns the approximate 3-month T-bill rate (TBILL_ANNUAL in
  backtest/run_momentum_backtest.py), and Sharpe is excess return over it.
  Every switch in or out pays config.SLIPPAGE_BPS on the whole portfolio.
- PASS = a smaller max drawdown than the benchmark AND a Sharpe at least as
  good, over the full period, per universe. Windows where both sides lose
  money are judged on return and drawdown, not Sharpe (see
  STRATEGY_LIBRARY.md).

Agent-mediated like every other backtest module: it reads saved
get_equity_historicals responses and never calls Robinhood itself.

    python -m backtest.risk_switch_backtest UNIVERSE RAW_JSON [RAW_JSON ...]

SPY must be in one of the raw files. It's read as the market signal whether
or not it's in the universe being protected.

Not modeled: the cost of the monthly equal-weight rebalance. It's identical
on both sides, and small at 5 bps, so it doesn't change the comparison.
"""

from __future__ import annotations

import json
import os
import sys

from agents.market_switch import VARIANTS, is_risk_on, market_view
from execution import config

from . import data as backtest_data
from . import momentum_backtest as mb
from . import run_momentum_backtest as rmb

# Older market periods for the long-history (spy_only) run, from well-known
# market history. They're reporting windows only; the switch never sees
# them. The signal needs ~32 months of warm-up from Robinhood's Sep 1999
# start, so the dot-com bust is only covered from Apr 2002 on.
LONG_HISTORY_WINDOWS = [
    ("dot-com bust (tail, from 2002)", "2002-01-01", "2003-03-31"),
    ("2003-07 bull",                  "2003-04-01", "2007-09-30"),
    ("2008 financial crisis",         "2007-10-01", "2009-03-31"),
    ("2009-16 bull",                  "2009-04-01", "2016-12-31"),
]

def market_stances(spy_bars: list[dict]) -> dict[str, str]:
    """{date: the market's momentum stance as of that month's close}, each
    computed from SPY's history up to and including that date only
    (bars_through() is the no-lookahead choke point). Dates without enough
    history are absent.

    The stance comes from agents.market_switch.market_view(), the SAME code
    the live switch runs, so this backtest can't drift from what's armed."""
    out: dict[str, str] = {}
    for b in spy_bars:
        hist = [x["close"] for x in backtest_data.bars_through(spy_bars, b["date"])]
        view = market_view(hist, "SPY")
        if view is not None:
            out[b["date"]] = view["stance"]
    return out


def run_overlay(bars_by_symbol: dict[str, list[dict]], stances: dict[str, str], variant: str,
                cash_annual_by_year: dict[int, float] | None = None, cost_bps: float = config.SLIPPAGE_BPS,
                periods_per_year: float = 12) -> dict:
    """
    Step month by month through the dates every basket symbol has. At each
    decision date with a market stance: invested -> earn the basket's
    next-month equal-weight return; risk-off -> earn that month's cash rate.
    The benchmark always earns the basket. Returns the per-month steps plus
    full-period summaries.
    """
    if not bars_by_symbol:
        raise ValueError("empty basket")
    closes = {sym: {b["date"]: b["close"] for b in bars} for sym, bars in bars_by_symbol.items()}
    dates = sorted(set.intersection(*(set(c) for c in closes.values())))
    cost = cost_bps / 10_000.0

    steps: list[dict] = []
    pos, started = 0, False
    for d, nxt in zip(dates, dates[1:]):
        if d not in stances:
            continue
        basket = sum(c[nxt] / c[d] - 1.0 for c in closes.values()) / len(closes)
        cash = (cash_annual_by_year.get(int(d[:4]), 0.0) / periods_per_year) if cash_annual_by_year else 0.0
        invested = 1 if is_risk_on(stances[d], variant) else 0
        steps.append({
            "date": d, "stance": stances[d], "invested": invested,
            "basket": basket, "cash": cash,
            "overlay": (basket if invested else cash) - abs(invested - pos) * cost,
            "buy_hold": basket - (0.0 if started else cost),
        })
        pos, started = invested, True
    return {"variant": variant, "steps": steps, **summarize(steps, periods_per_year)}


def summarize(steps: list[dict], periods_per_year: float = 12) -> dict:
    """Overlay vs. benchmark over `steps` (all of them, or a date window)."""
    if not steps:
        return {"months": 0}
    cash = [s["cash"] for s in steps]
    cash_series = cash if any(cash) else None
    overlay = mb._summary([s["overlay"] for s in steps], periods_per_year, cash_series)
    bench = mb._summary([s["buy_hold"] for s in steps], periods_per_year, cash_series)
    switches = sum(1 for a, b in zip(steps, steps[1:]) if a["invested"] != b["invested"])
    smaller_dd = overlay["max_drawdown"] > bench["max_drawdown"]
    sharpe_ok = (overlay["sharpe"] is not None and bench["sharpe"] is not None
                 and overlay["sharpe"] >= bench["sharpe"])
    return {
        "months": len(steps), "first": steps[0]["date"], "last": steps[-1]["date"],
        "time_invested": round(sum(s["invested"] for s in steps) / len(steps), 4),
        "switches": switches,
        "overlay": overlay, "buy_hold": bench,
        "smaller_drawdown": smaller_dd,
        "sharpe_at_least_as_good": sharpe_ok,
        "passes": smaller_dd and sharpe_ok,
    }


def risk_off_spells(steps: list[dict]) -> list[dict]:
    """Each run of consecutive risk-off decisions: when it stepped aside,
    for how long, and what the basket did meanwhile -- the losses dodged, or
    the gains missed."""
    spells, cur = [], []
    for s in steps + [{"invested": 1}]:
        if not s["invested"]:
            cur.append(s)
        elif cur:
            basket_meanwhile = 1.0
            for x in cur:
                basket_meanwhile *= 1.0 + x["basket"]
            spells.append({"from": cur[0]["date"], "to": cur[-1]["date"], "months": len(cur),
                           "basket_return_while_out": round(basket_meanwhile - 1.0, 4)})
            cur = []
    return spells


def eligible_basket(raw_by_symbol: dict[str, dict], first_decision: str) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Clean each symbol (same hygiene as the momentum driver) and keep only
    symbols with real, continuous prices from the first decision date on. A
    symbol that only starts trading mid-test (or resumes after a halt) can't
    be part of a fixed basket. It's reported, never silently dropped."""
    basket, excluded = {}, {}
    for sym, raw in raw_by_symbol.items():
        bars, note = rmb.clean_bars(sym, raw)
        if not bars or bars[0]["date"] > first_decision:
            excluded[sym] = (note or "no data") + "; not trading continuously for the whole test window"
            continue
        basket[sym] = bars
    return basket, excluded


def _line(name: str, r: dict) -> str:
    o, b = r["overlay"], r["buy_hold"]
    f = rmb._fmt
    return (f"  {name:32} ({r['months']:3} mo, invested {r['time_invested'] * 100:3.0f}%, {r['switches']:2} switches): "
            f"switch {f(o['total_return'])} Sharpe {f(o['sharpe'], False)} DD {f(o['max_drawdown'])}  |  "
            f"hold {f(b['total_return'])} Sharpe {f(b['sharpe'], False)} DD {f(b['max_drawdown'])}")


def main(label: str, raw_paths: list[str]) -> dict:
    all_raw = rmb.load_raw(raw_paths)
    if "SPY" not in all_raw:
        raise SystemExit("SPY must be in one of the raw files -- it's the market signal.")
    spy_bars, _ = rmb.clean_bars("SPY", all_raw["SPY"])
    stances = market_stances(spy_bars)
    first_decision = min(stances)

    universe_raw, excluded = rmb.select_universe(label, all_raw)
    basket, not_continuous = eligible_basket(universe_raw, first_decision)
    excluded.update(not_continuous)

    print(f"[{label}] {config.mode_banner()} -- market risk-off switch on SPY's 12-1 trend, "
          f"protecting an equal-weight basket of {len(basket)} symbols; cash earns ~3-month T-bill (approx.); "
          f"{config.SLIPPAGE_BPS:g} bps per switch")
    for sym, why in excluded.items():
        print(f"  {sym} EXCLUDED -- {why}")

    out = {"label": label, "basket": sorted(basket), "excluded": excluded, "variants": {}}
    for variant, desc in VARIANTS.items():
        res = run_overlay(basket, stances, variant, cash_annual_by_year=rmb.TBILL_ANNUAL)
        print(f"\n{variant}: {desc}")
        print(_line("full period", res))
        print(f"  -> smaller drawdown: {'YES' if res['smaller_drawdown'] else 'no'};  "
              f"Sharpe at least as good: {'YES' if res['sharpe_at_least_as_good'] else 'no'};  "
              f"PASS: {'YES' if res['passes'] else 'NO'}")
        regimes = []
        for name, start, end in LONG_HISTORY_WINDOWS + rmb.REGIME_WINDOWS:
            r = summarize([s for s in res["steps"] if start <= s["date"] <= end])
            if r["months"]:
                print(_line(name, r))
                regimes.append({"name": name, **r})
        spells = risk_off_spells(res["steps"])
        print("  stepped aside:" + ("" if spells else " never"))
        for sp in spells:
            print(f"    {sp['from'][:7]}..{sp['to'][:7]} ({sp['months']:2} mo): basket did "
                  f"{sp['basket_return_while_out'] * 100:+.1f}% meanwhile")
        out["variants"][variant] = {k: v for k, v in res.items() if k != "steps"} | {
            "regimes": regimes, "risk_off_spells": spells}

    out_path = os.path.join(config.LOG_DIR, "backtests", f"risk_switch_results_{label}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nFull results: {out_path}")
    return out


if __name__ == "__main__":
    if len(sys.argv) < 3 and not (len(sys.argv) == 2 and sys.argv[1] == "--self-test"):
        print(__doc__)
        sys.exit(1)
    if sys.argv[1:] != ["--self-test"]:
        main(sys.argv[1], sys.argv[2:])
        sys.exit(0)

    # --- Self-test: synthetic monthly data, offline, deterministic -------------
    from datetime import date

    def _monthly(closes: list[float], symbol: str = "SPY") -> list[dict]:
        out = []
        for i, c in enumerate(closes):
            y, m = 2000 + i // 12, i % 12 + 1
            out.append({"date": date(y, m, 1).strftime("%Y-%m-%d"), "open": c, "high": c, "low": c,
                        "close": c, "volume": 1000})
        return out

    # Long rise, a long slide (a bear market), then a long recovery.
    path = [100.0 * 1.012 ** i for i in range(60)]
    path += [path[-1] * 0.97 ** i for i in range(1, 25)]
    path += [path[-1] * 1.015 ** i for i in range(1, 40)]
    spy = _monthly(path)
    stances = market_stances(spy)

    print("No lookahead: a stance must not change when later months are removed...")
    probe = sorted(stances)[len(stances) // 2]
    truncated = market_stances(backtest_data.bars_through(spy, probe))
    assert truncated[probe] == stances[probe], (probe, truncated[probe], stances[probe])
    print(f"  {probe}: {stances[probe]} either way -- PASS")

    print("\nBear-market slide: the switch should step aside and cut the drawdown...")
    res = run_overlay({"SPY": spy}, stances, "bearish_only", cash_annual_by_year={y: 0.03 for y in range(2000, 2015)})
    print(f"  invested {res['time_invested']:.0%}, switches {res['switches']}, "
          f"switch DD {res['overlay']['max_drawdown']}, hold DD {res['buy_hold']['max_drawdown']}")
    assert res["time_invested"] < 1.0 and res["switches"] >= 2, res
    assert res["smaller_drawdown"], res
    spells = risk_off_spells(res["steps"])
    assert spells and all(sp["months"] >= 1 for sp in spells), spells
    print(f"  stepped aside: {[(s['from'][:7], s['to'][:7], s['basket_return_while_out']) for s in spells]}")

    print("\nThe benchmark ignores the switch: identical across variants...")
    strict = run_overlay({"SPY": spy}, stances, "not_bullish", cash_annual_by_year={y: 0.03 for y in range(2000, 2015)})
    assert strict["buy_hold"] == res["buy_hold"], (strict["buy_hold"], res["buy_hold"])
    assert strict["time_invested"] <= res["time_invested"], "stricter variant can't be invested MORE"
    print(f"  bearish_only invested {res['time_invested']:.0%} >= not_bullish {strict['time_invested']:.0%} -- PASS")

    print("\nPASS -- risk-off switch harness: no lookahead, steps aside in a slide, benchmark untouched.")
