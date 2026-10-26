#!/usr/bin/env bash
set -euo pipefail
hipson task run --task T-0001 --actor unreliable --role builder --mode rw --scope src/parser \
  -- python3 bin/crash-worker.py || test "$?" = 9
hipson task activity --assignment A-0001 --json
hipson task ack --assignment A-0001 --note "Inspected exit 9, no result, and partial project changes"
hipson task run --task T-0001 --actor replacement --role builder --mode rw --scope src/parser \
  --result-file replacement.json -- bin/fake-worker build replacement.json
python3 -c "from src.parser.pairs import parse_pair; assert parse_pair('a:b') == ('a', 'b')"
hipson task ack --assignment A-0002 --note "Verified colon parsing"
hipson task status --task T-0001 --to review --actor lead
