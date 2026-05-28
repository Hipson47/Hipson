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
- **Runtime session observability** for inspecting SQLite-backed chat sessions.
- **Explicit OpenAI-compatible runtime provider adapter** for configured real-provider chat.
- **Approval-gated learning proposals** that can be explicitly applied to local memory.
- **Tool registry observability** for reviewing tool risk levels, contracts, and path policies.
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

`.env` is optional and only needed for provider-backed sidecars or explicit
`hipson chat --provider openai-compatible` usage. Core local commands do not
require API keys or cloud services.

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

hipson chat -q "scan this repo"
hipson chat --fake -q "offline runtime smoke"
hipson chat --fake --fake-tool-call repo.changed_files --fake-tool-input '{"path":"."}' -q "check files"
hipson chat --provider openai-compatible --model openai/gpt-4o-mini -q "scan this repo"
hipson session list
hipson session show <session-id>
hipson session search "runtime hardening"
hipson session search "runtime hardening" --json
hipson tool list
hipson tool show repo.scan
hipson tool run repo.changed_files '{"path":"."}' --json
hipson learn propose --session-id <session-id>
hipson learn apply-memory --session-id <session-id> --proposal-id <proposal-id> --memory-dir memory

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

## Runtime Preview

The persistent runtime is local and provider-free by default. `hipson chat`
uses a deterministic local router for supported safe engineering tasks such as
repo scans, changed-file summaries, memory search, and skill listing. It runs
read-only tools through the same registry, approval, path-policy,
output-contract, redaction, and session-persistence checks used by the runtime:

```bash
hipson chat -q "scan this repo"
hipson chat -q "show changed files"
hipson chat -q "search memory for runtime approvals"
hipson chat --fake -q "offline runtime smoke"
hipson chat --fake --fake-tool-call repo.changed_files --fake-tool-input '{"path":"."}' -q "check changed files"
hipson chat --provider openai-compatible --model openai/gpt-4o-mini -q "scan this repo"
```

Unsupported default chat requests fail truthfully with the supported local
intents instead of pretending to be a general chatbot. The fake tool-call form
is an explicit offline demo path. It exercises the runtime tool-call boundary
without claiming real provider behavior.

The OpenAI-compatible provider adapter is explicit. It uses `OPENROUTER_API_KEY`
and `https://openrouter.ai/api/v1` by default, accepts `--api-key-env`,
`--provider-url`, `--model`, and `--provider-timeout`, rejects remote `http://`
provider URLs, and allows local HTTP only with `--allow-local-provider-http`.
Unit tests use stub transports; live provider smoke checks are manual and
should not be required for CI.

Runtime sessions are stored in SQLite under Hipson home by default, with
`--session-db` available for tests and local debugging. Messages, tool calls,
approval records, and approved memory summaries are redacted and bounded before
persistence. JSON search output includes `search_backend` so callers can see
whether SQLite FTS is active with fallback, or fallback-only. Observability
commands are read-only:

```bash
hipson session list --session-db ~/.config/hipson/runtime.sqlite
hipson session show <session-id> --session-db ~/.config/hipson/runtime.sqlite
hipson session search "approval" --session-db ~/.config/hipson/runtime.sqlite
hipson tool list
hipson tool show memory.search
hipson tool run repo.changed_files '{"path":"."}' --json
```

Manual tool execution is intentionally narrow: `tool run` only runs read-risk
tools that do not require approval. Write, external, exec, and dangerous tools
fail closed outside the runtime approval policy. Runtime, scheduler, and manual
tool-run decisions are recorded as bounded approval records in the session DB.
Approval records can include optional expiry metadata for future approval UX.

Learning is approval-gated. `learn propose` reads a session trajectory and
prints memory plus draft/reference-only skill candidates without writing durable
memory. Memory proposals include message, tool-call, and approval provenance.
`learn apply-memory` writes one selected memory proposal only when invoked
explicitly:

```bash
hipson learn propose --session-id <session-id>
hipson learn apply-memory --session-id <session-id> --proposal-id <proposal-id> --memory-dir memory
```

Skill proposals are reference-only drafts; Hipson does not auto-create or
auto-activate skills.

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
- runtime sessions store redacted, bounded transcripts/tool-call summaries for local debugging;
- learning proposals never become durable memory without an explicit apply command;
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
