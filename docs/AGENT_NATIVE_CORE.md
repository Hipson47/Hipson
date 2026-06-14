# Agent-Native Core

Agent-Native Core is the stable local layer that lets coding agents work with a
repository without turning model output into authority.

Hipson's role is to:

- expose a first-class agent contract;
- route work into bounded local workflows;
- create packets instead of sending whole repositories;
- run local verification;
- record evidence and audit bundles;
- keep memory compact and explicit;
- preserve local-first, provider-free defaults.

## Agent Contract

The machine-readable contract is available through:

```bash
hipson contract show --json
```

The JSON includes:

- `artifact_kind: hipson.agent_contract`;
- `schema_version`;
- `repo_state`;
- `supported_workflows`;
- `available_command_surfaces`;
- `artifact_types`;
- `risk_policy`;
- `path_write_policy`;
- `provider_policy`;
- `memory_policy`;
- `verification_policy`;
- `adapter_capabilities` for `codex`, `hermes`, `mcp_future`, and `mcp_stdio`.

Codex remains the primary coding interface. Hermes is optional status, intake,
Telegram, scheduler, and long-flow infrastructure. Future MCP adapters should
read the contract and artifacts instead of inventing their own trust model.

## Artifact Contracts

Generated JSON artifacts carry `artifact_kind` so downstream agents and adapters
can route them safely:

- `hipson.work_plan`;
- `hipson.packet_preflight`;
- `hipson.verification`;
- `hipson.quality_report`;
- `hipson.quality_eval`;
- `hipson.evidence_record`;
- `hipson.audit_bundle`.
- agent bootstrap, install, policy, doctor, review-kit, autopilot, and MCP
  catalog artifacts used by agent integrations.

Schemas live under `schemas/` and are intentionally structural. They define the
required control-plane fields while leaving room for compatible additions.

## Path And Write Policy

Generated artifacts are expected under generated artifact directories such as
`runs/`, `scans/`, `docs/`, or `memory/`. Commands reject path traversal, broad
home/profile paths, and sensitive paths such as env files, keys, and local
databases.

The explicit override is:

```bash
--allow-unsafe-output
```

Use it only when the caller intentionally wants to write outside the generated
artifact policy.

## Provider Policy

Core commands are provider-free:

- `contract`;
- `work`;
- `packet preflight`;
- `verify`;
- `quality report`;
- `quality eval`;
- `evidence`;
- `audit`.

Provider-backed calls are explicit and live on surfaces such as `sidecar run`,
`sidecar route --llm`, and `chat --provider`. Free or model-selected sidecars
are advisory only and cannot approve work or release claims.

## Gate Semantics

Hipson separates local proof from review signals:

- `verification_gate`: passed only when selected local commands exit 0.
- `sidecar_eval_gate`: sidecar output is passed, blocked, unverified, or not
  applicable.
- `human_decision_gate`: the human decision is pending, passed, or blocked.
- `release_claim_gate`: release claims remain blocked unless the other gates
  support them.

A passed verification artifact does not verify sidecar findings. Sidecar output
must be checked against local files, tests, and human review before it can
support a release or security claim.

## Control Kit Workflow

The AI Review Control Kit workflow is:

```text
current diff -> work plan -> packet -> preflight -> optional sidecar -> verify -> quality report/eval -> evidence -> audit
```

Run it through:

```bash
hipson kit review --project . --task "review current diff" --json
```

The command writes a single `runs/<work_id>/` directory with:

- `contract.json`;
- `work.json`;
- `review-packet.md`;
- `preflight.json`;
- `verify.json`;
- `quality.json`;
- optional `quality-eval.json` when a sidecar report exists;
- `evidence.jsonl`;
- `audit.json`;
- `summary.md`.

Use it as the canonical path for agent-native full-stack development and
handoff. The workflow is intentionally local-first. Provider calls are optional,
explicit, and bounded by packet preflight. `--ai-profile <name>` prepares the
advisory sidecar path; the real provider call requires `--run-sidecar`.

Verification profiles make repeated agent work predictable:

- `--verify-profile quick` runs the first planned local verification command.
- `--verify-profile full` runs every command listed in `work.json`.
- `--verify-profile release` also runs every planned command, but labels the run
  as release-oriented evidence for downstream review.

If a run is interrupted, resume the existing run directory:

```bash
hipson kit review resume --run runs/<work_id> --rerun-step verify --json
```

Resume fills missing `contract.json`, `preflight.json`, `verify.json`,
`quality.json`, `quality-eval.json`, `evidence.jsonl`, `audit.json`, or
`summary.md` without rebuilding the existing work plan or packet. Missing
`work.json` or `review-packet.md` requires a new run.

Autopilot uses the same artifact model:

```bash
hipson autopilot review --task "review current diff" --json
hipson autopilot implement --task "implement bounded parser fix" --allowed-edit src/hipson/parser.py,tests --json
hipson autopilot resume --run runs/<work_id> --rerun-step verify --json
```

Project policy is checked before autopilot runs. Denied-path changes, invalid
policy, local-only sidecar blocks, and prompt-required operations stop the
workflow before packets or provider calls are used.
