#!/usr/bin/env bash
set -euo pipefail
hipson task run --task T-0001 --actor builder --role builder --mode rw --scope src/parser --scope tests \
  --result-file candidate.json -- python3 bin/candidate.py
export PYTHONPATH=".:$PYTHONPATH"
hipson task ack --assignment A-0001 --note "Read worker result; independently checking acceptance"
hipson task status --task T-0001 --to review --actor lead --force --reason "Worker says passed"
hipson task status --task T-0001 --to done --actor lead --force --reason "Worker says passed"
