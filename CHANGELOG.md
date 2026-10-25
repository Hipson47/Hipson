# Changelog

## Unreleased

- **Task ledger and recovery**
  - Add a transactional SQLite ledger and `hipson task` CLI for lead-recorded
    tasks, assignments, overlapping path claims, lifecycle transitions, and
    append-only events.
  - Expose ten MCP `task.*` tools with explicit approval for all six writes,
    CLI-compatible JSON, a server-configured store, non-creating reads, and
    recoverable, redacted errors.
  - Reject blank task text and invalid assignment scopes in MCP before opening
    or migrating the store, using the ledger's shared semantic validators.
  - Recover orphaned assignments through CLI/MCP `task reap`/`task.reap`, with
    read-only CLI dry runs, optional PID/boot/host ownership, UTC leases, and
    automatic recovery before overlapping write claims are checked. Preserve
    claims while either the runner PID or its worker process group survives.
  - Cover the published worker runner with CLI integration tests for process
    results, stdin, claim conflicts, signal cleanup, leftover descendants, and
    recovery after the runner is killed. Verify cleanup when the runner leads a
    process group within another session and when an unreaped zombie remains.
  - Migrate v1 stores to v2 transactionally, preserving history. Keep CLI/MCP
    reads non-migrating and read-only: plain SQLite `mode=ro` connections that
    cooperate with concurrent writers. Remove the separate OFD guard, fall back
    on sidecar permission errors, and use direct read-only WAL access or validated
    in-memory snapshots in non-writable directories, sharing a 10 s retry budget.
- **Lead and worker instructions**
  - Add worker mode to agent instructions and installer output: lead-assigned
    workers stay within their brief, skip orchestration and progress/handoff
    notes, and return verification evidence without committing, pushing, or
    writing the task ledger. Preserve the standalone workflow outside worker mode.
- **Evidence and audit**
  - Serialize evidence appends with interprocess locking; recursively redact
    credential-key values and strings before hashing. Add optional unauthenticated
    actor/task attribution, task filters, and `hipson evidence verify`, preserving
    existing records and hashes. Redact nested work plans and release claim copies.
  - Validate finalized, redacted evidence with the reader's rules before append,
    rejecting malformed records without changing existing ledger bytes.
  - Complete MCP `hipson://latest-audit`; align audit/evidence ledger defaults
    with the work plan's project. Validate candidate shapes, isolate corrupt
    artifacts with diagnostics, and return a schema-valid empty audit bundle.
  - Split JSONL on physical newlines, preserving Unicode separators within records.
- **Documentation**
  - Move historical planning and audit material to `docs/archive/` with an archive index.
  - Index the live docs, document the lead/worker ledger flow, and correct CLI,
    path, provider, and historical-status guidance.

## 1.1.0

- Add agent-readable `SKILLS.md` playbook and packaged asset copy.
- Add deterministic `hipson route --task "..."` workflow router.
- Update Codex workflow assets to point agents at the router and playbook.
- Include router coverage in the configured mutmut target set.

## 1.0.0

- Harden runtime asset loading so Hipson never trusts project CWD for its own
  runtime assets.
- Make scan failures explicit for invalid paths.
- Redact quoted env-style secrets across scan, packet, memory, and sidecar
  paths.
- Add CLI subprocess timeouts and wheel/installed-package smoke coverage.
- Keep the Codex workflow toolkit canonical under
  `src/hipson/assets/codex-workflow-kit/`.
- Add optional LLM routing behind `hipson sidecar route --llm`.
- Add visual experience and HyperFrames video sidecar agents and skills.
- Add `uv` and `ruff` development workflow and CI coverage for Python 3.11 and
  3.12.
