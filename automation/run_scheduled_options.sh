#!/bin/bash
# automation/run_scheduled_options.sh — the local launchd-scheduled
# entrypoint for the SPY options paper-trading pass, run twice a day
# (once shortly after market open, once shortly before market close).
#
# Deliberately a SEPARATE script and SEPARATE launchd job from
# automation/run_scheduled.sh (the once-daily equity pass), not a change
# to it — the options layer has its own cadence (twice/day vs once/day),
# its own broker/log/portfolio state (execution/options_paper_broker.py),
# and per docs/superpowers/specs/2026-07-17-live-spy-options-design.md is
# meant to stay fully isolated from the equity system end to end.
#
# Runs LOCALLY, not as a cloud routine — same reason as
# run_scheduled.sh: Robinhood's MCP OAuth session only exists in this
# machine's authenticated Claude Code CLI context.
#
# SAFETY: this script never touches execution/config.py.
# OPTIONS_AUTOMATION_DRY_RUN stays whatever it's already set to in the
# repo (True by default) — nothing here can arm real paper execution,
# let alone real orders. The agent it invokes is explicitly instructed
# never to edit that file either. --allowedTools below has no
# order-placing tool at all.
#
# Same silent-failure fix as run_scheduled.sh: every invocation — success,
# failure, or crash — appends exactly one timestamped line to
# logs/options_automation_runs.log, and a run producing neither an
# OPTIONS_AUTOMATION_RUN_OK nor OPTIONS_AUTOMATION_RUN_FAILED marker is
# treated as a failure, not silently dropped.

# Same DarkWake-race fix as run_scheduled.sh (see that script's own
# comment for the full pmset-log-confirmed diagnosis): launchd wakes this
# Mac from a brief background "DarkWake" to fire this job, and on
# battery the system can re-enter Maintenance Sleep as little as 8
# seconds later. caffeinate must start as the literal first line — not
# wrapped around the later `claude -p` call — so its assertion is live
# before any setup overhead (PATH export, cd, venv activation, prompt
# writing) burns through that window. `-w $$` ties the assertion's
# lifetime to this script's own PID; it releases and exits itself
# automatically once this script does, no trap/cleanup needed.
caffeinate -i -w $$ &

set -uo pipefail  # deliberately NOT -e: failures are caught and logged below, not just abandoned

# launchd runs services with a minimal PATH — same fix as run_scheduled.sh.
export PATH="/Users/ethandungo/.local/bin:/Library/Frameworks/Python.framework/Versions/3.14/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

PROJECT_DIR="/Users/ethandungo/agent-trader"
LOG_FILE="$PROJECT_DIR/logs/options_automation_runs.log"
RUN_LOG_DIR="$PROJECT_DIR/logs/options_automation_runs"
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
RUN_LOG_FILE="$RUN_LOG_DIR/$TIMESTAMP.log"

mkdir -p "$RUN_LOG_DIR"

cd "$PROJECT_DIR" || {
  echo "$TIMESTAMP RUN FAILED: could not cd to $PROJECT_DIR" >> "$LOG_FILE"
  exit 1
}

