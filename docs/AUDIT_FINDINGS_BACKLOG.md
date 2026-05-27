# Audit Findings Backlog

## Status Update — Runtime Tool Boundary Hardening

The runtime tool boundary hardening pass addressed the audit findings for explicit per-tool path policies, exception-safe tool execution, bounded persisted/provider-visible tool outputs, enforced output contracts, and pre-approval input validation. These should remain covered by regression tests before any real provider adapter is added.

## Status Update — Provider/Prompt Boundary Hardening

The provider/sidecar prompt hardening pass addressed the audit findings for remote provider URL policy, redacted and bounded provider HTTP/error bodies, advisory sidecar provider output, and role-separated prompt assembly. The real-agent completion pass adds an explicit OpenAI-compatible primary runtime provider adapter while preserving provider-free defaults and network-free unit tests.

## Status Update — Focused Fault-Injection Hardening

The focused fault-injection passes added regression tests for approval fail-closed behavior, path-policy blocking before handler execution, registry primitive/composite type strictness and JSONDecodeError containment, provider URL/redaction helper behavior, runtime provider request/tool descriptor shape, capped rejected-tool summaries, untrusted-data delimiter escaping in prompts and sidecar reports, direct session-store bounding/redaction, scheduler dangerous-job refusal, MCP approval-required read-tool refusal, precise sandbox generated write roots, symlink escape rejection, and absence of `shell.run` in the default runtime registry. `pyproject.toml` now points mutmut at runtime-critical modules. Time-boxed mutmut runs did not complete, and partial results still need survivor triage.

## Status Update — Runtime Observability And Learning MVP

The Hermes-style repair pass added read-only `hipson session list/show/search`, read-only `hipson tool list/show`, deterministic learning proposal IDs, `hipson learn propose`, and explicit `hipson learn apply-memory`. Tests cover temp SQLite DB usage, redacted/bounded session output, tool metadata display, proposal-only behavior, explicit memory apply, provenance, and non-memory proposal refusal. Real-agent completion now adds provider adapter tests, durable approval records, and session search across messages/tool calls/memory summaries.

## Status Update — Local Provider-Free Production Readiness Repair

The local/provider-free production readiness repair added `hipson tool run` for read-risk tools that do not require approval, an explicit `hipson chat --fake --fake-tool-call ... --fake-tool-input ...` offline demo path, bounded max-tool-iteration visibility, and trajectory-based learning proposals. Tests cover safe read-only tool execution, unsafe/invalid/path-rejected tool runs, optional session persistence, fake chat tool-call execution, unsafe fake tool-call refusal, max-iteration audit visibility, and improved learning provenance. Real-agent completion resolves the real-provider adapter and durable approval-record gaps with stubbed tests. Write/external/exec/dangerous manual tool execution and full mutmut survivor triage remain deferred.

## Status Update — Hermes-Style Real Agent Completion

The real-agent completion pass added `src/hipson/providers/openai_compatible.py`, explicit `hipson chat --provider openai-compatible` configuration, redacted/bounded provider transport errors, strict provider tool-call argument parsing, durable `approval_records`, and session search across messages, tool-call summaries, and memory summaries. Unit tests remain network-free and credential-free. Live provider smoke is manual and full mutation survivor triage remains open.

## Status Update — Local Runtime Router

The local runtime-router pass added `src/hipson/local_router.py` and changed default `hipson chat` behavior for supported safe provider-free workflows. `hipson chat -q "scan this repo and propose the next safe PR"` now executes `repo.scan` locally through the existing runtime registry, approval, path-policy, output-contract, redaction, bounded-persistence, and session-store boundary. `hipson chat -q "show changed files"` executes `repo.changed_files`. Unsupported requests fail truthfully with supported local-router intents. Real-provider support was not expanded by this pass.

## P0 — Must Fix Before Using Runtime

### [P0] Make `hipson chat` honest and fail-closed outside explicit fake mode
- Severity: P0
- Status: Fixed by `fix(runtime): make chat mode and approvals fail closed`; superseded for supported provider-free engineering workflows by the local runtime router. Keep regression tests for explicit fake mode, unsupported local requests, and provider-specific fail-closed behavior.
- Evidence: CLI smoke `HIPSON_HOME=<temp> uv run hipson chat -q "scan this repo and propose the next safe PR"` printed `Fake provider response`. `src/hipson/cli.py` constructs `FakeProvider.with_text(args.fake_response)` for `chat`; no CLI path exercises provider tool calls.
- Affected files: `src/hipson/cli.py`, `src/hipson/runtime.py`, `tests/test_runtime.py`
- Why it matters: The public command looks like a runtime MVP, but it cannot perform the requested scan/planning workflow. Users may trust a facade as if it were an agent loop.
- Recommended fix: Completed in two stages: explicit fake/offline mode and provider fail-closed behavior first, then a deterministic local-router mode for supported safe workflows.
- Tests to add: CLI smoke proving fake mode is explicit; no-provider runtime fails closed; CLI does not claim scan/tool execution unless a fake tool-call provider is injected through a test path.
- Acceptance criteria: `hipson chat -q ...` has truthful output and cannot be mistaken for a real provider/tool runtime. Fake tests remain network-free.
- Estimated PR size: Small
- Dependencies: None

