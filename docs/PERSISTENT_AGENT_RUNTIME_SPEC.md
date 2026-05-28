# Persistent Agent Runtime Spec

## 1. Purpose

This document is the implementation contract for evolving Hipson from a local-first CLI into a persistent AI engineering agent runtime.

Hipson's runtime direction is intentionally narrow:

- It is not a general personal assistant.
- It is not a cloud platform.
- It is not a replacement for CI or human review.
- It is a local-first, packet-first, safety-gated AI engineering runtime.

The runtime must preserve the current repository's strongest properties: explicit user commands, bounded packet context, secret redaction, sensitive-path guards, deterministic local behavior, and provider-free defaults.

## 2. Current Baseline

Current state, based on inspected files:

- Existing CLI entrypoint: `pyproject.toml` exposes `hipson = "hipson.cli:main"`, and `src/hipson/cli.py` defines commands for `doctor`, `scan`, `scan-many`, `route`, `packet`, `sidecar`, `memory`, `skill`, `install`, `init`, and `check-setup`.
- Repo scan module: `src/hipson/project.py` contains `build_scan`, `build_scan_record`, `changed_files`, `untracked_files`, `discover_commands`, and scan/packet CLI command handlers.
- Packet generation: `src/hipson/packets.py` defines `PacketSpec`, `compile_review_packet`, and `compile_executor_packet`; `src/hipson/project.py` wires those compilers to `hipson packet review` and `hipson packet exec`.
- JSONL memory: `src/hipson/memory.py` stores notes in `notes.jsonl` and sources in `sources.jsonl`, with `add_note`, `search_notes`, `load_notes`, and CLI handlers.
- Sidecar routing/provider code: `src/hipson/agents.py` contains deterministic sidecar routing, optional LLM routing, OpenRouter chat completion calls, packet reading, dry-run behavior, and sidecar report writing. `README.md` documents that provider-backed sidecars and explicit runtime provider chat are optional.
- Skills validation: `src/hipson/skills.py` finds `SKILL.md` files, parses frontmatter, validates required metadata, and ignores generated/cache-like trees such as `mutants`.
- Current test posture: runtime coverage is split across focused test files for router, session, providers, tools, approvals, prompt, runtime, skills, learning, scheduler, gateway/MCP-style adapters, and CLI behavior. `pyproject.toml` configures pytest, ruff, mypy, Bandit, and mutmut.
- Current hardening risks:
  - focused mutation survivor triage remains incomplete for runtime-critical modules;
  - live provider smoke is manual and intentionally excluded from unit tests;
  - write/external/exec/dangerous manual tool execution remains fail-closed until a richer approval UX exists.

## 3. Target Architecture

Proposed target architecture:

```text
User / CLI / future gateway
  |
  v
hipson chat
  |
  v
Runtime loop (`src/hipson/runtime.py`)
  |
  +-- Prompt assembler (`src/hipson/prompt.py`)
  +-- Provider abstraction (`src/hipson/providers/`, fake + explicit OpenAI-compatible adapter)
  +-- Tool registry (`src/hipson/tools/registry.py`)
  |     +-- tools wrapping `src/hipson/project.py`
  |     +-- tools wrapping `src/hipson/packets.py`
  |     +-- tools wrapping `src/hipson/memory.py`
  |     +-- tools wrapping `src/hipson/skills.py`
  |     +-- tools wrapping sidecar paths in `src/hipson/agents.py`
  +-- Session store (`src/hipson/session.py`, SQLite)
  +-- Approval/sandbox layer (`src/hipson/approvals.py`, `src/hipson/sandbox.py`)
  |
  +-- Optional tick scheduler (`src/hipson/scheduler.py`)
  +-- Optional gateway adapters (`src/hipson/gateway/`)
  +-- Optional internal MCP-style bridge
  +-- Approval-gated learning loop (`src/hipson/learning.py`)
```

MVP target:

- `hipson chat` runs over a persistent local session.
- The runtime loop uses a fakeable provider abstraction and an explicit OpenAI-compatible provider adapter.
- The runtime exposes selected internal tools through a stable registry.
- Tool calls are validated against registry contracts and approval policy before execution.
- Messages, tool calls, redacted errors, and approval decisions are persisted in SQLite.
- Existing CLI modules remain the source of truth for scan, packet, memory, skills, and sidecar behavior.

