# Hipson Handoff

## Context
This repo is the local Hipson Orchestrator Hub.

Important paths:
- `src/hipson/`: packaged CLI implementation.
- `ORCHESTRATOR.md`: operating model.
- `repos.example.yaml`: portable repo registry template.
- `repos.yaml`: local-only registry, ignored by git.
- `scripts/hipson_project.py`: scans and packet generation.
- `scripts/hipson_agents.py`: OpenRouter sidecar runner.
- `config/agents.json`: sidecar agent definitions.
- `docs/release-1.0.md`: release-readiness gates and remaining blockers.
- `memory/`: local JSONL memory store, with generated `*.jsonl` ignored.
- `skills/hipson-gpt/`: reference knowledge package.
- `knowledge/source/`: canonical source reference documents.
- `src/hipson/assets/codex-workflow-kit/`: canonical installable Codex workflow kit.
- Runtime assets are bundled under `src/hipson/assets/` for installed-package use.

## Decisions
- The repo must be portable for other developers.
- No user-specific paths should be committed.
- No provider keys should be committed.
- Generated `runs/`, `scans/`, and `memory/*.jsonl` reports are local artifacts.
- Use cheap paid OpenRouter models by default.
- Use `HIPSON_HOME` for Hipson config and `CODEX_HOME` only for Codex config.

## Verification
- `uv run ruff check .`: passed.
- `python3 scripts/run_tests.py`: passed, 63/63 tests.
- `python3 -m compileall src scripts tests`: passed.
- `uv build`: passed.
- Wheel install smoke: `hipson --help`, `hipson doctor`, `hipson skill validate`, `hipson install codex --dry-run`, and fake-CWD asset shadowing checks passed from a temporary venv outside the repo.
- `hipson --help`, `hipson doctor`, `hipson scan .`, `hipson memory list`, `hipson sidecar route`, `hipson sidecar route --llm --llm-dry-run`, `hipson skill validate`, `hipson install codex --dry-run`: passed in editable install smoke checks.
- `bash -n src/hipson/assets/codex-workflow-kit/install.sh`: passed.
- `python3 -m json.tool config/agents.json`: passed.

## Setup
```bash
cp repos.example.yaml repos.yaml
mkdir -p ~/.config/hipson
cp .env.example .env
uv sync --all-extras
uv run python scripts/run_tests.py
uv run hipson doctor
uv run hipson skill validate
uv run hipson install codex --dry-run
```

## Remaining Risks
- Sidecar packets must be generated carefully to avoid leaking sensitive project context.
- Sidecar reports are advisory and must be verified locally.
- Stable 1.0 should wait for remote CI on the rewritten public branch.
