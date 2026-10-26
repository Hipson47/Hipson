# Hipson 1.2.0

Hipson 1.2.0 turns the task ledger into the center of the project: one lead agent, many workers,
one local ledger of who did what, with evidence. It runs on Linux, macOS, native Windows and WSL2.

Status: **beta**. Hipson coordinates cooperating agents. It is not a security boundary, a sandbox or
an enforced release gate; see "Limits" below.

## Highlights

- **Task ledger** (`hipson task …`, MCP `task.*`): tasks, assignments, path claims and an append-only
  event log in one transactional SQLite store. An overlapping live writer is refused before it starts
  (exit 75); a dead run's claims are reaped.
- **Ledger invariants** (from an external audit): `done` is refused while work is active, claims are
  held or blocking/major findings are open, unless an explicit `--force --reason` override, which is
  recorded with every blocker. A late blocking/major finding reopens a done task. A verified live
  owner keeps its claims when a lease expires.
- **Agent-facing workflow**: `hipson task brief` builds a worker brief from the ledger (objective,
  acceptance, open findings, failed approaches, result contract) with worker text inside an
  untrusted-data boundary; `hipson task context` gives a running worker its task; reviewer findings
  are ledger records with a lifecycle; worker results follow `schemas/task-result.schema.json`.
- **Worker wrapper** (`examples/agent-run.sh`): process-group ownership, signal forwarding, claim
  recording, structured result ingestion, and a project check that refuses to run a worker outside
  the task's repository (exit 76) unless explicitly overridden.
- **Cross-platform**: macOS (wrapper included) and native Windows (CLI, ledger, MCP, evidence;
  liveness probes never signal a process). CI runs on Ubuntu, macOS and Windows.
- **Agent e2e tests**: a real Codex agent gets natural-language missions and must reach them with
  Hipson alone; oracles check the resulting ledger. Opt-in and local
  ([docs/AGENT_E2E.md](AGENT_E2E.md)). The first live run found a real bug (claims reaped across PID
  namespaces), fixed in this release.
- **Test suite you can trust**: zero skips on Linux CI, a strict skip allowlist on macOS/Windows,
  full JSON-schema validation in tests and a mutation gate against a committed baseline.

## External audit (2026-10-26)

An independent review of main reported six findings. Each was reproduced before the fix and has a
regression test that fails on the old code.

| Finding | Fix |
|---|---|
| H-01 `done` allowed with active work and open blocking findings | refused with the blockers listed; audited override only |
| H-02 lease expiry released a verified live owner | live owners keep claims; leases apply to unverifiable owners |
| H-03 wrapper ran in a different repository than the task's | refused before the worker starts (exit 76) |
| H-04 actor and refs stored secret-shaped text unredacted | all ledger metadata redacted at write and in output |
| H-05 git allowlist treated `git branch -D` as read-only | argument-aware allowlist; helpers and pagers disabled at execution |
| H-06 config backups could overwrite each other | unique backup names, never overwritten |

A second review of the fixes found three more gaps (closing via late findings, inherited
`GIT_DIR`/`GIT_WORK_TREE`, implicit git helpers); all fixed in this release.

## Install

```bash
uv tool install git+https://github.com/Hipson47/Hipson@v1.2.0
```

Per-OS instructions and MCP registration for Claude Code: [docs/INSTALL.md](INSTALL.md).

## Limits

- Actor names are recorded labels, not authenticated identities; MCP `approved=true` is a
  declaration by the caller, not proof of human approval.
- Path claims coordinate cooperating agents; they are not filesystem permissions. Worker mode is an
  instruction, not a sandbox.
- The ledger and the evidence log are separate stores; a task ID in evidence is checked for syntax,
  not cross-referenced.
- Native Windows has no worker wrapper (use WSL2) and no boot identity: after a reboot, a reused PID
  can hold a claim until its lease expires or the lead finishes it.
- Agent e2e covers three scenarios; it is a smoke test of agent usability, not a reliability
  benchmark.
