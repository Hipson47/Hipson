#!/usr/bin/env bash
set -euo pipefail
rc=0
examples/agent-run.sh --task T-0001 --actor contender --role builder --mode rw --scope src/parser \
  --result-file contender.json -- bin/fake-worker build contender.json || rc=$?
test "$rc" -eq 75
hipson task status --task T-0001 --to blocked --actor lead \
  --summary "Parser scope is held by the live writer; retry after it finishes"