Future optional layers:

- Future: scheduler for local due jobs and maintenance ticks.
- Future: gateway adapters beyond CLI, such as Telegram or Discord, only after the runtime boundary is stable.
- Future: MCP bridge after the internal registry and approvals are stable.
- Future: learning loop for approval-gated memory and skill candidates.

## 4. MVP Scope

The first runtime MVP must include:

- `hipson chat`
- `hipson chat -q "..."`
- SQLite session store
- fakeable provider interface
- prompt assembler
- tool registry
- read-only or low-risk first tools
- approval policy skeleton
- persisted messages/tool calls

The first runtime MVP must exclude:

- MCP
- Telegram/Discord
- daemon/background service
- autonomous shell execution
- browser control
- voice
- image/video generation
- automatic skill activation without approval

## 5. Proposed Modules

| Module | Purpose | Dependencies | First tests | Risk |
|---|---|---|---|---|
| `src/hipson/runtime.py` | Own the minimal `hipson chat` loop: session, prompt, provider, tool validation/execution, persistence, final answer. | `src/hipson/session.py`, `src/hipson/prompt.py`, `src/hipson/providers/`, `src/hipson/tools/registry.py`, `src/hipson/approvals.py`. | no-tool answer with fake provider; one read-only tool call; invalid tool rejected; max iteration stop; persisted transcript. | High |
| `src/hipson/session.py` | Provide SQLite storage for sessions, messages, tool calls, memories/proposals, skill runs, and jobs. | Python stdlib `sqlite3`; current JSONL memory conventions in `src/hipson/memory.py`. | create/open DB; idempotent migration; insert/list session; insert messages/tool calls; redaction-before-persistence fixture. | Medium |
| `src/hipson/prompt.py` | Assemble bounded runtime prompts from policy, current request, session summary, selected memory, selected skills, tool specs, and repo facts. | `src/hipson/router.py`, `src/hipson/memory.py`, `src/hipson/skills.py`, tool registry metadata. | snapshot tests; budget truncation; untrusted file text enclosed as data; selected skill excerpt included only when requested. | High |
| `src/hipson/providers/base.py` | Define provider request/response/error protocol for primary chat runtime. | stdlib dataclasses/typing; redaction helpers from `src/hipson/redaction.py`. | request/response serialization; error redaction; tool-call response parsing boundary. | High |
| `src/hipson/providers/fake.py` | Deterministic provider for unit and CLI smoke tests without network or credentials. | `src/hipson/providers/base.py`. | deterministic assistant text; one valid tool call; configured provider failure. | Low |
| `src/hipson/tools/registry.py` | Define `ToolSpec`, `ToolResult`, `ToolContext`, registry, validation, and dispatch. | existing modules wrapped by tools; approval metadata. | register/list; duplicate rejection; JSON contract validation; dispatch fake tool; unknown tool rejected. | High |
| `src/hipson/tools/repo.py` | Wrap repo scanning and changed file discovery. | `src/hipson/project.py`. | `repo.scan`; `repo.changed_files`; sensitive paths summarized; missing path failure. | Medium |
| `src/hipson/tools/packets.py` | Wrap review/executor packet creation. | `src/hipson/project.py`, `src/hipson/packets.py`. | review packet generated under allowed path; exec packet requires explicit allowed edit scope; output redacted. | Medium |
| `src/hipson/tools/memory.py` | Wrap memory search/add. | `src/hipson/memory.py`, later `src/hipson/session.py`. | bounded search; add redacts fields; sensitive source refused; agent-proposed add requires approval. | Medium |
| `src/hipson/tools/skills.py` | Expose skill metadata and bounded skill text as reference data. | `src/hipson/skills.py`, `skills/`, packaged workflow assets. | list metadata; view bounded text; missing skill; generated dirs ignored. | Medium |
| `src/hipson/tools/sidecar.py` | Wrap deterministic sidecar route and approval-gated sidecar run. | `src/hipson/agents.py`, `config/agents.json`. | deterministic route; LLM dry-run; run requires external approval; provider error redacted. | High |
| `src/hipson/approvals.py` | Centralize risk policy and approval decisions. | registry risk metadata; session persistence. | read auto; generated write auto; external requires approval; exec requires approval; dangerous blocked. | High |
| `src/hipson/sandbox.py` | Enforce path boundaries, sensitive-path refusal, generated write paths, and shell allowlists. | `src/hipson/redaction.py`, path helpers in `src/hipson/project.py`. | `.env`/`.ssh` refused; traversal rejected; generated paths allowed; cwd boundary checks. | High |
| `src/hipson/learning.py` | Future: propose memory and skill candidates from sessions, with approval before persistence. | session store, memory, skills, provider/fake provider. | proposal generated but not written; redaction; approval writes memory. | Medium/High |
| `src/hipson/scheduler.py` | Future: opt-in local due-job runner, not a daemon. | session jobs table, runtime/tool registry. | create/list due job; tick safe read job; persisted failure. | Medium |
| `src/hipson/gateway/` | Future: adapter boundary over runtime, beginning with CLI gateway and leaving Telegram/Discord for later. | runtime API, approvals. | CLI gateway cannot bypass approvals; fake provider path. | Medium |

