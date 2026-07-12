"""
SEC EDGAR client — pulls company filings and financial data from the SEC's
free public API (data.sec.gov). No account or API key required.

The SEC asks that every request identify who's making it, via a User-Agent
header with a contact. Set SEC_USER_AGENT in your environment (or config/.env)
to something like "Your Name your-email@example.com". A default is provided so
things work out of the box, but you should set your real contact before running
this heavily — it's the SEC's fair-access rule, and they can rate-limit or block
generic agents.

Docs: https://www.sec.gov/os/webmaster-faq#developers
"""

import os
import time
import requests

# --- Endpoints -------------------------------------------------------------
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json"

USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "agent-trader research (set SEC_USER_AGENT to your contact)"
)
HEADERS = {"User-Agent": USER_AGENT}

# The SEC allows up to ~10 requests/sec. We keep a small delay to stay polite.
_MIN_INTERVAL = 0.15
_last_call = 0.0


def _get(url: str) -> requests.Response:
    """GET with the required header, polite rate-limiting, and error raising."""
    global _last_call
    wait = _MIN_INTERVAL - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(url, headers=HEADERS, timeout=20)
    _last_call = time.time()
    resp.raise_for_status()
    return resp


# --- Ticker -> CIK ---------------------------------------------------------
_ticker_map_cache: dict | None = None


def _ticker_map() -> dict:
    """The SEC's full ticker->company table, fetched once and cached."""
    global _ticker_map_cache
    if _ticker_map_cache is None:
        _ticker_map_cache = _get(TICKER_MAP_URL).json()
    return _ticker_map_cache


def ticker_to_cik(ticker: str) -> str:
    """Resolve a stock ticker (e.g. 'AAPL') to its 10-digit zero-padded CIK."""
    ticker = ticker.upper().strip()
    for row in _ticker_map().values():
        if row["ticker"] == ticker:
            return str(row["cik_str"]).zfill(10)
    raise ValueError(f"Ticker {ticker!r} not found in SEC ticker list.")


# --- Filings ---------------------------------------------------------------
def get_recent_filings(cik: str, forms: list[str] | None = None, limit: int = 10) -> list[dict]:
    """
    Return the company's most recent filings.

    forms: optional filter, e.g. ['10-K', '10-Q', '8-K']. None = all forms.
    Each item includes form type, filing date, and a direct URL to the document.
    """
    data = _get(SUBMISSIONS_URL.format(cik=cik)).json()
    recent = data["filings"]["recent"]
    cik_int = int(cik)  # URLs use the un-padded CIK

    results: list[dict] = []
    for i in range(len(recent["accessionNumber"])):
        form = recent["form"][i]
        if forms and form not in forms:
            continue
        accession = recent["accessionNumber"][i]
        accession_nodash = accession.replace("-", "")
        primary_doc = recent["primaryDocument"][i]
        results.append(
            {
                "form": form,
                "filing_date": recent["filingDate"][i],
                "report_date": recent["reportDate"][i],
                "description": recent["primaryDocDescription"][i],
                "accession": accession,
                "url": f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{primary_doc}",
            }
        )
        if len(results) >= limit:
            break
    return results


# --- Financial facts -------------------------------------------------------
def get_concept(cik: str, tag: str, unit: str = "USD") -> list[dict]:
    """
    Fetch a single XBRL financial concept's reported values over time.

    tag examples (US-GAAP): 'Revenues', 'NetIncomeLoss', 'Assets',
    'CashAndCashEquivalentsAtCarryingValue'.
    Returns a list of {value, end, form, fiscal_year, fiscal_period} newest-last.
    """
    data = _get(CONCEPT_URL.format(cik=cik, tag=tag)).json()
    points = []
    for p in data.get("units", {}).get(unit, []):
        points.append(
            {
                "value": p["val"],
                "end": p["end"],
                "form": p.get("form"),
                "fiscal_year": p.get("fy"),
                "fiscal_period": p.get("fp"),
            }
        )
    return points


def latest_concept(cik: str, tag: str, unit: str = "USD") -> dict | None:
    """Convenience: just the most recently reported value for a concept."""
    points = get_concept(cik, tag, unit)
    return points[-1] if points else None


# --- Manual test -----------------------------------------------------------
if __name__ == "__main__":
    import sys

    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    cik = ticker_to_cik(ticker)
    print(f"\n{ticker}  (CIK {cik})")
    print("=" * 50)

    print("\nRecent 10-K / 10-Q / 8-K filings:")
    for f in get_recent_filings(cik, forms=["10-K", "10-Q", "8-K"], limit=6):
        print(f"  {f['filing_date']}  {f['form']:5}  {f['url']}")

    print("\nKey financials (most recent reported):")
    for tag in ["Revenues", "NetIncomeLoss", "Assets"]:
        pt = latest_concept(cik, tag)
        if pt:
            print(f"  {tag:16} {pt['value']:>18,}  ({pt['fiscal_year']} {pt['fiscal_period']}, ends {pt['end']})")
        else:
            print(f"  {tag:16} (not reported under this tag)")
    print()
