#!/bin/bash
# automation/run_scheduled.sh — the local launchd-scheduled entrypoint for
# the daily council pass.
#
# Runs LOCALLY, not as a cloud routine. Robinhood's MCP OAuth session only
# exists in this machine's authenticated Claude Code CLI context — a cloud
# sandbox has no way to reach it (confirmed the hard way: a cloud routine
# was tried first and failed silently at the fetch step). See
# agents/AUTOMATION_DESIGN.md's "Why a scheduled Claude Code routine"
# section — that reasoning still holds for why this can't be a bare cron
# job calling Python directly; it just has to be a LOCAL agent session
# now, not a cloud one.
#
# THE SILENT-FAILURE FIX: the cloud routine's failure was invisible because
# nothing durable was ever written before it died — a run that fails
# upstream of run_pass()'s own logging left zero trace anywhere. Every
# invocation of this script — success, failure, or crash — appends exactly
# one timestamped line to logs/automation_runs.log. A run that produces
# neither an AUTOMATION_RUN_OK nor an AUTOMATION_RUN_FAILED marker from the
# agent is treated as a failure, not silently dropped — see the "no
# completion marker" case below, which closes the exact hole that bit us.
#
# SAFETY: this script never touches execution/config.py. AUTOMATION_DRY_RUN
# stays whatever it's already set to in the repo (True by default) —
# nothing here can arm real paper execution, let alone real orders. The
# agent it invokes is explicitly instructed never to edit that file either.
#
# The sub-agent's --allowedTools below is deliberately scoped to
# Bash(python3 *) — not bare Bash — plus the four specific read-only
# Robinhood MCP tools it actually needs. This runs unattended, daily, with
# no human approving individual commands, so "the agent only needed to run
# python3 one-liners" gets enforced structurally rather than trusted to
# the prompt alone; there's no order-placement tool in this list at all.

# THE RACE THIS CLOSES: launchd wakes this Mac from a brief background
# "DarkWake" specifically to fire this job — but on battery, `pmset -g
# log` shows the system re-entering "Maintenance Sleep" as little as 8
# SECONDS after that DarkWake (confirmed for the 2026-07-15, 07-16, and
# 07-17 run failures — all three on battery at 14-33% charge; the one
# clean weekday run, 07-14, had no DarkWake cycling in its window at
# all). The old fix wrapped ONLY the final `claude -p` call in
# `caffeinate -i`, but by then several seconds of setup (PATH export,
# cd, venv activation, writing the prompt heredoc) had already burned
# through that 8-second window, and `claude -p`'s HTTPS connection to
# the API got severed mid-response before the assertion ever took hold
# ("Connection closed mid-response", no completion marker, run failed).
# Starting caffeinate here, as the literal first line, with `-w $$`
# (hold the assertion until THIS script's own PID exits, then exit
# itself — no trap/cleanup needed) closes that gap: the assertion is
# live within milliseconds of the script starting, before any setup
# overhead, not after it.
caffeinate -i -w $$ &

set -uo pipefail  # deliberately NOT -e: failures are caught and logged below, not just abandoned

# launchd runs services with a minimal PATH (no ~/.local/bin, no
# Python.framework) — without this, `claude` and `python3` simply aren't
# found and the job fails before it even starts logging.
export PATH="/Users/ethandungo/.local/bin:/Library/Frameworks/Python.framework/Versions/3.14/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

PROJECT_DIR="/Users/ethandungo/agent-trader"
LOG_FILE="$PROJECT_DIR/logs/automation_runs.log"
RUN_LOG_DIR="$PROJECT_DIR/logs/automation_runs"
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
RUN_LOG_FILE="$RUN_LOG_DIR/$TIMESTAMP.log"

mkdir -p "$RUN_LOG_DIR"

cd "$PROJECT_DIR" || {
  echo "$TIMESTAMP RUN FAILED: could not cd to $PROJECT_DIR" >> "$LOG_FILE"
  exit 1
}

