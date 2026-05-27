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

Mutmut was not rerun in this 10-minute stabilization pass. Reason: the previous time-boxed focused run already exited 124 after partial progress, and this pass is intended to freeze docs and prepare the next package rather than spend the window generating another partial set.

Recommended follow-up command:

```bash
timeout 180s uv run mutmut run || true
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

- Real-provider readiness.
- Release readiness.
- FTS-backed search readiness.
- Write/external/exec/dangerous `hipson tool run`.
- Durable approval records.
- Completed mutation survivor triage.

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

Known prior signal from `docs/AUDIT_CONTEXT_FOR_HIPSON.md`:

- A previous configured mutmut run timed out with exit 124.
- Last observed progress was roughly 1,597/2,219 mutants.
- Partial results still included survivors or unchecked mutants in approval, sandbox, registry, provider, prompt, and runtime helpers.

No new survivor list was generated during this stabilization pass.

## 6. Low-Risk / Equivalent Survivors

Unknown. The next package should classify survivors only after reading `uv run mutmut results` and mapping each survivor to an observable safety property.

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

Avoid tests that merely mirror implementation internals or assert low-value formatting details.

## 8. Recommended Next Package

`test(security): triage focused mutmut survivors in runtime-critical boundaries`

Scope:

- Run mutmut in manageable batches.
- Inspect high-risk survivors first.
- Add requirement-level tests for real safety regressions.
- Document equivalent or low-risk survivors with rationale.
- Do not add real provider support, `tool run`, scheduler/MCP expansion, or new product features.
