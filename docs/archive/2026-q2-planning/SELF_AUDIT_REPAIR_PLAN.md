# Hipson Self-Audit Repair Plan

## 1. Repair Strategy

Prioritize the remaining safety-critical test gap before adding observability CLI features or docs polish. The current runtime is still provider-free by default, but future real-provider work will depend on compact helper functions in `agents.py`, `prompt.py`, `runtime.py`, `sandbox.py`, and `tools/registry.py`. Those helpers need stronger fault-detection tests.

## 2. Chosen Repair Package

Chosen package: **Test/security mutation survivor triage**.

This package is supported by `docs/AUDIT_CONTEXT_FOR_HIPSON.md`, which records a time-boxed mutmut run that did not complete and partial survivors in runtime-critical boundaries. The repair will add focused requirement-level tests for high-risk observable behavior rather than new product features.

## 3. Non-Goals

- Do not implement a real provider adapter.
- Do not add live provider or network tests.
- Do not add `hipson session` or `hipson tool` commands.
- Do not expand scheduler, gateway, MCP, or learning capabilities.
- Do not add autonomous shell execution.
- Do not claim real-provider readiness.
- Do not rewrite docs wholesale.

## 4. Files Expected To Change

- `tests/test_hipson_helpers.py`
- `tests/test_runtime.py`
- `tests/test_tools.py`
- `tests/test_approvals.py`
- Optional: `tests/test_sandbox.py` if direct sandbox tests fit better in a new file.
- Optional production files only if strengthened tests reveal a real safety bug.
- `docs/SELF_AUDIT_FINDINGS.md`
- `docs/SELF_AUDIT_REPAIR_PLAN.md`
- Optional: `docs/AUDIT_CONTEXT_FOR_HIPSON.md` and `docs/AUDIT_FINDINGS_BACKLOG.md` to record the new coverage.

## 5. Tests To Add Or Update

- Provider/sidecar helper tests:
  - HTTPS provider base URL accepted and normalized.
  - Remote `http://` rejected.
  - Local HTTP requires explicit opt-in.
  - Unsupported/malformed provider URL rejected.
  - Provider error text redacts secret-like values and truncates large bodies.
  - Untrusted data delimiters are escaped before report/prompt embedding.
- Runtime tests:
  - Fake provider receives role-separated messages and stable tool descriptors without handler objects.
  - Multiple rejected tool calls are summarized, capped, and redacted in the final answer.
- Registry tests:
  - Union/null/list type contracts are enforced.
  - Unsupported schema/output types fail as tool failures, not crashes.
  - Bounded tool output redacts and truncates nested values.
- Sandbox/approval tests:
  - Symlink-style escape is rejected.
  - Sensitive path names such as `.env`, `.ssh`, `.aws`, `.config`, `.gnupg`, private-key-like names, certs, and local DB files are rejected through sandbox checks.
  - Generated/docs write paths are allowed while source writes require approval or block.

## 6. Implementation Plan

1. Add the self-audit findings and repair plan docs before code/test changes.
2. Add direct provider helper tests in `tests/test_hipson_helpers.py`.
3. Add runtime provider-request and multi-rejection tests in `tests/test_runtime.py`.
4. Add registry contract/type/bounded-output tests in `tests/test_tools.py`.
5. Add direct sandbox tests in a focused test file or extend `tests/test_approvals.py`.
6. Run targeted tests.
7. Fix only safety bugs exposed by those tests.
8. Run full verification.
9. Update audit docs with what is fixed and what remains.

## 7. Verification Plan

Targeted:

```bash
uv run pytest tests/test_runtime.py tests/test_approvals.py tests/test_tools.py tests/test_prompt.py tests/test_hipson_helpers.py -q
uv run pytest -q -k "security or injection or redaction or approval or sandbox or mutation or contract or persistence or mcp or scheduler"
```

Full:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src/hipson
uv run bandit -q -r src/hipson -c pyproject.toml
python -m compileall src/hipson
uv run python scripts/run_tests.py
uv run hipson doctor
uv run hipson skill validate
```

Mutation:

```bash
uv run mutmut run
uv run mutmut results
```

Mutation is preferred but not required for this pass. If it times out again, record partial results and keep survivor triage as the recommended next package.

## 8. Rollback Notes

Most changes should be tests and docs. If any production code is adjusted due to a failing safety test, the rollback unit is that focused fix plus its test. Do not roll back unrelated runtime-hardening changes already present in the working tree.

## 9. Deferred Findings

- Read-only `hipson session list/show/search` and `hipson tool list`.
- FTS-backed session search/population.
- Durable scheduler approval records.
- README/spec reconciliation for runtime command status.
- Real provider runtime support.
- Full mutation survivor triage after smaller module/function batching is available.

## 10. Repair Result

Implemented in this pass:

- Added direct provider URL/redaction/untrusted-delimiter helper tests.
- Added runtime provider-request tool descriptor and multi-rejection summary tests.
- Added registry composite type, unsupported output type, and bounded output tests.
- Added sandbox symlink escape, sensitive path, and generated-write-root tests.

Verification:

- `uv run pytest tests/test_runtime.py tests/test_approvals.py tests/test_tools.py tests/test_prompt.py tests/test_hipson_helpers.py -q`: 166 passed.
- `uv run pytest -q -k "security or injection or redaction or approval or sandbox or mutation or contract or persistence or mcp or scheduler"`: 44 passed, 157 deselected.
- `uv run pytest -q`: 201 passed.
- `uv run ruff check .`: passed.
- `uv run mypy src/hipson`: passed.
- `uv run bandit -q -r src/hipson -c pyproject.toml`: passed.
- `python -m compileall src/hipson`: passed.
- `uv run python scripts/run_tests.py`: 201/201 passed.
- `uv run hipson doctor`: passed.
- `uv run hipson skill validate`: passed.
- `timeout 180s uv run mutmut run --max-children 2`: timed out after partial progress; full survivor triage remains deferred.
