# Documentation

Live documentation is indexed below. Commands and code-form source paths assume
the repository root unless a document says otherwise; generated artifacts and
user-local configuration are identified separately.
Replace angle-bracket placeholders such as `<work_id>` with the actual values
before running examples.

## Getting started

- [Install on each OS](INSTALL.md) — Linux, macOS, native Windows and WSL2 quick starts, Claude Code MCP registration, support limits and storage paths.
- [Testing on a Mac](TESTING_MACOS.md) — five-minute smoke test for macOS testers: `bash scripts/platform_smoke.sh` and the report to send back.
- [Usage reference](USAGE.md) — every command group, workflows, runtime, configuration, safety model and development commands; for all users.
- [AI full-stack dev workflow](AI_FULL_STACK_DEV_WORKFLOW.md) — first-run review kits, manual steps, and verification; for developers adopting Hipson.
- [Skill library](skill-library.md) — local skill sources, task-fit curation, and provenance; for agents and skill maintainers.
- [Computer vision roadmap](computer-vision/hipson-cv-roadmap.md) — CV skill contracts and proposed demo phases; for contributors planning CV work.

## Agent integration

- [Install for agents](INSTALL_FOR_AGENTS.md) — managed instructions, worker mode, policy, and MCP setup; for Codex, Cursor, Claude Code, and MCP users.
- [Agent-native core](AGENT_NATIVE_CORE.md) — contract, artifact, trust, and review-kit interfaces; for agent and adapter authors.
- [Agent autopilot layer](AGENT_AUTOPILOT_LAYER.md) — bounded workflow bundles, resume, MCP tools, and human gates; for agent integrators.
- [Orchestrator operating model](hipson-orchestrator.md) — standalone and lead/worker responsibilities, bounded delegation, and review; for leads and workers.
- [Hermes integration](hermes-integration.md) — optional intake/status bus, skill installation, and gateway setup pointers; for Hermes bridge users.

## Task ledger & evidence

- [Task ledger](TASK_LEDGER.md) — lead/worker flow, CLI/MCP contracts, path claims, and orphan recovery; for leads and wrapper authors.
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
- [Release notes 1.4.0](release-1.4.md) — activity journal, result acknowledgement, `task now`, `lead resume`, measured results.
- [Release notes 1.3.0](release-1.3.md) — `hipson init` and `hipson task run`, review fixes and limits.
- [Release notes 1.2.0](release-1.2.md) — ledger, agent workflow, cross-platform support, external audit fixes and limits.
- [1.1 release notes](release-1.1.md) — agent playbook/router changes and suggested checks; for users and release maintainers.
- [Mutation triage notes](MUTATION_TRIAGE_NOTES.md) — historical partial mutation results and follow-up strategy; for test maintainers.
- [Handoff reference](handoff.md) — setup paths, decisions, and historical verification; for contributors resuming maintenance.
- [Progress reference](hipson-progress.md) — earlier workflow milestones, decisions, and verification snapshots; for maintainers tracing context.
- [Vendored skill provenance](vendored-skills-provenance.json) — recorded source paths, counts, and tree hashes; for skill and supply-chain reviewers.
- [Changelog](../CHANGELOG.md) — unreleased changes and released versions; for users and release maintainers.

Historical planning and audit material is indexed in the [archive](archive/README.md).
