#!/usr/bin/env bash
set -euo pipefail
hipson task reap --task T-0001 --dry-run
hipson task reap --task T-0001
examples/agent-run.sh --task T-0001 --actor replacement --role builder --mode rw --scope src/parser \
  --result-file replacement.json -- bin/fake-worker build replacement.json
hipson task show --task T-0001
