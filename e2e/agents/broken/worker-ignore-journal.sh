#!/usr/bin/env bash
set -euo pipefail
hipson task run --task T-0001 --actor unreliable --role builder --mode rw --scope src/parser \
  -- python3 bin/crash-worker.py || test "$?" = 9
hipson task run --task T-0001 --actor replacement --role builder --mode rw --scope src/parser \
  --result-file replacement.json -- bin/fake-worker build replacement.json
# Recover the parser but leave the failed delivery unconsumed.
hipson task ack --assignment A-0002
hipson task verify --task T-0001 -- python3 -c "from src.parser.pairs import parse_pair; assert parse_pair('a:b') == ('a', 'b')"
hipson task status --task T-0001 --to review --actor lead
