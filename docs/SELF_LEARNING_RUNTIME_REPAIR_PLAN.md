# Self-Learning Runtime Repair Plan

## 1. Selected 4-5h Scope

Implement Runtime Observability + Approval-Gated Learning MVP:

- read-only `hipson session list/show/search`;
- read-only `hipson tool list/show`;
- `hipson learn propose` from a completed session;
- explicit `hipson learn apply-memory` into the JSONL memory store.

This is the smallest coherent package that moves Hipson toward a local AI engineering control plane without adding real-provider or autonomous execution risk.

Status: implemented in this repair pass. Verification results are tracked in `docs/AUDIT_CONTEXT_FOR_HIPSON.md` and the command outputs from the implementation session.

## 2. Non-Goals

- Do not implement a real provider adapter.
- Do not call live providers or require credentials.
- Do not add network-dependent tests.
- Do not add shell execution.
- Do not add `hipson tool run` in this pass.
- Do not expand scheduler, gateway, MCP, or sidecar capabilities.
- Do not auto-write memory from runtime output.
- Do not create, modify, or auto-activate skills from learning proposals.
- Do not claim real-provider or release readiness.

## 3. Files Expected To Change

- `src/hipson/cli.py`
- `src/hipson/session.py`
- `src/hipson/learning.py`
- `tests/test_session.py`
- `tests/test_tools.py`
- `tests/test_learning.py`
- `README.md`
- `docs/AUDIT_CONTEXT_FOR_HIPSON.md`
- `docs/AUDIT_FINDINGS_BACKLOG.md`
- `docs/PERSISTENT_AGENT_RUNTIME_SPEC.md`

## 4. Tests To Add Or Update

- Session CLI uses temp DBs and never needs provider credentials.
- `session list` shows bounded rows and counts.
- `session show` redacts secret-looking message/tool data.
- `session search` finds message text through a safe fallback search.
- Missing session IDs fail cleanly.
- `tool list` shows registered tool names and risk levels.
- `tool show` displays schema, output contract, approval, and path policy metadata.
- Unknown tools fail cleanly.
- `learn propose` prints redacted proposals without persistence.
- `learn apply-memory` explicitly writes one redacted memory note with source provenance.
- Skill proposals remain reference-only and are not auto-applied.
- `hipson chat` still fails closed without a provider and `--fake` still works.

## 5. Implementation Plan

1. Add bounded CLI formatting helpers.
2. Add session-store helper methods for counts and safe LIKE search.
3. Add `session` parser group and command handlers.
4. Add tool spec serialization helpers plus `tool` parser group.
5. Make learning proposal IDs deterministic for review/apply workflows.
6. Add `learn propose` and `learn apply-memory` command handlers.
7. Add focused tests for session, tool, and learning commands.
8. Update focused docs after tests pass.

## 6. Verification Plan

Targeted:

```bash
uv run pytest tests/test_session.py tests/test_tools.py tests/test_learning.py tests/test_runtime.py -q
uv run pytest -q -k "session or tool or learn or learning or runtime or memory or redaction"
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
uv run hipson --help
uv run hipson chat --help
uv run hipson chat -q "scan this repo and propose the next safe PR"
uv run hipson chat --fake -q "offline runtime smoke"
uv run hipson session list --session-db ./.tmp-runtime-test.sqlite
uv run hipson tool list
uv run hipson learn --help
```

## 7. Rollback Notes

Rollback can remove the `session`, `tool`, and `learn` CLI groups plus related tests/docs. The SQLite schema remains compatible because this pass should only add read/query helpers and not change existing table definitions.

## 8. Deferred Findings

- `hipson tool run` remains deferred to avoid adding a manual execution surface before durable approval UX.
- FTS-backed search remains deferred; implement safe fallback search and document FTS as future unless table population is added.
- Durable approval records remain deferred.
- Full mutation survivor triage remains deferred.
