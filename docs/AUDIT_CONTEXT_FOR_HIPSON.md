# Audit Context for Hipson

## 1. Current Verified State

The repository is on branch `main` at `/home/hipson47/code/Hipson`. The Hermes-style repair pass started from a clean git scan and then added runtime observability and approval-gated learning CLI changes.

Verified local checks:

- `uv run pytest -q`: 216 tests passed.
- `uv run ruff check .`: passed.
- `uv run mypy src/hipson`: passed.
- `uv run bandit -q -r src/hipson -c pyproject.toml`: passed.
- `python -m compileall src/hipson`: passed.
- `uv run python scripts/run_tests.py`: 216/216 passed.
- `uv run hipson doctor`: passed.
- `uv run hipson skill validate`: passed.

Unconfigured Bandit (`uv run bandit -q -r src/hipson`) failed on existing low-severity subprocess findings in `src/hipson/project.py`.

Provider/prompt boundary hardening added after the audit:

- Remote sidecar provider URLs are HTTPS-only by default; local HTTP requires explicit opt-in for localhost-style endpoints.
- Sidecar provider `HTTPError`/`URLError` bodies are redacted and bounded before display.
- Sidecar provider output is written as advisory untrusted report data, with redaction and deterministic truncation.
- Runtime prompt assembly now separates stable system policy from dynamic untrusted user/session/tool/skill/repo content.

Focused fault-injection hardening added after provider/prompt hardening:

- Approval tests now cover dangerous override attempts, explicit approval not bypassing unsafe write paths, and declared path fields with wrong types.
- Registry tests now reject `bool` values for `int` contracts, contain `JSONDecodeError`, and validate output type failures.
- Prompt and sidecar report tests now cover escaped `</untrusted_data>` delimiter injection.
- Session-store tests now verify direct message/tool-call persistence is redacted and bounded.
- Runtime, scheduler, and MCP tests now cover path-policy handler suppression, no default `shell.run`, dangerous scheduler jobs blocked even with `--approved`, and approval-required read tools blocked through the MCP bridge.
- `pyproject.toml` mutmut configuration now includes `src/hipson/runtime.py`, `src/hipson/approvals.py`, `src/hipson/sandbox.py`, `src/hipson/tools/registry.py`, `src/hipson/prompt.py`, and `src/hipson/agents.py`.

Self-audit repair pass added after the first fault-injection hardening:

- `docs/SELF_AUDIT_FINDINGS.md` and `docs/SELF_AUDIT_REPAIR_PLAN.md` capture the current self-audit, Hipson command results, chosen repair package, and deferred findings.
- Provider helper tests now directly pin HTTPS/local-HTTP URL policy, provider error text redaction/bounds, and untrusted data delimiter escaping.
- Runtime tests now assert the provider request contains role-separated messages and stable tool descriptors, and that multiple rejected tool calls are capped/redacted in the final answer.
- Registry tests now cover composite type contracts, unsupported output types, and bounded/redacted nested output summaries.
- Sandbox tests now cover symlink escape, sensitive path names/suffixes, and precise generated write roots.

Hermes-style runtime observability and learning repair pass:

- `src/hipson/cli.py` now exposes `session`, `tool`, and `learn` command groups.
- `src/hipson/session.py` now exposes read helpers for session counts and safe fallback message search.
- `src/hipson/learning.py` now emits deterministic proposal IDs so proposals can be reviewed and explicitly applied.
- `hipson session list/show/search` read SQLite runtime sessions without requiring provider credentials or network access.
- `hipson tool list/show` inspect registered tools, risk levels, approval requirements, schemas, output contracts, and path policies.
- `hipson learn propose` prints approval-gated memory/skill-reference proposals without durable writes.
- `hipson learn apply-memory` explicitly persists one selected memory proposal into JSONL memory with redacted summary and session/message provenance.

Local provider-free production readiness repair:

- `hipson tool run <name> <json>` now executes only read-risk tools that do not require approval, through registry validation, path policy, approval policy, output contracts, bounded/redacted output, and optional session persistence.
- `hipson chat --fake --fake-tool-call <name> --fake-tool-input <json>` now provides an explicit fake/offline tool-call demo through the runtime boundary.
- Runtime max-tool-iteration stops now include bounded attempted-tool context and persist skipped calls as `approval_status=max_tool_iterations`.
- Learning memory proposals now summarize bounded session trajectory, final outcome, relevant tool-call summaries, and message/tool provenance.

