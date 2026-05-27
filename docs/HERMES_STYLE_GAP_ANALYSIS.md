# Hermes-Style Gap Analysis for Hipson

## 1. Executive Summary

Hipson already has the foundations of a local-first AI engineering runtime: a SQLite session store, deterministic fake provider, bounded prompt assembly, tool registry, approval/sandbox policy, proposal-only learning helpers, opt-in scheduler, and internal gateway/MCP-style adapters.

The most important Hermes-style gap is observability and approved learning. A runtime that can persist sessions but cannot inspect them from the CLI is hard to debug, audit, or use as a control plane. A runtime that can propose memories but cannot apply an explicitly approved memory has no closed learning loop.

Recommended 4-5h repair scope: add read-only session/tool CLI observability plus `learn propose` and explicit `learn apply-memory`, keeping real providers, autonomous shell execution, scheduler expansion, and MCP expansion out of scope.

Repair status: this document was created before implementation as the planning artifact. The selected repair package was then implemented as the runtime observability and approval-gated learning MVP.

Later update: the local runtime-router pass superseded the earlier fail-closed default for supported provider-free engineering tasks. `hipson chat -q "scan this repo and propose the next safe PR"` now executes `repo.scan` locally through the runtime safety boundary; unsupported requests still fail truthfully.

## 2. Current Verified Runtime State

- `uv run hipson doctor` passed and reported Hipson `1.1.0`, Python `3.12.3`, WSL repo cwd `/home/hipson47/code/Hipson`, configured Hipson home `/home/hipson47/.config/hipson`, and 50 valid skills.
- `uv run hipson skill validate` passed for all observed project and packaged skills.
- `uv run hipson scan . --include-diff` succeeded and reported a clean git status.
- `uv run hipson route --task "audit Hipson Hermes-style runtime gaps, observability, memory, and learning loop" --json` classified the task as `memory` and recommended memory commands, which is advisory but not sufficient for this implementation task.
- Original pre-router smoke: `uv run hipson chat -q "scan this repo and propose the next safe PR"` failed closed with the no-provider message. Later local-router smoke now executes `repo.scan` for this supported request.
- `HIPSON_HOME=<temp> uv run hipson chat --fake -q "offline runtime smoke"` succeeded with explicit fake/offline output.
- Pre-repair audit: `uv run hipson session list` and `uv run hipson tool list` failed because those top-level commands were not implemented.
- Post-repair smoke: `uv run hipson session list --session-db <temp>/runtime.sqlite`, `uv run hipson tool list`, and `uv run hipson learn --help` succeeded.
- `uv run hipson scheduler --help` shows opt-in `create`, `list`, and `tick`; it is not a daemon.
- `uv run hipson sidecar --help` shows advisory sidecar commands.
- `uv run hipson packet review ... -o runs/hermes-gap-review-packet.md` wrote a bounded review packet under `runs/`.

## 3. What Exists Today

- Persistent sessions: `src/hipson/session.py` defines SQLite-backed sessions, messages, tool calls, memories, skill runs, jobs, migrations, redaction, and optional FTS table creation.
- Runtime loop: `src/hipson/runtime.py` creates or loads sessions, persists user/assistant messages, assembles prompts, calls an injected provider, validates tool calls, evaluates approval policy, executes registry tools, and persists bounded tool results.
- Fake provider: `src/hipson/providers/fake.py` enables deterministic no-network runtime tests.
- Tool registry: `src/hipson/tools/registry.py` defines `ToolSpec`, `ToolResult`, `ToolContext`, output contracts, path policy metadata, and bounded output helpers.
- Tools: `src/hipson/tools/repo.py`, `memory.py`, `packets.py`, and `skills.py` wrap local Hipson functions.
- Learning: `src/hipson/learning.py` can propose memory and skill-reference candidates from a session without persisting them.
- Safety: `src/hipson/approvals.py`, `src/hipson/sandbox.py`, `src/hipson/redaction.py`, and tests cover many fail-closed and redaction paths.
- CLI: `src/hipson/cli.py` exposes `chat`, `skill`, `scheduler`, `memory`, `packet`, `sidecar`, `session`, `tool`, `learn`, and core commands.

## 4. What Is Missing For Hermes-Style AI Agents

- First-class runtime observability: addressed for the MVP with `hipson session list/show/search`.
- Tool introspection: addressed for the MVP with `hipson tool list/show`.
- Approved learning loop: addressed for memory proposals with `hipson learn propose` and explicit `hipson learn apply-memory`.
- Runtime session search: safe fallback message search exists; optional FTS tables still need a population/search contract.
- Durable approval records: scheduler approval remains a boolean flag; this pass should not expand it.
- Real-provider readiness: intentionally absent and still out of scope.

## 5. Self-Learning Gap

`src/hipson/learning.py` generates proposal objects only unless the user explicitly runs an apply command. This is correct for safety. The repair pass added CLI support to:

- ask Hipson to propose learning candidates from a completed session;
- review those candidates with stable IDs;
- explicitly apply one memory candidate into the JSONL memory store;
- preserve provenance from session/message/tool-call IDs.

The MVP keeps skill candidates as draft/reference only and does not write skills or auto-activate them.

## 6. Runtime Observability Gap

`src/hipson/session.py` stores sessions, messages, and tool calls, but users cannot inspect them except by writing custom Python or opening SQLite manually. This blocks practical debugging of fake-provider runtime sessions, rejected tool calls, provider failures, and tool output persistence.

The MVP should expose:

- `hipson session list`
- `hipson session show <session_id>`
- `hipson session search "query"`

All output must remain bounded and redacted. Commands should support `--session-db` for tests and local debugging.

## 7. Safety/Approval Gap

The core tool boundary is significantly stronger than the earlier audit baseline, but two safety gaps remain outside this repair scope:

- direct registry callers can bypass approval policy unless they use runtime/scheduler/MCP helpers;
- scheduler approval is a simple boolean, not a durable approval actor/reason record.

This repair package should not broaden execution. The optional `tool run` command is deferred to avoid creating a new execution surface.

## 8. Provider Readiness Gap

`hipson chat` correctly fails closed without `--fake`, and real primary chat provider support remains intentionally absent. Provider-backed sidecars remain separate advisory paths. This pass must not add a real provider adapter or make fake output look like real repository analysis.

## 9. Recommended 4-5h Repair Scope

Selected package: Runtime Observability + Approval-Gated Learning MVP.

Deliverables:

- Added `hipson session list/show/search` as read-only commands.
- Added `hipson tool list/show` as read-only registry inspection.
- Added `hipson learn propose --session-id <id> [--json]`.
- Added `hipson learn apply-memory --session-id <id> --proposal-id <id> --memory-dir <path>`.
- Added tests using temp SQLite DBs and temp memory dirs.
- Updated README and audit/spec docs to match current behavior.

## 10. Deferred Work

- Real provider adapter for `hipson chat`.
- Autonomous shell execution.
- `hipson tool run`.
- Durable approval records and stronger approval UX.
- Scheduler/gateway/MCP capability expansion.
- FTS-backed session search and retention policy.
- Automatic skill creation or activation.
- Full mutation survivor triage.
