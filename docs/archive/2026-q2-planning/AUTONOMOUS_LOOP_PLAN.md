# Autonomous Loop Plan

## Selected Package

`feat(runtime): add provider-free local deterministic chat router`

## Why This Package

Default `hipson chat` still needed a useful provider-free path. The local MVP already has safe read-only tools and session persistence, so the next highest-value package is to route supported local chat requests to those tools through the existing runtime safety boundary without requiring a real provider.

## Non-Goals

- Do not add live provider usage.
- Do not add new product commands.
- Do not add shell execution.
- Do not broaden scheduler, MCP, gateway, or sidecar surfaces.
- Do not change provider defaults.
- Do not expose write/external/exec/dangerous tools through default chat.
- Do not claim 100/100 while live-provider smoke and full survivor triage remain open.

## Files Expected To Change

- `src/hipson/local_router.py`
- `src/hipson/runtime.py`
- `src/hipson/cli.py`
- `tests/test_runtime.py`
- `tests/test_tools.py`
- `tests/test_hipson_helpers.py`
- `docs/AUTONOMOUS_LOOP_STATE.md`
- `docs/AUTONOMOUS_LOOP_LOG.md`
- `docs/AUDIT_CONTEXT_FOR_HIPSON.md`
- `docs/AUDIT_FINDINGS_BACKLOG.md`
- `docs/PRODUCTION_READINESS_SCORECARD.md`
- `docs/REAL_AGENT_READINESS_SCORECARD.md`
- `README.md`

Production code changes are not expected unless a strengthened test exposes a real safety bug.

## Tests To Add Or Update

- Local router maps supported requests to `repo.scan`, `repo.changed_files`, `memory.search`, and `skill.list`.
- Default `hipson chat -q "scan this repo..."` executes a real local `repo.scan` action and persists the session/tool call.
- Default `hipson chat -q "show changed files"` executes `repo.changed_files`.
- Unsupported local chat requests fail truthfully with supported intents.
- Explicit `--fake` mode remains unchanged.

## Implementation Steps

1. Add a small dependency-free local router module.
2. Add `HipsonRuntime.run_local(...)` and keep tool execution through the existing `_handle_tool_call` boundary.
3. Change default `hipson chat` to local/router mode when neither `--fake` nor `--provider` is selected.
4. Keep unsupported requests bounded and truthful.
5. Add focused runtime/CLI tests.
6. Update README and scorecards after tests pass.

## Verification Plan

Targeted:

- `uv run pytest tests/test_runtime.py tests/test_tools.py tests/test_hipson_helpers.py -q`
- `uv run pytest -q -k "chat or local_router or tool_run or runtime or tool or approval or session or redaction"`

Full:

- `uv run pytest -q`
- `uv run ruff check .`
- `uv run mypy src/hipson`
- `uv run bandit -q -r src/hipson -c pyproject.toml`
- `python -m compileall src/hipson`
- `uv run python scripts/run_tests.py`
- `uv run hipson doctor`
- `uv run hipson skill validate`

CLI smoke:

- `uv run hipson chat -q "scan this repo and propose the next safe PR"`
- `uv run hipson chat -q "show changed files"`
- `uv run hipson chat --fake -q "offline runtime smoke" || true`
- `uv run hipson tool run repo.changed_files '{"path":"."}' --json || true`

## Rollback Notes

If local routing accidentally needs write/external/exec/dangerous tools, stop and keep those requests unsupported. If tests reveal a safety bug outside this package, document it as deferred rather than broadening scope.
