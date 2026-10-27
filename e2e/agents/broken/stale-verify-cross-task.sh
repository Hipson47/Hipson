#!/usr/bin/env bash
set -euo pipefail
hipson task run --task T-0001 --actor builder --role builder --mode rw --scope src/parser --scope tests \
  --result-file candidate.json -- python3 bin/candidate.py
export PYTHONPATH=".:$PYTHONPATH"
hipson task ack --assignment A-0001 --note "Read worker result"
hipson task verify --task T-0001 -- python3 tests/acceptance.py
other=$(mktemp -d)
git -C "$other" init -q
printf 'value = 1\n' > "$other/source.py"
git -C "$other" add .
git -C "$other" -c user.name=Fixture -c user.email=fixture@example.com commit -qm "Unrelated candidate"
hipson task open --project "$other" --title "Unrelated task" --objective "Cross-task probe"
hipson task status --task T-0002 --to in_progress --actor lead
hipson task verify --task T-0002 -- python3 -c pass
printf 'value = 2\n' > "$other/source.py"
hipson task status --task T-0002 --to review --actor lead && exit 1
hipson task status --task T-0001 --to review --actor lead
hipson task status --task T-0001 --to done --actor lead
