# Hipson Progress

## Current Goal
Build this repository into a portable, git-ready Hipson Orchestrator Hub for cross-repo AI workflow coordination, delta review, sidecar agents, and durable handoff.

## Current State
- The repository has its own `.git` root and no longer relies on a parent user-profile git root.
- Python package entrypoint `hipson` lives under `src/hipson/`.
- Runtime assets are bundled under `src/hipson/assets/` so installed packages can run without a source checkout.
- `repos.yaml` is local-only and ignored.
- `repos.example.yaml` is the portable template for other developers.
- OpenRouter provider keys live outside the repo.
- Generated `runs/`, `scans/`, and `memory/*.jsonl` artifacts are ignored by default.
- Packet generation is compiled through structured packet specs.
- Local JSONL memory is available through `hipson memory add/search/list`.
- Sidecar agents include routing metadata and can be suggested with `hipson sidecar route`.
- Canonical source docs live under `knowledge/source/`.

## Decisions
- Use project-local orchestration in this hub, not global restrictions on Codex.
- Prefer delta scans, progress files, and git diffs over full repo rescans.
- Use cheap paid OpenRouter sidecars only by default.
- Treat sidecar output as advisory; verify findings against local files.
- Keep user-facing responses compact.
- Keep `skills/hipson-gpt/` as the structured reference knowledge package.
- Keep root minimal and git-friendly.
- Keep legacy scripts as wrappers around packaged modules.
- Use `HIPSON_HOME` for Hipson config and `CODEX_HOME` for Codex config.
- Redact secrets before scan output, packet persistence, and sidecar send paths.
- Keep memory writes orchestrator-owned and redacted; agents receive bounded packet context.
- Use deterministic routing metadata before considering model-based routing.

## Verification
- `uv run ruff check .`: passed.
- `python3 scripts/run_tests.py`: passed, 48/48 tests.
- `python3 -m compileall src scripts tests`: passed.
- `uv build`: passed.
- Wheel install smoke from a temporary venv outside the repo: passed.
- `hipson --help`, `hipson doctor`, `hipson scan .`, `hipson memory list`, `hipson sidecar route`, `hipson skill validate`, `hipson install codex --dry-run`: passed in editable install smoke checks.
- `bash -n codex-workflow-kit/install.sh`: passed.
- `python3 -m json.tool config/agents.json`: passed.
- Secret scan excluding generated reports and test fixtures: passed.
- Sensitive `.env` packet refusal: passed.

## Risks
- `repos.yaml` must stay local because it may contain developer-specific absolute paths.
- Sidecar packets can still contain sensitive project context if users manually create them poorly.
- Cheap paid models can produce false positives; Architect must verify findings.

## Next Task
Review the final public GitHub page after pushing, then add retrieval-backed packet context with strict size caps.

## Handoff Notes
For a fresh local setup:

```bash
cp repos.example.yaml repos.yaml
cp .env.example .env
python3 scripts/run_tests.py
hipson doctor
```