# This project doesn't currently ship a venv, but don't assume — activate
# quietly if one shows up later, proceed with system python3 otherwise.
if [ -f "$PROJECT_DIR/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.venv/bin/activate"
fi

# config/.env is gitignored and optional (see research/sec_client.py's
# docstring — there's no automatic .env loading anywhere in this project,
# so this is the one place that convention gets honored for a local run).
if [ -f "$PROJECT_DIR/config/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/config/.env"
  set +a
fi
# launchd's environment won't have picked up anything from your shell
# profile — export SEC_USER_AGENT if it's already set (e.g. from
# config/.env above), otherwise fall back to the known-good contact
# rather than silently defaulting to SEC's generic, rate-limit-prone one.
export SEC_USER_AGENT="${SEC_USER_AGENT:-Ethan knt6hptzgv@privaterelay.appleid.com}"

echo "$TIMESTAMP RUN START" >> "$LOG_FILE"

# Written to a plain file first, not `$(cat <<'EOF' ... EOF)` directly —
# a heredoc nested inside a command substitution gets confused by an odd
# count of literal apostrophes in the body even with a quoted delimiter
# (a real bash parser quirk, confirmed the hard way), and this prompt's
# prose has plenty of them ("it's", "doesn't", "don't").
PROMPT_FILE="$PROJECT_DIR/logs/automation_runs/.prompt_$$.txt"
cat > "$PROMPT_FILE" <<'PROMPT_EOF'
You are running one daily automated pass of the "agent-trader" paper-trading council, launched LOCALLY via launchd (not a cloud routine — Robinhood's MCP session is authenticated only in this machine's local Claude Code context). The repo is already at the current directory. Read agents/AUTOMATION_DESIGN.md and automation/run_pass.py's module docstring if you want the full fail-safe rationale — the essentials are repeated below.

NON-NEGOTIABLE RULES:
- Never edit execution/config.py. Never touch PAPER_MODE, AGENT_TRADER_LIVE, or AUTOMATION_DRY_RUN. This project stays in paper mode / dry-run until a human deliberately changes those lines outside of this run.
- Never fabricate, estimate, or carry over stale data for any symbol.
- Your ABSOLUTE FINAL line of output must be EXACTLY ONE of:
      AUTOMATION_RUN_OK: <one-line summary>
  or
      AUTOMATION_RUN_FAILED: <specific, one-line reason>
  This is a hard requirement, not a suggestion. The wrapper script that invoked you greps your output for these exact markers to know whether this run actually succeeded — a run that ends without printing one of them is indistinguishable from a silent crash, and the wrapper will correctly treat it as a failure regardless of your exit code. Print it as the very last thing you do, after everything else.

STEPS:

0. Cheap pre-check, no MCP calls:
   python3 -c "from datetime import datetime, timezone; from execution import config; print(config.market_is_open(datetime.now(timezone.utc)))"
   If it prints False: run
   python3 -c "from automation.run_pass import run_pass; print(run_pass({}))"
   print that result, then print "AUTOMATION_RUN_OK: market closed, no-op" and STOP. Do not proceed to step 1.

1. Confirm the Robinhood MCP tools are available (try get_accounts). If they are not available, not authenticated, or error out, print "AUTOMATION_RUN_FAILED: robinhood MCP unavailable — <specific error>" and STOP immediately. Do not fabricate data. Do not proceed.

2. For every symbol in execution/config.py's WATCHLIST, call:
   - get_equity_quotes
   - get_equity_technical_indicators for ATR (period 14), RSI (period 14), and EMA — TWICE: once at the short period used elsewhere in this codebase (period 9), and once at period=config.REGIME_EMA_LOOKBACK_DAYS for the regime filter. These must be two genuinely separate calls with different periods — read execution/robinhood.py's get_regime_ema() docstring for why, and how much price history to request so the longer EMA is accurate.
   - get_equity_fundamentals (sector, for the risk vetoer)
   Read agents/demo_council.py's module docstring for the exact expected shape of each raw response — match it exactly. If any symbol's data fails to fetch or looks malformed, that's fine — automation/run_pass.py's own per-symbol sanity check handles it; don't pre-filter or guess.

3. For every symbol, form its Fundamentals verdict: call agents.fundamentals_seat.build_brief(ticker) via python3 (pure Python, pulls public SEC data directly, no MCP needed), read the resulting brief, and form your own genuine stance/confidence/reasons judgment grounded in what it actually shows — this must be your real judgment, not a mechanical score. Package it with agents.fundamentals_seat.form_verdict(ticker, stance, confidence, reasons).

4. Assemble one bundle: {symbol: {"quote":..., "atr":..., "rsi":..., "ema":..., "regime_ema":..., "robinhood_fundamentals":..., "fundamentals_verdict":...}} for every watchlist symbol — see automation/run_pass.py's BUNDLE_HELP constant for the exact required shape.

5. Call automation.run_pass.run_pass(bundle) via python3 — no now or broker override; this is a real pass against the real shared paper account and trade log. It already runs assert_paper_mode() first, the market-hours guard, the per-symbol data sanity check, exit-sweep-before-entries, and routes through AUTOMATION_DRY_RUN.

6. Print the full run summary it returns.

7. Print "AUTOMATION_RUN_OK: <short summary — symbols evaluated/skipped, entries/exits/holds counts, current round-trip stats>" as your absolute final line.

If anything else fails unexpectedly at any step (an exception, a malformed response you can't work around, anything else not covered above), print "AUTOMATION_RUN_FAILED: <what failed and why>" and stop — don't guess, don't retry silently, don't partially proceed.
PROMPT_EOF

PROMPT=$(cat "$PROMPT_FILE")
rm -f "$PROMPT_FILE"

# No caffeinate wrapper here anymore -- the whole script (not just this
# call) has been covered by the background `caffeinate -i -w $$` since
# the very first line.
OUTPUT=$(claude -p "$PROMPT" \
  --allowedTools "Bash(python3 *) mcp__robinhood-trading__get_equity_quotes mcp__robinhood-trading__get_equity_technical_indicators mcp__robinhood-trading__get_equity_fundamentals mcp__robinhood-trading__get_accounts" \
  --output-format text 2>&1)
EXIT_CODE=$?

echo "$OUTPUT" > "$RUN_LOG_FILE"

# Primary signal is the marker, not the exit code — claude -p can exit 0
# even when the agent inside it gave up without doing real work (exactly
# how the cloud routine failed silently). A run that produces NEITHER
# marker is treated as a failure, closing that exact hole.
if echo "$OUTPUT" | grep -q "AUTOMATION_RUN_FAILED:"; then
  REASON=$(echo "$OUTPUT" | grep "AUTOMATION_RUN_FAILED:" | tail -1)
  echo "$TIMESTAMP RUN FAILED: $REASON (full output: $RUN_LOG_FILE)" >> "$LOG_FILE"
  exit 1
elif [ $EXIT_CODE -ne 0 ]; then
  echo "$TIMESTAMP RUN FAILED: claude -p exited $EXIT_CODE, no failure marker found (full output: $RUN_LOG_FILE)" >> "$LOG_FILE"
  exit 1
elif echo "$OUTPUT" | grep -q "AUTOMATION_RUN_OK:"; then
  SUMMARY=$(echo "$OUTPUT" | grep "AUTOMATION_RUN_OK:" | tail -1)
  echo "$TIMESTAMP RUN OK: $SUMMARY (full output: $RUN_LOG_FILE)" >> "$LOG_FILE"
  exit 0
else
  echo "$TIMESTAMP RUN FAILED: no completion marker in output — treat as failed, not silently OK (full output: $RUN_LOG_FILE)" >> "$LOG_FILE"
  exit 1
fi
