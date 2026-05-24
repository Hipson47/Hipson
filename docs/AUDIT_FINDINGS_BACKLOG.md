# Audit Findings Backlog

## P0 — Must Fix Before Using Runtime

### [P0] Make `hipson chat` honest and fail-closed outside explicit fake mode
- Severity: P0
- Evidence: CLI smoke `HIPSON_HOME=<temp> uv run hipson chat -q "scan this repo and propose the next safe PR"` printed `Fake provider response`. `src/hipson/cli.py` constructs `FakeProvider.with_text(args.fake_response)` for `chat`; no CLI path exercises provider tool calls.
- Affected files: `src/hipson/cli.py`, `src/hipson/runtime.py`, `tests/test_runtime.py`
- Why it matters: The public command looks like a runtime MVP, but it cannot perform the requested scan/planning workflow. Users may trust a facade as if it were an agent loop.
- Recommended fix: Require an explicit fake/offline mode or clearly label current behavior as fake-only. Add a fail-closed no-provider message until a safe provider adapter exists.
- Tests to add: CLI smoke proving fake mode is explicit; no-provider runtime fails closed; CLI does not claim scan/tool execution unless a fake tool-call provider is injected through a test path.
- Acceptance criteria: `hipson chat -q ...` has truthful output and cannot be mistaken for a real provider/tool runtime. Fake tests remain network-free.
- Estimated PR size: Small
- Dependencies: None

### [P0] Remove hardcoded fake-provider approval context from runtime tool execution
- Severity: P0
- Evidence: `src/hipson/runtime.py:162` calls `approval_policy.evaluate_tool(..., fake_provider=True)` for every runtime tool call.
- Affected files: `src/hipson/runtime.py`, `src/hipson/approvals.py`, `tests/test_runtime.py`, `tests/test_approvals.py`
- Why it matters: If an external-risk tool is registered later, the runtime would treat it as fake-provider approved even when a real provider object is injected.
- Recommended fix: Carry provider/execution mode explicitly in runtime context. Default to non-fake for injected providers unless the runtime is intentionally constructed in fake mode.
- Tests to add: Runtime with a non-fake provider object and an external-risk test tool must require approval; fake-provider mode may allow only documented fake/dry-run cases.
- Acceptance criteria: Approval decisions depend on explicit runtime mode, not a hardcoded flag.
- Estimated PR size: Small
- Dependencies: Requires agreement on fake/offline mode naming.

## P1 — Must Fix Before Real Provider Usage

### [P1] Make approval path checks schema-aware for every path-bearing tool input
- Severity: P1
- Evidence: `src/hipson/approvals.py` checks only `path`, `project`, `packet`, and `source`. `memory.search` accepts `memory_dir`; a manual probe approved `memory_dir=str(Path.home())` with reason `Read allowed after sandbox checks`.
- Affected files: `src/hipson/approvals.py`, `src/hipson/sandbox.py`, `src/hipson/tools/memory.py`, `src/hipson/tools/skills.py`, tests
- Why it matters: Sensitive or broad paths can bypass policy when a tool uses a different field name.
- Recommended fix: Move path policy into tool specs or add explicit path-field metadata per tool. Validate `memory_dir`, `root`, `cwd`, `output`, and future path fields consistently.
- Tests to add: `memory_dir` home/sensitive/traversal refusal; skill `root` refusal; repo/tool path keys; future regression table for every registered tool schema path field.
- Acceptance criteria: Any path-like input on a registered tool is checked before execution.
- Estimated PR size: Medium
- Dependencies: Tool registry contract update.

### [P1] Bound and summarize tool outputs before SQLite persistence
- Severity: P1
- Evidence: `src/hipson/runtime.py` persists `result.output` directly. `repo.scan` returns `markdown` in `src/hipson/tools/repo.py`; manual probe found persisted keys `artifact`, `changed_files`, `commands`, and `markdown`.
- Affected files: `src/hipson/runtime.py`, `src/hipson/session.py`, `src/hipson/tools/repo.py`, tests
- Why it matters: The spec says no full repo dumps in SQLite. Large scan outputs can persist sensitive or excessive repository context.
- Recommended fix: Add per-tool persistence policy: store summaries, counts, artifact paths, and bounded snippets only. Keep full artifacts in generated files when appropriate.
- Tests to add: Large scan output is bounded; secret-like strings are redacted before persistence; persisted tool output excludes full markdown when too large.
- Acceptance criteria: SQLite stores redacted, bounded tool-call data only.
- Estimated PR size: Medium
- Dependencies: Tool contract hardening.