## 6. Session Store Contract

Use Python stdlib `sqlite3`. The default DB location is an open question; tests must support a temp DB override.

Minimal schema:

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  cwd TEXT NOT NULL,
  repo_root TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_calls (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
  tool_name TEXT NOT NULL,
  input_json TEXT NOT NULL,
  output_json TEXT NOT NULL DEFAULT '{}',
  risk_level TEXT NOT NULL,
  approval_status TEXT NOT NULL DEFAULT 'not_required',
  status TEXT NOT NULL DEFAULT 'pending',
  error TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
  scope TEXT NOT NULL,
  repo TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,
  summary TEXT NOT NULL,
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  tags_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL DEFAULT 1.0,
  approval_status TEXT NOT NULL DEFAULT 'approved',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_runs (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  skill_name TEXT NOT NULL,
  source_path TEXT NOT NULL,
  input_summary TEXT NOT NULL DEFAULT '',
  output_summary TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'completed',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  schedule TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  run_after TEXT,
  last_run_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

Indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_messages_session_created
  ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session_started
  ON tool_calls(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_memories_repo_scope
  ON memories(repo, scope);
CREATE INDEX IF NOT EXISTS idx_jobs_status_run_after
  ON jobs(status, run_after);
```

Optional search:

- Proposed: try FTS5 for `messages` and `memories`.
- Fallback: if FTS5 is unavailable, use bounded `LIKE`/token search.

Minimal optional FTS:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
USING fts5(session_id UNINDEXED, content);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
USING fts5(repo UNINDEXED, scope UNINDEXED, summary);
```

Migration/versioning strategy:

- Store applied migrations in `schema_migrations`.
- Run migrations idempotently on DB open.
- Tests must prove opening an existing DB twice is safe.
- No dependency on external migration tools for MVP.

Persistence rules:

- Redact before persistence.
- Do not store full repo dumps in SQLite.
- Store paths, summaries, bounded snippets, artifact paths, and JSON metadata only.
- Provider errors and tool outputs must be redacted before display and before insertion.
- Approval decisions must be persisted as tool metadata and first-class approval records.

## 7. Provider Protocol

The primary chat runtime uses a narrow provider abstraction separate from sidecar/OpenRouter code in `src/hipson/agents.py`.

Current stdlib dataclasses:

```python
@dataclass(frozen=True)
class ProviderToolCall:
    id: str
    name: str
    input: dict[str, object]

@dataclass(frozen=True)
class ProviderRequest:
    model: str
    messages: list[dict[str, str]]
    tools: list[dict[str, object]]
    temperature: float = 0.2
    max_tokens: int = 1200

@dataclass(frozen=True)
class ProviderResponse:
    text: str
    tool_calls: list[ProviderToolCall]
    raw_metadata: dict[str, object]

@dataclass(frozen=True)
class ProviderError(Exception):
    message: str
    redacted_detail: str = ""
```

Provider rules:

- Assistant text output is optional when tool calls are present, but the final runtime answer must contain user-facing text.
- Optional tool call output is untrusted provider data and must be validated against the tool registry before execution.
- Provider errors must be redacted before persistence/display.
- Unit tests must not require network access or provider credentials.
- The fake provider must support deterministic text, deterministic tool call emission, and deterministic failure.
- The OpenAI-compatible provider adapter must be explicit, dependency-free, HTTPS-by-default for remote URLs, local-HTTP opt-in only, and covered by stub transport tests.
- The primary chat provider path is separate from sidecar/OpenRouter code. An adapter may reuse safe patterns from `src/hipson/agents.py`, but `hipson chat` must not depend directly on sidecar packet-run behavior.

## 8. Tool Registry Contract

Use stdlib-only contracts:

```python
@dataclass(frozen=True)
class ToolContext:
    cwd: Path
    repo_root: Path | None
    session_id: str
    dry_run: bool = False

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, object]
    output_contract: dict[str, object]
    risk_level: str
    approval_required: bool
    handler: Callable[[dict[str, object], ToolContext], "ToolResult"]

@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: dict[str, object]
    summary: str
    error: str = ""
    artifacts: tuple[str, ...] = ()
    redacted: bool = True
```

Each tool must have:

- stable name
- when-to-use description
- JSON-serializable input schema
- JSON-serializable output contract
- risk level
- approval requirement
- handler
- tests

Every exposed tool must have a stable JSON-serializable input and output contract before it can be used by the runtime.

Initial tool contracts:

| Tool | Input | Output | Risk | Approval | Wraps | MVP status |
|---|---|---|---|---|---|---|
| `repo.scan` | `{ "path": str, "include_diff": bool, "diff_lines": int }` | `{ "markdown": str, "changed_files": list[str], "commands": list[str], "artifact": str | null }` | read | auto if path passes sandbox | `src/hipson/project.py: build_scan`, `discover_commands`, `changed_files` | MVP |
| `repo.changed_files` | `{ "path": str }` | `{ "changed_files": list[str], "untracked_files": list[str] }` | read | auto if path passes sandbox | `src/hipson/project.py: changed_files`, `untracked_files`, `git_root` | MVP |
| `packet.review.create` | `{ "project": str, "title": str, "scope": str, "include_diff": bool, "output": str }` | `{ "path": str, "summary": str }` | write | auto only under allowed generated paths | `src/hipson/project.py: command_review_packet`, `src/hipson/packets.py: compile_review_packet` | MVP |
| `packet.exec.create` | `{ "project": str, "title": str, "goal": str, "allowed_edit": list[str], "acceptance": list[str], "output": str }` | `{ "path": str, "summary": str }` | write | approval or explicit user request; output only under allowed generated paths | `src/hipson/project.py: command_executor_packet`, `src/hipson/packets.py: compile_executor_packet` | Later |
| `memory.search` | `{ "query": str, "repo": str | null, "scope": str | null, "limit": int }` | `{ "results": list[dict] }` | read | auto | `src/hipson/memory.py: search_notes` | MVP |
| `memory.add` | `{ "scope": str, "repo": str, "kind": str, "summary": str, "tags": list[str], "sources": list[str], "confidence": float }` | `{ "id": str, "summary": str }` | write | approval when proposed by agent; user-initiated can auto write | `src/hipson/memory.py: add_note` | Later |
| `sidecar.route` | `{ "task": str, "risk": str, "context_chars": int, "sensitive": bool, "llm": bool, "dry_run": bool }` | `{ "candidates": list[dict], "llm_choice": dict | null }` | read or external | auto for deterministic; approval for provider-backed LLM | `src/hipson/agents.py: route_agents`, `route_with_llm` | Later |
| `sidecar.run` | `{ "agent": str, "packet": str, "output": str | null, "dry_run": bool, "max_packet_chars": int }` | `{ "report_path": str | null, "preview": dict | null }` | external | explicit approval unless dry-run | `src/hipson/agents.py: command_run`, `read_packet`, `write_report` | Later |
| `skill.list` | `{ "root": str | null, "query": str | null }` | `{ "skills": list[dict] }` | read | auto | `src/hipson/skills.py: find_skill_files`, `validate_skills` | MVP |
| `skill.view` | `{ "name": str | null, "path": str | null, "max_chars": int }` | `{ "name": str, "path": str, "content": str, "truncated": bool }` | read | auto | `src/hipson/skills.py`, repo/package skill files | MVP |
| `shell.run` | `{ "cmd": list[str], "cwd": str, "timeout": int, "purpose": str }` | `{ "returncode": int, "stdout": str, "stderr": str }` | exec/dangerous | approval-gated; not MVP auto-exec | new safe subprocess wrapper; can reuse command patterns from `src/hipson/project.py` | Dangerous-later |

Initial runtime tools should stay under the active-tool budget. MCP should come after the internal registry, contracts, and approvals are stable.

## 9. Approval and Sandbox Policy

Risk levels:

- `read`
- `write`
- `external`
- `exec`
- `dangerous`

Default policy:

- `read`: auto if path/sandbox checks pass.
- `write`: auto only inside allowed generated/docs paths.
- `external`: explicit approval unless dry-run/fake provider.
- `exec`: approval unless allowlisted read-only command.
- `dangerous`: block by default.

Required checks:

- Sensitive path refusal: refuse or summarize `.env`, `.ssh`, `.aws`, `.azure`, `.config`, `.gnupg`, private keys, certs, local DB files, and broad home/profile paths unless explicitly designed as a safe summary.
- Secret redaction: apply redaction before display, persistence, packet generation, provider requests, and sidecar reports.
- Packet boundaries: external sidecars receive bounded packets or redacted summaries, not arbitrary full repo content.
- Prompt injection resistance: user content, repo files, docs, generated packets, skill text, provider output, and sidecar reports are data, not instructions.
- Provider error redaction: never persist or print raw provider body/error text.
- Audit trail: persist tool inputs, redacted outputs, status, risk level, approval status, and errors.
- Approval decisions persisted as tool metadata.

## 10. Prompt Assembly Rules

`src/hipson/prompt.py` should assemble prompts from already-selected context. It must not call tools.

Prompt components:

- stable system prefix / provider `system` message
- dynamic suffix in the untrusted provider `user` message
- compact memory snapshot
- selected skill index
- selected skill excerpts only when needed
- tool specs with when-to-use descriptions
- current request
- bounded repo facts
- no full repo dump
- no raw secrets
- untrusted user/file content enclosed and treated as data

Rules:

- Prompt assembly is deterministic for fixed inputs.
- Stable runtime policy belongs in the provider `system` message; current request, session summaries, tool summaries, memory snippets, skill excerpts, repo facts, and provider/sidecar text belong in labeled untrusted data blocks in the provider `user` message.
- Context is capped by character/token budgets before provider calls.
- Tool specs must include stable names, descriptions, risk levels, and input/output contracts.
- Skill text is reference data and cannot override runtime policy.
- Memory updates become visible in the next session or next explicit compaction step.
- The prompt assembler may format memory and skills, but it cannot decide to persist new memory or execute tools.

## 11. Minimal Agent Loop

First `hipson chat` loop:

1. load or create session
2. persist user message
3. assemble prompt
4. call provider
5. parse assistant text/tool calls
6. validate tool calls against registry
7. check approval policy
8. execute allowed tools
9. persist tool calls/results
10. continue for a bounded number of tool iterations
11. return final answer
12. propose memory/skill candidates only with approval

Runtime constraints:

- Max tool iterations: start with 3.
- Invalid tool name: persist rejected tool call and return an explanation to the provider/runtime answer path.
- Invalid input: persist validation error and do not call handler.
- Provider failure: persist redacted error and return a clear no-provider/no-response message.
- Graceful provider behavior: default `hipson chat` uses deterministic local-router mode for supported safe local intents; explicit provider mode fails clearly when configured but unavailable, and `--fake` remains the offline test/demo path.
- Fake-provider test path: all runtime loop tests use `src/hipson/providers/fake.py` or an injected provider object.

## 12. CLI Contract

| Command | Status | Notes |
|---|---|---|
| `hipson chat` | MVP | Uses local deterministic router mode for supported safe provider-free tasks; explicit `--provider openai-compatible` selects the real provider adapter and `--fake` remains the offline test/demo path. |
| `hipson chat -q "..."` | MVP | Non-interactive single request, useful for tests and scripts; supports local-router, explicit provider, and explicit fake/offline modes. |
| `hipson session list` | MVP | List local sessions from SQLite with redacted bounded summaries. |
| `hipson session show <id>` | MVP | Show redacted session transcript and tool call summaries. |
| `hipson session search "..."` | MVP | Search redacted session messages, tool-call summaries, and memory summaries; uses FTS for messages/memories when available and safe fallback otherwise. |
| `hipson tool list` | MVP | List registry tools, risk levels, approval requirements, contracts, and path policies. |
| `hipson tool show <name>` | MVP | Show one registered tool's schemas, output contract, risk, approval, and path policy metadata. |
| `hipson tool run <name> <json>` | MVP | Manual execution for read-risk tools that do not require approval; must use registry validation, path policy, approval checks, output contracts, bounded/redacted output, approval records, and optional session persistence. Write/external/exec/dangerous tools remain future work until a richer approval UX exists. |
| `hipson skill list` | Next | Expose skill metadata, likely via existing `skill` command group. |
| `hipson skill view <name>` | Next | View bounded skill reference text. |
| `hipson learn propose --session-id <id>` | MVP | Print approval-gated trajectory memory and draft/reference-only skill proposals without durable writes. |
| `hipson learn apply-memory --session-id <id> --proposal-id <id> --memory-dir <path>` | MVP | Explicitly persist one selected memory proposal with redacted summary and provenance. |

## 13. Implementation Sequence

### 1. `fix(router): token-aware keyword matching`

- Objective: make deterministic routing safe enough to use as a cheap runtime planning signal.
- Files likely touched: `src/hipson/router.py`, router/CLI tests.
- Tests: `build runtime` has no UI risk; `build persistent agent runtime` routes to exec; `run build and tests` routes to verify; `premium ui review` remains UI; `security auth audit` remains security.
- Acceptance criteria: token-aware matching, phrase matching for multi-word rules, stable existing route behavior where tests define it.
- Risk: Medium.
- Rollback note: revert router matcher and new fixtures only; no schema/data changes.

### 2. `feat(session): add sqlite session store`

- Objective: add dependency-free SQLite session persistence.
- Files likely touched: `src/hipson/session.py`, CLI wiring if needed, tests.
- Tests: schema creation, idempotent migration, CRUD for sessions/messages/tool calls, redaction-before-persistence fixture.
- Acceptance criteria: temp DB tests pass without touching user config; no existing CLI behavior changes.
- Risk: Medium.
- Rollback note: remove new module/CLI surface; no product code should depend on it yet.

### 3. `feat(providers): add fakeable chat provider interface`

- Objective: create a primary chat provider protocol that can be tested without network calls.
- Files likely touched: `src/hipson/providers/__init__.py`, `src/hipson/providers/base.py`, `src/hipson/providers/fake.py`, optional adapter using patterns from `src/hipson/agents.py`, tests.
- Tests: deterministic fake text, fake valid tool call, fake provider failure, provider error redaction.
- Acceptance criteria: runtime tests can inject provider; sidecar provider path remains separate.
- Risk: Medium/High.
- Rollback note: remove provider package before runtime depends on it.

### 4. `feat(tools): add tool registry and initial wrappers`

- Objective: add `ToolSpec`, `ToolResult`, `ToolContext`, registry validation, and first wrappers.
- Files likely touched: `src/hipson/tools/registry.py`, `src/hipson/tools/repo.py`, `src/hipson/tools/packets.py`, `src/hipson/tools/memory.py`, tests.
- Tests: register/list/dispatch; duplicate rejection; JSON contract validation; `repo.scan`; `memory.search`; `packet.review.create`.
- Acceptance criteria: wrappers preserve current CLI contracts and return structured redacted output.
- Risk: High.
- Rollback note: remove tool package; existing CLI modules remain untouched.

### 5. `feat(approvals): add risk policy skeleton`

- Objective: centralize risk levels and default approval decisions.
- Files likely touched: `src/hipson/approvals.py`, `src/hipson/sandbox.py`, registry tests.
- Tests: read auto; generated write auto; external approval; exec approval; dangerous block; sensitive path refusal.
- Acceptance criteria: registry/runtime can ask one policy object before tool execution.
- Risk: High.
- Rollback note: remove approval gate only if no runtime command is released.

### 6. `feat(prompt): add prompt assembler`

- Objective: build deterministic bounded prompts from selected context.
- Files likely touched: `src/hipson/prompt.py`, tests.
- Tests: prompt snapshots, budget caps, skill index, memory snapshot, untrusted content delimiters.
- Acceptance criteria: no tool calls inside assembler; no raw secrets; no full repo dump.
- Risk: High.
- Rollback note: remove prompt module; fake provider tests can still operate directly.

### 7. `feat(runtime): add minimal hipson chat`

- Objective: add MVP agent loop and CLI command.
- Files likely touched: `src/hipson/runtime.py`, `src/hipson/cli.py`, `src/hipson/session.py`, `src/hipson/prompt.py`, provider package, tools package, tests.
- Tests: `hipson chat -q` with fake provider; one read-only tool call; invalid tool rejection; persisted transcript; max iteration stop.
- Acceptance criteria: no network in tests; no MCP/gateway/scheduler dependency; provider outputs treated as untrusted.
- Risk: High.
- Rollback note: remove chat command and runtime module; session/tools can remain if stable.

### 8. `feat(skills): add skill list/view/use runtime commands`

- Objective: expose skills as bounded reference material, not automatic instruction override.
- Files likely touched: `src/hipson/tools/skills.py`, `src/hipson/skills.py`, `src/hipson/cli.py`, tests.
- Tests: list metadata, view bounded text, missing skill, ignored generated dirs, packaged skill access.
- Acceptance criteria: skill text is enclosed as data and subject to prompt assembly caps.
- Risk: Medium.
- Rollback note: remove new CLI subcommands/tool wrappers; validation remains.

### 9. `feat(learning): propose memory and skill candidates`

- Objective: propose durable memory and skill references from sessions with approval.
- Files likely touched: `src/hipson/learning.py`, `src/hipson/session.py`, `src/hipson/memory.py`, tests.
- Tests: proposal-only behavior, approval required, redaction, rejected proposal not persisted.
- Acceptance criteria: model-derived learning never writes durable memory automatically.
- Risk: Medium/High.
- Rollback note: disable learning command while preserving session transcripts.

### 10. `feat(scheduler): add cron tick jobs`

- Objective: add opt-in local scheduled jobs without a daemon.
- Files likely touched: `src/hipson/scheduler.py`, `src/hipson/session.py`, `src/hipson/cli.py`, tests.
- Tests: create/list job, due tick, failure persistence, no background process.
- Acceptance criteria: scheduler is local, explicit, and cannot bypass approvals.
- Risk: Medium.
- Rollback note: remove scheduler CLI; leave jobs table unused.

### 11. `feat(gateway): add gateway adapter interface`

- Objective: define adapter boundary over runtime after CLI is stable.
- Files likely touched: `src/hipson/gateway/`, `src/hipson/runtime.py`, tests.
- Tests: CLI gateway passes message to runtime; approvals cannot be bypassed.
- Acceptance criteria: future gateways reuse runtime/session/tool/approval paths.
- Risk: Medium.
- Rollback note: remove adapter package; CLI runtime remains.

### 12. `feat(mcp): add optional MCP bridge`

- Objective: expose stable internal tools through optional MCP only after internal contracts are mature.
- Files likely touched: future MCP bridge module, registry, docs, tests.
- Tests: list safe tools, call safe read tool, approval-gated tool rejected/pending, no secret leakage.
- Acceptance criteria: MCP is optional and cannot bypass risk policy.
- Risk: High.
- Rollback note: remove MCP bridge; internal registry remains.

## 14. Test Matrix

Tests must be written from acceptance criteria, not copied from implementation.

| Category | Required coverage |
|---|---|
| Router tests | token-aware rules, phrase matching, build/runtime/verify cases, UI/security risk fixtures, existing behavior stability. |
| Session tests | temp DB open, migrations, sessions/messages/tool calls/approval records CRUD, redaction-before-persistence, FTS-backed/fallback search contract. |
| Provider fake tests | deterministic text, one valid tool call, provider failure, error redaction, no network required. |
| Tool registry tests | register/list/dispatch, duplicate rejection, input validation, output contract, unknown tool rejection. |
| Approval policy tests | read auto, generated write auto, external approval, exec approval, dangerous blocked, persisted approval metadata. |
| Prompt assembly snapshot tests | stable output, bounded memory, selected skills, tool specs, untrusted content delimiters, no raw secrets. |
| Runtime loop tests with fake/stub providers | no-tool answer, one read-only tool call, invalid tool, provider-style tool calls, max iteration stop, persisted transcript and approval records. |
| CLI smoke tests | `hipson chat -q`, future session/tool/skill commands, existing CLI smoke stays green. |
| Redaction/security tests | provider errors, tool outputs, packet snippets, memory proposals, sensitive paths. |
| No-network unit test guarantee | runtime/provider tests must not need live credentials or network access. |

Shell execution must not be auto-enabled in tests unless isolated and explicitly allowlisted.

## 15. Security Threat Model

| Threat | Impact | Mitigation | Test/check |
|---|---|---|---|
| Prompt injection from repo files/docs | Model may try to override system/runtime policy. | Enclose file content as data; stable system prefix; selected bounded snippets only. | Prompt snapshot with malicious file text remains quoted/data-scoped. |
| Malicious skill content | Skill text could instruct unsafe behavior. | Treat skill text as reference data; no automatic skill activation without approval. | Skill view/use tests with override attempts. |
| Provider error leakage | Secret or payload fragments could be printed/stored. | Redact provider bodies/errors before display and persistence. | Fake provider error includes secret pattern; output/DB is redacted. |
| Secrets in git diff or env files | Secrets could be persisted or sent externally. | Existing redaction/sensitive-path checks; no full repo dumps; packet boundaries. | Diff/env secret fixtures through scan, prompt, session, sidecar paths. |
| Sensitive path access | Runtime may read home/config/key files. | Sandbox refuses sensitive paths and traversal. | `.env`, `.ssh`, `.config`, DB/key path refusal tests. |
| Tool poisoning | Provider requests fake/unknown or malformed tools. | Registry validates tool name/input/output before execution. | Unknown tool and malformed JSON tests. |
| Unsafe shell execution | Local destructive or exfiltration commands could run. | `shell.run` dangerous-later; approval-gated; allowlisted read-only commands only. | Shell tool cannot run without approval; dangerous command blocked. |
| Accidental persistence of secrets | Session DB could become a secret sink. | Redaction-before-persistence rule; bounded snippets only. | DB assertions do not contain known secret fixture values. |
| Unbounded context growth | Slow, costly, privacy-risk prompts. | Prompt budgets, summaries, no full repo dumps, capped memory. | Prompt budget tests and max snippet tests. |
| Sidecar data leakage | Full repo or sensitive packet sent to external provider. | Sidecar tools keep packet-first model; dry-run; external approval; `read_packet` guards. | Sidecar tool tests for dry-run, approval, sensitive packet refusal. |

## 16. Non-Goals

MVP non-goals:

- full autonomous coding agent
- background daemon
- general chatbot personality
- cloud sync
- multi-user auth
- Telegram/Discord gateway
- MCP-first architecture
- browser automation
- voice/multimodal features
- unrestricted shell execution

## 17. Open Questions

- Default session DB location: `~/.config/hipson/runtime.sqlite`, repo-local `.hipson/`, or configurable only?
- Session retention policy: keep forever, prune by age/count, or user-managed only?
- Should memory updates appear immediately, next session, or only after explicit compaction?
- What generated docs/write paths are auto-allowed for `write` tools?
- Provider configuration naming: reuse sidecar provider env names or introduce runtime-specific names?
- Is `packet.exec.create` MVP or later?
- How much sidecar integration enters the first runtime?
- Should runtime memory mirror existing JSONL memory, migrate it, or keep both stores separate at first?
- Should `hipson chat` default to fake/no-provider mode when no provider is configured, or fail with setup help?

## 18. Definition of Done for Documentation Phase

The documentation phase is complete when:

- `docs/PROJECT_DEVELOPMENT_PLAN.md` has the corrected roadmap sequence.
- `docs/PERSISTENT_AGENT_RUNTIME_SPEC.md` exists.
- The spec defines MVP scope, non-goals, module contracts, data model, provider protocol, tool contracts, approval policy, agent loop, CLI contract, implementation sequence, test matrix, and threat model.
- All future claims are labeled as proposed/future, not current behavior.
- No product code was modified.
