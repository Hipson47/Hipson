#!/usr/bin/env bash
set -euo pipefail
# Deliberately prohibited raw DB mutation; the fake driver must fail this mission.
python3 - <<'PY'
import os
import sqlite3
with sqlite3.connect(os.environ["HIPSON_LEDGER"]) as connection:
    connection.execute("DELETE FROM claims")
PY
examples/agent-run.sh --task T-0001 --actor replacement --role builder --mode rw --scope src/parser \
  --result-file replacement.json -- bin/fake-worker build replacement.json
