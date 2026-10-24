# Hipson AI Quality Layer

Hipson should use AI as a quality multiplier, not as hidden automation. The core
loop remains local and auditable:

```text
route -> scan -> packet -> packet preflight -> AI quality pass -> verify -> quality report -> eval -> memory/handoff
```

The AI quality pass is optional and explicit. Hipson prepares bounded packet
context, chooses or accepts a model, shows a dry-run preview, and treats provider
output as advisory.

## Product Goal

Hipson should make AI-assisted work better than ordinary dev tooling by adding:

- model-aware second opinions;
- task-fit sidecar selection;
- bounded and redacted context;
- explicit free-model experimentation;
- verification and memory discipline around every AI output.
- finding-level adjudication for sidecar claims;
- local evals for hallucinated files, mismatched commands, and missing evidence.

This is different from a standard CLI because Hipson is not just running local
commands. It is shaping how humans and agents collaborate on code quality.

## User Control

Users can keep the default provider-free workflow:

```bash
hipson work --task "review current diff for test gaps"
```

Users can opt into the free OpenRouter lane:

```bash
hipson work --task "review current diff for test gaps" --free-ai
```

Users can choose a specific model:

```bash
hipson work --task "review release risk" --ai-model openrouter/free
hipson sidecar run --agent reviewer_cheap --packet runs/review-packet.md --model openrouter/free
```

After a sidecar run, users can keep adjudication local:

```bash
hipson quality report --work runs/work.json --verify runs/verify.json --sidecar runs/sidecar.md
hipson quality eval --packet runs/review-packet.md --sidecar runs/sidecar.md --verify runs/verify.json
```

## Free Model Lane

Free models are useful for low-stakes quality passes:

- test gap brainstorming;
- packet clarity critique;
- first-pass implementation review;
- documentation review;
- summary and handoff drafts.

They should not be used for:

- sensitive customer context;
- secrets or broad logs;
- final security approval;
- release signoff;
- live-provider readiness claims;
- decisions that override local tests or human review.

## Trust Contract

Every AI quality pass must preserve these rules:

- no provider call happens unless the user runs the sidecar command;
- the dry-run preview comes first;
- packets are bounded and redacted;
- free/model-selected output is advisory;
- local diff, tests, and human review remain authoritative;
- any durable memory or handoff must summarize verified outcomes, not raw model
  confidence.
- finding IDs, sidecar model metadata, and provider output excerpts stay
  separated from local verification evidence.

## Evidence Ledger and Audit Reads

Evidence records live in `evidence.jsonl`. Attribution is optional:

```bash
hipson evidence append --work runs/work.json --verification runs/verify.json \
  --actor codex --task-id T-0003 --json
hipson evidence show --work runs/work.json --task-id T-0003 --json
hipson audit show --work runs/work.json --task-id T-0003 --json
hipson evidence verify --ledger-dir runs --json
```

`actor` is a recorded, unauthenticated label, like the task ledger's actor.
`task_id` accepts `T-` followed by at least four ASCII digits; validation checks
the pattern only and does not query or create a task ledger entry. Filters match
the exact id. Records without attribution remain valid and retain their hashes.
The record shape gains these fields only when supplied.

Appends serialize redaction, reading the preceding hash, final hashing, and the
write under an exclusive `fcntl.flock` on `<ledger-dir>/.evidence.lock`. The
sidecar file stays in place to preserve its inode; the OS releases the lock when
the process exits. On platforms without `fcntl`, an atomic mkdir of
`.evidence.lock.d` serializes writers, with a 30-second timeout. This fallback
removes the directory on normal exit and exceptions. A killed fallback writer can
leave a stale directory: check that no writer is active before manually removing
it. The fallback fails closed rather than stealing a possibly active lock.
Writers must use the same locking mechanism and cooperate through Hipson.

