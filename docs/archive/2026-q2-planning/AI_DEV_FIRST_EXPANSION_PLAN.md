# Hipson AI-Dev-First Expansion Plan

This plan turns Hipson from a classic developer CLI with AI features into a
local-first operating layer for AI developers.

It synthesizes five read-only subagent reviews:

- product strategy and AI developer persona;
- architecture and CLI roadmap;
- subagent, skill, and model orchestration;
- trust, verification, and auditability;
- onboarding, GTM, and daily/weekly rituals.

## Executive Decision

Hipson should be built as an AI development control plane, not as another
autonomous coding agent and not as a normal dev tool with model calls added.

The core product promise:

> Hipson helps an AI developer route work, bound context, choose the right
> skill/model/sidecar, verify with local evidence, and preserve a compact
> handoff that explains what happened and what is still unknown.

The winning product loop is:

```text
intent -> route -> scan -> packet -> packet preflight -> AI quality pass -> verify -> quality report -> evidence -> memory/handoff
```

Codex remains the primary control surface. Hipson becomes the local workflow
authority. Hermes remains optional intake, status, scheduling, Telegram, and
long-flow infrastructure.

## AI Developer Persona

The first user is not a generic developer. The first user is an AI-native
developer, staff engineer, founder, consultant, or small-team lead who already
delegates implementation, review, docs, and verification to coding agents.

This user does not mainly need another command runner. They need control over:

- what context an agent received;
- which skill, sidecar, and model were selected;
- whether private or sensitive context stayed local;
- which tests, linters, type checks, and scans actually ran;
- which model findings were confirmed, rejected, or left unknown;
- what should be remembered without keeping a raw transcript.

Hipson should make that control obvious, repeatable, and exportable.

## Product Principles

1. Local-first is the default.
   Core commands work without provider keys and without hidden network calls.

2. AI is a quality layer, not hidden automation.
   Provider-backed work is explicit, packet-bound, dry-run visible, and advisory.

3. Context is a product surface.
   Hipson should treat packets, redaction, context budgets, and sensitivity
   classes as first-class artifacts.

4. Verification beats confidence.
   Model output can suggest risk, but only local evidence and human review can
   support claims.

5. Curation beats catalog size.
   The product should explain which skill, sidecar, and model profile fits the
   current work, not expose a giant undifferentiated skill list.

6. Memory is provenance, not transcript storage.
   Persist decisions, verification, changed files, risks, and next steps.

## What Makes Hipson Different

Most developer tools optimize old workflows: edit, run tests, open PR, maybe
ask an AI assistant. Hipson should optimize the AI-dev workflow:

- agent work starts from an explicit task and acceptance criteria;
- Hipson creates bounded packets instead of broad repo dumps;
- packet preflight blocks unsafe provider handoffs before any sidecar call;
- sidecars and free models are used as second opinions, not authorities;
- quality reports compare model claims against local commands;
- evidence records show what actually happened;
- handoffs preserve the final state for the next agent session.

The product category should be framed as:

> Local-first trust layer for AI development.

Avoid framing Hipson as:

- an autonomous engineer;
- a replacement for Codex;
- a cloud agent platform;
- an enterprise governance suite today;
- a tool where model output proves correctness.

## Core Product Surfaces

### 1. Hipson Work Brief

`hipson work` should remain the daily front door.

Near-term direction:

- add `work_id`, `created_at`, `repo_root`, `git_head`, and `diff_hash`;
- allow `--work-output runs/work.json`;
- keep the human-readable Markdown brief;
- make the JSON contract stable enough for Codex, sidecars, and future tools.

The Work Brief is a plan, not proof. It should say what to run and why, but it
must not imply verification has passed before commands actually execute.

### 2. Hipson Verify

Add a verification command that executes selected commands from a work plan and
writes redacted, bounded evidence artifacts.

Proposed command:

```bash
hipson verify run --work runs/work.json
```

The output should include command, cwd, exit code, duration, bounded stdout and
stderr references, status, and hashes.

### 3. Hipson Evidence Ledger

Add a local JSONL evidence ledger with hash-chain records.

Proposed commands:

```bash
hipson evidence append --work runs/work.json --verification runs/verify.json
hipson evidence show --latest
hipson evidence export --work <work-id>
```

Evidence records should connect task, repo state, route, packet, provider use,
verification, mutation status, claims, unknowns, and human decision.

