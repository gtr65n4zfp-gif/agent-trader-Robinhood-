"""
Real-data (or snapshot / demo) calibration backtest.

    python -m kalshi.run_real_backtest                 # demo on synthetic data
    python -m kalshi.run_real_backtest --fetch         # pull real settled markets
    python -m kalshi.run_real_backtest --snapshot logs/kalshi_markets.json

What it does, honestly:
  1. Get a set of RESOLVED markets (price + realized outcome).
  2. Split into train / test.
  3. Fit a price-only CalibrationModel on train — this MEASURES the market's
     mispricing (favorite-longshot bias) from data.
  4. Trade the test split with that model through the exact same fee-gated
     strategy and paper broker. No lookahead: the model never saw a test
     market's outcome.
  5. Report the calibration table and net-of-fees results, next to a naive
     "price is truth" baseline (which, correctly, barely trades a fair market).

Data sources:
  --fetch      : live Kalshi API (blocked in the build sandbox; run locally).
                 Saves a snapshot to --snapshot for reuse.
  --snapshot P : load/replay a saved snapshot of raw Kalshi market dicts.
  (default)    : SYNTHETIC demo data from market_sim, clearly labeled — so the
                 whole pipeline is runnable with no network and no key. Numbers
                 from demo data describe the SIMULATOR, not reality.
"""

import argparse
import os

from . import config
from .backtest import run_on_markets
from .calibration import CalibrationModel, train_test_split
from .market_sim import MarketGenerator

_DEFAULT_SNAPSHOT = os.path.join(config.LOG_DIR, "kalshi_markets.json")


def _load_markets(args):
    """Return (markets, source_label, is_real)."""
    if args.fetch:
        from .client import KalshiClient, to_resolved_market
        client = KalshiClient()
        raw = client.list_markets(status="settled", limit=args.limit,
                                  max_pages=args.max_pages)
        client.save_snapshot(raw, args.snapshot)
        markets = [m for m in (to_resolved_market(r) for r in raw) if m]
        return markets, f"LIVE Kalshi ({len(raw)} raw -> {len(markets)} usable)", True

    if args.snapshot and os.path.exists(args.snapshot) and not args.demo:
        from .client import KalshiClient, to_resolved_market
        raw = KalshiClient.load_snapshot(args.snapshot)
        markets = [m for m in (to_resolved_market(r) for r in raw) if m]
        return markets, f"snapshot {args.snapshot} ({len(markets)} markets)", True

    # Synthetic demo fallback.
    gen = MarketGenerator(seed=args.seed, markets_per_day=1,
                          mispricing=args.demo_mispricing, prob_dist="realistic")
    markets = []
    for _, day in gen.stream(args.demo_n):
        markets.extend(day)
    label = (f"SYNTHETIC demo ({args.demo_n} markets, mispricing="
             f"{args.demo_mispricing}) — describes the SIMULATOR, not reality")
    return markets, label, False


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fetch", action="store_true", help="pull live settled markets")
    p.add_argument("--snapshot", default=_DEFAULT_SNAPSHOT)
    p.add_argument("--demo", action="store_true",
                   help="force synthetic demo even if a snapshot exists")
    p.add_argument("--demo-n", type=int, default=6000)
    p.add_argument("--demo-mispricing", type=float, default=0.10)
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--max-pages", type=int, default=25)
    p.add_argument("--train-frac", type=float, default=0.6)
    p.add_argument("--days", type=int, default=252)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()

    print(config.mode_banner())
    try:
        markets, source, is_real = _load_markets(args)
    except Exception as e:
        print(f"\nCould not load market data: {e}")
        print("Tip: in a sandbox with Kalshi blocked, omit --fetch to run the "
              "synthetic demo,\nor point --snapshot at a file you saved locally "
              "with --fetch.")
        return

    print(f"\nData source: {source}")
    if len(markets) < 200:
        print(f"Only {len(markets)} resolved markets — too few to calibrate "
              "meaningfully.\nFetch more (raise --limit/--max-pages) or use the "
              "demo.")
        return

    train, test = train_test_split(markets, train_frac=args.train_frac,
                                   seed=args.seed)
    model = CalibrationModel().fit(train)

    print(f"\nTrain/test: {len(train)} / {len(test)} markets")
    print("\nCalibration (fit on TRAIN — is the market actually mispriced?):")
    print(model.report())
    print(f"\n  mean |gap| over trusted bins: {model.mean_abs_gap():.4f}  "
          f"(near 0 = efficient; bigger = more exploitable mispricing)")
    print("  CAVEAT: a bin of n samples has ~0.5/sqrt(n) of pure sampling noise "
          "in its gap,\n  so a small mean|gap| can be entirely noise. The "
          "OUT-OF-SAMPLE backtest below —\n  not this number — is what decides "
          "whether the gap is real and tradable.")

    print("\n" + "=" * 70)
    print("OUT-OF-SAMPLE BACKTEST ON TEST SPLIT (calibration model)")
    print("=" * 70)
    r = run_on_markets(test, model.predict, days=args.days)
    print(r.report())

    print("\n" + "-" * 70)
    print("BASELINE — 'price is truth' (no edge; should barely trade):")
    base = run_on_markets(test, lambda m: m.yes_price, days=args.days)
    print(f"  bets settled ............ {base.bets_settled}")
    print(f"  total return ............ {base.total_return*100:+.2f}%")

    print("\n" + "=" * 70)
    if not is_real:
        print("NOTE: this was SYNTHETIC demo data. It proves the pipeline works "
              "and\nrecovers a known mispricing — it says NOTHING about real "
              "Kalshi markets.\nRun with --fetch (locally, where Kalshi is "
              "reachable) for a real answer.")
    else:
        print("This ran on REAL resolved markets. Even so, one historical window "
              "is not\nthe go-live gate — see kalshi/KALSHI_DESIGN.md §7. Real "
              "money stays blocked\nby config.assert_paper_mode() until that bar "
              "is met on a forward paper record.")


if __name__ == "__main__":
    main()