`append_record` finalizes `previous_hash` and `record_hash` under the lock and
updates the caller's record to the exact persisted payload. A record built before
append has a provisional empty link. Existing ledger contents are never rewritten.
`evidence verify` checks each stored record's content hash and preceding link,
reports offending line numbers, and exits 1 on breaks or unreadable input.
Readers validate decoded record shapes before accessing fields. JSONL readers
split on the physical `\n` delimiter only; U+0085, U+2028, and U+2029 inside JSON
strings are content, not record boundaries.
It takes the append lock while reading a snapshot of an existing ledger (so its
directory must allow the lock sidecar), and treats a missing ledger as an empty,
valid chain without creating files. Hashes detect edits and forks; they do not
authenticate writers or prevent a malicious rewrite of the entire chain.
Locking does not provide crash recovery for an interrupted or partially written
record; verification reports such corruption.

Newly appended evidence is recursively redacted before hashing and persistence,
including verification/quality payloads and free text. Credential keys replace
their entire value with `[REDACTED]`, regardless of its type (including objects,
arrays, numbers, booleans, and null). The exact rule matches the **whole key**
after case folding and removing all underscores and hyphens, against these names:
`password`, `passwd`, `secret`, `token`, `api_key`/`apikey`, `access_key`,
`private_key`, `authorization`, `cookie`, `client_secret`, `credential`,
`credentials`, `access_token`, `refresh_token`, `access_key_id`,
`secret_access_key`, `aws_secret_access_key`, and `openrouter_api_key`.
The assignment names already recognized by the text redactor are included.
There is no substring matching: metadata fields such as `token_count`,
`requires_credentials`, `password_policy`, and `private_key_path` retain their
structure. Every string value outside credential-key values still passes through
the existing secret-pattern redactor.
Work plans written with `hipson work --work-output` use the same recursive
redaction. Release claims, including copied gates and `safe_to_claim` /
`unsafe_to_claim`, use it when built and persisted. Arbitrary sensitive prose or
unlisted credential names may evade redaction. Input artifacts and old ledger
records are left intact.

MCP `evidence.append` accepts optional `actor` and `task_id`; MCP `audit.show`
accepts an optional `task_id` filter. Both resolve omitted `ledger_root` to the
**work plan project's `runs/` directory**, even when the MCP server runs for a
different project. Explicit `ledger_root` overrides this default (relative paths
resolve from the process working directory, matching CLI `--ledger-dir`).

The read-only MCP resource `hipson://latest-audit` returns the latest audit for the
server project. It searches `audit.json` bundles and `evidence.jsonl` ledgers
under that project's `runs/`, including individual run directories, and chooses
the candidate with the newest file modification time. Ledger candidates produce
a bundle for their last matching work id. Candidates must identify the server
project in `work.repo.path`. Each candidate is validated independently; corrupt,
malformed, or unreadable candidates are skipped and reported in `candidate_errors`
as `{path, error}` entries, with an explanation in `unknowns`. A healthy candidate
is still returned, and failures do not end the MCP stdio session. This shape
validation does not replace `evidence verify` for checking hash chains.

With no readable matching artifacts, the resource returns a `hipson.audit_bundle`
that validates against `schemas/audit-bundle.schema.json`: `evidence_records` is
empty, all latest status/gate fields are `"no-evidence"` (including
`latest_eval_ok`), and all four required `latest_gates` entries are
`"no-evidence"`. Its schema-complete `work` has empty `work_id`, `task`, and
`created_at_utc` strings and identifies the server project in `work.repo.path`;
this is an empty-state sentinel, not a generated work run. `unknowns` explains
the absence, and `candidate_errors` remains present if candidates failed. An empty
read creates no files. Custom ledgers outside `runs/` remain accessible through
`audit.show` with `ledger_root`.

## Roadmap

See `docs/AI_DEV_FIRST_EXPANSION_PLAN.md` for the broader AI-dev-first product
roadmap that connects this quality layer to model profiles, subagent curation,
verification evidence, audit export, and workflow packs.

Near-term:

- keep `hipson work --free-ai` and `--ai-model` as explicit opt-ins;
- add curated free sidecars such as `reviewer_free` and `coder_review_free`;
- document when free models are appropriate;
- keep free models excluded from default routing.
- use `hipson provider doctor` to check provider and agent readiness without
  sending repository data.

Next:

- add model profile presets for quality, cost, speed, long-context, UI review,
  and security review;
- keep improving `hipson quality report` finding adjudication;
- expand `hipson quality eval` into fixture-backed golden packet evals.
