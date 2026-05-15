# Hipson 1.0 Readiness

This checklist defines what should block or allow a stable Hipson 1.0 release.

## Release Posture

Hipson is close to a production-ready local developer tool. The core safety
model is stable: local-first execution, bounded packets, deterministic routing by
default, optional provider calls, redaction before persistence/provider paths,
and CI coverage for packaging and smoke workflows.

## Blocking Before 1.0

- Publish from the rewritten public `main` only; do not push
  `backup/pre-public-history`.
- Run the full CI matrix on GitHub after the force-with-lease push.
- Confirm external bundled skills are intentionally shipped, with acceptable
  size, license posture, and maintenance policy.
- Decide whether public package metadata should point to the final repository
  URL before tagging.
- Perform one clean clone test outside this working directory:
  `uv sync --all-extras`, `uv run hipson doctor`, `uv run python scripts/run_tests.py`,
  `uv build`, and wheel install smoke.
- Run a final secret/path scan on the pushed branch, excluding tests and known
  external fixture examples.
- Keep the runtime asset trust boundary intact: installed package assets or
  explicit valid `HIPSON_DEV_ROOT` only, never implicit CWD lookup.

## Non-Blocking Before 1.0

- Retrieval-backed memory injection into packet generation with strict size caps.
- Provider-specific pricing refresh for model routing docs.
- Signed release artifacts.
- GitHub Pages or richer examples.

## 1.0 Gates

```bash
uv sync --all-extras
uv run ruff check .
uv run python scripts/run_tests.py
uv run python -m pytest -q
uv run python -m compileall src scripts tests
uv build
hipson doctor
hipson skill validate
python -m hipson.cli scan /definitely/missing/path
hipson sidecar route --task "security review" --risk security
hipson sidecar route --task "security review" --risk security --task-type review --file src/hipson/agents.py --skills hipson-backend --context-chars 4200 --llm --llm-dry-run
```

## Release Decision

Tag `v1.0.0` only after the blocking items pass on the public remote. Until then,
keep the package version below 1.0 and treat the repository as release-candidate
quality rather than final 1.0.
