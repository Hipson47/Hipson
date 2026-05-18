# Hipson

Hipson is a local-first orchestration CLI for AI-assisted software work. It scans
Git repositories, creates bounded agent packets, routes advisory sidecars, stores
compact local memory, and installs a Codex workflow kit.

The project is designed as an agent-native tool-use layer rather than a human
dashboard: the repository, git diff, tests, and human-reviewed decisions stay
the source of truth.

## Features

- **Stable 1.1 local-first CLI** for bounded, human-reviewed AI software work.
- **Delta scans** for one repo or many repos from `repos.yaml`.
- **Structured packet compiler** for review and implementation subagents.
- **Local JSONL memory** for durable decisions, risks, handoffs, and source refs.
- **MoE-like sidecar routing** from explicit agent metadata in `config/agents.json`.
- **Optional LLM router** behind `hipson sidecar route --llm`, using only a redacted JSON summary.
- **OpenRouter sidecars** for optional bounded second opinions.
- **Codex installer** with dry-run mode, backups, and managed marker blocks.
- **Agent-readable `SKILLS.md` and deterministic workflow router** for autonomous tool choice.
- **Secret redaction and sensitive-path guards** before persistence or provider calls.
- **Dependency-light runtime** with `uv` and `ruff` for mature development workflow.
- **Visual direction and optional HyperFrames video sidecars** for bounded UI,
  motion, and website-to-video briefs.

## Install

Development install with `uv`:

```bash
uv sync --all-extras
uv run hipson --help
```

Editable install with standard Python tooling:

```bash
python -m pip install -e ".[dev]"
hipson --help
```

Package-style local install:

```bash
pipx install .
```

## Quick Start

```bash
cp repos.example.yaml repos.yaml
cp .env.example .env

uv run hipson doctor
uv run hipson route --task "security review of auth"
uv run hipson scan .
uv run hipson packet review . --title "Review current delta" --include-diff -o runs/review-packet.md
uv run hipson sidecar route --task "security review of release diff" --risk security
uv run hipson memory add --scope repo --repo Hipson --kind decision --summary "Keep sidecars advisory"
uv run hipson memory search "sidecar routing"
```

`.env` is optional and only needed for provider-backed sidecars. Core commands do
not require API keys or cloud services.

## Common Commands

```bash
hipson doctor
hipson route --task "implement parser fix"
hipson route --task "security review of auth" --json
hipson scan . --include-diff
hipson scan . --include-diff -o runs/latest-scan.md
hipson scan-many repos.yaml -o scans/latest.md --json scans/latest.json

hipson packet review . --title "Review current delta" --include-diff -o runs/review-packet.md
hipson packet exec . --title "Implement next task" --goal "..." --allowed-edit src,tests --skills hipson-testing

hipson memory add --scope repo --repo Hipson --kind decision --summary "..."
hipson memory search "release checklist"
hipson memory list

hipson sidecar list
hipson sidecar route --task "architecture security review" --risk security
hipson sidecar route --task "studio mode interactive hero visual direction" --risk ui
hipson sidecar route --task "HyperFrames website to video launch short" --risk ui
hipson sidecar route --task "security review" --risk security --task-type review --file src/auth.py --skills hipson-backend --context-chars 4200 --llm
hipson sidecar run --agent reviewer_cheap --packet runs/review-packet.md --dry-run

hipson skill validate
hipson check-setup
hipson install codex --dry-run
hipson install codex --apply
```

## Agent-Native Usage

Hipson is designed for coding agents. For non-trivial tasks, ask the agent to run:

```bash
hipson route --task "..."
```

The router returns the recommended Hipson skill and exact safe commands. It works
with Codex-style CLI workflows; Claude and Cursor can consume Hipson packets
manually. Codex has the most native install support. Core Hipson requires no API
key.

## Workflow

1. Run `hipson doctor`.
2. Run `hipson route --task "..."` for non-trivial work.
3. Run `hipson scan .` or `hipson scan-many repos.yaml`.
4. Search memory when prior decisions matter.
5. Generate a bounded review or executor packet.
6. Route advisory sidecars when a second opinion is useful.
7. Review the resulting git diff and verification output.
8. Fold durable decisions back into project memory or progress docs.

## Project Layout

```text
.
  .github/workflows/ci.yml
  config/agents.json
  docs/
  knowledge/
  memory/
  scripts/
  skills/
  src/hipson/
  templates/
  tests/
  CHANGELOG.md
  pyproject.toml
  uv.lock
```

The canonical toolkit copy is `src/hipson/assets/codex-workflow-kit/`.

## Configuration

Sidecar provider key resolution:

1. Already-exported shell environment.
2. `HIPSON_AGENTS_ENV` file, if set.
3. `.env` in the current working directory.
4. `~/.config/hipson/agents.env`.

Recommended user-level setup:

```bash
mkdir -p ~/.config/hipson
cp config/providers.example.env ~/.config/hipson/agents.env
```

Keep real provider keys out of git.

Runtime assets are loaded from the installed Hipson package or from the imported
source checkout. Hipson does not trust the current project directory for its own
runtime assets, so it is safe to run inside arbitrary repositories that happen
to contain Hipson-looking files. `HIPSON_DEV_ROOT` can override this boundary
for local development only; invalid values fail hard.

## Optional LLM Router

The default router is deterministic and metadata-based. For complex,
multi-dimensional tasks, `hipson sidecar route --llm` can ask a small configured
model to choose one sidecar agent. This path sends only a redacted JSON summary,
not the packet:

```json
{"task_type":"review","risk":"security","files":["src/auth.py"],"chars":4200,"skills":["hipson-backend"]}
```

Use it when task shape is ambiguous. Avoid it in the normal flow when cost,
latency, or deterministic behavior matters more.

## Safety Model

Hipson is intentionally packet-based:

- project repo files are treated as data, not trusted runtime assets;
- sidecars receive bounded packets, not whole repos;
- sidecar output is advisory;
- sensitive files are skipped or summarized;
- common API keys, bearer tokens, private keys, quoted env-style secrets, and structured secret values are redacted;
- local memory stores compact facts, not transcripts;
- git diff and verification commands remain the final contract.

Redaction is a safety layer, not a substitute for reviewing packet contents before
sending them to external providers.

Invalid scan paths fail with a non-zero exit and a clear error instead of
producing a misleading clean scan.

## Development

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
```

The dependency-free test runner is kept intentionally small so the core project
can be validated even in constrained environments:

```bash
python scripts/run_tests.py
```

## CI

GitHub Actions validates Python 3.11 and 3.12, runs Ruff, mypy, Bandit,
pip-audit, the local test runner, pytest, and the configured mutmut target set,
then compiles source files, builds wheel/sdist artifacts, smoke-tests the
installed wheel CLI, and checks both deterministic and dry-run LLM routing.

## Repository Hygiene

Committed:

- source code, tests, templates, docs, skills, and runtime assets;
- vendored skill provenance in `docs/vendored-skills-provenance.json`;
- `uv.lock` for reproducible development tooling;
- `.gitkeep` markers for generated artifact directories.

Ignored:

- `.env`, `.env.*`, nested env files, and provider env files;
- local `repos.yaml`;
- generated `runs/*.md`, `scans/*.md`, and `memory/*.jsonl`;
- generated `reports/`, `exports/`, `tmp/`, `hyperframes-output/`, logs, video,
  audio, and subtitle render outputs;
- build outputs, virtualenvs, caches, and bytecode.

Source PDFs, docs, spreadsheets, and archives are not globally ignored because
some vendored skills intentionally include reviewed source assets.

## License

Apache License 2.0. See [LICENSE](LICENSE).
