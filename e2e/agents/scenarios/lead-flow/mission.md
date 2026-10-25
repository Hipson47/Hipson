You are the lead for this fixture repository. Get a parser fix completed by a
builder and checked by a separate reviewer. The parser should split `a:b` into
`('a', 'b')` and trim spaces, so ` a : b ` has the same result. The reviewer will
report one finding. Get that finding fixed by a follow-up builder, resolve it
with a link to that fix assignment, verify the result, then review and close the
task yourself. Release all worker claims before closing.

Use only Hipson to coordinate and change the task ledger. `hipson` is on PATH;
`hipson --help` documents it, and `docs/TASK_LEDGER.md` is available. Workers are
launched with `examples/agent-run.sh` and `bin/fake-worker`, a deterministic
stand-in that edits the parser and writes a valid task-result JSON. Its arguments
are `build|review|fix RESULT.json`; give the same result path to the launcher so
the worker evidence is ingested. Builders need write scope `src/parser`; the
reviewer reads it. The fake worker does the edits; you act as lead. Worker
process success alone does not verify task acceptance. Do not edit the ledger
file or bypass claim conflicts. Do not commit, install tools, or use other agent
services. Your final message is not used as evidence.
