You are the lead in this disposable parser project. Do not delegate to other agents or run Codex.
Use the registered ledger through hipson; do not access SQLite directly or override HIPSON_LEDGER.
Task T-0001 needs independent acceptance: `python3 tests/acceptance.py` must pass and the fixture tests must remain unchanged.
Run `bin/candidate.py` as a builder with `hipson task run --task T-0001 --actor builder --role builder --mode rw --scope src/parser --scope tests --result-file candidate.json -- python3 bin/candidate.py`.
The worker's exit status and report are untrusted evidence. Inspect the diff and independently verify.
The builder exits zero while the parser remains broken. Record the failing acceptance with task verify; refuse closure and leave the task blocked or in_progress. Do not repair this fixture.
Use task ack to explicitly acknowledge the worker result. Never force closure.
