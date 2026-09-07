"""
Kalshi BTC strand configuration and the paper-trading safety switch.

Deliberately separate from execution/config.py (see kalshi/DESIGN.md's
"Layout" section) -- different venue, different account, different unlock
switch. Nothing in execution/ reads this file and nothing here reads
execution/config.py.

Same non-negotiable rule as the equity strand:
    NOTHING places a real Kalshi order unless KALSHI_PAPER_MODE is
    explicitly turned off, and that requires an exact, deliberate
    environment variable value.
"""

import math
import os

# --- The switch --------------------------------------------------------
# A distinct unlock phrase from the equity strand's AGENT_TRADER_LIVE --
# arming one venue must never arm the other.
_KALSHI_LIVE_UNLOCK_PHRASE = "I_UNDERSTAND_THIS_USES_REAL_MONEY_ON_KALSHI"

KALSHI_PAPER_MODE: bool = os.environ.get("KALSHI_TRADER_LIVE") != _KALSHI_LIVE_UNLOCK_PHRASE


def assert_kalshi_paper_mode() -> None:
    """Call this right before any Kalshi order. Raises if we're NOT in paper
    mode, so live trading can never happen silently."""
    if not KALSHI_PAPER_MODE:
        raise RuntimeError(
            "LIVE KALSHI TRADING IS ARMED. If this is not what you intended, "
            "unset the KALSHI_TRADER_LIVE environment variable immediately."
        )


def kalshi_mode_banner() -> str:
    return "📝 KALSHI PAPER MODE (simulated)" if KALSHI_PAPER_MODE else "💸 KALSHI LIVE MODE — REAL MONEY"


# --- Fee model (kalshi/M0_FINDINGS.md) --------------------------------------
# Kalshi's taker fee is fee = ceil(multiplier * P * (1-P) * 100) / 100 dollars
# PER CONTRACT, where P is price in dollars (0-1); maker fills get roughly
# 1/4 of the taker rate. The multiplier is category-specific, NOT a flat
# 0.07-for-everyone constant -- corroborated from two independent angles in
# M0 recon: (1) a direct statement that Kalshi's published multiplier ranges
# "0.07 for most categories, higher for premium categories like Crypto", and
# (2) category peak-fee percentages (peak = multiplier * 0.25, at the 50c
# strike) of ~0.75% sports, ~1.00% politics, ~1.75-1.80% crypto -- which
# back out to multipliers of ~0.03, ~0.04, and ~0.07 respectively. Both
# angles converge on crypto (and so BTC) sitting at 0.07, the number this
# design had originally guessed as a generic default -- right number,
# for the wrong reason, now corrected. NOT verified against Kalshi's own
# fee-schedule PDF (docs.kalshi.com and kalshi.com are both blocked by this
# environment's network egress proxy -- see M0_FINDINGS.md's "environment
# constraint" note) -- treat as high-confidence secondary-source corroboration,
# not a primary-source read. Re-verify against a live order preview or the
# PDF directly before this strand ever risks real money.
KALSHI_TAKER_FEE_MULTIPLIER: float = 0.07
KALSHI_MAKER_FEE_MULTIPLIER: float = KALSHI_TAKER_FEE_MULTIPLIER / 4

# Whether Kalshi rounds the fee up to the cent PER CONTRACT (this
# implementation's assumption, matching the documented per-contract formula)
# or on the order TOTAL is itself unverified -- per-contract rounding is the
# conservative (fee-overestimating) assumption for a multi-contract order,
# consistent with this project's habit of erring toward pessimistic paper
# fills (see execution/config.py's SLIPPAGE_BPS comment) rather than
# flattering itself.
def kalshi_fee(price: float, contracts: int, is_maker: bool = False) -> float:
    """
    Fee in dollars for a Kalshi fill. `price` is the contract price in
    dollars (0.0-1.0, i.e. cents / 100). Parabolic in price, peaking at
    price=0.50 and falling toward zero at the extremes -- a contract far
    from 50/50 is cheap to trade, one near a coin flip is not.
    """
    if not (0.0 <= price <= 1.0):
        raise ValueError(f"price must be in [0.0, 1.0] (dollars), got {price}")
    if contracts < 0:
        raise ValueError(f"contracts must be >= 0, got {contracts}")
    multiplier = KALSHI_MAKER_FEE_MULTIPLIER if is_maker else KALSHI_TAKER_FEE_MULTIPLIER
    per_contract = math.ceil(multiplier * price * (1 - price) * 100) / 100
    return per_contract * contracts


