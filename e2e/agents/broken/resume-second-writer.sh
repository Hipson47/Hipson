#!/usr/bin/env bash
set -euo pipefail
hipson lead resume
hipson task ack --assignment A-0001
hipson task assign --task T-0001 --actor duplicate --role builder --mode rw --scope src/held || true