### [P1] Harden sidecar provider URL and error redaction
- Severity: P1
- Evidence: `src/hipson/agents.py` allows `http` and `https` provider base URLs, and raises raw HTTP response bodies in `OpenRouter HTTP ...` errors.
- Affected files: `src/hipson/agents.py`, `tests/test_hipson_helpers.py`
- Why it matters: Sidecar/provider errors can leak provider response bodies or secrets, and HTTP endpoints weaken transport safety.
- Recommended fix: Use HTTPS-only defaults, require explicit opt-in for local HTTP if needed, and redact/truncate provider error bodies before display/persistence.
- Tests to add: HTTP URL rejected by default; local/test exception if deliberately supported; HTTP error body with secret-like text is redacted.
- Acceptance criteria: No raw provider error body is printed; unsafe transport is blocked by default.
- Estimated PR size: Small
- Dependencies: Existing sidecar tests may need contract updates.

### [P1] Enforce tool output contracts, not only JSON serializability
- Severity: P1
- Evidence: `ToolRegistry.run` only checks JSON serializability of `ToolResult.output`; it does not validate the declared `output_contract` shape.
- Affected files: `src/hipson/tools/registry.py`, tool wrappers, `tests/test_tools.py`
- Why it matters: Runtime prompt/persistence layers depend on stable outputs. A tool can drift while tests still pass.
- Recommended fix: Add a small stdlib contract validator for required output keys, primitive types, bounded list/string fields, and unexpected large payloads.
- Tests to add: Tool returning missing key, wrong type, unbounded field, or non-serializable nested value fails predictably.
- Acceptance criteria: Every exposed tool has an enforced stable output contract.
- Estimated PR size: Medium
- Dependencies: None

### [P1] Surface rejected tool calls in runtime answers
- Severity: P1
- Evidence: Runtime persists rejected tool calls, but tests such as the gateway path still return `Fake provider response`; user-facing answers may hide rejected/blocked tool calls.
- Affected files: `src/hipson/runtime.py`, `src/hipson/gateway/cli.py`, `tests/test_runtime.py`, `tests/test_gateway.py`
- Why it matters: Silent rejection makes the runtime misleading and weakens auditability for users.
- Recommended fix: Include a bounded, redacted tool rejection summary in the final runtime result when no later assistant answer explains it.
- Tests to add: Unknown tool, invalid input, blocked approval, and max-iteration cases are visible in `RuntimeResult.answer`.
- Acceptance criteria: Rejected tool calls are persisted and visible to the caller.
- Estimated PR size: Small
- Dependencies: Runtime result UX decision.

## P2 — Hardening Before Release

### [P2] Add read-only session and tool CLI commands
- Severity: P2
- Evidence: Spec lists `hipson session list` and `hipson tool list` as next commands, but CLI smoke shows both are invalid choices.
- Affected files: `src/hipson/cli.py`, `src/hipson/session.py`, `src/hipson/tools/registry.py`, tests
- Why it matters: Auditing runtime state requires first-class read-only inspection commands.
- Recommended fix: Add `session list/show/search` and `tool list` as read-only commands after persistence and registry contracts are hardened.
- Tests to add: CLI list/show/search use temp DB and do not touch user home unless explicitly configured.
- Acceptance criteria: Users can inspect sessions/tools without provider credentials or network.
- Estimated PR size: Medium
- Dependencies: Session schema and tool contract stability.