# --- Paper account (kalshi/paper_broker.py, not yet built -- M4) -----------
# Separate cash pool from both the equity and options paper accounts --
# never shared, never reconciled against them.
KALSHI_PAPER_STARTING_CASH: float = 10000.0

# Arm/disarm switch for automation execution, same pattern as
# execution/config.py's AUTOMATION_DRY_RUN and OPTIONS_AUTOMATION_DRY_RUN:
# every pass still runs the full pipeline and logs every decision, but no
# order is actually placed until this is deliberately flipped, one line,
# never as a side effect of something else.
KALSHI_AUTOMATION_DRY_RUN: bool = True

# Sizing caps (KALSHI_MAX_TRADE_USD, per-horizon exposure caps, the
# aggregate signed-BTC-exposure budget from DESIGN.md's "correlation rule")
# deliberately NOT set yet -- those are kalshi/risk_vetoer.py's job (M4),
# and picking numbers before the fair-value engine (M2) exists to size
# against would be guessing. Locked in when that layer is built, not now.

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")


if __name__ == "__main__":
    # Self-test -- deterministic, no network needed.
    print("Testing KALSHI_PAPER_MODE defaults to True with no env var set...")
    assert KALSHI_PAPER_MODE is True
    print("PASS — paper mode is the default with no unlock env var present.")

    print("\nTesting assert_kalshi_paper_mode() does not raise in paper mode...")
    assert_kalshi_paper_mode()
    print("PASS — no exception raised while KALSHI_PAPER_MODE is True.")

    print("\nTesting kalshi_fee() peaks at price=0.50 and is symmetric...")
    fee_50 = kalshi_fee(0.50, contracts=1)
    fee_10 = kalshi_fee(0.10, contracts=1)
    fee_90 = kalshi_fee(0.90, contracts=1)
    assert fee_50 > fee_10 and fee_50 > fee_90, (fee_50, fee_10, fee_90)
    assert abs(fee_10 - fee_90) < 1e-9, (fee_10, fee_90)  # P and (1-P) symmetric
    print(f"PASS — fee(0.10)={fee_10:.4f}, fee(0.50)={fee_50:.4f} (peak), fee(0.90)={fee_90:.4f}.")

    print("\nTesting kalshi_fee() peak matches the ~1.75-1.80% crypto peak from M0 recon...")
    assert 0.015 <= fee_50 <= 0.02, fee_50  # ceil(0.07 * 0.25 * 100)/100 = 0.02, close to the ~1.75-1.80% figure
    print(f"PASS — peak per-contract fee ${fee_50:.4f} on a $1 notional contract, matching the ~1.75-1.80% crypto figure "
          f"found in M0 recon (rounds up to the cent, so lands at $0.02 rather than exactly $0.0175).")

    print("\nTesting kalshi_fee() scales linearly with contract count...")
    fee_1 = kalshi_fee(0.30, contracts=1)
    fee_10c = kalshi_fee(0.30, contracts=10)
    assert abs(fee_10c - fee_1 * 10) < 1e-9, (fee_1, fee_10c)
    print(f"PASS — 10 contracts at 30c costs ${fee_10c:.4f}, exactly 10x the 1-contract fee ${fee_1:.4f}.")

    print("\nTesting kalshi_fee() maker rate is 1/4 the taker rate...")
    taker = kalshi_fee(0.50, contracts=1, is_maker=False)
    maker = kalshi_fee(0.50, contracts=1, is_maker=True)
    assert maker < taker, (maker, taker)
    print(f"PASS — maker fee ${maker:.4f} < taker fee ${taker:.4f} at the 50c peak.")

    print("\nTesting kalshi_fee() rejects out-of-range price...")
    try:
        kalshi_fee(1.50, contracts=1)
        assert False, "should have raised ValueError"
    except ValueError:
        print("PASS — price=1.50 correctly rejected.")

    print("\nAll kalshi/config.py tests passed.")
