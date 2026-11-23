# Documentation

Live documentation is indexed below. Commands and code-form source paths assume
the repository root unless a document says otherwise; generated artifacts and
user-local configuration are identified separately.
Replace angle-bracket placeholders such as `<work_id>` with the actual values
before running examples.

## Getting started

- [Platform compatibility](PLATFORMS.md) — what works on native Windows, macOS, Linux and WSL2, and what each needs; for users picking an environment.
- [Install on each OS](INSTALL.md) — Linux, macOS, native Windows and WSL2 quick starts, Claude Code MCP registration, support limits and storage paths.
- [Testing on a Mac](TESTING_MACOS.md) — five-minute smoke test for macOS testers: `bash scripts/platform_smoke.sh` and the report to send back.
- [Usage reference](USAGE.md) — every command group, workflows, runtime, configuration, safety model and development commands; for all users.
- [AI full-stack dev workflow](AI_FULL_STACK_DEV_WORKFLOW.md) — first-run review kits, manual steps, and verification (autopilot and release-claim parts deprecated); for developers adopting Hipson.
- [Skill library](skill-library.md) — local skill sources, task-fit curation, and provenance; for agents and skill maintainers.
- [Computer vision roadmap](computer-vision/hipson-cv-roadmap.md) — CV skill contracts and proposed demo phases; for contributors planning CV work.

## Agent integration

- [Install for agents](INSTALL_FOR_AGENTS.md) — managed instructions, worker mode, policy, and MCP setup; for Codex, Cursor, Claude Code, and MCP users.
- [Agent runtimes](AGENT_RUNTIMES.md) — native Claude Code, Codex and Antigravity runs, usage reporting and limits; for leads and adapter authors.
- [Agent-native core](AGENT_NATIVE_CORE.md) — contract, artifact, trust, and review-kit interfaces (autopilot, release-claim, hermes and scheduler parts deprecated); for agent and adapter authors.
- [Agent autopilot layer](AGENT_AUTOPILOT_LAYER.md) — **deprecated since 1.7, removed in 2.0**; bounded workflow bundles, resume, MCP tools, and human gates; for agent integrators.
- [Orchestrator operating model](hipson-orchestrator.md) — standalone and lead/worker responsibilities, bounded delegation, and review; for leads and workers.
- [Hermes integration](hermes-integration.md) — **deprecated since 1.7, removed in 2.0**; optional intake/status bus, skill installation, and gateway setup pointers; for Hermes bridge users.
- [Conversational boundary](CONVERSATIONAL_BOUNDARY.md) — proposal only, not implemented: the boundary for a future conversational client; for contributors planning it.

## Task ledger & evidence

- [Task ledger](TASK_LEDGER.md) — lead/worker flow, CLI/MCP contracts, path claims, and orphan recovery; for leads and wrapper authors.
- [Flow evaluation](FLOW_EVAL.md) — lead procedures compared under fixed worker behaviours with a hidden oracle; for flow maintainers.
- [Agent UX baseline](evals/AGENT_UX.md) — how the compact-read and resume measurements are taken, with the owner decision; for contributors measuring agent-facing output.
- [Agent e2e harness](AGENT_E2E.md) — local natural-language agent missions, deterministic oracles, and friction reports; for CLI and agent workflow maintainers.
- [AI quality layer](AI_QUALITY_LAYER.md) — advisory review, evidence locking/redaction, chain verification, and audit reads; for reviewers and evidence consumers.

## Runtime & providers

- [Agent provider model](agent-provider-model.md) — sidecar configuration, packet boundaries, and local memory; for sidecar users.
- [Model routing](model-routing.md) — configured agents, routing controls, and historical cost snapshot; for users choosing an explicit sidecar lane.
- [OpenRouter discovery design](OPENROUTER_MODEL_DISCOVERY.md) — proposed metadata cache and allowlist contracts, currently unimplemented; for provider contributors.

## Release & maintenance

- [1.0 readiness](release-1.0.md) — historical 1.0 gates and packaging decisions; for release maintainers.
- [Releasing](RELEASING.md) — how versions reach PyPI through Trusted Publishing, and the one-time maintainer setup.
- [Roadmap](ROADMAP.md) — what comes next and the evidence behind the order.
- [Deprecations](DEPRECATIONS.md) — commands hidden since 1.7 and removed in 2.0, with their replacements.
- [Release notes 1.8](release-1.8.md) — compact reads, next actions, lead MCP profile, usage; 1.8.1 fixes are in the [changelog](../CHANGELOG.md).
- [Release notes 1.7.0](release-1.7.md) — `hipson task flow`, native agent runtimes, deprecations.
- [Release notes 1.6.0](release-1.6.md) — declared acceptance checks and `task do`.
- [Release notes 1.5.0](release-1.5.md) — `hipson task verify` and revision-bound acceptance, measured results.
- [Release notes 1.4.0](release-1.4.md) — activity journal, result acknowledgement, `task now`, `lead resume`, measured results.
- [Release notes 1.3.0](release-1.3.md) — `hipson init` and `hipson task run`, review fixes and limits.
- [Release notes 1.2.0](release-1.2.md) — ledger, agent workflow, cross-platform support, external audit fixes and limits.
- [1.1 release notes](release-1.1.md) — agent playbook/router changes and suggested checks; for users and release maintainers.
- [Mutation triage notes](MUTATION_TRIAGE_NOTES.md) — historical partial mutation results and follow-up strategy (kept here because a test reads it); for test maintainers.
- [Progress reference](hipson-progress.md) — historical; earlier workflow milestones, decisions, and verification snapshots; for maintainers tracing context.
- [Vendored skill provenance](vendored-skills-provenance.json) — recorded source paths, counts, and tree hashes; for skill and supply-chain reviewers.
- [Changelog](../CHANGELOG.md) — unreleased changes and released versions; for users and release maintainers.

Historical planning, audit and stabilization material (including the 1.6 stabilization review, the 1.8 plan and the old handoff) is indexed in the [archive](archive/README.md).