if [ -f "$PROJECT_DIR/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.venv/bin/activate"
fi

echo "$TIMESTAMP RUN START" >> "$LOG_FILE"

# Same nested-heredoc-in-command-substitution gotcha as run_scheduled.sh —
# written to a plain file first, not $(cat <<'EOF' ... EOF) directly.
PROMPT_FILE="$PROJECT_DIR/logs/options_automation_runs/.prompt_$$.txt"
cat > "$PROMPT_FILE" <<'PROMPT_EOF'
You are running one automated pass (market-open or market-close leg) of
the "agent-trader" project's SPY options paper-trading layer, launched
LOCALLY via launchd (same reason as the equity pass — Robinhood's MCP
session is authenticated only in this machine's local Claude Code
context). The repo is already at the current directory. Read
docs/superpowers/specs/2026-07-17-live-spy-options-design.md and
automation/run_options_pass.py's module docstring if you want the full
fail-safe rationale — the essentials are repeated below.

NON-NEGOTIABLE RULES:
- Never edit execution/config.py. Never touch PAPER_MODE,
  AGENT_TRADER_LIVE, AUTOMATION_DRY_RUN, or OPTIONS_AUTOMATION_DRY_RUN.
  This project stays in paper mode / dry-run until a human deliberately
  changes those lines outside of this run.
- Never fabricate, estimate, or carry over stale data for SPY or any
  option contract. If a live quote or instrument lookup you need isn't
  available, that leg is skipped by execute_options_pass() itself —
  don't work around that by substituting a guessed value.
- Your ABSOLUTE FINAL line of output must be EXACTLY ONE of:
      OPTIONS_AUTOMATION_RUN_OK: <one-line summary>
  or
      OPTIONS_AUTOMATION_RUN_FAILED: <specific, one-line reason>
  This is a hard requirement, not a suggestion. The wrapper script that
  invoked you greps your output for these exact markers to know whether
  this run actually succeeded — a run that ends without printing one of
  them is indistinguishable from a silent crash, and the wrapper will
  correctly treat it as a failure regardless of your exit code. Print it
  as the very last thing you do, after everything else.

STEPS:

0. Cheap pre-check, no MCP calls:
   python3 -c "from datetime import datetime, timezone; from execution import config; print(config.market_is_open(datetime.now(timezone.utc)))"
   If it prints False, print "OPTIONS_AUTOMATION_RUN_OK: market closed, no-op" and STOP. Do not proceed to step 1.

1. Confirm the Robinhood MCP tools are available (try get_accounts). If they are not available, not authenticated, or error out, print "OPTIONS_AUTOMATION_RUN_FAILED: robinhood MCP unavailable — <specific error>" and STOP immediately. Do not fabricate data. Do not proceed.

2. Fetch SPY's data — no fundamentals, no sector; SPY has neither:
   - get_equity_quotes for SPY
   - get_equity_technical_indicators for SPY: ATR (period 14), RSI (period 14), EMA (period 9), and a SEPARATE call for EMA at period=config.REGIME_EMA_LOOKBACK_DAYS for the regime filter — these must be two genuinely different calls with different periods, read execution/robinhood.py's get_regime_ema() docstring for why.
   Assemble these five raw responses into a bundle dict exactly per automation/run_options_pass.py's BUNDLE_HELP constant (keys "quote", "atr", "rsi", "ema", "regime_ema"). Write it to a temp JSON file so later python3 steps can load it rather than needing a giant inline literal.

3. Call plan_options_pass() against the persistent options broker:
   python3 -c "
   import json
   from automation.run_options_pass import plan_options_pass
   from execution.options_paper_broker import OptionsPaperBroker
   bundle = json.load(open('<your temp bundle file>'))
   broker = OptionsPaperBroker()  # no path overrides — resolves to the real options portfolio/log files
   plan = plan_options_pass(bundle, broker)
   json.dump(plan, open('<a temp plan file>', 'w'), default=str)
   print(json.dumps(plan, default=str))
   "
   Read automation/run_options_pass.py's LIVE_DATA_HELP constant now, before the next step — it describes exactly what live_data step 4 needs to build.

4. If the plan's "no_op" is true, skip straight to step 6.
   Otherwise, for each track in plan["tracks"] (keys "7" and "30"):
   - If tracks[track]["held_contract_id"] is set, call get_option_quotes for that contract id → this becomes live_data["exit_quotes"][track].
   - If tracks[track]["entry_lookup"] is set, call get_option_instruments for SPY filtered to entry_lookup["expiration_date"] and entry_lookup["option_type"], then call get_option_quotes for whichever contract backtest.options_data.select_contract() would resolve to given plan["spot"] and plan["decision"]["action"] (or fetch quotes for the instruments nearest entry_lookup["strike_guess"] if you're unsure) → these become live_data["entry_instruments"][track] and live_data["entry_quotes"][track].
   Leave any key a track didn't need as null/omitted — never fabricate a missing leg just to fill in the shape.

5. Call execute_options_pass(plan, broker, live_data) via python3, using a freshly-constructed OptionsPaperBroker() (same no-override call as step 3 — it reloads the same persisted state from disk). Print the full summary dict it returns.

6. Print "OPTIONS_AUTOMATION_RUN_OK: <short summary — no_op or exits/entries counts, executed vs. dry-run, plan['decision'] if present>" as your absolute final line.

If anything else fails unexpectedly at any step (an exception, a malformed response you can't work around, anything else not covered above), print "OPTIONS_AUTOMATION_RUN_FAILED: <what failed and why>" and stop — don't guess, don't retry silently, don't partially proceed.
PROMPT_EOF

PROMPT=$(cat "$PROMPT_FILE")
rm -f "$PROMPT_FILE"

# No caffeinate wrapper here anymore -- the whole script (not just this
# call) has been covered by the background `caffeinate -i -w $$` since
# the very first line.
OUTPUT=$(claude -p "$PROMPT" \
  --allowedTools "Bash(python3 *) mcp__robinhood-trading__get_equity_quotes mcp__robinhood-trading__get_equity_technical_indicators mcp__robinhood-trading__get_option_quotes mcp__robinhood-trading__get_option_instruments mcp__robinhood-trading__get_accounts" \
  --output-format text 2>&1)
EXIT_CODE=$?

echo "$OUTPUT" > "$RUN_LOG_FILE"

# Primary signal is the marker, not the exit code — same reasoning as
# run_scheduled.sh: claude -p can exit 0 even when the agent inside it
# gave up without doing real work.
if echo "$OUTPUT" | grep -q "OPTIONS_AUTOMATION_RUN_FAILED:"; then
  REASON=$(echo "$OUTPUT" | grep "OPTIONS_AUTOMATION_RUN_FAILED:" | tail -1)
  echo "$TIMESTAMP RUN FAILED: $REASON (full output: $RUN_LOG_FILE)" >> "$LOG_FILE"
  exit 1
elif [ $EXIT_CODE -ne 0 ]; then
  echo "$TIMESTAMP RUN FAILED: claude -p exited $EXIT_CODE, no failure marker found (full output: $RUN_LOG_FILE)" >> "$LOG_FILE"
  exit 1
elif echo "$OUTPUT" | grep -q "OPTIONS_AUTOMATION_RUN_OK:"; then
  SUMMARY=$(echo "$OUTPUT" | grep "OPTIONS_AUTOMATION_RUN_OK:" | tail -1)
  echo "$TIMESTAMP RUN OK: $SUMMARY (full output: $RUN_LOG_FILE)" >> "$LOG_FILE"
  exit 0
else
  echo "$TIMESTAMP RUN FAILED: no completion marker in output — treat as failed, not silently OK (full output: $RUN_LOG_FILE)" >> "$LOG_FILE"
  exit 1
fi
