# Mutation Triage Notes

## 1. Run

Post-test stabilization pass after Runtime Observability + Approval-Gated Learning MVP, updated after the local provider-free production readiness repair.

State freeze commands:

- `git status --short`: clean before this documentation-only stabilization patch.
- `git diff --stat`: no diff before this documentation-only stabilization patch.
- `git diff --name-only`: no changed files before this documentation-only stabilization patch.

Verification already reported for the completed observability/learning MVP:

- `uv run pytest -q`: 209 passed.
- `uv run ruff check .`: passed.
- `uv run mypy src/hipson`: passed.
- `uv run bandit -q -r src/hipson -c pyproject.toml`: passed.
- `python -m compileall src/hipson`: passed.
- `uv run python scripts/run_tests.py`: 209/209 passed.
- `uv run hipson doctor`: passed.
- `uv run hipson skill validate`: passed.

Latest local provider-free production readiness repair verification:

- `uv run pytest -q`: 216 passed.
- `uv run python scripts/run_tests.py`: 216/216 passed.
- `uv run ruff check .`, `uv run mypy src/hipson`, configured Bandit, compileall, doctor, and skill validation passed.

Latest Hermes-style real-agent completion verification:

- `uv run pytest -q`: 223 passed.
- `uv run python scripts/run_tests.py`: 223/223 passed.
- `uv run ruff check .`, `uv run mypy src/hipson`, configured Bandit, compileall, doctor, and skill validation passed.
- Real-provider adapter tests use stub transports only; no live provider credentials or network checks were used.

Mutmut reconnaissance was rerun with `timeout 300s uv run mutmut run || true`. It did not complete the configured 2,219-mutant set within the timeout. Last observed progress was 1,965/2,219 mutants with 1,643 killed, 130 timeouts, and 192 survivors. `uv run mutmut results || true` still reported survivors, timeouts, and not-checked mutants in safety-adjacent modules.

Latest provider/tool-loop approval/search/learning completion pass reran `timeout 300s uv run mutmut run || true`. It generated 2,291 mutants and again did not complete within the timeout. Last observed progress was 2,005/2,291 mutants with 1,673 killed, 148 timeouts, and 184 survivors. `uv run mutmut results || true` still reports survivors/timeouts/not-checked mutants in `agents`, `approvals`, `prompt`, `sandbox`, `redaction`, `router`, `runtime`, and `tools.registry`.

Recommended follow-up command:

```bash
timeout 300s uv run mutmut run || true
uv run mutmut results || true
```

If the full configured run remains too broad, run smaller module batches or use mutmut's current supported filtering options after checking `uv run mutmut run --help`.

## 2. Current Post-Test State

Runtime Observability + Approval-Gated Learning MVP is implemented and documented.

Implemented CLI:

- `hipson session list`
- `hipson session show <session_id>`
- `hipson session search "query"`
- `hipson tool list`
- `hipson tool show <tool_name>`
- `hipson learn propose --session-id <id>`
- `hipson learn apply-memory --session-id <id> --proposal-id <id> --memory-dir <path>`

Still not claimed:

- Release readiness.
- Write/external/exec/dangerous `hipson tool run`.
- Completed mutation survivor triage.
- Live-provider smoke readiness.

Newly implemented after the original stabilization note:

- Explicit `hipson chat --provider openai-compatible` provider mode.
- Durable `approval_records` persisted for runtime, scheduler, and manual tool-run decisions.
- Session search across messages, tool-call summaries, and memory summaries with FTS/fallback behavior.
- Optional approval expiry metadata is now included in approval records.
- Learning proposals now include approval-record provenance and draft/reference-only skill metadata.

## 3. High-Risk Survivor Categories

- Approval allowed/blocked inversion.
- Dangerous risk no longer blocked.
- External or exec action no longer requires approval.
- Path traversal or sensitive path bypass.
- Redaction removal.
- Output bounding removal.
- Tool input/output contract weakening.
- Unknown tool rejection weakening.
- Runtime rejected/failed tool visibility weakening.
- `learn apply-memory` without explicit apply command.
- Skill proposal auto-apply.
- Provider error body leakage.
- Prompt injection escaping untrusted data blocks.
- Session persistence leaking secrets.

## 4. Module Targets

- `src/hipson/approvals.py`
- `src/hipson/sandbox.py`
- `src/hipson/tools/registry.py`
- `src/hipson/runtime.py`
- `src/hipson/prompt.py`
- `src/hipson/agents.py`
- `src/hipson/session.py`
- `src/hipson/learning.py`

## 5. Observed Survivors

Current signal from the latest `timeout 300s uv run mutmut run || true`:

- The run did not complete within `timeout 300s`.
- Last observed progress was 2,005/2,291 mutants.
- Partial results included 184 survivors and 148 timeouts.
- Results still include survivors, timeouts, or not-checked mutants in approval, sandbox, registry, provider/agents, prompt, redaction, router, and runtime helpers.

Autonomous loop iteration 1 selected-mutant follow-up:

- Killed `hipson.approvals.x__check_input_paths__mutmut_3` with a legacy approval path-key test covering `path`, `project`, `packet`, and `source`.
- Killed `hipson.sandbox.x_check_skill_root_path__mutmut_3` with a relative workspace skill-root and packaged-asset skill-root test.
- Killed `hipson.tools.registry.x__handler_failure__mutmut_1` with a handler-failure diagnostic detail test.
- Killed `hipson.runtime.x__rejection_summary__mutmut_11` with a direct rejection-summary header/count/bounds contract test.
- `hipson.redaction.x_sanitize_path__mutmut_1` remained a survivor and is classified as equivalent/low-risk for now because `summarize_sensitive_path(...)` intentionally ignores its argument and returns the same constant skipped marker.

No new survivor list was generated during this stabilization pass.

## 6. Low-Risk / Equivalent Survivors

- `hipson.redaction.x_sanitize_path__mutmut_1`: equivalent/low-risk under current behavior because sensitive path summarization is intentionally constant.
- Other low-risk/equivalent survivors remain unknown until smaller module batches are inspected.

## 7. Tests To Add Next

Add tests only for mutants that weaken observable safety behavior. Prioritize:

- approval fail-closed decisions;
- sensitive path and traversal rejection;
- registry input/output contract enforcement;
- provider error redaction and bounding;
- prompt untrusted-data delimiter handling;
- runtime rejection visibility;
- session persistence redaction/bounding;
- explicit-only learning apply behavior.

Next concrete batches:

- `agents.py`: URL validation, provider error bounding, and advisory report output survivors.
- `prompt.py`: untrusted block delimiter, role separation, and cap/budget survivors.
- `approvals.py`: remaining path policy and write/exec decision survivors.
- `tools/registry.py`: input/output contract and bounded output survivors.
- `sandbox.py`: broad home and path traversal timeout/survivor cases.

Avoid tests that merely mirror implementation internals or assert low-value formatting details.

## 8. Recommended Next Package

`test(security): triage focused mutmut survivors in runtime-critical boundaries`

Scope:

- Run mutmut in manageable batches.
- Inspect high-risk survivors first.
- Add requirement-level tests for real safety regressions.
- Document equivalent or low-risk survivors with rationale.
- Do not add real provider support, `tool run`, scheduler/MCP expansion, or new product features.
