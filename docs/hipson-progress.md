# Hipson Progress

Historical progress reference. Verification counts and machine-readiness claims
below are earlier recorded snapshots, not current checkout evidence. Active work
is tracked in the [task ledger](TASK_LEDGER.md); use the [documentation index](README.md)
and [changelog](../CHANGELOG.md) for current interfaces and unreleased changes.

## Recorded Goal
Build this repository into a portable, git-ready Hipson Orchestrator Hub for cross-repo AI workflow coordination, delta review, sidecar agents, and durable handoff.

## Recorded State
- The repository has its own `.git` root and no longer relies on a parent user-profile git root.
- Python package entrypoint `hipson` lives under `src/hipson/`.
- Runtime assets are bundled under `src/hipson/assets/` so installed packages can run without a source checkout.
- Runtime asset lookup ignores untrusted CWD files unless `HIPSON_DEV_ROOT` is explicitly set to a valid source checkout.
- `repos.yaml` is local-only and ignored.
- `repos.example.yaml` is the portable template for other developers.
- OpenRouter provider keys are not versioned; use exported variables or ignored
  local `.env` / Hipson-home `agents.env` files.
- Generated `runs/`, `scans/`, and `memory/*.jsonl` artifacts are ignored by default.
- Packet generation is compiled through structured packet specs.
- `hipson work` creates a provider-free Codex work brief that joins route,
  scan, packet, verification, memory/handoff, skills, and audit guidance.
- `hipson work` can prepare an explicit AI quality pass through `--free-ai`,
  `--ai-model`, or `--ai-agent` while keeping the default provider-free.
- `hipson work --work-output`, `hipson verify run`, `hipson evidence`, and
  `hipson audit` provide the first local evidence loop for AI-dev work.
- `hipson provider doctor` checks provider and agent readiness without sending
  repository data.
- `hipson model profile`, `hipson packet preflight`, and `hipson quality report`
  start the AI Review Control Kit layer for curated model choice, packet safety,
  and evidence-backed quality reporting.
- Local JSONL memory is available through `hipson memory add`,
  `hipson memory search`, and `hipson memory list`.
- Sidecar agents include routing metadata and can be suggested with `hipson sidecar route`.
- Optional LLM routing is available behind `hipson sidecar route --llm` and sends only redacted routing summaries.
- Canonical source docs live under `knowledge/source/`.
- Hermes Agent is integrated as an intake/scheduling/status bridge through
  `hipson hermes`, with bus events under `~/.config/hipson/hermes-bus/events.jsonl`
  and an installable Hermes skill at `~/.hermes/skills/hipson-codex-orchestrator/SKILL.md`.

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
- Fail hard for invalid scan paths instead of returning a misleading clean/unavailable report.
- Keep memory writes orchestrator-owned and redacted; agents receive bounded packet context.
- Use deterministic routing metadata by default; use model-based routing only behind an explicit flag.
- Use Hermes as an orchestration layer only; Hipson remains the workflow authority
  and Codex remains responsible for bounded implementation/review/verification.
- Keep Codex as the primary control surface. Users continue working from Codex;
  Codex decides when Hermes is useful for status tracking, scheduling,
  Telegram/gateway dispatch, or async bus events.
- Prefer `hipson work --task "..."` for daily Codex work; use `hipson route`
  when only the routing decision is needed.
- Keep release claims honest: local-first core can be claimed, but
  production/stable, live-provider, Telegram gateway, and complete mutation
  readiness require fresh evidence.
- Curate skills by task fit instead of adding broad skill dumps. UI/motion,
  security review, implementation, and handoff each have small preferred sets.
- Treat AI as a quality multiplier rather than hidden automation: free or
  model-selected OpenRouter sidecars are opt-in, bounded, redacted, and advisory.
- Develop Hipson as an AI-dev-first local trust layer. The
  [archived expansion plan](archive/2026-q2-planning/AI_DEV_FIRST_EXPANSION_PLAN.md)
  records the earlier product roadmap for subagent orchestration, model profiles,
  evidence, and workflow packs; it is historical background.
- Current AI Review Control Kit state: `hipson work --ai-profile` prepares
  model-profile sidecar commands, includes packet preflight before sidecar use,
  blocks unsafe profile/task combinations, and `hipson quality report` returns a
  blocked gate when local verification is missing or failed.