## 2. Implemented Runtime Modules

Runtime-related modules observed in the working tree:

- `src/hipson/session.py`: SQLite session store with sessions, messages, tool calls, memories, skill runs, jobs, migrations, redaction, and placeholder FTS setup.
- `src/hipson/providers/base.py` and `src/hipson/providers/fake.py`: provider protocol dataclasses and deterministic fake provider.
- `src/hipson/tools/registry.py`: tool registry with `ToolSpec`, `ToolResult`, `ToolContext`, duplicate rejection, unknown tool rejection, and basic input validation.
- `src/hipson/tools/repo.py`, `packets.py`, `memory.py`, `skills.py`: wrappers for scan/changed-files, review packet creation, memory search, and skill list/view.
- `src/hipson/approvals.py` and `src/hipson/sandbox.py`: risk policy and path checks.
- `src/hipson/prompt.py`: bounded prompt assembly.
- `src/hipson/runtime.py`: minimal runtime loop.
- `src/hipson/learning.py`: proposal-only learning helpers.
- `src/hipson/scheduler.py`: opt-in tick runner.
- `src/hipson/gateway/cli.py` and `src/hipson/gateway/mcp.py`: thin CLI gateway and internal MCP-style adapter.

## 3. Actual CLI Commands Observed

Observed with `uv run hipson --help`:

- Existing/core: `doctor`, `scan`, `scan-many`, `route`, `init`, `check-setup`, `install`, `packet`, `sidecar`, `memory`.
- New/modified: `chat`, `skill`, `scheduler`, `session`, `tool`, `learn`.

Observed behavior:

- `uv run hipson chat --help` exists and exposes `--session-db`, `--session-id`, `--fake`, `--fake-response`, `--fake-tool-call`, and `--fake-tool-input`.
- `uv run hipson chat -q "scan this repo and propose the next safe PR"` fails closed when no chat provider is configured.
- `uv run hipson chat --fake -q "scan this repo and propose the next safe PR"` runs the explicit fake/offline provider path.
- `uv run hipson chat --fake --fake-tool-call repo.changed_files --fake-tool-input '{"path":"."}' -q "check files"` runs a read-only tool call through the fake/offline runtime path and prints a bounded tool-call summary.
- `uv run hipson skill list` succeeds.
- `uv run hipson session list --session-db <temp>/runtime.sqlite` succeeds and reports no sessions without creating the missing temp DB.
- `uv run hipson tool list` succeeds and lists registered runtime tools with risk and approval metadata.
- `uv run hipson tool run repo.changed_files '{"path":"."}' --json` succeeds through the safe read-only tool execution boundary.
- `uv run hipson learn --help` succeeds and exposes `propose` and `apply-memory`.

## 4. Verified Behaviors

- Tests can exercise runtime tool calls by injecting `FakeProvider.with_tool_calls(...)` into `HipsonRuntime`.
- Runtime creates a session, persists user and assistant messages, validates tool names/inputs through the registry, checks approval before execution, executes allowed tools, persists tool calls/results, and stops after a bounded number of tool iterations.
- Session store redacts message and tool-call fields before persistence.
- Approval policy blocks dangerous risk, requires approval for exec except allowlisted read-only commands, and blocks common sensitive/path traversal cases.
- Registered tools now declare path policies for path-bearing inputs, and runtime/scheduler/MCP validate inputs before approval.
- Tool handler exceptions and output contract failures become failed tool results rather than runtime crashes.
- Runtime tool-call persistence and scheduler/MCP tool outputs use bounded, redacted output views.
- Prompt assembler is deterministic, bounded, redacts content, emits role-separated system/user messages for provider requests, and treats dynamic content as untrusted data.
- Sidecar provider errors and report output are redacted/bounded; arbitrary remote HTTP provider URLs are rejected by default.
- Scheduler is tick-only, not a daemon.
- Gateway adapter calls runtime rather than duplicating tool execution.
- MCP-style adapter is optional/internal and exposes read-only tools by default.

## 5. Failed or Skipped Checks