### [P2] Treat scheduler and MCP bridge as experimental until foundations are stable
- Severity: P2
- Evidence: Scheduler and MCP-style bridge exist even though the spec marked them later/future. MCP bridge is an internal adapter, not a real MCP protocol server.
- Affected files: `src/hipson/scheduler.py`, `src/hipson/gateway/mcp.py`, `src/hipson/cli.py`, tests, docs
- Why it matters: Premature later-stage features increase audit surface before core runtime safety is trustworthy.
- Recommended fix: Mark these commands/adapters experimental in CLI help/docs or defer their merge until runtime/provider/tool approval gaps are closed.
- Tests to add: Scheduler cannot run unsafe approved jobs without recorded approval metadata; MCP adapter exposes only safe tools.
- Acceptance criteria: Users cannot mistake experimental later-stage features for production-ready runtime capabilities.
- Estimated PR size: Small
- Dependencies: Product decision on keeping scheduler/MCP code.

### [P2] Add prompt-injection tests for tool summaries and skill excerpts
- Severity: P2
- Evidence: User request and skill view can be enclosed as untrusted data, but session/tool summaries are inserted as plain text into prompts.
- Affected files: `src/hipson/prompt.py`, `src/hipson/runtime.py`, `tests/test_prompt.py`
- Why it matters: Tool output and session summaries are untrusted data and can carry prompt injection.
- Recommended fix: Enclose all dynamic user/file/tool/skill/provider content in labeled untrusted data blocks, then add snapshot tests.
- Tests to add: Malicious tool summary cannot escape the untrusted block; skill excerpt cannot override runtime policy.
- Acceptance criteria: Prompt assembly consistently separates policy from data.
- Estimated PR size: Small
- Dependencies: None

### [P2] Add mutation or targeted fault-injection coverage for runtime-critical files
- Severity: P2
- Evidence: `uv run pytest` passes, but optional mutation check was skipped; `pyproject.toml` mutmut paths do not yet target new runtime modules.
- Affected files: test configuration, runtime-critical tests
- Why it matters: Current tests cover happy paths and some failures, but may not catch logic inversions in approvals, bounds, and registry validation.
- Recommended fix: Add focused mutation configuration for `runtime.py`, `approvals.py`, `sandbox.py`, `tools/registry.py`, and `prompt.py`.
- Tests to add: No new product tests necessarily; strengthen existing assertions to kill obvious mutants.
- Acceptance criteria: Focused mutation run has a documented survivor budget and no high-risk approval/path survivors.
- Estimated PR size: Medium
- Dependencies: Agreement to run mutation locally/CI.

## P3 — Cleanup / DevEx / Docs

### [P3] Update README/runtime docs only after behavior is made truthful
- Severity: P3
- Evidence: `README.md` does not mention `hipson chat`, scheduler, or runtime DB behavior; docs/spec still describe many modules as proposed/future.
- Affected files: `README.md`, `docs/PERSISTENT_AGENT_RUNTIME_SPEC.md`, `docs/PROJECT_DEVELOPMENT_PLAN.md`
- Why it matters: Documentation should not advertise fake-only or unsafe behavior as production runtime capability.
- Recommended fix: After P0/P1 fixes, document fake/offline runtime mode, session storage, command status, and non-goals.
- Tests to add: None; docs review only.
- Acceptance criteria: Current behavior, future work, and safety boundaries are not conflated.
- Estimated PR size: Small
- Dependencies: P0/P1 behavior decisions.

### [P3] Clarify scheduler time semantics
- Severity: P3
- Evidence: `run_after` is stored as a string and queried lexicographically; `schedule` is stored but unused for recurrence.
- Affected files: `src/hipson/scheduler.py`, `src/hipson/session.py`, tests, docs
- Why it matters: Ambiguous timestamps make scheduled jobs fragile.
- Recommended fix: Define UTC ISO format validation and keep recurrence out of scope until a real cron parser is chosen.
- Tests to add: Invalid timestamp rejection; due/future boundary tests with UTC strings.
- Acceptance criteria: Scheduler time comparison is deterministic and documented.
- Estimated PR size: Small
- Dependencies: None

## Finding Template

### [P?] Title
- Severity:
- Evidence:
- Affected files:
- Why it matters:
- Recommended fix:
- Tests to add:
- Acceptance criteria:
- Estimated PR size:
- Dependencies:
