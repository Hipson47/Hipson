# Hipson 1.1.0 Agent-Native Layer

Hipson 1.1.0 adds an agent-readable playbook and deterministic workflow router.
The release keeps Hipson local-first and provider-free by default.

## Added

- Root `SKILLS.md` with compact agent skill entries.
- Packaged `src/hipson/assets/SKILLS.md` copy with sync coverage.
- `hipson route --task "..."` and `--json` for deterministic workflow routing.
- Codex workflow assets that point agents to the router and playbook.

## Routing Contract

The router never calls external LLMs or sidecar providers. It maps task text to
scan, review, executor, verify, handoff, sidecar-review, or memory flow and marks
security, architecture, and data-loss risks for human review.

## Suggested Gate

```bash
uv run ruff check .
uv run python -m pytest -q
uv run python scripts/run_tests.py
uv run mypy src/hipson
uv run bandit -q -r src/hipson -c pyproject.toml
uv run python -m compileall -q src scripts tests
uv build
```