- Second AI Review Control Kit slice: sidecar reports now include local metadata
  blocks, `hipson quality report` extracts sidecar agent/model/packet metadata
  and finding IDs, and `hipson quality eval` performs provider-free checks for
  empty sidecar output, missing findings, hallucinated file references,
  repo-mismatched commands, and missing verification.
- OpenRouter discovery direction is captured in
  `docs/OPENROUTER_MODEL_DISCOVERY.md`: discovery is explicit metadata refresh
  with local cache/allowlist policy, never a hidden provider call in default
  workflows.
- Evidence/audit integration now accepts quality artifacts:
  `hipson evidence append --quality-report ... --quality-eval ...` stores
  `quality.report`, `quality.eval`, and `quality.summary`; audit bundles expose
  `latest_quality_gate` and `latest_eval_ok`.

## Historical Verification
- `uv run ruff check .`: passed.
- `python3 scripts/run_tests.py`: passed, 63/63 tests.
- `python3 -m compileall src scripts tests`: passed.
- `uv build`: passed.
- Wheel install smoke from a temporary venv outside the repo, including fake-CWD asset shadowing checks: passed.
- Editable install smoke checks were recorded as passed for help, doctor, scan,
  memory, sidecar routing, skill validation, and Codex install previews. Current
  routing syntax requires a task: `hipson sidecar route --task "review current diff"`
  or `hipson sidecar route --task "review current diff" --llm --llm-dry-run`.
- `bash -n src/hipson/assets/codex-workflow-kit/install.sh`: passed.
- `python3 -m json.tool config/agents.json`: passed.
- Secret scan excluding generated reports and test fixtures: passed.
- Sensitive `.env` packet refusal: passed.
- `hipson hermes doctor --json`: passed; Hermes CLI, config, bus, and Hipson skill are present.
- `hermes doctor`: passed with remaining manual provider/API-key setup only.
- `hermes skills list`: passed; `hipson-codex-orchestrator` is enabled as a local skill.
- `uv run pytest -q`: passed, 239/239 tests.
- `uv run mypy src/hipson`: passed.
- `uv run ruff check .`: passed.
- `git diff --check`: passed.
- `uv run ruff check .`: passed after adding `hipson work`.
- `uv run mypy src/hipson`: passed, 36 source files.
- `uv run python -m pytest -q`: passed, 244/244 tests.
- `uv run python scripts/run_tests.py`: passed, 244/244 tests.
- `uv run hipson doctor`: passed.
- `uv run hipson skill validate`: passed, 53 skills checked.
- `uv run hipson work --task "security review of auth" --no-diff --json`
  produced valid JSON.
- `python3 -m json.tool config/agents.json` and packaged agent config: passed.
- `python -m compileall src scripts tests`: passed.
- `uv build`: passed and included `src/hipson/workflow.py` in the wheel.
- `git diff --check`: passed.
- `hipson work --task "review current diff for test gaps" --free-ai --json`
  now exposes the advisory `ai_quality` contract; this prepares commands without
  calling a provider.

## Risks
- `repos.yaml` must stay local because it may contain developer-specific absolute paths.
- Sidecar packets can still contain sensitive project context if users manually create them poorly.
- Cheap paid models can produce false positives; Architect must verify findings.
- Telegram control still needs a BotFather token and Telegram allowlist/pairing in
  `~/.hermes/.env`; do not add bot tokens to Hipson memory, packets, or bus events.
- Full mutation survivor triage remains open. Current mutmut targets are
  `approvals.py`, `redaction.py`, and `sandbox.py`, not `hipson work`; complete
  mutation closure is not claimed.
- Live provider smoke is still manual and credential-gated.
- Free/model-selected sidecars can produce noisy or wrong advice; their output
  must never replace local verification or human review.

## Historical Next-Task Plan
The [two-week execution plan](archive/2026-q2-planning/NEXT_TWO_WEEK_EXECUTION_PLAN.md)
is archived and no longer assigns current work. It proposed fixture-backed
packet evals, model-discovery cache commands, evidence integration, and focused
mutation batches. Consult the task ledger for the current assignment.

## Handoff Notes
For a fresh local setup:

```bash
cp repos.example.yaml repos.yaml
cp .env.example .env
python3 scripts/run_tests.py
hipson doctor
```
