# Changelog

## Unreleased

- Harden evidence appends with interprocess locking, recursive credential-key and string redaction,
  optional unauthenticated actor/task attribution, task filters, and
  `hipson evidence verify`. Preserve existing ledger records and hashes. Complete
  MCP `hipson://latest-audit` and align audit/evidence ledger defaults with the
  work plan's project. Redact nested work plan fields and release claim copies.
- Read JSONL using physical newlines, preserving Unicode separators inside
  records. Validate evidence/audit shapes, isolate corrupt latest-audit candidates
  with explicit diagnostics, and return a schema-valid empty audit bundle.
- Recover orphaned task assignments through `hipson task reap` and gated MCP
  `task.reap`, with read-only dry runs, optional PID/boot/host ownership and UTC
  leases, and automatic recovery before overlapping write claims are checked.
  Migrate task ledger databases from v1 to v2 transactionally, preserving history.
- Keep orphan claims while either the runner PID or its worker process group
  survives. Make CLI/MCP ledger reads non-migrating and read-only, including
  sidecar-free snapshots of checkpointed WAL stores in read-only directories.
- Expose the central task ledger through nine MCP stdio tools, with explicit
  approval for writes, CLI-compatible JSON payloads, a server-configured store,
  non-creating reads, and recoverable, redacted tool errors.
- Add a transactional SQLite task ledger and `hipson task` CLI for lead-recorded
  assignments, overlapping path claims, lifecycle transitions, and append-only events.

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
