# Hipson Business Development Review

This review synthesizes four read-only subagent perspectives: product
positioning, developer adoption, monetization/GTM, and trust/release readiness.
It is based on repository evidence, not external market research.

## Executive Decision

Hipson should be developed as a local-first, Codex-native trust and workflow
control plane for AI-assisted software work.

The strongest wedge is not "autonomous agent platform." It is:

> Safe AI code review and implementation handoff for private repositories,
> grounded in git state, bounded packets, verification commands, local memory,
> and explicit human review.

Hipson should sell trust, repeatability, and auditability before it sells
automation spectacle.

The next product step is to make AI itself a visible quality layer: users can
keep the local workflow provider-free, opt into free OpenRouter second opinions,
or choose a specific model for a bounded packet.

## Evidence Base

- `README.md` positions Hipson as a local-first orchestration CLI with
  provider-free core commands, bounded packets, redaction, local memory, and
  Codex workflow support.
- `docs/CORE_STABILIZATION_ROADMAP.md` defines the product center as:
  `route -> scan -> packet/execute -> verify -> memory/handoff`.
- `docs/hermes-integration.md` keeps Hermes optional for intake, status,
  scheduling, Telegram, and async bus events; Codex remains the main coding
  control surface.
- `docs/skill-library.md` emphasizes curated task-fit skills and advisory
  sidecars instead of broad skill dumping.
- `docs/PRODUCTION_READINESS_SCORECARD.md` and
  `docs/REAL_AGENT_READINESS_SCORECARD.md` support a strong local/provider-free
  MVP story, while explicitly deferring live-provider, unrestricted release, and
  full enterprise readiness claims.
- `pyproject.toml` currently declares Hipson as `Development Status :: 4 -
  Beta`, which matches the honest release posture.

## Positioning

Recommended positioning sentence:

> Hipson is a local-first workflow control plane that makes Codex-driven
> software work auditable: it scans the repo, prepares bounded packets, routes
> optional expert sidecars, verifies with real commands, and records durable
> handoffs without requiring cloud keys.

Avoid positioning Hipson as:

- a general autonomous engineer;
- a cloud-first agent platform;
- a replacement for Codex;
- a production-ready enterprise governance suite today;
- a Hermes competitor.

## Ideal Customer Profile

Initial ICP:

- senior-led AI-native software teams with 2-20 developers;
- technical founders, staff engineers, consultants, and small agencies;
- teams using Codex or similar coding agents daily;
- teams working across private repositories where code privacy, secrets,
  reviewability, and traceable decisions matter;
- teams that want better AI workflow discipline before adopting heavier
  cloud-agent infrastructure.

First buyer/user persona:

- Staff Engineer, Tech Lead, technical founder, or senior consultant who already
  uses Codex and is responsible for code quality, review discipline, and safe
  delivery across one or more repositories.

## Product Wedge

Start with one narrow, repeatable outcome:

> Ten minutes from install to a useful, auditable AI review packet for the
> current git diff.

The canonical onboarding should become:

```bash
hipson doctor
hipson work --task "review current diff for test gaps" --write-packet --packet-output runs/first-review.md
git diff --check
hipson memory add --scope repo --repo . --kind handoff --summary "..."
```

Optional steps such as Codex install, provider-backed sidecars, Hermes, Telegram,
and live providers should come after the first local success.

## Strategic Directions

### 1. Make Core Trust Boring And Repeatable

Business value depends on Hipson reliably answering:

- what task was routed;
- what files changed;
- what packet or execution scope was used;
- what verification actually ran;
- what remains unknown;
- whether providers, sidecars, or Hermes were involved;
- what memory or handoff should persist.

Near-term work should prioritize core reliability, mutation triage, honest
release evidence, and local workflow ergonomics over new broad agent features.

### 2. Reduce Time To First Trusted Outcome

The current docs expose many commands early: route, work, Hermes, scan, packet,
sidecar, memory, chat, sessions, tools, learning, install. That is powerful for
maintainers but heavy for new users.

The business product should create one default path:

1. install;
2. doctor;
3. first `hipson work`;
4. written review packet;
5. verification commands;
6. memory/handoff;
7. optional sidecar/Hermes/provider setup.

### 3. Keep Hermes Optional

Hermes is valuable for status, intake, scheduling, Telegram, and long-running
coordination. It should not become the product center.

Business framing:

- Codex is the user's primary interface.
- Hipson is the local workflow authority.
- Hermes is an optional status and gateway layer.

### 4. Curate Skills As Product Surface

The skill stack is already broad. The business opportunity is not "more skills";
it is knowing which skill applies, why, and what evidence it produces.

Strong paid or premium directions:

- security/release review packs;
- UI/motion/visual QA packs;
- team handoff and memory conventions;
- governance and provenance packs;
- custom internal skill catalogs.

### 5. Build Toward Team Governance, Not Cloud Dependence

Hipson should keep the OSS core useful and trustworthy. Paid value should come
from:

- policy packs;
- workflow packs;
- audit exports;
- provenance verification;
- team templates;
- private sidecar configs;
- onboarding and support;
- optional BYOK sidecar orchestration.

