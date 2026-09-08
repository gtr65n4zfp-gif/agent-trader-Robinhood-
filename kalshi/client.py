"""
Kalshi market-data client — read-only, and safe to import with no credentials.

Purpose: when you run this repo somewhere Kalshi is reachable (your own machine)
and/or with an API key, this pulls REAL markets so the backtest/paper harness
runs on real prices and real resolutions instead of the synthetic generator. It
does exactly two things, both read-only:
  - list markets (optionally only settled ones, for backtesting on resolved data),
  - save/load a local JSON snapshot so a fetched dataset can be replayed offline.

It deliberately does NOT place orders. Live order placement is a separate,
later step gated by kalshi.config.assert_paper_mode() and the KALSHI_TRADER_LIVE
unlock phrase — the same discipline as the equities side. Reading is harmless;
writing real money is not, so they don't live in the same file.

NOTE: in the hosted cloud environment used to build this, api.kalshi.com is
blocked by network policy (a 403 at the egress proxy), so this path can't be
exercised here. That's expected — run it locally. The synthetic backtest
(market_sim.py) needs none of this and is what proves the logic in the meantime.
"""

import json
import os
import time

import requests

# Kalshi's public trading API base. Reads of markets/events are unauthenticated;
# only account and order endpoints require the API key.
_DEFAULT_BASE = os.environ.get(
    "KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")


class KalshiClientError(RuntimeError):
    pass


class KalshiClient:
    def __init__(self, base_url: str = _DEFAULT_BASE, api_key_id: str | None = None,
                 timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        # api_key_id is accepted so the same object can later be extended for
        # authenticated (account/order) calls; unused for the read-only methods.
        self.api_key_id = api_key_id or os.environ.get("KALSHI_API_KEY_ID")
        self.timeout = timeout
        self._session = requests.Session()

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            resp = self._session.get(url, params=params, timeout=self.timeout,
                                     headers={"Accept": "application/json"})
        except requests.RequestException as e:
            raise KalshiClientError(
                f"could not reach Kalshi at {url} ({e}). If you're in a sandbox "
                f"with network policy blocking kalshi.com, run locally or use a "
                f"snapshot.") from e
        if resp.status_code != 200:
            raise KalshiClientError(f"{url} returned {resp.status_code}: "
                                    f"{resp.text[:200]}")
        return resp.json()

    def list_markets(self, status: str | None = None, limit: int = 200,
                     max_pages: int = 25) -> list[dict]:
        """Return markets as raw Kalshi dicts, following pagination cursors.

        status: e.g. "settled" (for backtesting on resolved markets), "open",
        "closed". None returns all.
        """
        out: list[dict] = []
        cursor = None
        for _ in range(max_pages):
            params = {"limit": limit}
            if status:
                params["status"] = status
            if cursor:
                params["cursor"] = cursor
            data = self._get("markets", params)
            out.extend(data.get("markets", []))
            cursor = data.get("cursor")
            if not cursor:
                break
            time.sleep(0.2)   # be polite to the API
        return out

    # --- snapshot replay ---------------------------------------------------
    @staticmethod
    def save_snapshot(markets: list[dict], path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump({"saved_at": time.time(), "markets": markets}, f)

    @staticmethod
    def load_snapshot(path: str) -> list[dict]:
        with open(path) as f:
            return json.load(f)["markets"]


def to_resolved_market(raw: dict):
    """Adapt a raw Kalshi settled-market dict into the backtest's Market shape.

    Kalshi settled markets expose the resolved result and a last/close price. We
    map the closing YES price to `yes_price`; there is no hidden `true_prob` for
    real data (reality doesn't publish it), so we set it equal to the price — the
    backtest then grades purely on realized `outcome`, which is the honest way to
    score against real resolutions. Returns None if the record lacks what we need.
    """
    from .market_sim import Market

    result = raw.get("result")            # "yes" / "no" for settled markets
    if result not in ("yes", "no"):
        return None
    # Kalshi prices are in cents (0-100); prefer the last/close price.
    cents = raw.get("last_price") or raw.get("close_price") or raw.get("yes_ask")
    if cents is None:
        return None
    price = min(max(cents / 100.0, 0.01), 0.99)
    return Market(
        ticker=raw.get("ticker", "?"),
        yes_price=round(price, 2),
        true_prob=round(price, 2),        # unknown for real data; scored by outcome
        outcome=(result == "yes"),
        day=0)
