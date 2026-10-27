#!/usr/bin/env bash
# Fresh-ledger IDs are deterministic; these are the documented CLI/runner interfaces.
set -euo pipefail
hipson task open --project . --title "Fix pair parser" --objective "Split pairs and trim whitespace" \
  --acceptance "a:b and spaced input both produce a,b; separate review finding fixed" --lead lead
examples/agent-run.sh --task T-0001 --actor builder --role builder --mode rw --scope src/parser \
  --result-file build.json -- bin/fake-worker build build.json
examples/agent-run.sh --task T-0001 --actor reviewer --role reviewer --mode ro --scope src/parser \
  --result-file review.json -- bin/fake-worker review review.json
examples/agent-run.sh --task T-0001 --actor fixer --role builder --mode rw --scope src/parser \
  --result-file fix.json -- bin/fake-worker fix fix.json
hipson task finding update --finding F-0001 --status fixed --actor lead \
  --resolved-by-assignment A-0003 --resolution-note "Verified whitespace trimming"
hipson task show --task T-0001
hipson task verify --task T-0001 -- python3 -c "from src.parser.pairs import parse_pair; assert parse_pair('a:b') == ('a', 'b')"
hipson task status --task T-0001 --to review --actor lead
hipson task ack --assignment A-0001
hipson task ack --assignment A-0002
hipson task ack --assignment A-0003
hipson task status --task T-0001 --to done --actor lead
