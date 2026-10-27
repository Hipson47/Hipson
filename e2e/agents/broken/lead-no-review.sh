#!/usr/bin/env bash
set -euo pipefail
hipson task open --project . --title "Fix parser" --objective "Skip review" --lead lead
examples/agent-run.sh --task T-0001 --actor builder --role builder --mode rw --scope src/parser \
  --result-file fix.json -- bin/fake-worker fix fix.json
hipson task verify --task T-0001 -- python3 -c "from src.parser.pairs import parse_pair; assert parse_pair('a:b') == ('a', 'b')"
hipson task status --task T-0001 --to review --actor lead
hipson task ack --assignment A-0001
hipson task status --task T-0001 --to done --actor lead