### [P0] Remove hardcoded fake-provider approval context from runtime tool execution
- Severity: P0
- Status: Fixed by `fix(runtime): make chat mode and approvals fail closed`; keep regression tests.
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
- Status: Fixed by runtime tool boundary hardening for current registered tools; future tools must declare path policies.
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
- Status: Fixed for runtime tool-call persistence and scheduler/MCP visible outputs; keep size/redaction regression tests.
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
- Status: Fixed by provider/sidecar prompt hardening; keep regression tests.
- Evidence: Prior audit found `src/hipson/agents.py` allowed `http` and `https` provider base URLs and raised raw HTTP response bodies in `OpenRouter HTTP ...` errors. The hardening pass centralizes provider URL validation, rejects arbitrary remote `http://` URLs, requires explicit local HTTP opt-in, and bounds/redacts `HTTPError`/`URLError` bodies before `SystemExit`.
- Affected files: `src/hipson/agents.py`, `tests/test_hipson_helpers.py`
- Why it matters: Sidecar/provider errors can leak provider response bodies or secrets, and HTTP endpoints weaken transport safety.
- Recommended fix: Use HTTPS-only defaults, require explicit opt-in for local HTTP if needed, and redact/truncate provider error bodies before display/persistence.
- Tests to add: HTTP URL rejected by default; local/test exception if deliberately supported; HTTP error body with secret-like text is redacted.
- Acceptance criteria: No raw provider error body is printed; unsafe transport is blocked by default.
- Estimated PR size: Small
- Dependencies: Existing sidecar tests may need contract updates.

### [P1] Enforce tool output contracts, not only JSON serializability
- Severity: P1
- Status: Fixed by lightweight registry output validation; expand contracts as tools grow.
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
- Status: Fixed for rejected, failed, and max-tool-iteration runtime tool calls; keep CLI/runtime regression tests.
- Evidence: Runtime persists rejected tool calls, but tests such as the gateway path still return `Fake provider response`; user-facing answers may hide rejected/blocked tool calls.
- Affected files: `src/hipson/runtime.py`, `src/hipson/gateway/cli.py`, `tests/test_runtime.py`, `tests/test_gateway.py`
- Why it matters: Silent rejection makes the runtime misleading and weakens auditability for users.
- Recommended fix: Include a bounded, redacted tool rejection summary in the final runtime result when no later assistant answer explains it.
- Tests to add: Unknown tool, invalid input, blocked approval, and max-iteration cases are visible in `RuntimeResult.answer`.
- Acceptance criteria: Rejected tool calls are persisted and visible to the caller.
- Estimated PR size: Small
- Dependencies: Runtime result UX decision.

### [P1] Triage focused mutation survivors in safety-critical boundaries
- Severity: P1
- Status: Partially fixed. New direct helper/fault-injection tests were added, but full survivor triage remains open.
- Evidence: `timeout 300s uv run mutmut run || true` used the focused configuration and did not complete the 2,219-mutant set. Last observed progress was 1,965/2,219 mutants with 1,643 killed, 130 timeouts, and 192 survivors. `uv run mutmut results || true` still listed survivors, timeouts, or unchecked mutants in `hipson.approvals`, `hipson.sandbox`, `hipson.tools.registry`, `hipson.agents`, `hipson.prompt`, `hipson.redaction`, `hipson.router`, and `hipson.runtime`.
- Autonomous loop update: selected follow-up checks killed `hipson.approvals.x__check_input_paths__mutmut_3`, `hipson.sandbox.x_check_skill_root_path__mutmut_3`, `hipson.tools.registry.x__handler_failure__mutmut_1`, and `hipson.runtime.x__rejection_summary__mutmut_11`. `hipson.redaction.x_sanitize_path__mutmut_1` remains classified as equivalent/low-risk under the current constant skipped-marker behavior.
- Affected files: `src/hipson/approvals.py`, `src/hipson/sandbox.py`, `src/hipson/tools/registry.py`, `src/hipson/agents.py`, `src/hipson/prompt.py`, `src/hipson/runtime.py`, tests
- Why it matters: Fault-injection tests improved the boundary, but mutation survivors in approval/path/registry/redaction/prompt/runtime code can still hide logic inversions before broad release readiness.
- Recommended fix: Run mutmut in smaller batches by module/function, inspect high-risk survivors first, and add requirement-level tests for real approval, path, output-contract, redaction, and untrusted-delimiter mutants.
- Tests to add: Targeted tests for specific high-risk survivors discovered in each module batch.
- Acceptance criteria: No known high-risk survivors remain in approval/path/redaction/registry/prompt/runtime safety logic, or each survivor is documented as equivalent/low-risk.
- Estimated PR size: Medium
- Dependencies: Focused mutmut batching or CI support.

