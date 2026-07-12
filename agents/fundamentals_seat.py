"""
Fundamentals seat — second seat of the trade council (see agents/COUNCIL_DESIGN.md).

Domain-isolated: this module only ever touches research/sec_client.py (SEC
EDGAR filings and financial facts). It never sees price, volume, or any
other market data — that's the Technicals seat's job. Because "cheap vs.
expensive" is a valuation judgment that needs a price anchor this seat
deliberately doesn't have, what this seat actually assesses is business
quality and trajectory from the filings alone — is revenue/income growing,
is the balance sheet holding up — not whether the stock is a good buy at
today's price. Combining that with price is the Judge's job once it
exists, not this seat's; mixing the two here would break the isolation
the design relies on to reduce correlated errors.

Like execution/robinhood.py, this module is agent-mediated for the part
that needs real judgment: build_brief() below is pure data — it pulls and
organizes SEC facts into a clean, structured summary, with no verdict
attached. The actual thesis (sound / deteriorating / mixed, and why) is
formed by whichever LLM agent is driving the run at the time, using this
brief as its only fundamentals input — not a separate API call, not a
fixed formula.
"""

import sys

import requests

from research import sec_client

# Well-established US-GAAP tags, not exhaustive. Several filers stopped
# reporting under "Revenues" after adopting ASC 606 (~2018) in favor of
# "RevenueFromContractWithCustomerExcludingAssessedTax" — without a
# fallback, a company like that shows a stale multi-year-old "latest"
# revenue figure with no warning. Each entry here is a list of candidate
# tags, tried in order, keeping whichever has the most recent data point.
_CONCEPTS: dict[str, list[str]] = {
    "Revenues": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"],
    "NetIncomeLoss": ["NetIncomeLoss"],
    "Assets": ["Assets"],
    "StockholdersEquity": ["StockholdersEquity"],
}


def _trend(points: list[dict], lookback: int = 8) -> dict | None:
    """Summarize a get_concept() series: latest value, the point before it,
    and the change between them. None if nothing was reported at all."""
    if not points:
        return None
    recent = points[-lookback:]
    latest = recent[-1]
    prior = recent[-2] if len(recent) > 1 else None
    change_pct = (
        (latest["value"] - prior["value"]) / abs(prior["value"])
        if prior and prior["value"] else None
    )
    return {
        "latest_value": latest["value"],
        "latest_period": f"{latest.get('fiscal_year')} {latest.get('fiscal_period')}",
        "as_of": latest["end"],
        "prior_value": prior["value"] if prior else None,
        "change_pct": round(change_pct, 4) if change_pct is not None else None,
        "history": recent,
    }


def build_brief(ticker: str, filing_forms: list[str] | None = None) -> dict:
    """
    Pull and organize this company's SEC data into a structured brief — no
    verdict, just the facts a Fundamentals judgment would be formed from.

    ticker: stock ticker, e.g. "AAPL".
    filing_forms: which filing types to include (default 10-K/10-Q/8-K).
    """
    ticker = ticker.upper().strip()
    cik = sec_client.ticker_to_cik(ticker)

    concepts = {}
    for label, tag_candidates in _CONCEPTS.items():
        best_points: list[dict] = []
        for tag in tag_candidates:
            try:
                points = sec_client.get_concept(cik, tag)
            except requests.RequestException:
                continue   # not reported under this tag for this filer
            if points and (not best_points or points[-1]["end"] > best_points[-1]["end"]):
                best_points = points
        concepts[label] = _trend(best_points)

    filings = sec_client.get_recent_filings(
        cik, forms=filing_forms or ["10-K", "10-Q", "8-K"], limit=6
    )

    return {
        "seat": "fundamentals",
        "ticker": ticker,
        "cik": cik,
        "concepts": concepts,
        "recent_filings": filings,
    }


if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    brief = build_brief(ticker)

    print(f"Fundamentals brief for {brief['ticker']} (CIK {brief['cik']})")
    print("=" * 60)
    for tag, trend in brief["concepts"].items():
        if trend is None:
            print(f"  {tag:20} not reported under this tag")
            continue
        change = f"{trend['change_pct'] * 100:+.1f}%" if trend["change_pct"] is not None else "n/a"
        print(f"  {tag:20} {trend['latest_value']:>18,}  ({trend['latest_period']}, {change} vs prior)")

    print("\nRecent filings:")
    for f in brief["recent_filings"]:
        print(f"  {f['filing_date']}  {f['form']:5}  {f['url']}")
