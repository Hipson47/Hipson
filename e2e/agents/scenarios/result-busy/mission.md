You have parser task T-0001 and independent lead work T-0002. Launch `bin/delayed-worker.py`
with `hipson task run` in the background (builder, rw src/parser, result file delivered.json).
Wait until `.worker-ready` exists, then create `.lead-busy` to let the result arrive while you
record an event on T-0002. After the runner ends, use the board to discover pending delivery.
Inspect the result, independently verify colon parsing, explicitly acknowledge it, and close T-0001
through review. Leave T-0002 in progress. Do not force done or access the database directly.
