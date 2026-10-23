You are running one daily automated pass of the "agent-trader" paper-trading system. The repo is already checked out at the current directory. Read `agents/AUTOMATION_DESIGN.md` and `automation/run_pass.py`'s module docstring first. They define the fail-safe rules you must follow exactly. Do not deviate from them.

**Non-negotiable rules — read before doing anything else:**
- Never edit `execution/config.py`. In particular, never touch `PAPER_MODE`, `AGENT_TRADER_LIVE`, `AUTOMATION_DRY_RUN`, or the `MARKET_SWITCH_*` settings. This project stays in paper mode / dry-run until a human deliberately changes those lines outside of this routine.
- If the Robinhood MCP tools are not available in this session, STOP immediately. They're named like `mcp__robinhood_agentic__*` or `mcp__robinhood-trading__*`, depending on how the connector is attached. Do not fabricate or estimate any price/indicator data. Print a clear one-line failure explaining the tools weren't available, commit nothing, and end the session.
- Never invent, guess, or carry over stale data for any symbol, or for the market switch's SPY monthly bars. If something looks wrong, let the code's own checks handle it:
  - `automation/run_pass.py`'s per-symbol sanity check skips bad or stale symbol data safely.
  - `agents/market_switch.py` treats bad market data as "unknown", which safely blocks new buys.
  - Don't pre-filter based on your own judgment of "looks right."
- There is NO SEC / fundamentals step. That track was retired (see README's "History"). Do not fetch SEC data, and do not form any "fundamentals verdict".

**Setup — run once, before anything else:**

```
pip install -r requirements.txt
```

**Step 0 — cheap market-hours pre-check, before any MCP calls:**

```
python3 -c "from datetime import datetime, timezone; from execution import config; print(config.market_is_open(datetime.now(timezone.utc)))"
```

If it prints `False`, the market is closed right now. Don't fetch anything from Robinhood. Instead, immediately run:

```
python3 -c "from automation.run_pass import run_pass; print(run_pass({}))"
```

`run_pass` checks `assert_paper_mode()` and then the market-hours guard before it ever touches its arguments, so an empty `{}` is safe here. It short-circuits, logs the `automation_noop` entry, and returns. Print that result, skip straight to the final summary, and end the session. There's nothing to commit after a market-closed no-op. Do NOT proceed to the steps below.

If it prints `True`, proceed with the full pass below.

**Steps (only when the market is open):**

1. Confirm the Robinhood MCP tools are available (for example, try `get_accounts`). If not, STOP per the rule above: print a clear one-line failure, commit nothing, end the session.

2. For every symbol in `execution/config.py`'s `WATCHLIST`, call:
   - `get_equity_quotes` for the symbol.
   - `get_equity_technical_indicators` for ATR (period 14), RSI (period 14), and EMA. Call it TWICE for EMA:
     - once at the short period used elsewhere in this codebase (period 9);
     - once at `config.REGIME_EMA_LOOKBACK_DAYS` (currently 20), for the regime filter.

     These must be two genuinely separate calls with different periods. Read `execution/robinhood.py`'s `get_regime_ema()` docstring for why, and for how much price history to request so the longer EMA is accurate.
   - `get_equity_fundamentals` for the symbol. It's used only for sector, by the risk vetoer; it is not a strategy input.

   Read `agents/demo_council.py`'s module docstring for the exact expected shape of each of these raw responses, and match it exactly.

3. ONCE per pass (not per symbol), fetch the market switch's input:
   - Call `get_equity_historicals` with `symbols=[config.MARKET_SWITCH_SYMBOL]` (SPY) and `interval="month"`.
   - Leave `adjustment_type` at its default (`split`).
   - Set `start_time` to the first day of the month `config.MARKET_SWITCH_MONTHS_TO_FETCH` (48) months before today, at 00:00 UTC.

   Keep the raw response exactly as returned. If the call errors or comes back empty, use `None` for it in step 5. Never build or estimate it. The switch then reads "unknown", which safely blocks new buys and forces no exits.

4. Assemble one bundle: `{symbol: {"quote":..., "atr":..., "rsi":..., "ema":..., "regime_ema":..., "robinhood_fundamentals":...}}` for every watchlist symbol. See `automation/run_pass.py`'s `BUNDLE_HELP` constant for the exact required shape.

5. Call `automation.run_pass.run_pass(bundle, market_bundle=<the raw SPY monthly response from step 3, or None>)`. Pass no `now` or `broker` override: this is a real pass against the real shared paper account and trade log (`logs/paper_portfolio.json`, `logs/trades.jsonl`). It already runs, in order:
   - `assert_paper_mode()`;
   - the market-hours guard;
   - the market risk-off switch;
   - the per-symbol data sanity check;
   - the exit sweep, before entries;

   and it routes everything through `AUTOMATION_DRY_RUN`.

6. Print the full run summary it returns.

7. If `logs/trades.jsonl` and/or `logs/paper_portfolio.json` changed as a result of this pass (check `git status`), commit and push them:
   - These are the only two files this routine should ever modify.
   - Stage exactly those two paths.
   - Commit with a message summarizing what happened: the market switch state, symbol counts for exits/entries/holds/regime and market sit-outs, and the current round-trip stats.
   - Push to `origin main`. This is how state survives to the next scheduled wake: each cloud session starts from a fresh checkout, so nothing persists unless it's committed and pushed here.

   If nothing in those two files changed, skip the commit; don't create an empty one.

8. End with a short plain-English summary of what happened this pass:
   - market open or not;
   - the market switch's state (risk_on / risk_off / unknown) and why;
   - how many symbols were skipped, and why;
   - what exited (and by which path), what entered, and what held;
   - what sat out on regime or on the market switch;
   - the current round-trip stats.
