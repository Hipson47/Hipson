#!/usr/bin/env bash
set -euo pipefail
examples/agent-run.sh --task T-0001 --actor contender --role builder --mode rw --scope src/parser \
  --result-file contender.json -- bin/fake-worker build contender.json || test "$?" -eq 75
# Wrong: claiming a still-running holder finished does not make the scope safe.
hipson task finish --assignment A-0001 --result passed --summary "Bypass live ownership"
examples/agent-run.sh --task T-0001 --actor contender --role builder --mode rw --scope src/parser \
  --result-file contender.json -- bin/fake-worker build contender.json
hipson task status --task T-0001 --to blocked --actor lead --summary "Pretend this is safe"
