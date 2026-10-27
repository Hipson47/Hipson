#!/usr/bin/env bash
# End-to-end smoke test of a Hipson checkout on this machine (macOS or Linux).
# Run from the repository root:  bash scripts/platform_smoke.sh
# Writes platform-smoke-report.txt next to it; send that file back to the maintainers.
# Needs git and uv (https://docs.astral.sh/uv/). Uses a throwaway ledger; touches nothing else.
# Bash 3.2 compatible (the macOS default).
set -u

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/hipson-smoke.XXXXXX")
FINAL_REPORT="$ROOT/platform-smoke-report.txt"
# Logging into the candidate tree between verification and closure would make
# the evidence stale. Publish the report only after all lifecycle checks finish.
REPORT="$WORK/report.txt"
export HIPSON_LEDGER="$WORK/ledger.sqlite"
unset HIPSON_ROLE HIPSON_TASK HIPSON_ASSIGNMENT
PASS=0
FAIL=0
FAILED=""
BG=""

cleanup() {
  for pid in $BG; do kill -9 "-$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null; done
  [ ! -f "$REPORT" ] || cp "$REPORT" "$FINAL_REPORT"
  rm -rf "$WORK"
}
trap cleanup EXIT

# The report is sent to other people: replace the home directory and login name before writing it.
LOGIN=$(id -un 2>/dev/null || echo "")
private() { HOME_DIR="$HOME" LOGIN_NAME="$LOGIN" python3 -c '
import os, sys
text = sys.stdin.read()
for value, mask in ((os.environ["HOME_DIR"], "~"), (os.environ["LOGIN_NAME"], "<user>")):
    if len(value) > 1:
        text = text.replace(value, mask)
sys.stdout.write(text)' 2>/dev/null || sed "s#$HOME#~#g"; }
log() { printf '%s\n' "$*" | private | tee -a "$REPORT"; }
ok() { PASS=$((PASS + 1)); log "PASS  $1"; }
bad() { FAIL=$((FAIL + 1)); FAILED="$FAILED
  - $1"; log "FAIL  $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | sed 's/^/      /' | tail -n 15 | private | tee -a "$REPORT"; }
check() {  # check NAME COMMAND...  -> PASS when the command exits 0
  name=$1; shift
  if out=$("$@" 2>&1); then ok "$name"; else bad "$name (exit $?)" "$out"; fi
}
json_field() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(eval("d" + sys.argv[1]))' "$1"; }

: > "$REPORT"
log "Hipson platform smoke test - $(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "system:  $(uname -srm)"
if command -v sw_vers >/dev/null 2>&1; then log "macOS:   $(sw_vers -productVersion) ($(sw_vers -buildVersion))"; fi
log "bash:    $BASH_VERSION"
log "git rev: $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null) ($(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null))"
log "uv:      $(uv --version 2>/dev/null || echo 'missing')"
log ""

cd "$ROOT" || exit 1
if ! command -v uv >/dev/null 2>&1; then
  bad "uv is installed" "Install it: curl -LsSf https://astral.sh/uv/install.sh | sh  (then reopen the terminal)"
  log ""; log "RESULT: $PASS passed, $FAIL failed"; exit 1
fi
check "uv sync --all-extras (install from this checkout)" uv sync -q --all-extras
export PATH="$ROOT/.venv/bin:$PATH"
H="$ROOT/.venv/bin/hipson"
log "python:  $(python3 -c 'import sys, sqlite3; print(sys.version.split()[0], "sqlite", sqlite3.sqlite_version)')"
log ""

check "hipson --help" "$H" --help
check "hipson doctor" "$H" doctor
check "hipson init --dry-run (no client config edits)" "$H" init --project "$ROOT" --client none --dry-run --json

# Platform identity the ledger relies on for crash recovery.
boot=$(python3 -c 'from hipson.ledger import _boot_id; print(_boot_id() or "")' 2>&1)
if [ "$(uname -s)" = "Darwin" ] || [ "$(uname -s)" = "Linux" ]; then
  if [ -n "$boot" ]; then ok "boot identity available ($boot)"; else bad "boot identity available" "_boot_id() returned nothing"; fi
fi

# Manual ledger lifecycle (the runner below automates assign/finish).
check "task open" "$H" task open --project . --title "Fix parser" --objective "Parser accepts trailing commas" --acceptance "tests pass"
check "task assign (rw claim)" "$H" task assign --task T-0001 --actor codex --role builder --mode rw --scope src/parser
check "task board" "$H" task board
check "task finish" "$H" task finish --assignment A-0001 --result passed --summary "worker exited 0" --ref runs/report.md
check "task ack" "$H" task ack --assignment A-0001 --note "Read worker outcome and independently checked the change"
check "task verify" "$H" task verify --task T-0001 -- git diff --check
check "task status -> review" "$H" task status --task T-0001 --to review --actor lead --summary "verified: tests pass"
check "task status -> done" "$H" task status --task T-0001 --to done --actor lead
check "task brief" "$H" task brief --task T-0001 --role reviewer

# Worker wrapper: a live writer holds a scope, a second writer must be refused with exit 75.
"$H" task open --project . --title "Wrapper" --objective "Wrapper checks" >/dev/null 2>&1
HIPSON_BIN="$H" examples/agent-run.sh --task T-0002 --actor holder --role builder --mode rw --scope src \
  -- sleep 30 >"$WORK/holder.log" 2>&1 &
holder=$!
BG="$BG $holder"
i=0; while [ $i -lt 100 ] && ! "$H" task show --task T-0002 --json 2>/dev/null | grep -q '"holder"'; do sleep 0.1; i=$((i + 1)); done
HIPSON_BIN="$H" examples/agent-run.sh --task T-0002 --actor second --role builder --mode rw --scope src -- true \
  >"$WORK/second.log" 2>&1
code=$?
if [ "$code" = 75 ]; then ok "second writer on the same scope refused (exit 75)"; else bad "second writer refused with exit 75 (got $code)" "$(cat "$WORK/second.log" "$WORK/holder.log")"; fi

# The holder is alive: reap must keep it.
"$H" task reap --json >"$WORK/reap1.json" 2>&1
if "$H" task show --task T-0002 --json | grep -q '"status": "active"'; then ok "reap keeps a live worker"; else bad "reap keeps a live worker" "$(cat "$WORK/reap1.json")"; fi

# Kill the runner and its worker group hard: reap must release the claim (owner gone).
owner=$("$H" task show --task T-0002 --json | json_field '["assignments"][0]["owner_pid"]')
if [ "$owner" = "$holder" ]; then  # Only ever signal the runner this script started itself.
  kill -9 "-$holder" 2>/dev/null || kill -9 "$holder" 2>/dev/null
else
  bad "recorded owner is the runner this script started" "owner_pid=$owner, started=$holder"
  kill -9 "$holder" 2>/dev/null
fi
wait "$holder" 2>/dev/null
BG=""  # Reaped: its PID may be reused later, so the EXIT trap must not signal it again.
sleep 0.5
"$H" task reap --json >"$WORK/reap2.json" 2>&1
if grep -q '"pid_gone"\|"cancelled"' "$WORK/reap2.json"; then ok "reap releases a killed worker's claim"; else bad "reap releases a killed worker's claim" "$(cat "$WORK/reap2.json")"; fi
HIPSON_BIN="$H" examples/agent-run.sh --task T-0002 --actor third --role builder --mode rw --scope src -- true \
  >"$WORK/third.log" 2>&1
code=$?
if [ "$code" = 0 ]; then ok "scope reusable after reap"; else bad "scope reusable after reap (exit $code)" "$(cat "$WORK/third.log")"; fi
check "agent-run.sh --help" examples/agent-run.sh --help

# Native runner: same claim/finish contract, available without a source-clone wrapper.
check "hipson task run --help" "$H" task run --help
"$H" task open --project . --title "Native runner" --objective "Cross-platform worker checks" >/dev/null 2>&1
"$H" task run --task T-0003 --actor native-holder --role builder --mode rw --scope src \
  -- python3 -c 'import time; time.sleep(30)' >"$WORK/native-holder.log" 2>&1 &
holder=$!
BG="$holder"
i=0; while [ $i -lt 100 ] && ! "$H" task show --task T-0003 --json 2>/dev/null | grep -q '"native-holder"'; do sleep 0.1; i=$((i + 1)); done
"$H" task run --task T-0003 --actor native-second --role builder --mode rw --scope src \
  -- python3 -c 'pass' >"$WORK/native-second.log" 2>&1
code=$?
if [ "$code" = 75 ]; then ok "native runner refuses overlapping writer (exit 75)"; else bad "native runner conflict (got $code)" "$(cat "$WORK/native-second.log")"; fi
kill -TERM "$holder" 2>/dev/null
wait "$holder" 2>/dev/null
code=$?
BG=""
if [ "$code" = 143 ]; then ok "native runner forwards TERM (exit 143)"; else bad "native runner TERM (got $code)" "$(cat "$WORK/native-holder.log")"; fi
check "native runner scope reusable, stdin and refs" "$H" task run --task T-0003 --actor native-next \
  --role builder --mode rw --scope src --ref runs/report.md -- python3 -c 'import sys; assert sys.stdin.read() == "brief\n"' <<<"brief"
"$H" task run --task T-0003 --actor native-failed --role reviewer -- python3 -c 'raise SystemExit(9)' \
  >"$WORK/native-failed.log" 2>&1
code=$?
if [ "$code" = 9 ]; then ok "native runner preserves failed child code (exit 9)"; else bad "native runner failed code (got $code)" "$(cat "$WORK/native-failed.log")"; fi
(cd "$WORK" && "$H" task run --task T-0003 --actor wrong-project --role reviewer -- python3 -c 'pass') \
  >"$WORK/native-mismatch.log" 2>&1
code=$?
if [ "$code" = 76 ]; then ok "native runner binds project (exit 76)"; else bad "native runner project check (got $code)" "$(cat "$WORK/native-mismatch.log")"; fi

# MCP over stdio: initialize + tools/list + a read tool.
mcp_out=$(printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"task.board","arguments":{}}}' \
  | "$H" mcp serve --project . --stdio 2>&1)
