#!/usr/bin/env bash
set -euo pipefail
hipson task run --task T-0001 --actor builder --role builder --mode rw --scope src/parser --scope tests \
  --result-file candidate.json -- python3 bin/candidate.py
hipson task ack --assignment A-0001 --note "Read worker result"
hipson task verify --task T-0001 -- false && exit 1
hipson task status --task T-0001 --to blocked --actor lead --summary "Some check failed"
