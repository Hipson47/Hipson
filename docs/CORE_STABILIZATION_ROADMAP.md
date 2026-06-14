# Hipson Core Stabilization Roadmap

This roadmap turns Hipson's near-term direction into verifiable local work.
The product center is a Codex-first local control plane: route the task, scan
the repo, create a bounded packet or execute locally, verify with real commands,
then record a compact handoff or memory note.

## Product Direction

Hipson should optimize for trust over autonomy spectacle:

- local commands must work without API keys;
- provider and sidecar paths must be explicit opt-in;
- AI quality passes should raise review and implementation quality through
  bounded packets, explicit model choice, and dry-run previews;
- packets must be bounded, redacted, and treated as untrusted context by
  downstream agents;
- Hermes remains optional intake/status infrastructure, not the coding control
  surface;
- every meaningful outcome should point back to repo state, diff, verification
  output, and human-reviewed decisions.

## Daily Codex Workflow

Default loop:

```bash
hipson work --task "security review of auth"
```

`hipson work` is the preferred front door for ordinary Codex work. It builds a
provider-free work brief with:

- deterministic route result;
- redacted delta scan;
- review or executor packet command;
- curated skills and sidecar hints;
- verification commands;
- memory/handoff command;
- audit contract and known unknowns.

When a task benefits from a second opinion, keep the AI layer explicit:

```bash
hipson work --task "review current diff for test gaps" --free-ai
hipson work --task "review release risk" --ai-model openrouter/free
```

These flags prepare advisory sidecar commands for bounded packets. They do not
make provider calls by themselves.

Use `hipson route --task "..."` when only the low-level routing decision is
needed. Use `hipson hermes intake ...` only when a task needs cross-session
status, scheduling, Telegram/gateway dispatch, or bus events.

## Core Release Claims

Allowed claims:

- local-first CLI;
- provider-free default operation;
- deterministic workflow routing;
- bounded review/executor packet generation;
- redacted scans, packets, sessions, provider errors, and sidecar reports;
- explicit provider mode with stub-tested safety boundaries;
- optional Hermes bridge for intake/status only.

Claims that require fresh release evidence:

- production/stable package status;
- live provider readiness;
- Telegram gateway readiness;
- complete mutation closure;
- sidecar output correctness.

## CI And Verification Gates

Release candidates should record:

```bash
uv run ruff check .
uv run mypy src/hipson
uv run bandit -q -r src -c pyproject.toml
uv run pip-audit
uv run python scripts/run_tests.py
uv run python -m pytest -q
uv run python -m compileall src scripts tests
uv build
uv run hipson doctor
uv run hipson skill validate
uv run hipson work --task "release verification" --no-diff --json
```

CI should keep `hipson work` in both editable and installed-wheel smoke checks
so the daily workflow remains package-safe.

## Mutation Triage

Mutation work should stay focused on observable safety boundaries rather than
line-by-line implementation mirroring. Prioritize:

- approval fail-closed behavior;
- sensitive path and traversal rejection;
- packet redaction and output bounding;
- provider URL validation and error redaction;
- prompt untrusted-data delimiter escaping;
- session persistence redaction/bounding;
- explicit-only memory and learning application.

The current status is intentionally not "complete mutation closure." Use
`docs/MUTATION_TRIAGE_NOTES.md` as the running ledger.

## Skills And Sidecar Curation

The library is already broad enough for current Hipson work. Favor quality and
routing clarity over adding more folders.

Default pairings:

| Work type | Primary skill | Optional sidecar |
|---|---|---|
| Repo state | `repo-delta-scan` | none |
| Implementation | `executor-packet` | `coder_review_cheap` after diff |
| Free first-pass quality review | `review-packet` or `executor-packet` | `reviewer_free` or `coder_review_free` |
| Security review | `review-packet`, `security-threat-model` | `reviewer_cheap` |
| Architecture decision | `review-packet`, `skill_reasoning-decomposition` | `architect_strong` |
| UI polish | `hipson-premium-ui-ux` | `premium_ui_ux` |
| Visual/motion direction | `hipson-visual-experience-director`, `hipson-creative-frontend-motion-architect` | `visual_experience_director` |
| Handoff/progress | `handoff`, `memory` | `memory_summarizer_cheap` |

Sidecars remain advisory. Do not send secrets, broad logs, unbounded diffs, or
sensitive files to provider-backed sidecars.

## Auditability Standard

Every completed work item should be able to answer:

- What task was routed, and which mode/risk did Hipson select?
- Which files changed?
- Which packet or local execution scope was used?
- Which verification commands actually ran?
- What is still unknown or unverified?
- Was any provider, sidecar, or Hermes bridge involved?
- What compact handoff or memory note should persist?

If that information cannot be reconstructed from local output, the workflow is
not yet trustworthy enough for release-grade use.
