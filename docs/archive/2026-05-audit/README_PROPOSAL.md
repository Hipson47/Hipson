# Hipson

Hipson is a local-first Python CLI for AI-assisted engineering workflows: scan a repository, compile bounded work packets, run safe local tools, inspect runtime sessions, and keep approval-gated project memory without requiring a cloud provider by default.

Hipson is best understood as a **local engineering control plane**. It is not an unrestricted shell executor, not a cloud platform, and not a general-purpose autonomous assistant.

## Current Status

This README proposal describes verified behavior in the WSL checkout at `/home/hipson47/code/Hipson`.

| Capability | Status | Notes |
|---|---:|---|
| Repository scan | Works now | Local Git/project inspection |
| Review/executor packets | Works now | Markdown packets for AI-assisted workflows |
| JSONL memory | Works now | Local file-backed memory commands |
| SQLite runtime sessions | Works now | Messages, tool calls, memories, jobs, approval records |
| Local deterministic chat router | Works now | Provider-free routing for supported safe intents |
| Safe read-only tool execution | Works now | Registry and approval-gated `hipson tool run` |
| Session list/show/search | Works now | Uses FTS plus fallback search |
| Approval-gated learning | Works now | Proposal first, explicit memory apply |
| Fake/offline provider mode | Works now | Deterministic test/demo mode |
| OpenAI-compatible provider adapter | Experimental | Explicit opt-in; unit/stub tested; live provider smoke is manual |
| Sidecar routing | Experimental | Deterministic and dry-run paths are available |
| Scheduler | Experimental | Opt-in tick model, not a daemon |
| MCP/gateway adapter | Experimental/internal | Optional adapter, not required for core use |
| Full Hermes-style real agent | Planned | Requires live-provider validation, mutation closure, and release hardening |

## Installation

From a checkout:

```bash
uv sync
uv run hipson --help
```

Editable install:

```bash
uv pip install -e .
hipson --help
```

Hipson has no required runtime dependencies beyond Python's standard library. Development and verification tools are installed through the project extras.

## Quickstart

Inspect a repository:

```bash
uv run hipson scan .
```

Ask the local deterministic router to scan the current repo:

```bash
uv run hipson chat -q "scan this repo and propose the next safe PR"
```

Show changed files through the local router:

```bash
uv run hipson chat -q "show changed files"
```

Run safe read-only tools directly:

```bash
uv run hipson tool run repo.changed_files '{"path":"."}' --json
uv run hipson tool run repo.scan '{"path":".","include_diff":false}' --json
```

Create a review packet:

```bash
uv run hipson packet review . \
  --title "Review current changes" \
  --scope "local workflow and safety" \
  -o runs/review-packet.md
```

Use local memory:

```bash
uv run hipson memory add --scope repo --repo Hipson --kind decision --summary "Keep sidecars advisory."
uv run hipson memory search "sidecar"
uv run hipson memory list
```

Use a custom memory directory:

```bash
uv run hipson memory --memory-dir ./memory add --scope repo --repo Hipson --kind decision --summary "Project-specific note."
uv run hipson memory --memory-dir ./memory search "Project-specific"
```

## Core Concepts

### Scans

`hipson scan` summarizes a repository using local files and Git metadata. It detects common project commands, recent commits, skills, and changed files.

### Packets

Packets are bounded Markdown task documents for review or implementation. They are designed to be used by AI coding sessions without handing over unnecessary repository context.

### Runtime Sessions

Hipson stores runtime sessions in SQLite. Sessions can include user messages, assistant messages, tool calls, approval records, memories, skill runs, and scheduler jobs.

```bash
uv run hipson session list
uv run hipson session show <session-id>
uv run hipson session search "runtime"
```

For isolated runs:

```bash
uv run hipson chat -q "show changed files" --session-db /tmp/hipson.sqlite
uv run hipson session list --session-db /tmp/hipson.sqlite
```

### Tools

Runtime tools are registered with explicit metadata: risk level, approval requirement, input schema, output contract, and optional path policy.

```bash
uv run hipson tool list
uv run hipson tool show repo.changed_files
uv run hipson tool run repo.changed_files '{"path":"."}' --json
```

By default, `hipson tool run` allows only safe read-risk tools that do not require approval. Write, external, exec, and dangerous tools fail closed unless a future explicit approval flow supports them.

### Local Chat Router

By default, `hipson chat` uses a deterministic local router for supported provider-free engineering intents:

