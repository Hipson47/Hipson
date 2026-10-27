#!/usr/bin/env bash
set -euo pipefail
hipson task run --task T-0001 --actor builder --role builder --mode rw --scope src/parser --scope tests \
  --result-file candidate.json -- python3 bin/candidate.py
export PYTHONPATH=".:$PYTHONPATH"
hipson task ack --assignment A-0001 --note "Read worker result; independently checking acceptance"
hipson task verify --task T-0001 -- python3 tests/acceptance.py
hipson task status --task T-0001 --to review --actor lead
hipson task status --task T-0001 --to done --actor lead
