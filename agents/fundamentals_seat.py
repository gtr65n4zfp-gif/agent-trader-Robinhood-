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


def _pct_change(latest: dict, prior: dict | None) -> float | None:
    if not prior or not prior.get("value"):
        return None
    return round((latest["value"] - prior["value"]) / abs(prior["value"]), 4)


def _trend(points: list[dict], lookback: int = 8) -> dict | None:
    """
    Summarize a get_concept() series: latest value plus two different
    comparisons, because they answer different questions and conflating
    them is exactly what produced a nonsense "-56%" reading for a seasonal
    business (Apple's holiday quarter vs. the following quarter):

    - change_pct_sequential: vs. whatever period was filed immediately
      before this one. Answers "did anything change since the last
      filing" — but for a seasonal business, consecutive fiscal periods
      (e.g. Q1 vs Q2) aren't a real trend, just seasonality.
    - change_pct_yoy: vs. the same fiscal_period one year earlier (e.g.
      Q2 vs. Q2). The correct comparison for seasonal businesses; None if
      no matching same-period-prior-year point is in the lookback window.

    None if nothing was reported at all for this concept.
    """
    if not points:
        return None
    recent = points[-lookback:]
    latest = recent[-1]

    sequential_prior = recent[-2] if len(recent) > 1 else None

    yoy_prior = next(
        (
            p for p in reversed(recent[:-1])
            if p.get("fiscal_period") == latest.get("fiscal_period")
            and isinstance(latest.get("fiscal_year"), int)
            and p.get("fiscal_year") == latest["fiscal_year"] - 1
        ),
        None,
    )

    return {
        "latest_value": latest["value"],
        "latest_period": f"{latest.get('fiscal_year')} {latest.get('fiscal_period')}",
        "as_of": latest["end"],
        "prior_value": sequential_prior["value"] if sequential_prior else None,
        "change_pct_sequential": _pct_change(latest, sequential_prior),
        "change_pct_yoy": _pct_change(latest, yoy_prior),
        "history": recent,
    }


def fetch_concept_trend(cik: str, tag_candidates: list[str], as_of: str | None = None) -> dict | None:
    """
    Fetch a concept's trend, trying each tag in tag_candidates and keeping
    whichever has the most recent data point — the ASC-606-style tag
    migration this handles for _CONCEPTS below applies to other concepts
    too (e.g. cash flow tags), so this is exposed for reuse rather than
    kept private to build_brief().

    as_of: point-in-time cutoff (ISO date string, e.g. "2022-03-15") for
    backtesting (see backtest/data.py). When given, only data points with
    a `filed` date <= as_of are considered — filtered BEFORE picking the
    best tag and BEFORE computing the trend, so a filing that hadn't
    happened yet on that date can never leak in via either path. A point
    with no `filed` value at all is excluded when as_of is given (can't
    verify it was actually knowable — conservative by design, never the
    other way around). None (default): today's live/unfiltered behavior,
    unchanged.
    """
    best_points: list[dict] = []
    for tag in tag_candidates:
        try:
            points = sec_client.get_concept(cik, tag)
        except requests.RequestException:
            continue   # not reported under this tag for this filer
        if as_of is not None:
            points = [p for p in points if p.get("filed") and p["filed"] <= as_of]
        if points and (not best_points or points[-1]["end"] > best_points[-1]["end"]):
            best_points = points
    return _trend(best_points)


def build_brief(ticker: str, filing_forms: list[str] | None = None, as_of: str | None = None) -> dict:
    """
    Pull and organize this company's SEC data into a structured brief — no
    verdict, just the facts a Fundamentals judgment would be formed from.

    ticker: stock ticker, e.g. "AAPL".
    filing_forms: which filing types to include (default 10-K/10-Q/8-K).
    as_of: point-in-time cutoff (ISO date string) for backtesting — see
    fetch_concept_trend()'s docstring for the filed-date contract. Also
    filters recent_filings to filing_date <= as_of. None (default):
    today's live/unfiltered behavior, unchanged.
    """
    ticker = ticker.upper().strip()
    cik = sec_client.ticker_to_cik(ticker)

    concepts = {
        label: fetch_concept_trend(cik, tag_candidates, as_of=as_of)
        for label, tag_candidates in _CONCEPTS.items()
    }

    # get_recent_filings() returns the N most recent filings AS OF TODAY,
    # newest-first — for a historical as_of, those 6 are almost certainly
    # all newer than the cutoff, which would silently filter down to
    # nothing. Fetch a much wider pool first when truncating, then keep
    # only the most recent 6 that actually clear the as_of bar.
    filings = sec_client.get_recent_filings(
        cik, forms=filing_forms or ["10-K", "10-Q", "8-K"], limit=200 if as_of is not None else 6
    )
    if as_of is not None:
        filings = [f for f in filings if f["filing_date"] <= as_of][:6]

    return {
        "seat": "fundamentals",
        "ticker": ticker,
        "cik": cik,
        "concepts": concepts,
        "recent_filings": filings,
        "as_of": as_of,
    }


def form_verdict(ticker: str, stance: str, confidence: float, reasons: list[str]) -> dict:
    """
    Package a Fundamentals judgment into the same {stance, confidence,
    reasons} shape agents.technicals.build_view() returns, so
    agents.judge can treat every seat's output uniformly. This function
    does NOT form the judgment — per this module's docstring, that's
    still the calling agent's job, reasoning over build_brief()'s output.
    It only validates and repackages it, which matters for isolation: the
    Judge only ever sees this — never the raw brief — so it can't
    accidentally weigh a filing detail the Fundamentals seat didn't
    actually surface as part of its verdict.

    stance: "bullish" | "bearish" | "neutral". Here that means the
    business's trajectory looks like it's strengthening/weakening — NOT
    "cheap vs expensive" (this seat has no price data to judge that; see
    the module docstring).
    confidence: 0-1.
    """
    stance = stance.lower()
    if stance not in ("bullish", "bearish", "neutral"):
        raise ValueError(f"stance must be bullish/bearish/neutral, got {stance!r}")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence must be in [0, 1], got {confidence!r}")
    return {
        "seat": "fundamentals",
        "symbol": ticker.upper().strip(),
        "stance": stance,
        "confidence": round(confidence, 4),
        "reasons": list(reasons),
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
        yoy = f"{trend['change_pct_yoy'] * 100:+.1f}% YoY" if trend["change_pct_yoy"] is not None else "YoY n/a"
        seq = (
            f"{trend['change_pct_sequential'] * 100:+.1f}% seq"
            if trend["change_pct_sequential"] is not None else "seq n/a"
        )
        print(f"  {tag:20} {trend['latest_value']:>18,}  ({trend['latest_period']}, {yoy}, {seq})")

    print("\nRecent filings:")
    for f in brief["recent_filings"]:
        print(f"  {f['filing_date']}  {f['form']:5}  {f['url']}")
