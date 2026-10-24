# Production Readiness Repair Plan

## 1. Current State

Baseline verification on 2026-05-27 reproduced a healthy local/provider-free runtime foundation:

- `uv run pytest -q`: 209 passed.
- `uv run ruff check .`: passed.
- `uv run mypy src/hipson`: passed.
- `uv run bandit -q -r src/hipson -c pyproject.toml`: passed.
- `python -m compileall src/hipson`: passed.
- `uv run python scripts/run_tests.py`: 209/209 passed.
- `uv run hipson doctor`: passed.
- `uv run hipson skill validate`: passed.

Observed product gaps remain in the local MVP surface:

- `hipson chat` fails closed without a provider, as intended, but there is no public offline tool-call demo path.
- `hipson tool` exposes `list` and `show`, but no safe read-only execution path.
- Runtime max-tool-iteration stops return a generic answer without the same bounded tool-call context used for other rejected/failed calls.
- Learning proposals are approval-gated but still mostly based on the last non-empty message.

## 2. Selected Scope

Selected package: **Safe End-to-End Control Plane + Learning Quality Repair**.

This scope targets a production-ready local/provider-free MVP, not real-provider readiness. The goal is to make Hipson usable as a local control plane that can inspect tools, run safe read-only tools through the hardened boundary, demonstrate a fake/offline tool-call chat path, persist auditable session records, and propose better approval-gated learning candidates.

## 3. Non-Goals

- Do not implement a real chat provider adapter.
- Do not call live providers or require provider credentials.
- Do not add shell execution.
- Do not make MCP required.
- Do not add write/external/exec/dangerous tool execution through `hipson tool run`.
- Do not claim real-provider readiness.
- Do not rewrite scheduler, gateway, MCP, sidecar, or provider modules.

## 4. Findings To Fix

- F1/P1: Public runtime has no safe user-facing end-to-end tool execution path.
- F2/P1: `hipson chat --fake` cannot demonstrate the runtime tool-call pipeline from CLI.
- F3/P1/P2: Max tool-iteration stops hide attempted tool-call context.
- F4/P2: Learning proposals need to summarize session trajectory and tool results, not just the last message.

## 5. Implementation Plan

1. Add `hipson tool run <tool_name> <json>` for read-only, no-approval tools only.
2. Route `tool run` through registry input validation, path policy, approval policy, registry execution, output contract validation, bounded/redacted output, and optional session persistence.
3. Add explicit `hipson chat --fake-tool-call <name> --fake-tool-input <json>` for fake/offline demos only.
4. Keep `hipson chat` fail-closed without `--fake`.
5. Improve max-tool-iteration answer and session metadata with bounded context about completed/attempted tool calls.
6. Improve learning proposals using bounded session trajectory: user request, assistant outcome, tool-call summaries, failures, and provenance.
7. Update README and audit/scorecard docs truthfully.

## 6. Tests To Add Or Update

- `tests/test_tools.py`: safe read-only `tool run`, blocked write-risk `tool run`, unknown/invalid/path-rejected inputs, optional session persistence, bounded/redacted JSON output.
- `tests/test_runtime.py`: explicit fake tool-call CLI path, unsafe fake tool-call rejection, max-iteration visibility and persistence.
- `tests/test_learning.py`: trajectory-based memory proposal, tool-call provenance, redaction, reference-only skill proposal.
- Existing regression tests for fail-closed chat, session observability, tool list/show, and learning apply-memory must remain passing.

## 7. Verification Plan

Targeted:

```bash
uv run pytest tests/test_tools.py tests/test_runtime.py tests/test_learning.py tests/test_session.py -q
uv run pytest -q -k "tool or runtime or chat or learn or learning or session or approval or redaction"
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

CLI smoke:

```bash
uv run hipson chat -q "scan this repo and propose the next safe PR"
uv run hipson chat --fake -q "offline runtime smoke"
uv run hipson chat --fake --fake-tool-call repo.changed_files --fake-tool-input '{"path":"."}' -q "check files"
uv run hipson tool run repo.changed_files '{"path":"."}' --json
uv run hipson tool run packet.review.create '{"project":".","title":"x"}' --json
```

## 8. Review Loop Plan

After implementation, run targeted checks, full verification, CLI smoke, inspect `git diff`, update the scorecard, and review remaining P0/P1/P2 issues. Fix only in-scope issues. If any local/provider-free P0/P1 remains, do not claim production readiness.

## 9. Rollback Notes

The rollback unit is the new CLI execution surface plus tests:

- `command_tool_run` and parser wiring in `src/hipson/cli.py`.
- fake tool-call CLI wiring in `src/hipson/cli.py`.
- max-iteration visibility changes in `src/hipson/runtime.py`.
- trajectory learning changes in `src/hipson/learning.py`.
- associated tests and docs.

## 10. Deferred Work

- Real provider adapter and real-provider readiness.
- Durable approval records beyond current session metadata.
- FTS-backed session search population/query.
- Write-risk `tool run` with explicit approval UX.
- Full focused mutmut survivor triage.
