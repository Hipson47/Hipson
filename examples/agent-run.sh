#!/usr/bin/env bash
# Linux/WSL2/macOS runner: record a worker run in the Hipson task ledger around a child command.
# Usage: agent-run.sh --task T-0001 --actor codex --role builder --mode rw [--scope PATH]...
#                     [--lease-seconds N] [--ref PATH]... [--result-file PATH] [--rollout codex] -- <command> [args...]
# The runner leads its own process group and runs the worker inside it; the ledger treats the owner as
# alive while any process of that group exists, so a SIGKILLed runner whose worker survives keeps its
# claims. INT/TERM/HUP are forwarded to the worker (KILL after a grace period) before the run is finished.
# The finish records the worker's process result, which is not lead verification.
# Exit codes: the child's code; 75 for refused assignment; 76 for project mismatch.
set -uo pipefail

usage() {
  cat <<'EOF'
Usage: agent-run.sh --task TASK --actor ACTOR --role ROLE --mode MODE [OPTIONS] -- COMMAND [ARGS...]

Record a worker assignment, run COMMAND, then finish it with the process outcome.

Options:
  --task TASK          Task ID (required)
  --actor ACTOR        Worker label (required)
  --role ROLE          builder, reviewer, explorer, or researcher (required)
  --mode MODE          ro or rw (required)
  --scope PATH         Claimed path; repeatable (required for rw)
  --lease-seconds N    Optional assignment lease
  --ref PATH           Evidence reference; repeatable
  --result-file PATH   Ingest structured worker result on finish
  --rollout codex      Attach this run's Codex rollout facts
  --allow-project-mismatch  Run in another project, recording an explicit override event
  -h, --help          Print this help and exit

Exit: child status; 75 for refused assignment; 76 for project mismatch; 64 for invalid arguments.
EOF
}

# Become a process-group leader (pid == pgid) so the ledger can check the whole worker group.
if [ "$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ')" != "$$" ] && [ -z "${AGENT_RUN_SETSID:-}" ]; then
  if command -v setsid >/dev/null 2>&1; then
    AGENT_RUN_SETSID=1 exec setsid bash "$0" "$@"
  else
    AGENT_RUN_SETSID=1 exec python3 -c 'import os,sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' bash "$0" "$@"
  fi
fi

HIPSON_BIN=${HIPSON_BIN:-hipson}
GRACE_SECONDS=${AGENT_RUN_GRACE_SECONDS:-15}
task="" actor="" role="" mode="" lease="" rollout="" result_file=""
allow_project_mismatch=0
scope=() refs=()
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0;;
    --task) task=$2; shift 2;;
    --actor) actor=$2; shift 2;;
    --role) role=$2; shift 2;;
    --mode) mode=$2; shift 2;;
    --scope) scope+=("$2"); shift 2;;
    --lease-seconds) lease=$2; shift 2;;
    --ref) refs+=("$2"); shift 2;;
    --result-file) result_file=$2; shift 2;;
    --rollout) rollout=$2; shift 2;;
    --allow-project-mismatch) allow_project_mismatch=1; shift;;
    --) shift; break;;
    *) echo "agent-run: unknown option $1" >&2; exit 64;;
  esac
done
[ -n "$task" ] && [ -n "$actor" ] && [ -n "$role" ] && [ -n "$mode" ] && [ $# -gt 0 ] || {
  echo "agent-run: --task, --actor, --role, --mode and a command after -- are required" >&2; exit 64; }

# Resolve both paths through the filesystem (symlinks and project subdirectories are fine).
# Keep this before assigning: a mismatched invocation must leave no worker or claim behind.
if ! task_json=$("$HIPSON_BIN" task show --task "$task" --json); then
  echo "agent-run: could not read task $task; worker not started" >&2
  exit 75
fi
if ! project_check=$(python3 -c '
import json, pathlib, subprocess, sys
task = json.load(sys.stdin)
project = pathlib.Path(task["project"]).resolve()
cwd = pathlib.Path.cwd().resolve()
try:
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd,
                         capture_output=True, text=True, check=False, timeout=10)
except FileNotFoundError:
    top = None
actual = pathlib.Path(top.stdout.strip()).resolve() if top is not None and top.returncode == 0 else cwd
inside_non_git_project = (top is None or top.returncode != 0) and cwd.is_relative_to(project)
print(json.dumps({"expected": str(project), "actual": str(actual), "matches": project == actual or inside_non_git_project}))
' <<<"$task_json"); then
  echo "agent-run: could not verify task project; worker not started" >&2
  exit 76