- Raw Bandit failed without project config because `src/hipson/project.py` imports and uses `subprocess`; configured Bandit passes.
- Live provider/network checks were skipped by requirement.
- `uv run mutmut run --paths-to-mutate ...` failed because this mutmut version does not support that CLI flag.
- `timeout 180s uv run mutmut run --max-children 2` started from the focused project config, generated 2,219 mutants, and exited 124 before completion. Last observed progress was roughly 1,597/2,219 mutants, with roughly 1,381 killed and 216 surviving. Partial `uv run mutmut results` output still listed survivors/not-checked mutants, including safety-adjacent survivors in approvals, sandbox, registry, provider, prompt, and runtime helpers.
- Historical pre-observability smoke showed `hipson session list` and `hipson tool list` missing. Post-repair smoke now shows `hipson session list --session-db <temp>/runtime.sqlite` and `hipson tool list` succeeding.

## 6. Known Bugs / Risks

- Real provider support is still intentionally absent; `hipson chat` remains fake/offline only when explicitly requested with `--fake`.
- Manual `hipson tool run` is intentionally limited to read-risk tools that do not require approval.
- Future tools must declare path policy metadata before registration.
- Session history retention remains minimal.
- Session search uses a safe SQLite `LIKE` fallback; FTS tables are still schema placeholders until population/search is implemented.

## 7. Test Gaps

- CLI fake/offline `chat` tool-call execution is covered; real-provider chat remains intentionally unimplemented and untested.
- No FTS population test; fallback message search is covered by `tests/test_session.py`.
- Focused fault-injection tests exist for runtime-critical modules, including direct provider helper, runtime tool-descriptor/rejection, registry composite-contract, bounded-output, and sandbox symlink/sensitive-path cases. Full mutation survivor triage is still incomplete.
- No live provider/network test by design; sidecar provider hardening is covered with local fakes only.

## 8. Security Gaps

- Approval enforcement is caller-dependent; direct registry callers can bypass approvals unless they explicitly use `ApprovalPolicy`.
- Scheduler `--approved` is a boolean flag, not a durable approval record.
- Partial mutmut results show remaining approval/path/registry/provider/prompt/runtime survivors that must be triaged before real-provider work.
- Real-provider prompt/tool exposure remains intentionally unimplemented and must not be added until provider, prompt, runtime, and tool-boundary mutmut survivors are triaged.

## 9. Documentation Drift

- `README.md` now documents fake-only chat behavior, runtime DB observability, tool inspection, and approval-gated learning. Scheduler docs remain minimal.
- `docs/PERSISTENT_AGENT_RUNTIME_SPEC.md` now lists read-only/no-approval `hipson tool run` as an MVP command; some later modules are still described as proposed/future even though implementation files exist in the working tree.
- `docs/PROJECT_DEVELOPMENT_PLAN.md` frames scheduler/MCP as future/optional, but implementation files already exist.
- The spec now lists `hipson session list/show/search`, `hipson tool list/show/run`, and `hipson learn propose/apply-memory` as MVP commands, with `tool run` constrained to read-risk/no-approval tools.

## 10. Recommended Next PR

`test(security): complete focused mutmut survivor triage in smaller batches`

Scope:

- Run mutmut in smaller module/function batches for `agents.py`, `prompt.py`, `runtime.py`, `approvals.py`, `sandbox.py`, and `tools/registry.py`.
- Add requirement-level tests for remaining high-risk survivors; document only equivalent/low-risk survivors.
- Keep tests credential-free and network-free.
- Preserve provider-free defaults and do not add a real chat provider adapter.

Do not add a real primary runtime provider adapter in this PR.

## 11. Open Product Decisions

- Should `hipson chat` default to fake mode, or should fake mode require `--fake`?
- Where should runtime sessions be stored by default, and what retention policy should apply?
- Should model-initiated writes under `runs/`, `scans/`, and `docs/` be auto-approved?
- What exact user approval UX should be used for write/external/exec tools?
- Should MCP remain an internal adapter until core runtime safety is stable?
- Should scheduler remain in the merge or be deferred until approval metadata is stronger?

## 12. Handoff Summary

The runtime implementation has a more defensible dependency-light tool boundary: registered tools declare path policies, inputs are validated before approval, handler/output failures are contained, persisted/provider-visible tool outputs are bounded and redacted, remote sidecar provider URLs are HTTPS-only by default, provider error bodies are redacted/bounded, prompt assembly separates stable policy from untrusted dynamic data, focused fault-injection tests cover several safety inversions, and Hipson can now inspect runtime sessions/tools plus explicitly apply approved memory proposals. It is still not ready for real provider usage. The next session should triage focused mutmut survivors before any real primary runtime provider adapter or expanded external tool surface.
