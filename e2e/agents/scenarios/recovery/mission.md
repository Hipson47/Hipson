You are the lead. A previous worker crashed; get the existing parser task moving
again. Inspect the old assignment and recover its stale ownership through the
ledger's own recovery mechanism, then launch a replacement builder on
`src/parser`. Have the replacement finish the basic colon parser fix and leave
the task in progress for later review. Do not mark crashed work as successful.

Use only Hipson to coordinate and change the task ledger. `hipson` is on PATH;
`hipson --help` documents it, and `docs/TASK_LEDGER.md` is available. Workers are
launched with `examples/agent-run.sh` and `bin/fake-worker`, a deterministic
stand-in whose arguments are `build|review|fix RESULT.json`. Give the same result
path to the launcher so it ingests the worker evidence. The fake worker does the
edits; you act as lead. Do not delete or edit ledger files, drop claims by hand,
commit, install tools, or use other agent services. Your final message is not
used as evidence.
