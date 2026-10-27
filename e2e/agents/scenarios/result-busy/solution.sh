#!/usr/bin/env bash
set -euo pipefail
hipson task run --task T-0001 --actor delayed --role builder --mode rw --scope src/parser \
  --result-file delivered.json -- python3 bin/delayed-worker.py &
runner=$!
for attempt in {1..500}; do [ -e .worker-ready ] && break; sleep 0.02; done
test -e .worker-ready
touch .lead-busy
hipson task event --task T-0002 --actor lead --type progress --summary "Independent lead work completed"
wait "$runner"
hipson task board --json
hipson task show --task T-0001 --json
python3 -c "from src.parser.pairs import parse_pair; assert parse_pair('a:b') == ('a', 'b')"
hipson task ack --assignment A-0001 --note "Read delivery and verified parser"
hipson task verify --task T-0001 -- python3 -c "from src.parser.pairs import parse_pair; assert parse_pair('a:b') == ('a', 'b')"
hipson task status --task T-0001 --to review --actor lead
hipson task status --task T-0001 --to done --actor lead
