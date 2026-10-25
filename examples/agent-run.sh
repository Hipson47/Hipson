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

assignment="" child="" finished=0 rc=0 signal="" facts="" note=""
workdir=$(mktemp -d); marker="$workdir/start"; touch "$marker"
nonce="ledger-run-$(cat /proc/sys/kernel/random/uuid)"

run_facts() {
  # Model/effort from the Codex rollout of THIS run: the one containing our nonce (sets globals).
  [ "$rollout" = codex ] || return 0
  local home=${CODEX_HOME:-$HOME/.codex} file model effort
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
    local summary="worker process exit=${signal:-$rc}${facts}${note}; process result only, not lead verification"
    local finish_args=(task finish --assignment "$assignment" --result "$result" --summary "$summary")
    for r in ${refs[@]+"${refs[@]}"}; do finish_args+=(--ref "$r"); done
    "$HIPSON_BIN" "${finish_args[@]}" >/dev/null || echo "agent-run: could not finish $assignment; run 'hipson task reap'" >&2
  fi
  rm -rf "$workdir"
}

group_members() {
  # Other processes of this runner's process group: the worker and all its descendants. Transient
  # helpers of this script (command substitutions) are children of $$ and skipped; the direct
  # worker child is reported via kill -0 instead.
  # Select by real process-group id (numeric `ps -g` means session on procps) and skip zombies:
  # an exited process cannot write and cannot be killed again.
  local pid ppid pgid stat comm out
  if [ -n "$child" ] && kill -0 "$child" 2>/dev/null; then
    stat=$(ps -o stat= -p "$child" 2>/dev/null)
    case "$stat" in Z*|"") ;; *) echo "$child";; esac
  fi
  out=$(ps -e -o pid=,ppid=,pgid=,stat=,comm= 2>/dev/null)
  while read -r pid ppid pgid stat comm; do
    [ "$pgid" = "$$" ] || continue
    case "$pid" in "$$"|"$BASHPID") continue;; esac
    case "$ppid" in "$$"|"$BASHPID") continue;; esac
    case "$stat" in Z*) continue;; esac
    [ "$comm" = ps ] && continue
    echo "$pid"
  done <<<"$out"
}

stop_group() {
  # Signal every worker process, wait a bounded grace period, KILL what is left, and only return
  # once the group is empty, so claims are never released while a worker process can still write.
  local sig=$1 waited=0 pid
  trap '' INT TERM HUP
  kill "-$sig" -- "-$$" 2>/dev/null
  while [ -n "$(group_members)" ] && [ $waited -lt $((GRACE_SECONDS * 10)) ]; do sleep 0.1; waited=$((waited + 1)); done
  for pid in $(group_members); do kill -KILL "$pid" 2>/dev/null; done
  waited=0
  while [ -n "$(group_members)" ] && [ $waited -lt 50 ]; do sleep 0.1; waited=$((waited + 1)); done
  if [ -n "$(group_members)" ]; then
    echo "agent-run: worker processes survived KILL; leaving $assignment active (run 'hipson task reap' later)" >&2
    assignment=""
  fi
}

stop_child() {
  signal=$1
  stop_group "$1"
  finish
  exit "$2"
}
# Traps go in before anything is recorded, so no window exists without cleanup.
trap 'stop_child INT 130' INT
trap 'stop_child TERM 143' TERM
trap 'stop_child HUP 129' HUP
trap finish EXIT

assign_args=(task assign --task "$task" --actor "$actor" --role "$role" --mode "$mode"
  --owner-pid "$$" --owner-host "$(hostname)" --json)
boot_id=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)
[ -n "$boot_id" ] && assign_args+=(--owner-boot-id "$boot_id")
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
# A worker that exits but leaves processes behind could keep writing after its claims are released.
if [ -n "$(group_members)" ]; then
  echo "agent-run: worker left processes running; stopping them before finishing" >&2
  note="; leftover processes stopped"
  stop_group TERM
fi
exit $rc
