"""
backtest/universes.py -- the named test universes every strategy in
STRATEGY_LIBRARY.md is backtested on. Single source of truth, so a result
can always say exactly which symbols it covered and how they were chosen.

Why more than the watchlist: agents/MOMENTUM_RESULTS.md found that the live
watchlist is a poor test bed on its own. It's eight of today's mega-cap
winners measured over a historic bull decade, so it's survivorship-biased
toward buy-and-hold, and almost any strategy whose job is to beat holding
them looks bad. A strategy is judged on all three universes below, not one.

HONEST LIMITS, stated up front:
- TOP30_2015 is chosen by a rule fixed at the START of the test (size at
  year-end 2014), so it includes names that later disappointed (GE, IBM,
  INTC, XOM, T, VZ, C, ...). That removes the hindsight bias of picking
  today's winners. The ranking comes from general knowledge, not a fetched
  dataset, so the exact order near the #30 cutoff is uncertain. It is NOT
  fully survivorship-free either: none of these mega-caps went bankrupt or
  delisted, and the Robinhood MCP can't serve delisted tickers anyway.
- MULTIASSET is the closest match to the momentum literature's own setting
  (a diversified basket across asset classes). It is ETF-only, so
  survivorship isn't an issue, but it stays long-only: this system can't
  short.
"""

# The live watchlist, mirrored from execution/config.WATCHLIST, plus SPY as
# a market reference. NBIS is listed but gets excluded by the data hygiene
# rule in backtest/run_momentum_backtest.py: as ex-Yandex it was halted
# 2022-2024, so too little continuous history remains.
WATCHLIST = ["AAPL", "MSFT", "GOOGL", "JPM", "JNJ", "NBIS", "NVDA", "TSLA", "SPY"]

# Roughly the 30 largest US-listed companies by market cap at year-end 2014.
# This is a point-in-time selection rule; see the module docstring for limits.
TOP30_2015 = [
    "AAPL", "XOM", "MSFT", "BRK.B", "GOOGL", "JNJ", "WFC", "GE", "WMT", "JPM",
    "PG", "CVX", "PFE", "META", "VZ", "ORCL", "KO", "BAC", "INTC", "T",
    "GILD", "MRK", "AMZN", "DIS", "IBM", "V", "PEP", "CMCSA", "HD", "CSCO",
    "C",  # ~#30 at the cutoff (Citigroup); included rather than guess which name sits just outside
]

# One liquid ETF per major asset class, plus the nine original SPDR sector
# funds. All have traded since at least 2008, well before the test window.
MULTIASSET = [
    "SPY",                        # US large-cap stocks
    "EFA", "EEM",                 # international developed / emerging stocks
    "TLT", "IEF",                 # long / intermediate US Treasuries
    "LQD", "HYG",                 # investment-grade / high-yield corporate bonds
    "GLD", "DBC",                 # gold / broad commodities
    "VNQ",                        # US real estate
    "XLE", "XLF", "XLK", "XLU", "XLV", "XLP", "XLY", "XLI", "XLB",   # US sectors
]

UNIVERSES = {"watchlist": WATCHLIST, "top30_2015": TOP30_2015, "multiasset": MULTIASSET}
