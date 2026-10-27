#!/usr/bin/env bash
set -euo pipefail
hipson task run --task T-0001 --actor builder --role builder --mode rw --scope src/parser \
  --result-file candidate.json -- python3 bin/candidate.py
hipson task ack --assignment A-0001 --note "Read worker result; the declared check decides acceptance"
hipson task close --task T-0001
