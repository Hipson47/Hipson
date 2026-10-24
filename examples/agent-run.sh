#!/usr/bin/env bash
# WSL-side runner: record a worker run in the Hipson task ledger around a child command.
# Usage: agent-run.sh --task T-0001 --actor codex --role builder --mode rw [--scope PATH]...
#                     [--lease-seconds N] [--ref PATH]... [--rollout codex] -- <command> [args...]
# The runner leads its own process group and runs the worker inside it; the ledger treats the owner as
# alive while any process of that group exists, so a SIGKILLed runner whose worker survives keeps its
# claims. INT/TERM/HUP are forwarded to the worker (KILL after a grace period) before the run is finished.
# The finish records the worker's process result, which is not lead verification.
# Exit codes: the child's code; 75 when the ledger refused the assignment (e.g. live claim conflict).
set -uo pipefail

# Become a process-group leader (pid == pgid) so the ledger can check the whole worker group.
if [ "$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ')" != "$$" ] && [ -z "${AGENT_RUN_SETSID:-}" ]; then
  AGENT_RUN_SETSID=1 exec setsid bash "$0" "$@"
fi

HIPSON_BIN=${HIPSON_BIN:-hipson}
GRACE_SECONDS=${AGENT_RUN_GRACE_SECONDS:-15}
task="" actor="" role="" mode="" lease="" rollout=""
scope=() refs=()
while [ $# -gt 0 ]; do
  case "$1" in
    --task) task=$2; shift 2;;
    --actor) actor=$2; shift 2;;
    --role) role=$2; shift 2;;
    --mode) mode=$2; shift 2;;
    --scope) scope+=("$2"); shift 2;;
    --lease-seconds) lease=$2; shift 2;;
    --ref) refs+=("$2"); shift 2;;
    --rollout) rollout=$2; shift 2;;
    --) shift; break;;
    *) echo "agent-run: unknown option $1" >&2; exit 64;;
  esac
done
[ -n "$task" ] && [ -n "$actor" ] && [ -n "$role" ] && [ -n "$mode" ] && [ $# -gt 0 ] || {
  echo "agent-run: --task, --actor, --role, --mode and a command after -- are required" >&2; exit 64; }

assignment="" child="" finished=0 rc=0 signal="" facts=""
workdir=$(mktemp -d); marker="$workdir/start"; touch "$marker"
nonce="ledger-run-$(cat /proc/sys/kernel/random/uuid)"

run_facts() {
  # Model/effort from the Codex rollout of THIS run: the one containing our nonce (sets globals).
  [ "$rollout" = codex ] || return 0
  local home=${CODEX_HOME:-$HOME/.codex-app} file model effort
  file=$(find "$home/sessions" -name '*.jsonl' -newer "$marker" -print0 2>/dev/null | xargs -0 -r grep -l -F "$nonce" 2>/dev/null | head -1)
  [ -n "$file" ] || { facts=" model=? effort=? (rollout not found)"; return 0; }
  model=$(grep -o '"model":"[^"]*"' "$file" | head -1 | cut -d'"' -f4)
  effort=$(grep -o '"reasoning_effort":"[^"]*"' "$file" | head -1 | cut -d'"' -f4)
  facts=" model=${model:-?} effort=${effort:-?}"
  refs+=("$file")
}

finish() {
  [ $finished = 1 ] && return
  finished=1
  if [ -n "$assignment" ]; then
    local result=passed
    if [ -n "$signal" ]; then result=cancelled; elif [ "$rc" != 0 ]; then result=failed; fi
    run_facts
    local summary="worker process exit=${signal:-$rc}${facts}; process result only, not lead verification"
    local finish_args=(task finish --assignment "$assignment" --result "$result" --summary "$summary")
    for r in ${refs[@]+"${refs[@]}"}; do finish_args+=(--ref "$r"); done
    "$HIPSON_BIN" "${finish_args[@]}" >/dev/null || echo "agent-run: could not finish $assignment; run 'hipson task reap'" >&2
  fi
  rm -rf "$workdir"
}

stop_child() {
  # Forward the signal, give the worker a bounded grace period, then kill what is left of it.
  signal=$1
  if [ -n "$child" ] && kill -0 "$child" 2>/dev/null; then
    kill "-$1" "$child" 2>/dev/null
    local waited=0
    while kill -0 "$child" 2>/dev/null && [ $waited -lt $((GRACE_SECONDS * 10)) ]; do sleep 0.1; waited=$((waited + 1)); done
    kill -KILL "$child" 2>/dev/null
  fi
  finish
  exit "$2"
}
# Traps go in before anything is recorded, so no window exists without cleanup.
trap 'stop_child INT 130' INT
trap 'stop_child TERM 143' TERM
trap 'stop_child HUP 129' HUP
trap finish EXIT

assign_args=(task assign --task "$task" --actor "$actor" --role "$role" --mode "$mode"
  --owner-pid "$$" --owner-boot-id "$(cat /proc/sys/kernel/random/boot_id)" --owner-host "$(hostname)" --json)
for s in ${scope[@]+"${scope[@]}"}; do assign_args+=(--scope "$s"); done
[ -n "$lease" ] && assign_args+=(--lease-seconds "$lease")
if ! assignment_json=$("$HIPSON_BIN" "${assign_args[@]}"); then
  echo "agent-run: ledger refused the assignment for $task; worker not started" >&2
  exit 75
fi
assignment=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$assignment_json")
echo "agent-run: $task $assignment $actor/$role/$mode" >&2
# Hipson's instructions put agents into worker mode on this (no own orchestration, no ledger writes).
export HIPSON_ROLE=worker HIPSON_TASK="$task" HIPSON_ASSIGNMENT="$assignment"

# Background + wait keeps the traps responsive; the explicit redirects keep the worker's stdin
# (a background job would otherwise read /dev/null). Codex briefs get the nonce appended so the
# exact rollout of this run can be found later.
if [ "$rollout" = codex ]; then
  { cat; printf '\n\n<!-- %s -->\n' "$nonce"; } > "$workdir/brief"
  "$@" < "$workdir/brief" &
else
  "$@" <&0 &
fi
child=$!
while :; do
  wait "$child"; rc=$?
  kill -0 "$child" 2>/dev/null || break
done
exit $rc