Do not make provider-backed operation mandatory.

## Monetization Hypothesis

Recommended model:

- open-source local CLI core;
- paid Pro workflow packs;
- paid team governance packs;
- paid implementation/onboarding support;
- optional BYOK or managed sidecar credits later.

Keep free/OSS:

- `doctor`, `route`, `work`, `scan`, `packet`, basic `memory`;
- provider-free defaults;
- redaction and sensitive-path guards;
- basic Codex workflow installer;
- deterministic routing;
- local audit contract.

Paid candidates:

- release review workflow pack;
- security review workflow pack;
- frontend/UI visual QA workflow pack;
- multi-repo health and handoff pack;
- policy/provenance verification;
- audit export;
- team templates and internal skill catalogs;
- enterprise onboarding and support.

Pricing hypotheses require market validation, but a plausible starting shape is:

- Pro local: yearly individual subscription for premium workflow packs;
- Team: seat-based pricing for governance/provenance/policy features;
- Enterprise/support: annual support or fixed onboarding packages.

## Claim Matrix

| Claim | Status | Notes |
|---|---|---|
| Local-first CLI | Claim now | Supported by current product shape and docs. |
| Provider-free default | Claim now | Core commands do not require API keys. |
| Deterministic routing | Claim now | Keep framed as local workflow routing. |
| Bounded packets | Claim now | Do not imply packet correctness without review. |
| Redaction and sensitive-path guards | Claim now | State that redaction reduces risk but does not replace human review. |
| Production-ready local/provider-free MVP | Claim with release evidence | Needs a clean release tag, green CI, and recorded verification. |
| Live provider readiness | Claim after evidence | Requires manual smoke with disposable credentials and recorded results. |
| Telegram/Hermes gateway readiness | Claim after evidence | Requires runbook, allowlist, smoke, and security notes. |
| Enterprise infrastructure | Do not claim yet | Requires release governance, approval UX, and mutation closure. |
| Unrestricted autonomous runtime | Do not claim | This conflicts with the current safety model. |

## 30/60/90 Roadmap

### Days 1-30: Trust And Onboarding

- Freeze release claims around local-first provider-free beta.
- Create a single Getting Started path centered on `hipson work`.
- Clean historical docs or mark them as archived.
- Add a release checklist and current release posture doc.
- Continue focused mutation triage for safety-critical modules.
- Add `SECURITY.md`, `CODEOWNERS`, data-handling notes, and support stance.
- Produce one canonical demo: first useful review packet from a git diff.

### Days 31-60: Proof And Pilot

- Run controlled pilots with 5-10 senior Codex users or small teams.
- Measure time to first useful packet and repeat usage per repo.
- Add generated CLI reference and three canonical workflows:
  security review, implementation handoff, and UI/release review.
- Add provider readiness checks that do not send repo data.
- Add provenance verification for vendored skills.
- Prepare public case-study style docs from local/private examples.

### Days 61-90: Productized Wedge

- Package an "AI Review Control Kit" around the strongest workflow.
- Add team templates for repos, memory conventions, review packets, and release
  evidence.
- Decide whether paid value starts as workflow packs, consulting/onboarding, or
  team governance.
- Add audit export and policy/provenance foundations.
- Keep managed provider credits optional and later; BYOK first.

## Adoption Metrics

Track locally or through voluntary user reporting:

- time to first useful brief;
- `doctor` success rate;
- first `hipson work` completion rate;
- first packet written rate;
- percentage of work briefs where verification commands were actually run;
- repeat usage per repo per week;
- memory/handoff notes per completed task;
- sidecar/provider opt-in rate;
- sensitive-path blocks and redaction events per run;
- docs funnel: README -> install -> doctor -> first packet.

## Main Business Risks

1. Overclaiming readiness. Hipson has a strong local MVP story, but the repo
   explicitly defers live-provider, Telegram gateway, full mutation closure, and
   enterprise infrastructure claims.
2. Onboarding overload. The command surface is powerful, but external adoption
   needs one obvious path to first value.
3. Trust regression. The business promise depends on redaction, packet
   boundaries, approval gates, and auditability staying correct.
4. Weak value capture. Apache-2.0 and public skills are strengths for adoption,
   but paid value must come from curation, provenance, governance, support, and
   packaged outcomes.
5. Market ambiguity. Hipson should initially serve Codex power users and
   senior-led teams instead of trying to be a generic agent platform.

## Recommended Next Product Packages

1. `hipson first-run`: an opinionated local onboarding flow that ends with a
   written packet and verification checklist.
2. AI quality layer: explicit free/model-selected sidecars, dry-run previews,
   and quality reports that keep model output advisory.
3. Release evidence kit: release checklist, scorecard, CI artifact summary, and
   claim matrix.
4. Security/review workflow pack: curated skills, packet templates,
   verification gates, and audit output.
5. Team governance pack: policy config, provenance checks, team memory
   conventions, and audit export.
6. Visual QA workflow pack: screenshot-backed UI/UX, motion, accessibility, and
   responsive review flow.
