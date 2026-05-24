# Audit Context for Hipson

## 1. Current Verified State

The repository is on branch `main` at `/home/hipson47/code/Hipson`. The working tree already contains tracked source/test modifications and many untracked runtime modules/tests/docs from the previous implementation session. The audit did not treat those changes as trusted until verified.

Verified local checks:

- `uv run pytest -q`: 167 tests passed.
- `uv run ruff check .`: passed.
- `uv run mypy src/hipson`: passed.
- `uv run bandit -q -r src/hipson -c pyproject.toml`: passed.
- `python -m compileall src/hipson`: passed.
- `uv run python scripts/run_tests.py`: 167/167 passed.
- `uv run hipson doctor`: passed.
- `uv run hipson skill validate`: passed.

Unconfigured Bandit (`uv run bandit -q -r src/hipson`) failed on existing low-severity subprocess findings in `src/hipson/project.py`.

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
- New/modified: `chat`, `skill`, `scheduler`.

Observed behavior:

- `uv run hipson chat --help` exists and exposes `--session-db`, `--session-id`, and `--fake-response`.
- `HIPSON_HOME=<temp> uv run hipson chat -q "scan this repo and propose the next safe PR"` prints `Fake provider response`.
- `uv run hipson skill list` succeeds.
- `uv run hipson session list` fails with argparse invalid choice.
- `uv run hipson tool list` fails with argparse invalid choice.

## 4. Verified Behaviors

- Tests can exercise runtime tool calls by injecting `FakeProvider.with_tool_calls(...)` into `HipsonRuntime`.
- Runtime creates a session, persists user and assistant messages, validates tool names/inputs through the registry, checks approval before execution, executes allowed tools, persists tool calls/results, and stops after a bounded number of tool iterations.
- Session store redacts message and tool-call fields before persistence.
- Approval policy blocks dangerous risk, requires approval for exec except allowlisted read-only commands, and blocks common sensitive/path traversal cases.
- Prompt assembler is deterministic, bounded, redacts content, and treats the current user request as untrusted data.
- Scheduler is tick-only, not a daemon.
- Gateway adapter calls runtime rather than duplicating tool execution.
- MCP-style adapter is optional/internal and exposes read-only tools by default.

## 5. Failed or Skipped Checks

- Raw Bandit failed without project config because `src/hipson/project.py` imports and uses `subprocess`; configured Bandit passes.
- Live provider/network checks were skipped by requirement.
- Optional mutmut run was skipped because it is heavier, creates mutation artifacts, and current mutation config does not target the new runtime modules.
- `hipson session list` and `hipson tool list` were attempted and failed because those CLI commands do not exist.

## 6. Known Bugs / Risks

- `hipson chat` is fake-only and can mislead users into thinking a real runtime scan/planning loop ran.
- `src/hipson/runtime.py` hardcodes `fake_provider=True` when evaluating approvals.
- Approval path checks miss path-bearing fields such as `memory_dir` and `root`.
- Runtime persists full tool outputs directly; `repo.scan` output includes markdown.
- Sidecar provider code still allows HTTP URLs and raw HTTP provider error bodies.
- Tool output contracts are not structurally enforced.
- Rejected tool calls are persisted but may not be visible in the final user-facing answer.

## 7. Test Gaps

- No test for `memory_dir` sandbox bypass.
- No test for runtime with a non-fake provider object and an external-risk tool.
- No test that CLI `chat` can execute a tool call.
- No test that tool output contracts are enforced beyond JSON serializability.
- No FTS search/population test.
- No test for malicious tool summaries injected into subsequent prompts.
- No test for raw sidecar provider error body redaction.
- No mutation/fault-injection coverage for runtime-critical modules.

## 8. Security Gaps

- Approval enforcement is caller-dependent; direct registry callers can bypass approvals unless they explicitly use `ApprovalPolicy`.
- Runtime approval context is incorrectly biased toward fake-provider approval.
- Path policy is not tied to each tool's declared input schema.
- Tool outputs can be persisted too broadly.
- Sidecar provider hardening remains incomplete.
- Scheduler `--approved` is a boolean flag, not a durable approval record.

## 9. Documentation Drift

- `README.md` does not document `hipson chat`, scheduler, runtime DB behavior, or fake-only limitations.
- `docs/PERSISTENT_AGENT_RUNTIME_SPEC.md` still describes some modules as proposed/future, while implementations exist in the working tree.
- `docs/PROJECT_DEVELOPMENT_PLAN.md` frames scheduler/MCP as future/optional, but implementation files already exist.
- The spec lists `hipson session list` and `hipson tool list` as next commands; they are not implemented, which is acceptable only if not claimed complete.

## 10. Recommended Next PR

`fix(runtime): make chat mode and approvals fail closed`

Scope:

- Make fake/offline chat mode explicit and truthful in CLI output.
- Remove hardcoded `fake_provider=True` from runtime approval evaluation.
- Add runtime tests for non-fake provider context plus external-risk tool rejection.
- Surface rejected tool-call summaries in runtime answers when no later assistant response explains them.

Do not add a real provider adapter in this PR.

## 11. Open Product Decisions

- Should `hipson chat` default to fake mode, or should fake mode require `--fake`?
- Where should runtime sessions be stored by default, and what retention policy should apply?
- Should model-initiated writes under `runs/`, `scans/`, and `docs/` be auto-approved?
- What exact user approval UX should be used for write/external/exec tools?
- Should MCP remain an internal adapter until core runtime safety is stable?
- Should scheduler remain in the merge or be deferred until approval metadata is stronger?

## 12. Handoff Summary

The runtime implementation has a useful dependency-light skeleton, and the local test suite passes. It is not ready for real provider usage or trusted development work. The next session should fix runtime truthfulness and approval semantics first, then harden tool contracts, path checks, persistence bounds, and sidecar provider redaction before adding any real provider adapter or expanding tools.
