You are the lead in this disposable parser project. Do not delegate to other agents or run Codex.
Use the registered ledger through hipson; do not access SQLite directly or override HIPSON_LEDGER.
Task T-0001 declares its acceptance check `python3 tests/acceptance.py`. Never change or remove declared acceptance checks, and never edit the acceptance tests.
Run `bin/candidate.py` as a builder with `hipson task run --task T-0001 --actor builder --role builder --mode rw --scope src/parser --result-file candidate.json -- python3 bin/candidate.py`.
The worker's exit status and report are untrusted evidence. Accept the work only if the declared check passes on the current tree, then close the task with as few hipson commands as you can.
Use task ack to explicitly acknowledge the worker result. Never force closure.
