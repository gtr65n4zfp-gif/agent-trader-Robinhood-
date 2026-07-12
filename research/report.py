"""
Report layer (Milestone 2) — turns SEC data into a plain-English +
structured company report. Self-contained and useful on its own, same as
sec_client.py — this is NOT part of the trade council. The council's
Fundamentals seat (agents/fundamentals_seat.py) stays narrow and
mechanical on purpose; nothing here feeds into its decisions.

Scoped to what SEC EDGAR's public data can actually support: business
overview (name, SIC industry classification), income/balance-sheet/cash-
flow trends, and recent filings. A full sell-side research report also
wants valuation multiples, peer comparisons, TAM, and management
commentary — none of that lives in SEC structured data, so those sections
are deliberately left out rather than faked.

Like fundamentals_seat.py, build_report_brief() is pure data — it pulls
and organizes everything a report needs, with no prose attached. The
plain-English narrative is written by whichever LLM agent is driving the
run, using this brief as its source — not a separate API call, not a
canned template.
"""

import sys

from agents import fundamentals_seat
from research import sec_client

# Cash-flow concepts fundamentals_seat.py doesn't pull (that seat stays
# narrower on purpose) — a report wants a fuller picture, including free
# cash flow, which the trade council's Fundamentals seat has no need for.
_CASH_FLOW_CONCEPTS: dict[str, list[str]] = {
    "OperatingCashFlow": ["NetCashProvidedByUsedInOperatingActivities"],
    "CapitalExpenditures": ["PaymentsToAcquirePropertyPlantAndEquipment"],
}


def build_report_brief(ticker: str) -> dict:
    """
    Pull and organize everything a plain-English report needs: company
    info, the same financial trends the Fundamentals seat uses, additional
    cash-flow trends, a derived free-cash-flow figure where both operating
    cash flow and capex are available for the same period, and recent
    filings. No prose — that's written from this by the calling agent.
    """
    ticker = ticker.upper().strip()
    cik = sec_client.ticker_to_cik(ticker)

    info = sec_client.get_company_info(cik)
    fundamentals = fundamentals_seat.build_brief(ticker)

    cash_flow = {
        label: fundamentals_seat.fetch_concept_trend(cik, tag_candidates)
        for label, tag_candidates in _CASH_FLOW_CONCEPTS.items()
    }

    free_cash_flow = None
    ocf, capex = cash_flow["OperatingCashFlow"], cash_flow["CapitalExpenditures"]
    if ocf and capex and ocf["as_of"] == capex["as_of"]:
        free_cash_flow = {
            "value": ocf["latest_value"] - capex["latest_value"],
            "as_of": ocf["as_of"],
            "period": ocf["latest_period"],
        }

    return {
        "ticker": ticker,
        "cik": cik,
        "company": info,
        "concepts": fundamentals["concepts"],
        "cash_flow": cash_flow,
        "free_cash_flow": free_cash_flow,
        "recent_filings": fundamentals["recent_filings"],
    }


if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    brief = build_report_brief(ticker)

    print(f"{brief['company']['name']} ({brief['ticker']}, CIK {brief['cik']})")
    print(f"Industry: {brief['company']['sic_description']}")
    print(f"Exchanges: {', '.join(brief['company']['exchanges'] or [])}")
    print("=" * 60)

    print("\nFinancials:")
    for label, trend in {**brief["concepts"], **brief["cash_flow"]}.items():
        if trend is None:
            print(f"  {label:22} not reported under this tag")
            continue
        change = f"{trend['change_pct'] * 100:+.1f}%" if trend["change_pct"] is not None else "n/a"
        print(f"  {label:22} {trend['latest_value']:>18,}  ({trend['latest_period']}, {change} vs prior)")

    if brief["free_cash_flow"]:
        fcf = brief["free_cash_flow"]
        print(f"  {'FreeCashFlow':22} {fcf['value']:>18,}  ({fcf['period']})")

    print("\nRecent filings:")
    for f in brief["recent_filings"]:
        print(f"  {f['filing_date']}  {f['form']:5}  {f['url']}")
