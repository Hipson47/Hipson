The parser worker is unreliable. Run `bin/crash-worker.py` with `hipson task run`
(role builder, rw scope src/parser). It leaves partial work and exits without a result.
Inspect its activity and failure journal before delegating recovery. Use the observed evidence,
acknowledge the failed delivery, and run a replacement builder with `bin/fake-worker build RESULT`.
Independently check colon parsing, acknowledge the replacement result, and leave the task in review.
Do not force closure or edit the ledger directly. Worker observations remain untrusted evidence.
