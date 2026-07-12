"""
Council demo — the keystone proof for Milestone 3: Fundamentals +
Technicals feed the Judge, the Judge decides, and only a buy/sell (never
a hold) reaches PaperBroker, which runs the Risk vetoer before anything
executes. Every outcome — trade, hold, or veto — is logged with reasoning.
Zero real-money risk: nothing here can place a live order.

Like every other data-touching module in this project (execution/robinhood.py,
agents/fundamentals_seat.py), this script is agent-mediated: it has no MCP
connection of its own. It expects a bundle of already-fetched raw MCP
responses, PLUS the Fundamentals verdict — which, per
agents/fundamentals_seat.py's own design, is formed by the calling agent
reading build_brief()'s output, not computed mechanically. Run it as:

    python -m agents.demo_council SYMBOL BUNDLE_JSON_PATH [QUANTITY]

BUNDLE_JSON_PATH must contain:
    "quote":                  get_equity_quotes response for [symbol]
    "atr", "rsi", "ema":      get_equity_technical_indicators responses
                               (type=atr/rsi/ema) for symbol
    "robinhood_fundamentals": get_equity_fundamentals response for
                               [symbol] — used only to resolve sector for
                               the Risk vetoer's sector check, NOT fed
                               into the Technicals seat (that would break
                               its price-only isolation)
    "fundamentals_verdict":   agents.fundamentals_seat.form_verdict()
                               output, formed by the calling agent from
                               build_brief(symbol)

Known limitations:
- sector_map here only covers the traded symbol, not every other held
  position — PaperBroker's sector check handles a missing entry
  gracefully (excluded from the sum, not an error), so this under-covers
  rather than misfires, but it means the sector check is incomplete for
  accounts holding multiple positions. A fuller run would fetch
  fundamentals for every held symbol too.
- the regime filter (agents/regime.py) reuses the same `ema`/`atr_pct`
  already fetched for the Technicals seat, rather than a separate
  longer-period EMA (config.REGIME_EMA_LOOKBACK_DAYS' guidance) — the
  bundle only carries one EMA reading. Fine for this demo; a production
  cadence would fetch a second, longer-period EMA for the regime read.
"""

import json
import sys

from . import judge, regime, technicals
from execution import config, robinhood, trade_log
from execution.paper_broker import PaperBroker, TradeError


def run_demo(symbol: str, quantity: float, bundle: dict) -> dict:
    """Run one full council pass for `symbol`; route a buy/sell through
    PaperBroker (Risk vetoer included), log a hold, or report a veto."""
    symbol = symbol.upper()
    price = robinhood.get_quote(symbol, bundle["quote"])
    atr_pct = robinhood.get_atr_pct(symbol, price, bundle["atr"])
    rsi = robinhood.get_rsi(symbol, bundle["rsi"])
    ema = robinhood.get_ema(symbol, bundle["ema"])
    sector_map = robinhood.get_sectors([symbol], bundle["robinhood_fundamentals"])

    technicals_view = technicals.build_view(symbol, price, ema=ema, rsi=rsi, atr_pct=atr_pct)
    regime_view = regime.regime_stance(symbol, price, ema=ema, atr_pct=atr_pct)
    fundamentals_verdict = bundle["fundamentals_verdict"]

    decision = judge.decide(fundamentals_verdict, technicals_view, regime=regime_view, quantity=quantity)
    # Baseline stays regime-blind on purpose — it's the "no seat isolation,
    # no gate at all" comparison; adding the regime gate to it would
    # defeat the point of measuring what the real Judge's gates add.
    baseline = judge.baseline_decide(fundamentals_verdict, technicals_view, quantity=quantity)

    print(config.mode_banner())
    print(f"{symbol} @ ${price:,.2f}   ATR {atr_pct * 100:.2f}%   RSI {rsi:.1f}   EMA {ema:.2f}")
    print(f"\nFundamentals: {fundamentals_verdict['stance']} "
          f"({fundamentals_verdict['confidence']}) — {fundamentals_verdict['reasons']}")
    print(f"Technicals:   {technicals_view['stance']} "
          f"({technicals_view['confidence']}) — {technicals_view['reasons']}")
    print(f"Regime:       {regime_view['state']} (tradeable={regime_view['tradeable']}) — {regime_view['reason']}")
    print(f"\nJudge decision: {decision}")
    print(f"Baseline (ablation, never acted on): {baseline}")

    # Ablation hook: log the baseline unconditionally, alongside whatever
    # the real Judge decided — this is what lets us later compare the two
    # decision logs and check whether seat isolation + the conjunctive
    # gate actually beat a plain single-model filter.
    trade_log.record(
        "baseline", symbol, baseline["target_quantity"], price, paper=True,
        reason=baseline["rationale"],
        # NOTE: key this "baseline_action", not "action" — trade_log.record()
        # already sets the top-level "action" field from its first
        # positional arg ("baseline" above), and entry.update(extra) would
        # silently clobber it if extra also had an "action" key.
        extra={"seat": "judge_baseline", "baseline_action": baseline["action"], "confidence": baseline["confidence"]},
    )

    broker = PaperBroker()
    if decision["action"] == "hold":
        # No-trade is the default, and it's just as visible in the log as
        # a fill — the Judge itself never writes to trade_log; that's the
        # orchestrator's job here, same as PaperBroker (not risk_vetoer)
        # is what writes veto records. A regime-caused hold gets its own
        # action ("regime_sitout" — see agents/regime.py) rather than the
        # generic "hold", so the track record shows how often and why the
        # council sat out on price conditions specifically — and, being
        # a non-fill either way, it's excluded from round_trip_stats()
        # exactly like a plain hold or a risk-vetoer veto.
        is_regime_sitout = not regime_view["tradeable"]
        trade_log.record(
            "regime_sitout" if is_regime_sitout else "hold", symbol, 0, price, paper=True,
            reason=decision["rationale"],
            extra={"seat": "judge", "confidence": decision["confidence"], "seat_inputs": decision["seat_inputs"]},
        )
        account = broker.account({symbol: price})
        print(f"\nNo trade — logged as {'regime_sitout' if is_regime_sitout else 'hold'}. "
              f"Paper account unchanged: {account}")
        return account

    reason = f"Judge: {decision['rationale']}"
    try:
        if decision["action"] == "buy":
            trade = broker.buy(
                symbol, decision["target_quantity"], price, reason=reason,
                prices={symbol: price}, atr_pct=atr_pct, sector_map=sector_map,
            )
        else:
            trade = broker.sell(
                symbol, decision["target_quantity"], price, reason=reason,
                prices={symbol: price},
            )
    except TradeError as e:
        # The Judge doesn't get the last word — this proves it: a
        # buy/sell the Judge approved can still be blocked here.
        print(f"\nJudge said {decision['action']}, but the Risk vetoer blocked it: {e}")
        return broker.account({symbol: price})

    account = broker.account({symbol: price})
    print(f"\nTrade executed: {trade}")
    print(f"Paper account: {account}")
    return account


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    cli_symbol = sys.argv[1]
    with open(sys.argv[2]) as f:
        cli_bundle = json.load(f)
    cli_quantity = float(sys.argv[3]) if len(sys.argv) > 3 else judge.DEFAULT_QUANTITY

    run_demo(cli_symbol, cli_quantity, cli_bundle)
