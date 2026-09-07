"""
Read-only Kalshi market-data client (kalshi/DESIGN.md Layer 2, Milestone 1).

Read-only by design, matching DESIGN.md's "read-only first" rule: nothing
here places an order. Auth is only needed for endpoints Kalshi gates behind
a key (M0 recon found /series, /events, /markets, and orderbook endpoints
accept but do NOT require auth headers) -- build_auth_headers() exists for
whichever endpoints turn out to need it (e.g. the historical candlesticks
endpoint, unconfirmed either way -- see below).

*** ENVIRONMENT CONSTRAINT -- READ BEFORE TRUSTING THIS MODULE ***
This was written and unit-tested inside a sandbox whose network egress
proxy blocks the entire kalshi.com domain and its API subdomains outright
(confirmed via the proxy's own status endpoint, not just a timeout --
see kalshi/M0_FINDINGS.md). That means:
  - BASE_URL below is a best-effort read of third-party docs/guides, not a
    verified value -- sources disagreed across "api.elections.kalshi.com",
    "trading-api.kalshi.com", and "external-api.kalshi.com", all under the
    same "/trade-api/v2" path shape. Confirm the live one before real use.
  - Every fetch_* function below has NEVER been run against the real API.
    Self-tests in this file only prove the pure logic (request shape,
    signing math, response parsing) using injected fake responses -- they
    do not, and cannot from here, prove Kalshi actually accepts these
    requests or returns data shaped this way.
  - The actual M1 exit criterion from DESIGN.md -- "pull a live book and
    reconcile one settled market by hand" -- is NOT met yet. It requires
    running this module somewhere with real network access to kalshi.com.
"""

import base64
import time

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# Unverified -- see the environment-constraint note above.
BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

# Kalshi's documented candlestick granularities, in minutes.
VALID_CANDLESTICK_PERIODS_MIN = (1, 60, 1440)


def sign_request(private_key_pem: bytes, method: str, path: str, timestamp_ms: int) -> str:
    """
    RSA-PSS/SHA256 signature over f"{timestamp_ms}{method}{path}", base64-encoded --
    Kalshi's documented request-signing scheme. `path` must be the full request
    path Kalshi expects to see signed (per its docs, including the
    "/trade-api/v2" prefix) -- unconfirmed from this sandbox; verify against
    a real 200 response before relying on it.
    """
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    message = f"{timestamp_ms}{method}{path}".encode("utf-8")
    signature = private_key.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


def build_auth_headers(api_key_id: str, private_key_pem: bytes, method: str, path: str) -> dict:
    """Headers for an authenticated Kalshi request. Use a READ-scoped key
    (DESIGN.md/M0_FINDINGS.md) -- this module never needs write access."""
    timestamp_ms = int(time.time() * 1000)
    signature = sign_request(private_key_pem, method, path, timestamp_ms)
    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
        "KALSHI-ACCESS-SIGNATURE": signature,
    }