Minimal record shape:

```json
{
  "schema_version": "1.0",
  "event_id": "...",
  "previous_hash": "...",
  "record_hash": "...",
  "created_at_utc": "...",
  "task": "...",
  "repo": {"path": "...", "head": "...", "branch": "...", "dirty": true},
  "route": {"mode": "...", "risk": "...", "recommended_skill": "..."},
  "packet": {"mode": "review", "path": "...", "sha256": "..."},
  "provider": {"used": false, "agent": null, "model": null},
  "verification": [{"command": "...", "exit_code": 0, "artifact_path": "..."}],
  "claims": {"safe": [], "unsafe": [], "evidence_refs": []},
  "unknowns": [],
  "human_decision": {"required": true, "outcome": "pending"}
}
```

### 4. Hipson Quality Pass

The existing `--free-ai`, `--ai-model`, and `--ai-agent` direction is correct.
The next step is a local quality report that compares AI findings with
verification evidence and human decisions.

Proposed command:

```bash
hipson quality report --work runs/work.json --sidecar runs/sidecar.md --verify runs/verify.json
```

The report should separate:

- verified findings;
- rejected findings;
- plausible but unverified findings;
- hallucinated files, commands, or claims;
- required local checks;
- final human decision.

### 5. Model Profiles

Hipson should not hardwire "agent equals model." It should introduce model
profiles that can choose or constrain models by task, cost, context, and
sensitivity.

Recommended profiles:

| Profile | Purpose | Default posture |
|---|---|---|
| `free_probe` | No-cost first pass on low-risk packets | Opt-in only |
| `cheap_review` | Normal review and implementation sanity checks | Preferred paid lane |
| `cheap_memory` | Handoff and changelog compression | No quality approval |
| `long_context` | Large docs or multi-repo synthesis | Explicit budget warning |
| `strong_arch` | High-risk architecture and security decisions | Escalation only |
| `ui_visual` | Screenshot-backed visual and UX review | Requires visual artifacts |
| `security_gate` | Auth, secrets, data loss, provider safety | Human-owned final gate |

OpenRouter free models should remain explicit and low-stakes. Because free model
availability can change, Hipson should eventually add provider discovery,
allowlists, smoke tests, and recording of the actual model used.

The first discovery design is captured in
`docs/OPENROUTER_MODEL_DISCOVERY.md`: discovery is explicit metadata refresh,
not hidden model execution, and model profiles remain the curated decision
surface.

### 6. Curated Subagent And Skill Roster

Hipson should make subagent choice a product feature.

| Lane | Required skills or packet source | Sidecar or agent | Evidence expectation |
|---|---|---|---|
| Repo orientation | `repo-delta-scan` | none | scan summary and changed files |
| Implementation | `executor-packet`, AI coding workflow skill | `coder_review_cheap` after diff | tests from acceptance criteria |
| Free first pass | review or executor packet | `reviewer_free`, `coder_review_free` | advisory gaps only |
| Code review | `review-packet` | `reviewer_cheap` | file-backed findings |
| Architecture | reasoning decomposition skill | `critic_lite`, `architect_strong` | tradeoffs and decision record |
| Security | security threat-model skill | `security_gate`, `reviewer_cheap` | local verification and human signoff |
| UI and motion | premium UI/UX and visual skills | `premium_ui_ux`, `visual_experience_director` | screenshots and viewport checks |
| Handoff | handoff and memory conventions | `memory_summarizer_cheap` | compact verified memory |

Subagent prompts should be assembled from skills and bounded evidence. They
should include role, goal, selected skills, target files, constraints, expected
output format, and verification expectations.

### 7. Workflow Packs

Workflow packs should become the first productized layer above the OSS core.

P0 packs:

- AI Review Control Kit: current diff -> packet -> sidecar -> verify -> report.
- Implementation Handoff Pack: acceptance criteria -> executor packet -> test
  freeze -> implementation review -> memory.
- Release Evidence Kit: CI, build, package smoke, claim matrix, evidence export.

P1 packs:

- Security Review Pack: auth, secrets, provider safety, data-loss checks.
- Visual QA Pack: screenshot-backed UI/UX and responsive review.
- Team Trust Pack: policy templates, memory conventions, audit exports.

Hermes should be a pack for async status, scheduling, Telegram, and long-running
flows, not the center of coding work.

