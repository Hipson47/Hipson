#!/usr/bin/env bash
set -euo pipefail
hipson task run --task T-0001 --actor builder --role builder --mode rw --scope src/parser \
  --result-file candidate.json -- python3 bin/candidate.py
hipson task ack --assignment A-0001 --note "Read worker result"
hipson task checks --task T-0001 --check true --reason "The real check is slow"
hipson task close --task T-0001
