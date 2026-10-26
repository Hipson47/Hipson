#!/usr/bin/env bash
set -euo pipefail
hipson lead resume
hipson task run --task T-0001 --actor repair --role builder --mode rw --scope src/parser \
  --result-file repair.json -- bin/fake-worker fix repair.json
hipson task finding update --finding F-0001 --status fixed --actor lead --resolved-by-assignment A-0003