fi
project_mismatch=$(python3 -c 'import json,sys; print(0 if json.load(sys.stdin)["matches"] else 1)' <<<"$project_check")
if [ "$project_mismatch" = 1 ] && [ "$allow_project_mismatch" = 0 ]; then
  echo "agent-run: project mismatch $project_check; worker not started (explicit override: --allow-project-mismatch)" >&2
  exit 76
fi

assignment="" child="" finished=0 rc=0 signal="" facts="" note=""
workdir=$(mktemp -d); marker="$workdir/start"; touch "$marker"
nonce="ledger-run-$(cat /proc/sys/kernel/random/uuid 2>/dev/null || uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"

run_facts() {
  # Model/effort from the Codex rollout of THIS run: the one containing our nonce (sets globals).
  [ "$rollout" = codex ] || return 0
  local rollout_home=${CODEX_HOME:-$HOME/.codex} file model effort
  file=$(find "$rollout_home/sessions" -name '*.jsonl' -newer "$marker" -exec grep -l -F "$nonce" {} + 2>/dev/null | head -1)
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
    local result=passed recorded=0
    if [ -n "$signal" ]; then result=cancelled; elif [ "$rc" != 0 ]; then result=failed; fi
    run_facts
    local summary="worker process exit=${signal:-$rc}${facts}${note}; process result only, not lead verification"
    local finish_args=(task finish --assignment "$assignment" --result "$result")
    for r in ${refs[@]+"${refs[@]}"}; do finish_args+=(--ref "$r"); done
    if [ -n "$result_file" ] && [ -s "$result_file" ]; then
      if "$HIPSON_BIN" "${finish_args[@]}" --summary "$summary" --result-file "$result_file" >/dev/null; then
        recorded=1
      else
        # Invalid worker evidence must not leave the process outcome and claims unfinished.
        summary="$summary; result file rejected"
      fi
    fi
    if [ "$recorded" = 0 ]; then
      "$HIPSON_BIN" "${finish_args[@]}" --summary "$summary" >/dev/null || echo "agent-run: could not finish $assignment; run 'hipson task reap'" >&2
    fi
  fi
  rm -rf "$workdir"
}

group_members() {
  # Other processes of this runner's process group: the worker and all its descendants. Transient
  # helpers of this script (command substitutions) are children of $$ and skipped; the direct
  # worker child is reported via kill -0 instead.
  # Select by real process-group id (numeric `ps -g` means session on procps) and skip zombies:
  # an exited process cannot write and cannot be killed again.
  local pid ppid pgid stat comm out probe_pid
  # Bash 3.2 has no BASHPID. An exec'd helper's PPID identifies this subshell
  # before ps runs, so the probe never mistakes its own transient helpers for workers.
  probe_pid=${BASHPID:-$(exec bash -c 'echo "$PPID"')}
  if [ -n "$child" ] && kill -0 "$child" 2>/dev/null; then
    stat=$(ps -o stat= -p "$child" 2>/dev/null)
    case "$stat" in Z*|"") ;; *) echo "$child";; esac
  fi
  out=$(ps -e -o pid=,ppid=,pgid=,stat=,comm= 2>/dev/null)
  while read -r pid ppid pgid stat comm; do
    [ "$pgid" = "$$" ] || continue
    case "$pid" in "$$"|"$probe_pid") continue;; esac
    case "$ppid" in "$$"|"$probe_pid") continue;; esac
    case "$stat" in Z*) continue;; esac
    [ "${comm##*/}" = ps ] && continue  # BSD ps may report the executable's full path.
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
boot_id=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || { [ "$(uname -s)" = Darwin ] && sysctl -n kern.bootsessionuuid 2>/dev/null; } || true)
[ -n "$boot_id" ] && assign_args+=(--owner-boot-id "$boot_id")
pid_namespace=$(readlink /proc/self/ns/pid 2>/dev/null || true)
[ -n "$pid_namespace" ] && assign_args+=(--owner-pid-namespace "$pid_namespace")
for s in ${scope[@]+"${scope[@]}"}; do assign_args+=(--scope "$s"); done
[ -n "$lease" ] && assign_args+=(--lease-seconds "$lease")
if ! assignment_json=$("$HIPSON_BIN" "${assign_args[@]}"); then
  echo "agent-run: ledger refused the assignment for $task; worker not started" >&2
  exit 75
fi
assignment=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$assignment_json")
if [ "$project_mismatch" = 1 ]; then
  if ! "$HIPSON_BIN" task event --task "$task" --actor "$actor" --type project_mismatch_override \
      --summary "Explicit --allow-project-mismatch: $project_check" --ref "$assignment" >/dev/null; then
    echo "agent-run: could not record project override; worker not started" >&2
    rc=75
    exit 75
  fi
fi
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