if printf '%s' "$mcp_out" | grep -q '"task.brief"' && printf '%s' "$mcp_out" | grep -q '"id": *3'; then
  ok "MCP stdio initialize, tools/list, task.board"
else
  bad "MCP stdio initialize, tools/list, task.board" "$mcp_out"
fi

# Full test suites.
suite() {  # suite NAME COMMAND...  -> runs once, logs the summary line, keeps failures in the report
  name=$1; shift
  if out=$("$@" 2>&1); then ok "$name"; else bad "$name (exit $?)" "$(printf '%s\n' "$out" | grep -E 'FAIL|Error|error' | head -n 15)"; fi
  log "      $(printf '%s\n' "$out" | tail -n 1)"
  printf '%s\n' "$out" | grep -E '^SKIP|SKIPPED' | sort | uniq -c | head -n 10 | sed 's/^/      /' | tee -a "$REPORT" >/dev/null
}
if [ -z "${SMOKE_SKIP_SUITES:-}" ]; then
  suite "test suite: scripts/run_tests.py" python3 scripts/run_tests.py
  suite "test suite: pytest" python3 -m pytest -q -p no:cacheprovider -rs
fi

log ""
log "RESULT: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then log "Failed checks:$FAILED"; fi
printf 'Report: %s\n' "$FINAL_REPORT"  # Terminal only: the absolute path names the user's home.
[ "$FAIL" -eq 0 ]