- scan this repo / project
- propose the next safe PR
- show changed files
- search memory for a topic
- list skills

Unsupported prompts fail truthfully with a list of supported local intents. The local router does not claim to be a model and does not call a provider.

### Fake Mode

`--fake` is a deterministic offline provider mode for tests and demos:

```bash
uv run hipson chat --fake -q "offline runtime smoke"
```

Fake mode is not model analysis.

## Provider / API Notes

Hipson includes an experimental OpenAI-compatible provider adapter for the primary runtime. Provider use is explicit and fail-closed when configuration is missing.

The local test suite does not require provider credentials and does not make live provider calls. Live provider smoke testing should be manual, minimal, and configured so secrets are never printed or persisted.

Example shape:

```bash
export OPENROUTER_API_KEY="..."
uv run hipson chat --provider openai-compatible -q "Summarize this repository"
```

Do not treat provider output as trusted. Hipson validates provider-requested tool calls through its registry, approval, sandbox/path, output-contract, redaction, and bounded-persistence boundaries.

## Safety Model

Hipson is designed around conservative local execution:

- provider-free by default
- no unrestricted shell execution
- explicit tool registry
- tool risk levels
- approval policy
- sandbox/path policy
- sensitive path refusal
- bounded and redacted output persistence
- explicit memory apply workflow
- fake/offline mode clearly labeled
- provider errors redacted and bounded

Unsafe tool classes are blocked by default from public execution paths.

## Sidecars

Sidecars are optional routing helpers for review, architecture, frontend, security, and related workflows.

```bash
uv run hipson sidecar list
uv run hipson sidecar route --task "security review of current changes" --risk security
uv run hipson sidecar route --task "security review" --risk security --llm --llm-dry-run
```

Dry-run mode previews provider requests without making a live call.

## Scheduler

The scheduler is experimental and opt-in. It is a local tick-based job runner, not a background daemon.

```bash
uv run hipson scheduler create --tool repo.changed_files --input '{"path":"."}'
uv run hipson scheduler list
uv run hipson scheduler tick
```

Only safe registry tools should be used until durable approval workflows are fully validated for broader risk classes.

## Learning

Hipson can propose learning from a session and explicitly apply a selected memory note.

```bash
uv run hipson learn propose --session-id <session-id>
uv run hipson learn apply-memory \
  --session-id <session-id> \
  --proposal-id <proposal-id> \
  --memory-dir ./memory
```

Learning proposals do not persist durable memory by default. Skill proposals are draft/reference only unless a separate explicit apply workflow is implemented.

## Verification

Common local checks:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src/hipson
uv run bandit -q -r src/hipson -c pyproject.toml
python -m compileall src/hipson scripts tests
uv run python scripts/run_tests.py
uv run hipson doctor
uv run hipson skill validate
```

Build and installed-wheel smoke:

```bash
uv build
python -m venv /tmp/hipson-wheel-smoke
/tmp/hipson-wheel-smoke/bin/pip install dist/*.whl
/tmp/hipson-wheel-smoke/bin/hipson --help
```

## Known Limitations

- Live provider operation is not required for local use and should be treated as experimental until manually validated.
- Mutation testing currently has unresolved survivors, timeouts, and not-checked mutants in runtime-critical modules.
- CI configuration is strong, but a release should be based on an observed clean CI run.
- Scheduler and MCP/gateway integrations are limited and experimental.
- Sidecar provider calls require explicit configuration and should be tested carefully before operational use.
- Hipson is not a shell automation framework.
- Hipson is not yet a complete Hermes-style real-agent runtime.

## Roadmap

Near-term:

1. Reposition package maturity and release docs honestly.
2. Triage high-risk mutation survivors in approvals, sandbox, prompt, redaction, registry, runtime, providers, and learning.
3. Add a manual live-provider smoke checklist that never runs in unit tests.
4. Tighten provider/tool-call regression tests.
5. Keep README, scorecards, and audit docs synchronized with verified behavior.

Later:

1. Strengthen durable approval workflows.
2. Expand provider-backed tool calling after safety gates are proven.
3. Improve session and memory search ergonomics.
4. Add richer skill draft review/apply workflows.
5. Validate optional scheduler and MCP/gateway layers.

## Development Notes

Canonical WSL working tree:

```text
/home/hipson47/code/Hipson
```

Avoid stale duplicate Windows-mounted checkouts for active development.

## License

See `LICENSE`.

