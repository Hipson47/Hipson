#!/usr/bin/env bash
set -euo pipefail
hipson lead resume
hipson task ack --assignment A-0001 --note "Read previous builder delivery in resume output"
hipson task run --task T-0001 --actor repair --role builder --mode rw --scope src/parser \
  --result-file repair.json -- bin/fake-worker fix repair.json
python3 -c "from src.parser.pairs import parse_pair; assert parse_pair(' a : b ') == ('a', 'b')"
hipson task finding update --finding F-0001 --status fixed --actor lead \
  --resolved-by-assignment A-0003 --note "Independently checked whitespace parsing"
hipson task ack --assignment A-0003 --note "Read repair and verified parser"