## 30/60/90 Day Roadmap

### Days 1-30: Trust And First Run

Goal: ten minutes from install to a useful, auditable Work Brief.

- stabilize `hipson work` as the daily AI-dev cockpit;
- add `WorkRun` and `PacketManifest` contracts;
- add `--work-output runs/work.json`;
- add `hipson provider doctor` without sending repo data;
- document the first-run path: `doctor -> work -> packet -> verify -> handoff`;
- keep claims limited to local-first, provider-free, explicit AI quality beta.

### Days 31-60: Evidence And Quality

Goal: make Hipson prove what happened.

- add `hipson verify run`;
- add evidence ledger JSONL with hashes;
- add `hipson audit show/export`;
- add `hipson quality report`;
- add agent config validation and negative tests;
- add packet schema validation, redaction stats, and sensitivity checks;
- run pilots on review, implementation handoff, and release/security review.

### Days 61-90: Productized AI-Dev Workbench

Goal: ship the first clear product wedge for AI developers.

- package AI Review Control Kit as the default wedge;
- add model profiles and OpenRouter discovery/allowlist smoke checks;
- add sidecar eval harness with golden packets;
- add Team Trust Pack templates;
- add Visual QA Pack for screenshot-backed frontend work;
- connect runtime sessions, memory, packets, sidecar reports, and evidence by
  stable IDs;
- prepare launch narrative and pilot metrics.

## Implementation Backlog

P0 code changes:

1. Add `src/hipson/contracts.py` with versioned contracts for `WorkRun`,
   `PacketManifest`, `VerificationResult`, `SidecarReport`, and `AuditBundle`.
2. Extend `build_work_plan()` with IDs, timestamps, repo metadata, hashes, and
   `--work-output`.
3. Add `src/hipson/verification.py` and `hipson verify run --work ...`.
4. Add audit bundle support and `hipson audit show/export`.
5. Add `src/hipson/agent_config.py` validation for `config/agents.json`.

Initial P0 implementation note:

- `contracts.py`, `verification.py`, `evidence.py`, and `provider_doctor.py`
  now provide local JSON work/evidence artifacts without provider calls.
- `hipson work --work-output`, `hipson verify run`, `hipson evidence`, and
  `hipson audit` establish the first auditable work loop.
- `hipson provider doctor` checks provider URLs, key presence, and agent config
  readiness without sending repository data.
- SQLite-backed work/session tables, richer agent config schemas, and model
  profile discovery remain follow-up work.

Initial P1 implementation note:

- `config/model_profiles.json` and packaged runtime profiles now define curated
  AI quality lanes.
- `hipson model profile list/show/recommend` exposes profile selection.
- `hipson work --ai-profile <name>` prepares profile-backed advisory sidecar
  commands without provider calls.
- `hipson packet preflight` performs local safety checks before sidecar use.
- `hipson quality report` correlates work, verification, and optional sidecar
  artifacts.
- `docs/NEXT_TWO_WEEK_EXECUTION_PLAN.md` tracks the 10-working-day execution
  plan for the AI Review Control Kit.

P1 code changes:

1. Add `packet.schema.json` and packet preflight checks.
2. Add model profiles and provider discovery with local allowlists.
3. Add sidecar report structure and hallucination checks.
4. Add quality report correlation against verification artifacts.
5. Split large CLI command wiring into focused modules as the surface grows.

P2 code changes:

1. Add multi-sidecar orchestration with explicit judge/reporting, not automatic
   authority.
2. Add timeline or TUI viewer for audit bundles.
3. Add Claude/Cursor packet adapters.
4. Add compact memory graph with provenance.
5. Add team policy packs and exportable governance templates.

## Claim Discipline

Safe to claim now:

- local-first CLI;
- provider-free default;
- deterministic workflow routing;
- bounded packet generation;
- redaction in scan, packet, session, provider error, and sidecar paths;
- explicit opt-in AI quality pass;
- Codex-first workflow integration.

Do not claim yet:

- production/stable release readiness;
- live provider readiness;
- Telegram gateway readiness;
- complete mutation closure;
- sidecar output correctness;
- security approval by free model;
- verification passed after only running `hipson work`.

The product edge is trust, not theatrical autonomy. Hipson should be the tool
that can answer: what did the agent do, what context did it use, what did local
verification prove, what remains unknown, and what should the next AI developer
inherit.
