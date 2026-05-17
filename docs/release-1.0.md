# Hipson 1.0 Readiness

This checklist defines what should block or allow a stable Hipson 1.0 release.

## Release Posture

Hipson is close to a production-ready local developer tool. The core safety
model is stable: local-first execution, bounded packets, deterministic routing by
default, optional provider calls, redaction before persistence/provider paths,
and CI coverage for packaging and smoke workflows.

## Final External Gates

- Run the full GitHub Actions matrix after pushing the rewritten public `main`.
- Tag `v1.0.0` only after remote CI is green.
- Do not push `backup/pre-public-history`.

## Resolved 1.0 Decisions

- External bundled skills are intentionally shipped as reviewed reference
  material. `skills/external/manifest.json` records source, installed folders,
  skipped items, and purpose. License files are vendored with the skill folders
  where upstream provides them.
- Public package metadata points to the intended public repository URL.
- Runtime assets must come from installed package assets, the imported source
  checkout under `src/hipson/assets/`, or explicit valid `HIPSON_DEV_ROOT`; never
  from implicit CWD.
- The root `codex-workflow-kit/` mirror is removed. Canonical toolkit assets live
  only under `src/hipson/assets/codex-workflow-kit/`.

## Toolkit Canonicalization

Runtime toolkit assets are canonically loaded from
`src/hipson/assets/codex-workflow-kit/`. Do not recreate a root
`codex-workflow-kit/` mirror.

## Non-Blocking Before 1.0

- Retrieval-backed memory injection into packet generation with strict size caps.
- Provider-specific pricing refresh for model routing docs.
- Signed release artifacts.
- GitHub Pages or richer examples.

## 1.0 Gates

```bash
uv sync --all-extras
uv run ruff check .
uv run mypy src/hipson
uv run bandit -q -r src -c pyproject.toml
uv run pip-audit
uv run python scripts/run_tests.py
uv run python -m pytest -q
uv run mutmut run --max-children 2
uv run python -m compileall src scripts tests
uv build
hipson doctor
hipson skill validate
python -m hipson.cli scan /definitely/missing/path
hipson install codex --dry-run
hipson sidecar route --task "security review" --risk security
hipson sidecar route --task "release verification packaging CI gates docs review" --risk architecture --task-type review --file pyproject.toml --skills hipson-gpt --context-chars 4200 --llm
```

## Release Decision

The package is prepared as `1.0.0`. Publish the rewritten public branch, wait for
remote CI, then tag `v1.0.0`.