### [P1] Complete live-provider manual smoke before external release
- Severity: P1
- Status: Open for release readiness; not required for network-free unit verification.
- Evidence: The OpenAI-compatible provider adapter is covered by stub transport tests, but no live provider smoke was run by design.
- Affected files: `src/hipson/providers/openai_compatible.py`, `src/hipson/cli.py`, provider setup docs
- Why it matters: Stub tests prove request/response/error handling but cannot catch provider-specific deployment/configuration issues.
- Recommended fix: With explicit user permission and a disposable provider key, run one minimal no-tool live smoke and one read-only tool-call smoke if the chosen provider supports tool calls.
- Tests to add: Do not add network-dependent unit tests; document manual smoke commands and expected redacted output.
- Acceptance criteria: Manual smoke passes without leaking credentials or bypassing approval/tool boundaries.
- Estimated PR size: Small
- Dependencies: User-provided provider credentials and explicit live-network approval.

## P2 — Hardening Before Release

### [P2] Add read-only session and tool CLI commands
- Severity: P2
- Status: Fixed by Runtime Observability + Approval-Gated Learning MVP and local production-readiness repair; keep CLI and temp-DB regression tests.
- Evidence: Prior CLI smoke showed `hipson session list` and `hipson tool list` as invalid choices. Current CLI smoke shows both command groups are available.
- Affected files: `src/hipson/cli.py`, `src/hipson/session.py`, `src/hipson/tools/registry.py`, tests
- Why it matters: Auditing runtime state requires first-class read-only inspection commands.
- Recommended fix: Complete for inspection and read-only safe execution. Keep `tool run` limited to read-risk tools that do not require approval until durable approval UX exists.
- Tests to add: Future regressions should keep CLI list/show/search on temp DBs and avoid touching user home unless explicitly configured.
- Acceptance criteria: Met for session/tool observability; users can inspect sessions/tools without provider credentials or network.
- Estimated PR size: Medium
- Dependencies: Session schema and tool contract stability.

### [P2] Add approval-gated learning CLI from runtime sessions
- Severity: P2
- Status: Fixed for memory proposals by Runtime Observability + Approval-Gated Learning MVP; skill proposals remain reference-only drafts.
- Evidence: Prior state had `src/hipson/learning.py` proposal helpers but no CLI review/apply workflow. Current CLI exposes `hipson learn propose` and explicit `hipson learn apply-memory`.
- Affected files: `src/hipson/cli.py`, `src/hipson/learning.py`, `src/hipson/memory.py`, `tests/test_learning.py`
- Why it matters: Hipson could not close the local learning loop from persisted sessions without custom Python.
- Recommended fix: Complete for memory proposals; never auto-persist model-derived learning.
- Tests to add: Future regressions should preserve proposal-only behavior, redaction, deterministic proposal IDs, explicit memory apply with provenance, and non-memory proposal refusal.
- Acceptance criteria: Met for memory proposals; users can propose and explicitly apply one redacted memory note from a session without provider credentials or network.
- Estimated PR size: Medium
- Dependencies: Session store and JSONL memory store.

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
- Status: Fixed for the current prompt assembler; future dynamic context sources must keep using labeled untrusted blocks.
- Evidence: Prior audit found user request and skill view could be enclosed as untrusted data while session/tool summaries were inserted as plain text. The hardening pass separates stable system policy from untrusted user content and wraps current request, session summary/tool summaries, skill excerpts, repo facts, memory, and dynamic suffix content in labeled untrusted data blocks.
- Affected files: `src/hipson/prompt.py`, `src/hipson/runtime.py`, `tests/test_prompt.py`
- Why it matters: Tool output and session summaries are untrusted data and can carry prompt injection.
- Recommended fix: Enclose all dynamic user/file/tool/skill/provider content in labeled untrusted data blocks, then add snapshot tests.
- Tests to add: Malicious tool summary cannot escape the untrusted block; skill excerpt cannot override runtime policy.
- Acceptance criteria: Prompt assembly consistently separates policy from data.
- Estimated PR size: Small
- Dependencies: None

### [P2] Add mutation or targeted fault-injection coverage for runtime-critical files
- Severity: P2
- Status: Partially fixed. Focused fault-injection tests and mutmut configuration were added; complete mutation survivor triage remains open as P1.
- Evidence: `pyproject.toml` now targets `agents.py`, `approvals.py`, `prompt.py`, `runtime.py`, `sandbox.py`, and `tools/registry.py`; `uv run pytest -q -k "security or injection or redaction or approval or sandbox or mutation or contract or persistence or mcp or scheduler"` passed 41 selected tests. A complete mutmut run still timed out and produced partial survivors.
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