def get_markets(series_ticker: str | None = None, status: str | None = "open", get_fn=None) -> dict:
    """Public endpoint per M0 recon -- no auth required. `get_fn` is
    injectable (defaults to requests.get) so this can be unit-tested without
    a real network call; production callers should just omit it."""
    get_fn = get_fn or requests.get
    params = {}
    if series_ticker:
        params["series_ticker"] = series_ticker
    if status:
        params["status"] = status
    resp = get_fn(f"{BASE_URL}/markets", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_orderbook(ticker: str, get_fn=None) -> dict:
    """Public endpoint per M0 recon -- no auth required."""
    get_fn = get_fn or requests.get
    resp = get_fn(f"{BASE_URL}/markets/{ticker}/orderbook", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_candlesticks(ticker: str, start_ts: int, end_ts: int, period_interval_min: int, get_fn=None) -> dict:
    """
    Historical OHLC candles for a settled/settling market. `period_interval_min`
    must be 1, 60, or 1440 (minute/hour/day -- Kalshi's documented granularities).
    Whether this endpoint requires auth is UNCONFIRMED from this sandbox --
    pass headers via get_fn's caller if a 401 shows up in live testing.
    """
    if period_interval_min not in VALID_CANDLESTICK_PERIODS_MIN:
        raise ValueError(
            f"period_interval_min must be one of {VALID_CANDLESTICK_PERIODS_MIN}, got {period_interval_min}"
        )
    get_fn = get_fn or requests.get
    params = {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval_min}
    resp = get_fn(f"{BASE_URL}/historical/markets/{ticker}/candlesticks", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def normalize_candlestick(raw: dict) -> dict:
    """Map a single raw Kalshi candlestick record into this project's shape.
    Field names below are a best guess from third-party docs, NOT verified
    against a real response -- expect to adjust once M1's live pull happens."""
    return {
        "ts": raw["end_period_ts"],
        "open": raw["price"]["open"] / 100.0,
        "high": raw["price"]["high"] / 100.0,
        "low": raw["price"]["low"] / 100.0,
        "close": raw["price"]["close"] / 100.0,
        "volume": raw.get("volume", 0),
    }


if __name__ == "__main__":
    # Self-tests below are deterministic and use NO real network call --
    # see the module docstring's environment-constraint note for why.
    from cryptography.hazmat.primitives.asymmetric import rsa

    print("Testing sign_request() produces a valid, verifiable RSA-PSS signature...")
    throwaway_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = throwaway_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    ts = 1735689600000
    sig_b64 = sign_request(pem, "GET", "/trade-api/v2/portfolio/balance", ts)
    sig_bytes = base64.b64decode(sig_b64)
    assert len(sig_bytes) == 256, len(sig_bytes)  # RSA-2048 signature is always 256 bytes
    public_key = throwaway_key.public_key()
    message = f"{ts}GET/trade-api/v2/portfolio/balance".encode("utf-8")
    public_key.verify(  # raises InvalidSignature if wrong -- the real assertion
        sig_bytes, message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    print("PASS — signature is 256 bytes and verifies against the same key's public half "
          "(proves the signing math is self-consistent; does NOT prove Kalshi's verifier accepts it).")

    print("\nTesting build_auth_headers() shape...")
    headers = build_auth_headers("test-key-id", pem, "GET", "/trade-api/v2/markets")
    assert set(headers.keys()) == {"KALSHI-ACCESS-KEY", "KALSHI-ACCESS-TIMESTAMP", "KALSHI-ACCESS-SIGNATURE"}, headers.keys()
    assert headers["KALSHI-ACCESS-KEY"] == "test-key-id"
    print(f"PASS — headers carry the three documented fields: {sorted(headers.keys())}.")

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload
        def raise_for_status(self):
            pass
        def json(self):
            return self._payload

    print("\nTesting get_markets() request shape via an injected fake transport (no real network)...")
    captured = {}
    def fake_get_markets(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse({"markets": [{"ticker": "KXBTCD-26SEP07"}]})
    result = get_markets(series_ticker="KXBTCD", get_fn=fake_get_markets)
    assert captured["url"] == f"{BASE_URL}/markets", captured["url"]
    assert captured["params"] == {"series_ticker": "KXBTCD", "status": "open"}, captured["params"]
    assert result["markets"][0]["ticker"] == "KXBTCD-26SEP07"
    print(f"PASS — get_markets(series_ticker='KXBTCD') hit {captured['url']} with params {captured['params']}.")

    print("\nTesting get_candlesticks() rejects an invalid period_interval_min...")
    try:
        get_candlesticks("KXBTCD-26SEP07", 0, 1, period_interval_min=5, get_fn=lambda *a, **k: None)
        assert False, "should have raised ValueError"
    except ValueError:
        print("PASS — period_interval_min=5 correctly rejected (must be 1, 60, or 1440).")

    print("\nTesting normalize_candlestick() on a fixture record...")
    fixture = {"end_period_ts": 1735689600, "price": {"open": 62, "high": 65, "low": 60, "close": 64}, "volume": 12}
    normalized = normalize_candlestick(fixture)
    assert normalized == {"ts": 1735689600, "open": 0.62, "high": 0.65, "low": 0.60, "close": 0.64, "volume": 12}, normalized
    print(f"PASS — fixture cents converted to dollars correctly: {normalized}.")

    print("\nAll kalshi/market_data.py pure-logic tests passed.")
    print("REMINDER: live reconciliation against a real Kalshi book/settlement is still unproven -- "
          "see the module docstring's environment-constraint note.")
