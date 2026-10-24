# Hipson Next Two-Week Execution Plan

This plan covers the next 10 working days after the first AI-dev evidence loop.
The goal is to turn Hipson from an auditable planner into a practical AI-dev
quality workbench.

## Sprint Goal

Ship the first usable AI Review Control Kit:

```text
work -> model profile -> packet preflight -> sidecar -> verify -> quality report -> evidence/audit
```

The sprint should preserve Hipson's core trust contract:

- local-first by default;
- provider calls are explicit;
- packets are bounded and redacted;
- model output is advisory;
- verification and human review remain authoritative.

## Week 1

### Day 1: Model Profiles

Deliver:

- curated `model_profiles.json`;
- `hipson model profile list/show/recommend`;
- `hipson work --ai-profile <name>`;
- tests for profile recommendation and work-plan integration.

Done in the first implementation slice:

- `free_probe`, `cheap_review`, `code_review`, `cheap_memory`,
  `long_context`, `strong_arch`, `ui_visual`, and `security_gate` profiles;
- packaged profile config under runtime assets;
- profile-backed AI quality pass commands;
- profile policy summaries and blocking for unsafe profile/task combinations.

### Day 2: Packet Preflight

Deliver:

- `hipson packet preflight <path>`;
- local-only checks for missing files, sensitive paths, size bounds, redaction
  changes, packet hash, and cautions;
- tests for redaction and sensitive path refusal.

Done in the first implementation slice:

- local packet preflight without provider calls;
- JSON artifact support for future evidence attachment;
- `hipson work` includes packet preflight as the required gate before sidecar
  preview or run commands.

### Day 3: Quality Report MVP

Deliver:

- `hipson quality report --work ... --verify ... --sidecar ...`;
- report sections for verified findings, advisory findings, unverified claims,
  required local checks, and human decision;
- redacted sidecar excerpts and hashes.

Done in the first implementation slice:

- local quality report correlation across work, verification, and optional
  sidecar artifacts;
- JSON and Markdown rendering;
- blocked quality gate and non-zero CLI exit when local verification is missing
  or failed;
- visible `Rejected Or Unverified` section in Markdown reports.

### Day 4: Workflow Docs And Demo

Deliver:

- README quick path for AI Review Control Kit;
- docs update for AI-dev workflow;
- one clean recorded demo command chain using generated `runs/*.json` artifacts.

Subagent adjustment:

- keep the canonical path above the broad command catalog:
  `work -> model profile -> packet preflight -> sidecar -> verify -> quality report -> evidence/audit`;
- keep Hermes and scheduler status flows secondary in the first user journey.

### Day 5: Hardening Pass

Deliver:

- negative tests for unsafe model/profile choices;
- packet preflight boundaries for oversized files;
- quality report behavior for failed or missing verification;
- release claim update.

Partially done in the first hardening pass:

- unsafe `--ai-profile` selection is blocked for mismatched tasks;
- sensitive-context model recommendation refuses provider profiles;
- quality report returns a blocked gate when verification is missing.

## Week 2

### Day 6: Sidecar Report Structure

Deliver:

- parse Hipson sidecar report metadata;
- record sidecar report hash and model/profile in quality report;
- keep provider output wrapped as untrusted data.

Done in the second implementation slice:

- sidecar reports include a trusted local `## Metadata` JSON block;
- provider output remains wrapped in `untrusted_data`;
- `hipson quality report` extracts sidecar agent/model/packet metadata and
  advisory finding IDs without treating provider output as proof.

### Day 7: Provider Model Discovery Design

Deliver:

- design doc for OpenRouter model discovery and local allowlists;
- no automatic provider calls in default workflow;
- `provider doctor` extension plan for optional live discovery.

Done in the second implementation slice:

- `docs/OPENROUTER_MODEL_DISCOVERY.md` defines explicit metadata-only discovery,
  local cache shape, model allowlist shape, and future `provider doctor`
  checks;
- default `work`, `verify`, `quality report`, `quality eval`, and evidence
  workflows remain provider-free.

### Day 8: Golden Packet Eval Harness

Deliver:

- small fixture set of review/executor packets;
- local scoring for hallucinated files, invented commands, missing verification,
  and empty output;
- no live provider requirement in CI.

Partially done in the second implementation slice:

- `hipson quality eval --packet ... --sidecar ... --verify ...` scores sidecar
  reports locally;
- the eval catches empty output, missing structured findings, hallucinated file
  references, repo-mismatched command recommendations, and missing verification;
- no provider key or live network call is required.

### Day 9: Workflow Pack Packaging

Deliver:

- AI Review Control Kit command recipe;
- Security Review Pack recipe;
- Implementation Handoff Pack recipe;
- docs that explain which pack to use when.

### Day 10: Release Evidence

Deliver:

- full verification run;
- evidence/audit bundle for the sprint;
- updated claim matrix;
- next two-week backlog based on results.

Partially done in the evidence integration slice:

- `hipson evidence append` accepts `--quality-report` and `--quality-eval`;
- evidence records include `quality.report`, `quality.eval`, and
  `quality.summary`;
- audit bundles expose `latest_quality_gate` and `latest_eval_ok`.

## Acceptance Criteria

The sprint is successful when:

- `hipson work --ai-profile free_probe` prepares an explicit advisory quality
  pass without provider calls;
- `hipson packet preflight` catches sensitive or oversized packets locally;
- `hipson quality report` can correlate work, verification, and sidecar output;
- `hipson quality report` returns non-zero when verification is missing or
  failed;
- evidence/audit records include quality report and eval artifacts when they are
  provided;
- all new commands are covered by CLI smoke tests;
- `uv run python -m pytest -q`, `uv run ruff check .`, `uv run mypy src/hipson`,
  `uv run hipson doctor`, `uv run hipson skill validate`, and `uv build` pass;
- docs clearly state that provider/model output is advisory and local evidence
  remains authoritative.

## Next Backlog After This Sprint

- Provider model discovery with explicit opt-in and local allowlists.
- Fixture-backed sidecar eval harness with golden packets.
- Packet schema JSON validation.
- Explicit provider discovery cache commands.
- Optional team policy packs for allowed profiles and sensitive-context rules.
