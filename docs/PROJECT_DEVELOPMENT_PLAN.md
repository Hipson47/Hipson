# Project Development Plan

## 1. Executive Summary

- What this project appears to be: Hipson is a local-first Python CLI for AI-assisted software workflow orchestration. It scans Git repositories, compiles bounded review/executor packets, stores compact JSONL memory, routes advisory sidecars, and installs a Codex workflow kit. Evidence: `README.md`, `pyproject.toml`, `src/hipson/cli.py`, `src/hipson/project.py`, `src/hipson/agents.py`, `src/hipson/memory.py`, `src/hipson/codex_install.py`.
- Current maturity level: mature local CLI with strong packaging and verification posture, but still a small-tool architecture rather than a large platform. Evidence: `pyproject.toml` declares version `1.1.0`, no runtime dependencies, Python 3.11/3.12 CI, and `README.md` describes "Stable 1.1 local-first CLI"; verification in this audit passed ruff, mypy, Bandit, test runners, compileall, build, skill validation, and setup checks.
- Biggest leverage opportunity: convert the broad single-file test suite into clearer module-level tests and use mutation survivors to harden the highest-risk paths: provider calls, redaction, sidecar routing, and packet generation. Evidence: `tests/test_hipson_helpers.py` has 2458 lines and 115 tests; `uv run mutmut results` reports survivors concentrated in `src/hipson/agents.py`, `src/hipson/packets.py`, and `src/hipson/redaction.py`.
- Biggest technical risk: safety depends on local conventions, regex redaction, path guards, packet boundaries, and human review. This is appropriate for a local-first tool, but external sidecar calls and vendored skills make prompt/data leakage and supply-chain drift the main risks. Evidence: `src/hipson/redaction.py`, `src/hipson/agents.py`, `docs/agent-provider-model.md`, `docs/skill-library.md`, `docs/vendored-skills-provenance.json`.
- Recommended next milestone: ship a 1.2 hardening milestone focused on test architecture, mutation-score improvement, provider safety constraints, provenance refresh automation, and clearer operator docs. Avoid broad feature expansion until the sidecar/redaction/memory boundaries are easier to verify.

## 2. Repository Snapshot

- Detected stack: Python package using `setuptools` and `uv`; runtime dependencies are intentionally empty; dev tooling includes `pytest`, `ruff`, `mypy`, `bandit`, `pip-audit`, and `mutmut`. Evidence: `pyproject.toml`, `uv.lock`.
- Key commands:
  - `uv sync --all-extras`
  - `uv run hipson doctor`
  - `uv run hipson scan . --include-diff`
  - `uv run hipson packet review . --title "..." --include-diff`
  - `uv run hipson packet exec . --title "..." --goal "..." --allowed-edit "..."`
  - `uv run hipson memory add/search/list`
  - `uv run hipson sidecar route ...`
  - `uv run hipson install codex --dry-run|--apply`
  Evidence: `README.md`, `src/hipson/cli.py`, `SKILLS.md`.
- Package/config files: `pyproject.toml`, `uv.lock`, `MANIFEST.in`, `.github/workflows/ci.yml`, `.gitignore`, `.env.example`, `config/agents.json`, `config/providers.example.env`, `repos.example.yaml`.
- Main entrypoints: console script `hipson = "hipson.cli:main"` in `pyproject.toml`; compatibility wrappers in `scripts/hipson_project.py` and `scripts/hipson_agents.py`; dependency-free test runner in `scripts/run_tests.py`.
- Main source package: `src/hipson/` with `cli.py`, `project.py`, `agents.py`, `memory.py`, `packets.py`, `router.py`, `redaction.py`, `codex_install.py`, `paths.py`, `home.py`, `skills.py`, and packaged runtime assets under `src/hipson/assets/`.
- Test framework: pytest-compatible tests plus a custom dependency-free runner. Evidence: `pyproject.toml` pytest config, `scripts/run_tests.py`, `tests/test_hipson_helpers.py`.
- Docs present: README, orchestrator model, provider model, model routing, release notes, skill library, progress/handoff docs, vendored skill provenance, knowledge references, and packaged Codex workflow docs. Evidence: `docs/*.md`, `knowledge/README.md`, `src/hipson/assets/codex-workflow-kit/`.
- Docs missing or thin: no dedicated architecture reference for module boundaries, no contributor guide separate from README, no operational runbook for provider failure modes/rate limits, no release checklist that reflects current 1.1/1.2 direction, and no generated API/CLI command reference.
- CI/CD present: GitHub Actions in `.github/workflows/ci.yml` validates Python 3.11 and 3.12, installs with `uv`, runs ruff, mypy, Bandit, pip-audit, both test runners, mutmut, compileall, build, wheel smoke, skill validation, CLI smoke, doctor, and temporary repo scan.

## 3. Architecture Map

Hipson is a modular local CLI with package assets and optional provider-backed sidecars.

```text
User / Codex
  |
  v
hipson CLI (`src/hipson/cli.py`)
  |
  +-- doctor/check/setup -> home/assets/skills/project discovery
  +-- scan/scan-many/init/packet -> `src/hipson/project.py` + `src/hipson/packets.py`
  +-- route -> deterministic workflow router in `src/hipson/router.py`
  +-- memory -> JSONL store in `src/hipson/memory.py`
  +-- sidecar -> `src/hipson/agents.py` + `config/agents.json` + OpenRouter
  +-- install codex -> `src/hipson/codex_install.py` + packaged Codex kit assets
  |
  v
Local repo state, git diff, docs, templates, generated runs/scans/memory
```

- Main modules/components:
  - CLI orchestration: `src/hipson/cli.py`.
  - Git/repo scanning and packet context: `src/hipson/project.py`.
  - Sidecar routing/provider calls: `src/hipson/agents.py`.
  - Local memory: `src/hipson/memory.py`.
  - Packet rendering: `src/hipson/packets.py`.
  - Workflow routing: `src/hipson/router.py`.
  - Redaction/path safety: `src/hipson/redaction.py`.
  - Codex installer: `src/hipson/codex_install.py`.
  - Runtime asset resolution: `src/hipson/assets/__init__.py`, `src/hipson/paths.py`.
  - Skill validation: `src/hipson/skills.py`.
- Data/control flow:
  - CLI parses command and delegates to narrow command handlers in `cli.py`.
  - Repo scan calls Git via `subprocess.run([...])`, captures status/diff/log, redacts sensitive paths/content, and renders Markdown/JSON.
  - Packet generation builds a scan/context bundle and renders structured Markdown via `PacketSpec`.
  - Sidecar route deterministically scores agent metadata or optionally sends only a redacted routing summary to an LLM router.
  - Sidecar run reads a bounded packet, refuses sensitive packet paths, redacts content, calls OpenRouter, and writes a redacted report.
  - Memory writes JSONL notes/sources after metadata and summary redaction.
  - Codex install merges a managed marker block into `AGENTS.md` and copies the packaged `hipson-workflow` skill.
- CLI/API/runtime boundaries:
  - Public CLI is `hipson` from `pyproject.toml`.
  - Legacy scripts under `scripts/` are thin wrappers around packaged modules.
  - Runtime assets come from installed package assets or a validated source checkout, not arbitrary current working directory files. Evidence: `src/hipson/assets/__init__.py`, `src/hipson/paths.py`, tests around fake CWD asset shadowing.
- External integrations:
  - Git CLI for repo state.
  - OpenRouter HTTP API through stdlib `urllib.request` in `src/hipson/agents.py`.
  - Codex filesystem config under `CODEX_HOME` or fallback locations.
  - GitHub Actions CI.
- Config/secrets handling:
  - Provider keys resolve from already-exported env, `HIPSON_AGENTS_ENV`, repo `.env`, then `~/.config/hipson/agents.env`. Evidence: `README.md`, `.env.example`, `config/providers.example.env`, `src/hipson/agents.py`.
  - `.gitignore` ignores `.env`, `.env.*`, nested env files, `config/providers.env`, `repos.yaml`, generated reports, memory JSONL, build outputs, caches, and media artifacts.
- Current coupling points:
  - `src/hipson/cli.py` knows every command module and has command-specific orchestration.
  - `src/hipson/project.py` combines Git discovery, YAML subset parsing, scan rendering, command discovery, and packet command wrappers.
  - `src/hipson/agents.py` combines env loading, agent routing, LLM-router payloads, OpenRouter HTTP calls, packet reading, report writing, and CLI commands.
  - Tests are concentrated in one large file, which makes contracts visible but increases friction for targeted maintenance.

## 4. Module-by-Module Assessment

### `src/hipson/cli.py`

- Purpose: top-level CLI command parser and dispatcher for doctor, scan, route, init, check-setup, skill, install, packet, sidecar, and memory commands.
- Current state: clear, direct, and small enough to follow; delegates behavior to modules instead of embedding all logic.
- Strengths: command surface is explicit; error handling catches `SystemExit` in delegated commands; doctor composes useful environment, asset, skill, and command checks.
- Problems/gaps: command behavior and parser definitions live in one file; no generated command reference; some command discovery output uses generic commands (`pytest`, `mypy .`) while README/CI use `uv run ...` and `mypy src/hipson`.
- Recommended changes: add parser snapshot tests and generate CLI reference docs from parser metadata; standardize discovered command recommendations to installed/current workflow commands.
- Priority: P2.

### `src/hipson/project.py`

- Purpose: repository resolution, Git scan, diff/status rendering, small `repos.yaml` parser, command discovery, packet context, and setup checks.
- Current state: core orchestration module and largest source file at 753 lines.
- Strengths: uses `subprocess.run` with argument lists and timeouts; explicitly rejects missing paths; redacts sensitive path names and diff content; avoids accidental parent home/profile Git roots; dependency-free registry parsing is deliberate.
- Problems/gaps: multiple responsibilities in one module; YAML subset parser has strict indentation assumptions; command discovery is heuristic and sometimes less precise than CI/README; scan rendering and data collection are tightly coupled; generated `__pycache__` can appear in filesystem inventories if tooling does not respect ignores.
- Recommended changes: split into `git_scan.py`, `registry.py`, `command_discovery.py`, and `scan_render.py`; add focused tests for relative/absolute registry paths, scoped subdirectory scans, and command discovery output; optionally expose a machine-readable scan model before rendering.
- Priority: P1.

### `src/hipson/agents.py`

- Purpose: sidecar agent config loading, provider env resolution, deterministic sidecar routing, optional LLM router, OpenRouter calls, packet reading/redaction, and report writing.
- Current state: functional and well-tested, but it is the highest-risk module because it touches secrets, external network calls, prompt boundaries, and generated reports.
- Strengths: refuses sensitive packet paths; caps packet size; sends only redacted summaries for LLM routing; validates provider URL scheme; normalizes and bounds router choices; treats sidecar output as advisory in docs; has many tests in `tests/test_hipson_helpers.py`.
- Problems/gaps: `provider_chat` allows `http` as well as `https`; provider response errors may include provider-returned body text and should be checked for redaction before display; mutation survivors are concentrated here; config schema is implicit; OpenRouter is the only concrete provider despite generic naming.
- Recommended changes: introduce typed config validation for providers/router/agents; restrict provider URLs to HTTPS except explicit localhost/test overrides; redact provider error bodies; extract provider client and router scoring into smaller modules; add mutation-killing tests around env parsing, provider payload shape, URL validation, fallback routes, and report paths.
- Priority: P0/P1.

### `src/hipson/redaction.py`

- Purpose: redact common secrets and block/summarize sensitive file paths before persistence or provider calls.
- Current state: compact regex-based layer with broad tests.
- Strengths: covers private keys, bearer tokens, GitHub tokens, OpenRouter/OpenAI-like keys, AWS access IDs, env assignments, JSON-style secret fields, URL query secrets, and sensitive paths such as `.env`, `.ssh`, `.aws`, `.config`, `.pem`, `.key`, `.p12`, `.sqlite`, `.db`.
- Problems/gaps: regex redaction is inherently incomplete; mutation survivors show some redaction/path-token behavior is not fully pinned; path redaction replaces entire lines, which is safe but coarse; no structured redaction report explaining what was redacted.
- Recommended changes: add a `RedactionResult` with counts/categories for auditability; add tests for multiline config formats and Windows/WSL path edge cases; consider optional allowlist/denylist extension points.
- Priority: P0/P1.

### `src/hipson/memory.py`

- Purpose: local JSONL note/source store for durable decisions, risks, handoffs, and compact context.
- Current state: dependency-free and simple; defaults to repo-local `memory/`.
- Strengths: redacts summaries and metadata; refuses sensitive source paths; search is transparent token overlap; generated JSONL is ignored by `.gitignore`.
- Problems/gaps: no file locking or corruption recovery; JSON parsing errors in a single bad line can break reads; no schema/version field; no compaction/archive command; no link from packet generation to retrieve relevant memory.
- Recommended changes: add schema version, tolerant read with bad-line reporting, optional file lock for writes, and a bounded memory injection command for packets.
- Priority: P2.

### `src/hipson/packets.py`

- Purpose: structured Markdown packet compiler for review and executor handoffs.
- Current state: clean dataclass-based renderer with clear sections and contract language.
- Strengths: separates packet spec from project scan logic; enforces constraints, allowed edit scope, acceptance criteria, and output format; test coverage checks contract sections.
- Problems/gaps: packet schema is Markdown-only; no JSON representation; mutation survivors in list helpers; no packet metadata header for version/tool info.
- Recommended changes: add optional JSON packet model or frontmatter with schema version; add tests for empty/edge list behavior; add packet size budget metadata.
- Priority: P2.

### `src/hipson/router.py`

- Purpose: deterministic workflow router mapping task text to scan/review/exec/verify/handoff/sidecar/memory flows and risk levels.
- Current state: small, dependency-free, predictable.
- Strengths: no provider call; returns commands and human-review signal; documented in `docs/release-1.1.md` and `SKILLS.md`.
- Problems/gaps: keyword rules are simple substring checks; "audit repository and create development plan" routes to review, which is sensible, but richer task distinctions will need test-driven rule evolution; command suggestions use generic `hipson` commands instead of consistently `uv run hipson` for source checkout.
- Recommended changes: add examples table in docs; add route fixtures for common Hipson workflows; consider config-driven rules only after keyword contracts become too large.
- Priority: P2.

### `src/hipson/codex_install.py`

- Purpose: install managed Hipson Codex instructions and the `hipson-workflow` skill into Codex home.
- Current state: compact and cautious.
- Strengths: dry-run mode; managed marker block; preserves user content; backs up existing files/skill directories before replacement; tested for preserve/replace/multiple-marker behavior.
- Problems/gaps: no rollback command; backup paths are timestamped but not surfaced as machine-readable install result; installer replaces the entire skill directory.
- Recommended changes: add `hipson install codex --rollback <backup>` or document manual rollback; return/print machine-readable JSON option; add post-install verification command.
- Priority: P2.

### `src/hipson/assets/`, `templates/`, and packaged Codex workflow kit

- Purpose: ship runtime templates, sidecar config, SKILLS/ORCHESTRATOR docs, and installable Codex workflow assets inside the package.
- Current state: well-canonicalized under `src/hipson/assets/`; root templates also exist.
- Strengths: tests enforce packaged assets availability and root toolkit mirror absence; CI wheel smoke checks installed package behavior; docs identify canonical copy.
- Problems/gaps: dual root templates vs packaged templates can drift; asset sync tests exist but future edits can still be confusing; no documented process for refreshing bundled skill assets.
- Recommended changes: document "edit source asset here, sync/check there" workflow; add a small asset sync/check script if not already sufficient; add provenance refresh instructions.
- Priority: P2.

### `src/hipson/skills.py` and vendored `skills/`

- Purpose: validate Codex `SKILL.md` frontmatter and provide a curated local skill library for packet building and sidecar prompts.
- Current state: large vendored library with provenance; validator is simple but useful.
- Strengths: `docs/vendored-skills-provenance.json` records source, file count, bytes, and tree hashes; `docs/skill-library.md` defines selection and supply-chain policy; CI runs `hipson skill validate`.
- Problems/gaps: vendored skill refresh appears manual; validation is frontmatter-only and does not inspect risky scripts/assets; `skills/` dominates repo file count (1009 of 1084 tracked files), which can make audits noisy.
- Recommended changes: add a provenance verifier command; add optional script-risk metadata for vendored skills; split generated provenance summary from detailed hashes if needed.
- Priority: P1/P2.

### `scripts/`

- Purpose: backward-compatible wrappers and dependency-free test runner.
- Current state: very small; wrappers import packaged modules from `src`.
- Strengths: preserves older invocation paths from docs; `scripts/run_tests.py` provides no-pytest fallback and ran 115/115 tests in this audit.
- Problems/gaps: custom runner only supports simple `tmp_path` injection and catches broad `Exception`; it does not provide pytest fixtures/markers/parametrization; docs still reference scripts in some places while package CLI is primary.
- Recommended changes: keep runner but document it as a constrained smoke runner; gradually update docs to prefer `uv run hipson` while preserving scripts for compatibility.
- Priority: P3.

### `tests/test_hipson_helpers.py`

- Purpose: contract tests for redaction, scanning, sidecar routing, memory, Codex installer, packets, runtime assets, workflow router, provider behavior, and CLI subprocess smoke.
- Current state: strong but monolithic.
- Strengths: high coverage of safety-critical behavior; tests passed through both custom runner and pytest; many subprocess smoke tests validate installed CLI behavior.
- Problems/gaps: 2458-line single test file is hard to navigate; mutation survivors show remaining weak spots; no coverage reporting gate; no dedicated integration test package layout.
- Recommended changes: split into `test_redaction.py`, `test_scan.py`, `test_agents.py`, `test_memory.py`, `test_packets.py`, `test_router.py`, `test_install.py`, `test_assets.py`, and `test_cli.py`; add coverage and mutation thresholds once split.
- Priority: P1.

### Docs and product narrative

- Purpose: explain local-first workflow, setup, sidecars, model routing, skills, release posture, progress, and handoff.
- Current state: rich docs, but some are stale relative to current state.
- Strengths: `README.md` is comprehensive; `docs/agent-provider-model.md` and `docs/model-routing.md` clearly document sidecar policy; `docs/skill-library.md` documents supply-chain policy; `docs/release-1.1.md` documents agent-native router.
- Problems/gaps: `docs/handoff.md` and `docs/hipson-progress.md` still mention older verification counts such as 63/63 tests; no single architecture doc; release docs are history-oriented rather than current roadmap; no dedicated contributing/testing guide.
- Recommended changes: create architecture, contributing, security model, and operator runbook docs; update stale verification counts or avoid embedding counts in long-lived docs.
- Priority: P1/P2.

## 5. Product Direction

- Inferred direction: Hipson is becoming a portable, agent-native local workflow kit for software teams or power users who want AI coding work to stay grounded in repo state, git diffs, bounded packets, local memory, and explicit verification. Evidence: `README.md`, `ORCHESTRATOR.md`, `SKILLS.md`, `docs/hipson-orchestrator.md`, `docs/release-1.1.md`.
- North-star direction: a small, trusted "local AI engineering control plane" that helps agents inspect repos, plan bounded work, route review/sidecar support, preserve durable decisions, and verify outcomes without becoming a cloud dashboard or broad autonomous executor.
- What this project should become:
  - A dependable local CLI and installable Codex workflow kit.
  - A repo-state-first packet compiler for multi-agent work.
  - A safe sidecar integration layer with strong redaction and strict provider boundaries.
  - A durable local memory and handoff layer with clear provenance.
  - A curated, auditable skill/reference library for high-quality AI workflows.
- What it should not become:
  - A general secret manager.
  - A broad remote SaaS orchestration platform.
  - A provider-first LLM client that sends whole repos to models.
  - A replacement for CI, git review, or human security/release approval.
  - A sprawling framework with runtime dependencies for simple local workflows.
- Core user workflows:
  - Install and run `hipson doctor`.
  - Scan a target repo or registry with `hipson scan` / `scan-many`.
  - Route a task with `hipson route --task`.
  - Generate review/executor packets.
  - Optionally route/run advisory sidecars on bounded packets.
  - Store compact memory notes for decisions/risks/handoffs.
  - Install Codex workflow instructions and skill.
- Differentiators:
  - Local-first and provider-free by default.
  - Runtime has no mandatory dependencies.
  - Git diff and verification commands remain the contract.
  - Sidecars receive bounded packets, not repo access.
  - Packaged Codex workflow kit plus local skill library.
  - Strong practical safety posture for secret redaction and sensitive path refusal.
- Likely next capabilities:
  - Memory retrieval into packets with strict size caps.
  - Better typed provider configuration and provider abstraction.
  - Provenance verification for vendored skills.
  - Generated CLI/architecture docs.
  - Better mutation/coverage gates.
  - Safer provider error redaction and HTTPS-only defaults.

## 6. Development Roadmap

### Milestone 1 — Stabilize Core

- Goals:
  - Reduce maintenance risk in the safety-critical code paths.
  - Make tests easier to navigate and mutation survivors actionable.
  - Refresh docs that encode stale verification counts.
- Tasks:
  - Split `tests/test_hipson_helpers.py` by domain.
  - Add mutation-killing tests for `agents.py`, `redaction.py`, and `packets.py`.
  - Extract `project.py` scan/registry/command-discovery helpers into smaller modules.
  - Add typed config validation for `config/agents.json`.
  - Update stale verification notes in `docs/handoff.md` and `docs/hipson-progress.md`.
- Files likely involved:
  - `tests/`
  - `src/hipson/agents.py`
  - `src/hipson/redaction.py`
  - `src/hipson/packets.py`
  - `src/hipson/project.py`
  - `docs/handoff.md`
  - `docs/hipson-progress.md`
- Acceptance criteria:
  - Existing CLI behavior remains backward compatible.
  - `uv run ruff check .`, `uv run mypy src/hipson`, both test runners, Bandit, compileall, and build pass.
  - `uv run mutmut results` has materially fewer survivors in sidecar/redaction/packet modules.
  - Docs no longer claim outdated test counts.
- Risks:
  - Refactoring `project.py` can subtly change scan output; preserve fixtures and output contracts.
  - Over-tightening config validation can break existing user configs.

### Milestone 2 — Expand CLI Capabilities

- Goals:
  - Add bounded memory retrieval and richer packet metadata without compromising local-first behavior.
  - Improve sidecar ergonomics while keeping provider calls explicit and safe.
  - Build runtime-adjacent CLI foundations without treating this milestone as the full persistent agent runtime.
- Tasks:
  - Add optional memory search injection into packet generation with caps and provenance.
  - Add packet schema/frontmatter with tool version, generated time, source repo, included commands, and byte/char budgets.
  - Add `--json` output for install plan and maybe scan/packet commands where missing.
  - Add provider abstraction boundaries behind OpenRouter implementation.
  - Add command to inspect sidecar/provider readiness without sending data.
- Files likely involved:
  - `src/hipson/memory.py`
  - `src/hipson/project.py`
  - `src/hipson/packets.py`
  - `src/hipson/agents.py`
  - `src/hipson/cli.py`
  - `templates/`
  - `src/hipson/assets/templates/`
- Acceptance criteria:
  - Memory injection is off by default or explicit.
  - Packet outputs remain redacted and bounded.
  - Provider readiness command never prints secrets.
  - New features have CLI subprocess tests.
- Risks:
  - Memory injection can bloat packets or introduce stale context if not clearly labeled.
  - Provider abstraction can add complexity before there is a second real provider.

### Milestone 3 — Production Hardening

- Goals:
  - Make releases, provider safety, and vendored skill supply chain more auditable.
  - Strengthen reliability under error cases.
- Tasks:
  - Restrict non-test provider URLs to HTTPS or explicit localhost.
  - Redact provider HTTP error bodies before display.
  - Add file locking/tolerant reads for memory JSONL.
  - Add provenance verification command for `docs/vendored-skills-provenance.json`.
  - Add release checklist for current version line and signed artifact plan.
  - Add rollback or documented rollback support for Codex installer backups.
- Files likely involved:
  - `src/hipson/agents.py`
  - `src/hipson/memory.py`
  - `src/hipson/skills.py`
  - `src/hipson/codex_install.py`
  - `docs/vendored-skills-provenance.json`
  - `.github/workflows/ci.yml`
  - `docs/`
- Acceptance criteria:
  - Provider errors cannot leak known secret patterns.
  - Skill provenance can be verified locally in CI.
  - Memory tolerates a malformed line with a clear warning/reporting path.
  - Installer rollback story is tested or documented.
- Risks:
  - Provenance verification over large vendored assets may be slow.
  - Backward compatibility for provider URL testing needs explicit test hooks.

### Milestone 4 — Advanced Capabilities

- Goals:
  - Improve multi-repo and multi-agent workflows without turning Hipson into a cloud product.
  - Add higher-level planning surfaces while preserving bounded local execution.
- Tasks:
  - Add multi-repo registry health summaries and stale progress detection.
  - Add task packet dependency graph or PR sequence generator.
  - Add optional local embedding/index support for memory and docs, with no mandatory runtime dependency.
  - Add richer sidecar comparison reports across multiple advisory agents.
  - Add examples for frontend/UI, backend/security, and release-review workflows.
- Files likely involved:
  - `src/hipson/project.py`
  - `src/hipson/memory.py`
  - `src/hipson/agents.py`
  - `docs/`
  - `examples/` if added
- Acceptance criteria:
  - Advanced features are optional and do not add mandatory runtime dependencies.
  - Bounded packet and redaction guarantees remain tested.
  - Multi-repo commands never scan the entire user profile accidentally.
- Risks:
  - Feature creep can dilute the small trusted CLI model.
  - Local embedding/index support can introduce dependency and privacy complexity.

## 7. PR-by-PR Execution Plan

### PR 0: Fix Router Token Matching

- Objective: make routing token-aware before the persistent runtime depends on router outputs.
- Files likely touched: `src/hipson/router.py`, relevant router/CLI tests.
- Implementation notes:
  - Stop naive substring matching for single-token keywords.
  - Allow phrase matching for multi-word keywords.
  - Treat `build runtime` as implementation/exec intent, not verify.
  - Treat `run build`, `build failed`, and `verify build` as verify intent.
  - Prevent false risk matches such as `ui` inside `build`.
- Tests to add/update:
  - `"build runtime"` must not classify as UI risk.
  - `"build persistent agent runtime"` should route to implementation/exec.
  - `"run build and tests"` should route to verify.
  - `"premium ui review"` should still classify UI risk.
  - `"security auth audit"` should still classify security risk.
- Acceptance criteria:
  - Router matching is token-aware.
  - Existing route behavior remains stable where tests define it.
  - Runtime phase can safely use router output as a cheap deterministic planner signal.
- Estimated risk: Medium.

### PR 1: Split The Monolithic Test Suite

- Objective: reorganize tests into domain files without changing behavior.
- Files likely touched: `tests/test_hipson_helpers.py`, new `tests/test_redaction.py`, `tests/test_scan.py`, `tests/test_agents.py`, `tests/test_memory.py`, `tests/test_packets.py`, `tests/test_router.py`, `tests/test_install.py`, `tests/test_assets.py`, `tests/test_cli.py`.
- Implementation notes: move tests mechanically; keep helper functions in `tests/conftest.py` or `tests/helpers.py`; preserve custom runner compatibility or update `scripts/run_tests.py` to import helpers safely.
- Tests to add/update: no new behavior required; all existing tests should still run under pytest and custom runner.
- Acceptance criteria: 115 tests still pass under both runners; ruff/mypy remain green.
- Estimated risk: Medium.

### PR 2: Kill High-Value Mutation Survivors

- Objective: improve behavioral pinning in sidecar, packet, and redaction contracts.
- Files likely touched: `tests/test_agents.py`, `tests/test_redaction.py`, `tests/test_packets.py`, possibly `src/hipson/agents.py` for behavior clarified by tests.
- Implementation notes: use `uv run mutmut results` survivors as a checklist; prioritize `provider_chat`, `load_env`, `agent_route_score`, `router_candidates`, `extract_json_object`, `read_packet`, and `redact_sensitive_paths`.
- Tests to add/update: targeted tests for env whitespace/quotes, invalid provider URL behavior, fallback route confidence, oversized packet boundaries, report path sanitization, and redaction edge cases.
- Acceptance criteria: mutation survivors decrease, especially in `src/hipson/agents.py` and `src/hipson/redaction.py`; all normal gates pass.
- Estimated risk: Low/Medium.

### PR 3: Add Typed Agent Config Validation

- Objective: fail early on malformed `config/agents.json` and packaged asset config.
- Files likely touched: `src/hipson/agents.py`, `src/hipson/cli.py`, `tests/test_agents.py`, `config/agents.json`, `src/hipson/assets/config/agents.json`.
- Implementation notes: add small stdlib dataclass/validation helpers; avoid adding Pydantic to keep runtime dependency-free.
- Tests to add/update: missing provider, missing model, invalid context budget, unknown router provider, agent without system prompt, packaged config parity.
- Acceptance criteria: `hipson doctor` reports config validation; invalid configs fail with actionable messages.
- Estimated risk: Medium.

### PR 4: Refactor Repo Scan Internals

- Objective: reduce `project.py` size and separate scan model from rendering.
- Files likely touched: `src/hipson/project.py`, new `src/hipson/git_scan.py`, `src/hipson/registry.py`, `src/hipson/command_discovery.py`, `tests/test_scan.py`.
- Implementation notes: move code without changing output first; introduce typed scan record after contract tests are stable.
- Tests to add/update: golden-ish assertions for scan clean/changed/untracked/sensitive states and registry relative paths.
- Acceptance criteria: existing scan/packet tests pass; `hipson scan . --include-diff` output remains compatible.
- Estimated risk: Medium/High.

### PR 5: Harden Provider Boundary

- Objective: reduce leakage and transport risks for sidecar calls.
- Files likely touched: `src/hipson/agents.py`, `tests/test_agents.py`, `docs/agent-provider-model.md`, `config/providers.example.env`.
- Implementation notes: require HTTPS provider URLs by default; allow `http://localhost` only for explicit tests/dev if needed; redact HTTP error bodies before surfacing.
- Tests to add/update: provider URL scheme cases, redacted error body, missing env help, no packet body in LLM dry-run.
- Acceptance criteria: provider failures cannot print known secret patterns; tests cover bad response cases.
- Estimated risk: Medium.

### PR 6: Add Packet Metadata And JSON Option

- Objective: make generated packets more auditable and machine-readable.
- Files likely touched: `src/hipson/packets.py`, `src/hipson/project.py`, `templates/`, `src/hipson/assets/templates/`, tests.
- Implementation notes: add optional Markdown frontmatter or metadata section; keep existing human-readable sections stable.
- Tests to add/update: metadata includes version, project path, scope, commands, timestamp, and char budgets; redaction still applies before persistence.
- Acceptance criteria: packet consumers remain compatible; metadata is documented.
- Estimated risk: Medium.

### PR 7: Add Memory Robustness

- Objective: make local JSONL memory safer under malformed files and concurrent-ish usage.
- Files likely touched: `src/hipson/memory.py`, `tests/test_memory.py`, `README.md`.
- Implementation notes: add schema version, tolerant bad-line handling, and optional lock/write temp behavior if feasible with stdlib.
- Tests to add/update: malformed JSONL line, duplicate note IDs if relevant, source path redaction, memory dir override.
- Acceptance criteria: memory commands fail/report gracefully and never expose sensitive values.
- Estimated risk: Medium.

### PR 8: Add Vendored Skill Provenance Verification

- Objective: make `docs/vendored-skills-provenance.json` enforceable.
- Files likely touched: `src/hipson/skills.py` or new `src/hipson/provenance.py`, `docs/vendored-skills-provenance.json`, `.github/workflows/ci.yml`, tests.
- Implementation notes: implement sha256 tree calculation matching provenance note; add `hipson skill provenance-check`.
- Tests to add/update: small temporary skill tree hash, missing path, changed content, ignored generated dirs.
- Acceptance criteria: CI can verify vendored skills have not drifted unintentionally.
- Estimated risk: Medium.

### PR 9: Update Architecture And Contributor Docs

- Objective: make onboarding less dependent on reading code.
- Files likely touched: `docs/architecture.md`, `docs/contributing.md`, `docs/security-model.md`, `README.md`.
- Implementation notes: reuse facts from this development plan; keep docs concise and command-first.
- Tests to add/update: optional docs link check or simple grep in README if desired.
- Acceptance criteria: new contributor can identify modules, commands, safety model, and release gates from docs.
- Estimated risk: Low.

### PR 10: Add Installer Rollback Story

- Objective: make `hipson install codex --apply` safer for users.
- Files likely touched: `src/hipson/codex_install.py`, `src/hipson/cli.py`, `tests/test_install.py`, README/docs.
- Implementation notes: either implement rollback command or document backup restore paths with tests around backup naming.
- Tests to add/update: apply creates backup when target exists; rollback restores marker/skill dir.
- Acceptance criteria: users have a clear recovery path for Codex install changes.
- Estimated risk: Medium.

## 8. Testing & Verification Plan

- Existing tests observed:
  - `tests/test_hipson_helpers.py` contains 115 pytest-compatible tests over redaction, scan, registry parsing, skill validation, runtime assets, sidecar routing, LLM router dry-run, memory, installer, packets, workflow router, provider behavior, CLI smoke, and sensitive path handling.
  - `scripts/run_tests.py` runs the same test functions with simple `tmp_path` support and no pytest dependency.
  - `pyproject.toml` configures pytest, ruff, mypy, Bandit, and mutmut.
- Verification commands run during this audit:
  - `pwd`: `/home/hipson47/code/Hipson`.
  - `git status --short`: clean before writing this plan.
  - `git ls-files`: 1084 tracked files.
  - `uv run ruff check .`: passed.
  - `uv run mypy src/hipson`: passed.
  - `uv run python scripts/run_tests.py`: 115/115 passed.
  - `timeout 90s uv run python -m pytest -q`: 115 passed.
  - `uv run bandit -q -r src -c pyproject.toml`: passed.
  - `uv run pip-audit`: no known vulnerabilities found; local package `hipson` skipped because it is not on PyPI.
  - `uv run python -m compileall src scripts tests`: passed.
  - `uv build`: built sdist and wheel.
  - `uv run hipson skill validate`: passed for checked skills.
  - `uv run hipson check-setup`: passed.
  - `uv run hipson scan . --include-diff`: clean scan before this plan file.
  - `uv run mutmut results`: existing mutation results show survivors in `agents.py`, `packets.py`, and `redaction.py`.
- Missing test categories:
  - Split module-level tests for maintainability.
  - Coverage threshold reporting.
  - Full provenance verification tests for vendored skills.
  - Provider error-body redaction tests.
  - Memory corruption and file locking tests.
  - CLI generated docs/command reference tests.
  - Cross-platform path tests for Windows/WSL edge cases beyond current path guards.
- Unit tests to add:
  - Config validation for providers/router/agents.
  - Redaction result categories and edge cases.
  - Registry parser edge cases.
  - Command discovery precision.
  - Packet metadata rendering.
  - Installer rollback helpers.
- Integration tests to add:
  - Installed wheel smoke with provider readiness but no network call.
  - `hipson scan-many` with multiple temporary repos and mixed statuses.
  - `hipson install codex --apply` and rollback in temporary `CODEX_HOME`.
  - Memory add/search/list across a temporary memory dir with malformed lines.
- CLI/API tests to add:
  - `hipson --version`.
  - `hipson doctor --json`.
  - `hipson scan` JSON output if added.
  - `hipson packet review/exec` metadata contracts.
  - `hipson sidecar route --llm --llm-dry-run` candidate filtering.
- Security tests to add:
  - Provider HTTP error body redaction.
  - HTTPS-only provider URL enforcement.
  - Secret patterns in sidecar output reports.
  - Sensitive path handling for absolute Windows/WSL paths.
  - Prompt injection strings inside packets remain data.
- Recommended CI gates:
  - Keep current `.github/workflows/ci.yml` gates.
  - Add provenance check after it exists.
  - Add coverage report after test split.
  - Keep mutmut bounded to high-risk modules, but track survivor count over time.
- Commands to run for normal PRs:
  - `uv run ruff check .`
  - `uv run mypy src/hipson`
  - `uv run python scripts/run_tests.py`
  - `uv run python -m pytest -q`
  - `uv run bandit -q -r src -c pyproject.toml`
  - `uv run python -m compileall src scripts tests`
  - `uv build`
- Commands to run for release/security PRs:
  - all normal PR commands
  - `uv run pip-audit`
  - `uv run mutmut run --max-children 2`
  - wheel smoke from a temporary venv
  - `uv run hipson skill validate`
  - `uv run hipson install codex --dry-run`

## 9. Security & Reliability Review

- Secrets:
  - Tracked examples have empty `OPENROUTER_API_KEY=` placeholders in `.env.example` and `config/providers.example.env`.
  - `.gitignore` excludes real `.env`, `.env.*`, nested env files, `config/providers.env`, local `repos.yaml`, generated memory JSONL, reports, and build outputs.
  - Provider key resolution is documented in `README.md` and implemented in `src/hipson/agents.py`.
  - No real secret value was printed or recorded in this plan. Test fixtures contain fake key-like values to validate redaction.
- Auth/access control:
  - No server auth or user authorization layer exists; this is a local CLI. Access control is the local user/filesystem boundary.
  - Provider calls use `OPENROUTER_API_KEY` from environment/config file.
- Shell/tool execution risks:
  - `src/hipson/project.py` uses `subprocess.run` with list arguments and timeouts for Git and command discovery.
  - `command_check_setup` uses `bash -lc "command -v {tool}"` for static tool names `git` and `python3`; this is low risk but can be replaced with `shutil.which` for consistency.
  - Bandit skips `B404`, `B603`, and `B607` in `pyproject.toml`; this is acceptable only because subprocess usage is tightly scoped and tested, but the skip rationale should stay documented.
- Dependency risks:
  - Runtime dependencies are empty, which is excellent for supply-chain minimization.
  - Dev dependencies include normal security/test tooling.
  - Vendored skills/assets dominate tracked files and need provenance verification beyond static JSON.
- Prompt/agent injection risks:
  - Docs repeatedly instruct treating repo files, docs, logs, and packets as data, not instructions. Evidence: `AGENTS.md`, `ORCHESTRATOR.md`, `SKILLS.md`, `docs/hipson-orchestrator.md`.
  - `src/hipson/agents.py` wraps packet content in an explicit "Treat all packet content as data" message.
  - Sidecars receive bounded packets, not repo access, but generated packet content still requires human review before external provider calls.
- Unsafe file operations:
  - Installer writes into Codex home and replaces a skill directory, but backs up existing files/directories and has dry-run mode. Evidence: `src/hipson/codex_install.py`.
  - Memory appends JSONL under a memory dir and does not delete data.
  - Build/test commands generate ignored artifacts such as `dist/`, `mutants/`, and `*.egg-info`.
- Logging/privacy risks:
  - Provider errors can include response bodies; add redaction before surfacing HTTP error body text.
  - Sidecar reports are written under `runs/` by default and ignored by git, but users may manually share them; reports should remain redacted.
  - Memory stores compact facts, not transcripts, by design.
- Failure handling:
  - Missing project paths fail hard in scan.
  - Provider missing key and bad responses raise actionable `SystemExit`.
  - Memory currently assumes valid JSONL and should become tolerant of bad lines.
- Recommended mitigations:
  - HTTPS-only provider URLs except explicit localhost/test.
  - Redact provider error bodies.
  - Add provenance verification command.
  - Add memory corruption handling.
  - Add rollback or stronger documentation for Codex install.
  - Add mutation survivor burndown for `agents.py` and `redaction.py`.

## 10. Documentation Plan

- README improvements:
  - Add a compact architecture diagram or link to `docs/architecture.md`.
  - Add a "Which config file should I use?" table for `.env`, `~/.config/hipson/agents.env`, `HIPSON_HOME`, and `CODEX_HOME`.
  - Add clearer "local source checkout" vs "installed package" command examples.
- Architecture docs:
  - Create `docs/architecture.md` with modules, control flow, external integrations, generated artifacts, and package asset loading rules.
  - Include the diagram from this plan.
- Developer setup:
  - Create `docs/contributing.md` with `uv sync --all-extras`, test commands, style/type/security commands, and PR expectations.
  - Document the custom runner's limitations.
- Command reference:
  - Generate or maintain `docs/cli-reference.md` from `argparse` help.
  - Include examples for every subcommand.
- Contribution/testing guide:
  - Explain test split layout after PR 1.
  - Document when to run mutmut and pip-audit.
  - Document how to smoke-test built wheels.
- Operational runbook:
  - Create `docs/operator-runbook.md` covering provider env setup, no-secret rules, sidecar dry-run, failed provider responses, Codex install rollback, and generated artifacts.
- Security model:
  - Create `docs/security-model.md` covering local-first boundary, sidecar packet boundary, redaction limits, sensitive paths, memory storage, and human review requirements.
- Release docs:
  - Keep historical release notes, but add a current release checklist for 1.2+.
  - Update stale verification counts in `docs/handoff.md` and `docs/hipson-progress.md`.

## 11. Dependency & Tooling Recommendations

- Dependencies to keep:
  - Empty runtime dependency list in `pyproject.toml`.
  - `setuptools` build backend and package data configuration.
  - `uv` lock and dev workflow.
  - `pytest`, `ruff`, `mypy`, `bandit`, `pip-audit`, `mutmut` as dev tooling.
- Dependencies to review/remove:
  - None recommended for runtime removal because there are no runtime dependencies.
  - Periodically review vendored skill assets for size, licensing, and current relevance.
- Tooling to add:
  - Provenance verification command for vendored skills.
  - Coverage reporting after test split.
  - Optional docs link/reference checker.
  - Optional generated CLI docs.
  - Optional pre-commit config if it does not complicate local-first setup.
- Tooling to avoid:
  - Heavy runtime frameworks for config validation unless the project deliberately accepts runtime dependencies.
  - Provider SDKs unless stdlib HTTP becomes too limiting.
  - Automatic free-model routing for sensitive or broad context, consistent with `docs/model-routing.md`.
  - Broad "awesome list" skill ingestion without review, consistent with `docs/skill-library.md`.
- Rationale:
  - The project differentiates itself by being local, portable, dependency-light, and auditable. Add tooling only where it strengthens those properties.

## 12. Open Questions

- Should Hipson remain permanently runtime dependency-free, or is a small dependency acceptable for typed config/schema validation?
- Should provider URL configuration allow arbitrary HTTP(S), or only HTTPS plus explicit localhost/test exceptions?
- What mutation survivor threshold should block CI?
- Should vendored skills stay in the main repo, or should very large assets move to an optional package/source while keeping provenance?
- Should local memory become a first-class packet input in 1.2, or remain manual until stronger stale-context controls exist?
- Should `hipson route` command suggestions prefer bare `hipson` for installed users or `uv run hipson` for source checkout users?
- Should sidecar LLM routing ever be allowed by default, or remain explicit forever?
- Should docs treat `docs/hipson-progress.md` as historical state or keep it continuously current?
- Should release artifacts be signed before the next public release?

## 13. Immediate Next Actions

1. Fix router token matching and add route fixtures for implementation, verify, UI, and security tasks.
2. Split the broad helper test file into domain-specific test files while preserving all passing tests.
3. Harden provider/redaction boundaries: HTTPS-only defaults, redacted provider error bodies, and explicit readiness checks.
4. Add a minimal SQLite session store skeleton in `src/hipson/session.py`.
5. Add a minimal tool registry skeleton with `repo.scan`, `memory.search`, and `packet.review.create`.

Items 4 and 5 are documentation-approved next implementation targets; they are not currently implemented.

## 14. Strategic Expansion: Persistent Agent Runtime

Assumption: "Hermes-like" means a persistent AI engineering agent runtime: session-aware, tool-driven, memory-backed, safety-gated, and able to orchestrate multi-step engineering work over time. This section is a strategic expansion track, not a claim about current implementation. The current repository implements a strong local-first CLI foundation through `src/hipson/cli.py`, repo scanning and packet generation in `src/hipson/project.py`, sidecar/provider handling in `src/hipson/agents.py`, JSONL memory in `src/hipson/memory.py`, workflow routing in `src/hipson/router.py`, redaction in `src/hipson/redaction.py`, and Codex workflow installation in `src/hipson/codex_install.py`.

The expansion should preserve Hipson's existing design constraints:

- Local-first by default.
- Packet-first for external/provider context.
- Provider-free unless explicitly requested.
- Git diff and verification commands as the final contract.
- Secret redaction and sensitive-path refusal before persistence or provider calls.
- Human approval for security, external, shell, destructive, or ambiguous operations.

### 14.1 Target Architecture

The target architecture should evolve Hipson from command-oriented helpers into a persistent runtime that can run an agent loop over local repo state while still using the current CLI modules as safe tools.

```text
User / CLI
  |
  v
`hipson chat`
  |
  v
Runtime (`src/hipson/runtime.py`)
  |
  +-- Session store (`src/hipson/session.py`, SQLite)
  +-- Prompt assembler (`src/hipson/prompt.py`)
  +-- Tool registry (`src/hipson/tools/registry.py`)
  |     +-- repo tools -> wraps `src/hipson/project.py`
  |     +-- packet tools -> wraps `src/hipson/project.py` + `src/hipson/packets.py`
  |     +-- memory tools -> wraps `src/hipson/memory.py`
  |     +-- skill tools -> wraps `src/hipson/skills.py` + vendored `skills/`
  |     +-- sidecar tools -> wraps `src/hipson/agents.py`
  |     +-- shell tool -> guarded subprocess execution
  +-- Approval policy (`src/hipson/approvals.py`)
  +-- Sandbox/path policy (`src/hipson/sandbox.py`)
  +-- Provider abstraction (`src/hipson/providers/` or provider helpers)
  |
  +-- Optional scheduler (`src/hipson/scheduler.py`)
  +-- Optional gateway adapters (`src/hipson/gateway/`)
  +-- Optional MCP bridge (after internal tools stabilize)
  |
  v
Local files, git state, SQLite sessions, JSONL/SQLite memory, bounded packets,
optional provider calls, optional sidecar reports
```

- Persistent session store: stores sessions, messages, tool calls, approvals, and jobs in SQLite. This should not replace repo files, git diffs, or verification outputs as source of truth. It should preserve compact runtime history and auditability.
- Agent loop: loads a session, assembles a prompt, exposes a bounded set of tools, calls a provider/model, validates requested tool calls, executes allowed tools, persists all steps, and returns an answer. MVP command: `hipson chat`.
- Prompt assembler: builds a small, stable prompt from system policy, repository context, selected skills, memory summaries, current user request, and available tools. It should use existing docs such as `AGENTS.md`, `SKILLS.md`, `ORCHESTRATOR.md`, and selected skill files as data.
- Tool registry: typed stdlib-only wrapper layer over existing Hipson functions. Tools must declare input schema, output shape, risk level, approval requirement, and whether they may read/write/external/exec.
- Memory retrieval/injection: starts by wrapping current `src/hipson/memory.py`; later can migrate or mirror into SQLite. Memory injection should be explicit, bounded, and provenance-labeled.
- Skill runtime: reads and indexes skill metadata from `skills/` and packaged workflow assets. It should not blindly execute skill instructions; it should expose skill text as reference data for prompt assembly.
- Approval/sandbox layer: blocks or asks before external calls, shell execution, writes outside generated paths, sensitive paths, and dangerous operations.
- Optional scheduler: can run periodic local jobs later, such as repo health scan, memory compaction, stale progress reminders, or queued sidecar dry-runs. This is not required for MVP.
- Optional gateway adapters: CLI should be first. Telegram, Discord, web, or other gateways should be adapters over the internal runtime only after local CLI behavior is stable.
- Optional MCP bridge: MCP should expose stable internal tools later; it should not be the first runtime abstraction.
- Provider abstraction: current OpenRouter code in `src/hipson/agents.py` proves a provider path exists, but the runtime needs a narrower provider interface for chat/tool-call loops.
- Sidecar integration: current sidecars remain advisory. The runtime may call sidecar route/run tools, but should keep packet boundaries and approval gates.

### 14.2 New Core Modules

The following modules should be added incrementally. Each should wrap existing code first and avoid changing product behavior until tests pin contracts.

| Module | Purpose | Existing code it wraps or depends on | Risk level | First tests to add |
|---|---|---|---|---|
| `src/hipson/runtime.py` | Own the minimal agent loop for `hipson chat`: load session, assemble prompt, call provider, validate/execute tools, persist steps, return answer. | `src/hipson/cli.py`, `src/hipson/agents.py`, new `session.py`, `prompt.py`, tool registry. | High | Loop with fake provider; no-tool answer; one read-only tool call; invalid tool call rejected; transcript persisted. |
| `src/hipson/session.py` | SQLite session store for sessions, messages, tool calls, jobs, and runtime audit trail. | Current JSONL memory patterns in `src/hipson/memory.py`; `sqlite3` stdlib. | Medium | Create/open DB; insert session/message/tool call; search messages; schema migration idempotence; no secret leakage in persisted test fixture. |
| `src/hipson/prompt.py` | Assemble bounded prompts from system policy, repo state, memory, skills, and tool specs. | `AGENTS.md`, `SKILLS.md`, `ORCHESTRATOR.md`, `src/hipson/router.py`, `src/hipson/skills.py`, `src/hipson/memory.py`. | High | Token/char budget truncation; prompt injection text treated as data; selected skill inclusion; memory provenance labels. |
| `src/hipson/tools/registry.py` | Define `ToolSpec`, `ToolResult`, registration, validation, risk metadata, and dispatch. | Existing command functions in `src/hipson/project.py`, `agents.py`, `memory.py`, `skills.py`. | High | Register/list tools; reject duplicate names; validate missing args; approval metadata; dispatch fake tool. |
| `src/hipson/tools/repo.py` | Expose repo scan and changed-file tools. | `build_scan`, `changed_files`, `git_root`, `discover_commands` from `src/hipson/project.py`. | Medium | `repo.scan` clean repo; changed files; sensitive path redaction; missing path failure. |
| `src/hipson/tools/packets.py` | Expose review/executor packet creation as runtime tools. | `command_review_packet`, `command_executor_packet`, `compile_review_packet`, `compile_executor_packet`. | Medium | Review packet output path under allowed generated dir; exec packet requires allowed edit scope; redaction before write. |
| `src/hipson/tools/memory.py` | Expose memory search/add tools. | `src/hipson/memory.py`. | Medium | Search returns bounded results; add refuses sensitive sources; proposed memory requires approval where policy says so. |
| `src/hipson/tools/skills.py` | Expose skill list/view/use reference tools. | `src/hipson/skills.py`, `skills/`, `src/hipson/assets/codex-workflow-kit/skills/`. | Medium | List skill metadata; view bounded skill text; generated/mutants dirs ignored; missing skill handled. |
| `src/hipson/tools/sidecar.py` | Expose sidecar route/run as approval-gated tools. | `src/hipson/agents.py`, `config/agents.json`, `src/hipson/assets/config/agents.json`. | High | Route dry-run; run requires external approval; sensitive packet refused; provider error redacted. |
| `src/hipson/approvals.py` | Centralize risk policy for read/write/external/exec/dangerous tools. | Existing safety rules in `AGENTS.md`, `SKILLS.md`, `docs/agent-provider-model.md`, `src/hipson/redaction.py`. | High | Default policy matrix; dry-run exception; generated-path write auto approval; dangerous blocked. |
| `src/hipson/sandbox.py` | Enforce path allow/deny rules, generated-path boundaries, sensitive path refusal, and shell allowlists. | `src/hipson/redaction.py`, `src/hipson/project.py` path helpers. | High | `.env` and `.ssh` blocked; `runs/`, `scans/`, `memory/` generated writes allowed where appropriate; path traversal rejected. |
| `src/hipson/learning.py` | Propose memory notes and skill candidates from completed sessions, requiring approval before persistence. | `src/hipson/memory.py`, `src/hipson/skills.py`, session DB. | Medium/High | Proposals generated but not auto-written; approval required; redaction before candidate display/persist. |
| `src/hipson/scheduler.py` | Optional local job/tick runner for scan, memory compaction, reminders, and maintenance tasks. | Session DB jobs table; `src/hipson/project.py`; future runtime APIs. | Medium | Create/list/run due job; idempotent tick; no background daemon required; failures persisted. |
| `src/hipson/gateway/` | Optional adapters over the runtime, starting with CLI gateway interface. Leave Telegram/Discord for later. | New runtime API; `src/hipson/cli.py`. | Medium | CLI gateway passes messages to runtime; adapter cannot bypass approvals; no provider call in dry-run tests. |

Assumption: a provider abstraction may live under `src/hipson/providers/` even though the requested module list does not name it. If keeping file count smaller, start with provider protocol/helpers inside `runtime.py` or `agents.py` and extract later.

### 14.3 Runtime Data Model

Use SQLite through Python stdlib `sqlite3`. The MVP should be one local DB under Hipson home, for example `~/.config/hipson/runtime.sqlite`, with an override for tests. Keep JSON payloads as TEXT to avoid extra dependencies.

Minimal schema proposal:

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  cwd TEXT NOT NULL,
  repo_root TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_calls (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
  tool_name TEXT NOT NULL,
  input_json TEXT NOT NULL,
  output_json TEXT NOT NULL DEFAULT '{}',
  risk_level TEXT NOT NULL,
  approval_status TEXT NOT NULL DEFAULT 'not_required',
  status TEXT NOT NULL DEFAULT 'pending',
  error TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
  scope TEXT NOT NULL,
  repo TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,
  summary TEXT NOT NULL,
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  tags_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL DEFAULT 1.0,
  approval_status TEXT NOT NULL DEFAULT 'approved',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_runs (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  skill_name TEXT NOT NULL,
  source_path TEXT NOT NULL,
  input_summary TEXT NOT NULL DEFAULT '',
  output_summary TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'completed',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  schedule TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  run_after TEXT,
  last_run_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session_created
  ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session_created
  ON tool_calls(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_memories_repo_scope
  ON memories(repo, scope);
CREATE INDEX IF NOT EXISTS idx_jobs_status_run_after
  ON jobs(status, run_after);
```

FTS is useful but optional. If enabled, use SQLite FTS5 only when available and fall back to LIKE/token search otherwise:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
USING fts5(session_id UNINDEXED, content, content='');

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
USING fts5(repo UNINDEXED, scope UNINDEXED, summary, content='');
```

Design constraints:

- Do not store raw provider secrets.
- Redact message/tool output before persistence when content may include scan output, packet text, provider errors, or sidecar output.
- Keep full repo files out of SQLite; store paths, summaries, and bounded snippets only.
- Keep generated packets/reports in files under `runs/` or `scans/`, with DB rows pointing to paths.

### 14.4 Tool Registry Design

Use a stdlib-only registry. The design below is a specification, not current product code.

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, object]
    risk_level: str
    approval_required: bool
    handler: Callable[[dict[str, object], ToolContext], "ToolResult"]

@dataclass
class ToolResult:
    ok: bool
    output: dict[str, object]
    summary: str
    error: str = ""
    artifacts: list[str] = field(default_factory=list)
    redacted: bool = True
```

Initial tools:

| Tool | Input | Output | Risk level | Approval requirement | Existing module/function to wrap |
|---|---|---|---|---|---|
| `repo.scan` | `path`, `include_diff`, `diff_lines` | redacted scan Markdown, changed files, discovered commands, artifact path optional | read | auto | `src/hipson/project.py: build_scan`, `resolve_project` |
| `repo.changed_files` | `path` | changed/untracked file list with sensitive paths summarized | read | auto | `changed_files`, `git_root` |
| `packet.review.create` | `project`, `title`, `scope`, `include_diff`, `output` | packet path and summary | write | auto only under allowed generated paths such as `runs/` | `command_review_packet`, `compile_review_packet` |
| `packet.exec.create` | `project`, `title`, `goal`, `allowed_edit`, `acceptance`, `verification`, `output` | packet path and summary | write | auto only under allowed generated paths; require explicit allowed edit scope | `command_executor_packet`, `compile_executor_packet` |
| `memory.search` | `query`, `repo`, `scope`, `limit` | bounded memory results | read | auto | `src/hipson/memory.py: search_notes` |
| `memory.add` | `scope`, `repo`, `kind`, `summary`, `tags`, `sources`, `confidence` | memory id and redacted summary | write | require approval when proposed by agent; user-initiated can auto write | `add_note` |
| `sidecar.route` | `task`, `risk`, `context_chars`, `sensitive`, optional LLM dry-run flags | candidate agents or redacted LLM-router summary | external if `--llm`, read otherwise | auto for deterministic route; approval for provider-backed LLM route | `src/hipson/agents.py: route_agents`, `route_with_llm` |
| `sidecar.run` | `agent`, `packet`, `output`, `dry_run`, `max_packet_chars` | report path or dry-run preview | external | explicit approval unless dry-run | `src/hipson/agents.py: command_run`, `read_packet`, `write_report` |
| `skill.list` | `root`, filters | skill metadata list | read | auto | `src/hipson/skills.py: find_skill_files`, `validate_skills` |
| `skill.view` | `skill_name` or `path`, `max_chars` | bounded skill text and metadata | read | auto | `src/hipson/skills.py`, `skills/`, packaged skill assets |
| `shell.run` | `cmd`, `cwd`, `timeout`, `purpose` | stdout/stderr/code summary with redaction | exec | approval unless command is allowlisted read-only | new wrapper over `subprocess.run`; can reuse safety patterns from `src/hipson/project.py: run` |

Tool registry rules:

- Every exposed tool must have a stable JSON-serializable input and output contract before it can be used by the runtime.
- Every tool must declare risk level and approval behavior.
- Tool handlers must return structured output and a human summary.
- Tool outputs must be redacted before persistence.
- Tools must not read sensitive paths unless the policy explicitly permits a safe summary.
- Runtime should validate model-requested tool names and inputs before execution.
- Shell should start extremely narrow: allowlisted read-only commands only, with explicit approval for everything else.
- The initial runtime should expose only the tools needed for the active loop and stay under the active-tool budget; optional MCP exposure should come after the internal registry, contracts, and approvals are stable.

### 14.5 Minimal Agent Loop

First version target commands:

```bash
hipson chat
hipson chat -q "scan this repo and propose the next safe PR"
```

MVP requirements:

- Do not require MCP.
- Do not require Telegram, Discord, web gateways, or a background daemon.
- Do not require scheduler.
- Do not require sidecar/provider calls unless the user selected a provider-backed chat model.
- Keep all repo inspection available through internal tools first.

Loop:

1. Load or create session.
2. Resolve current working directory and repo root where available.
3. Assemble prompt from:
   - fixed Hipson runtime policy;
   - current user request;
   - compact session summary;
   - selected memory snippets, if explicitly enabled;
   - skill index or selected skill excerpts;
   - available tool specs and risk policy;
   - current repo facts from safe read tools if already known.
4. Expose selected tools to the model/provider.
5. Call provider/model through a narrow provider abstraction.
6. Validate requested tool calls:
   - tool exists;
   - input shape is valid;
   - risk level is allowed;
   - approval exists when required;
   - paths pass sandbox checks.
7. Execute allowed tools.
8. Persist messages and tool calls in SQLite.
9. Return answer with artifacts, commands run, and remaining approvals.
10. Propose memory or skill candidates only with approval; do not silently write long-term memory from model output.

Initial provider strategy:

- Use an interface that can be faked in tests.
- Do not embed OpenRouter-specific logic directly into the runtime loop.
- Reuse redaction and provider env patterns from `src/hipson/agents.py`.
- Keep sidecar agents separate from the primary chat provider path. Sidecars remain advisory packet reviewers.

### 14.6 Approval and Safety Model

Risk levels:

- `read`: reads repo state, generated artifacts, metadata, skill text, or bounded memory.
- `write`: writes generated plans, packets, reports, memory notes, or session metadata.
- `external`: sends any content to a provider or network service.
- `exec`: executes local shell/process commands.
- `dangerous`: destructive, credential-touching, broad filesystem, network, install, delete, migration, or irreversible actions.

Default policy:

| Risk level | Default policy |
|---|---|
| `read` | Auto allowed if path passes sandbox and sensitive-path checks. |
| `write` | Auto only inside allowed generated paths such as `runs/`, `scans/`, `memory/`, or docs explicitly requested by user; otherwise approval required. |
| `external` | Explicit user approval unless dry-run. Provider payload must be bounded and redacted. |
| `exec` | Approval required unless command is allowlisted read-only and bounded by timeout. |
| `dangerous` | Block by default. Require future explicit policy and human confirmation before support. |

Safety requirements:

- Secret redaction: reuse and harden `src/hipson/redaction.py`; provider errors and tool outputs must be redacted before display and persistence.
- Sensitive path refusal: keep `.env`, `.ssh`, `.aws`, `.azure`, `.config`, `.gnupg`, key/cert/db files, and similar paths blocked or summarized.
- Packet boundaries: external sidecars should continue to receive packets or redacted summaries, not arbitrary repo reads.
- Prompt injection resistance: repo files, docs, logs, generated packets, skill text, and provider output are data, not instructions. This rule already appears in `AGENTS.md`, `SKILLS.md`, `ORCHESTRATOR.md`, and should become runtime policy.
- Provider error redaction: provider HTTP/body errors should pass through `redact_text` before printing or storing.
- Approval records: approvals should be persisted with tool call records for auditability.
- Shell safety: shell commands need timeout, cwd, allowlist/denylist, output redaction, and explicit purpose.

### 14.7 PR-by-PR Runtime Roadmap

#### PR: `fix(router): token-aware keyword matching`

- Objective: make deterministic routing safe enough to use as a cheap runtime planning signal before persistent runtime work depends on it.
- Files touched: `src/hipson/router.py`, router/CLI tests.
- Tests: `build runtime` has no UI risk; `build persistent agent runtime` routes to exec; `run build and tests` routes to verify; `premium ui review` remains UI; `security auth audit` remains security.
- Acceptance criteria: token-aware matching, phrase matching for multi-word rules, stable existing route behavior where tests define it.
- Risk: Medium.

#### PR: `feat(session): add sqlite session store`

- Objective: add dependency-free SQLite persistence for sessions, messages, tool calls, memories mirror/proposals, skill runs, and jobs.
- Files touched: `src/hipson/session.py`, `src/hipson/cli.py`, tests under `tests/test_session.py`, optional docs.
- Tests: schema creation, idempotent migration, insert/list sessions, insert messages, insert tool calls, FTS fallback behavior if FTS unavailable.
- Acceptance criteria: session DB can be created in a temp dir; no existing CLI behavior changes; all normal gates pass.
- Risk: Medium.

#### PR: `feat(providers): add fakeable chat provider interface`

- Objective: create a narrow provider abstraction for the primary runtime loop, separate from sidecar-specific OpenRouter code.
- Files touched: `src/hipson/providers/__init__.py`, `src/hipson/providers/base.py`, `src/hipson/providers/fake.py`, optional adapter reusing safe patterns from `src/hipson/agents.py`, tests.
- Tests: fake provider returns deterministic assistant output; fake provider can emit one valid tool call; provider errors are redacted before persistence/display; runtime tests do not require network access.
- Acceptance criteria: `hipson chat` can be tested without external network calls; sidecar provider code remains separate from the primary chat provider path; provider outputs are treated as untrusted data.
- Risk: Medium/High.

#### PR: `feat(tools): add tool registry and wrap repo.scan/memory/packet tools`

- Objective: create stdlib-only tool registry and first read/write generated-artifact tools.
- Files touched: `src/hipson/tools/registry.py`, `src/hipson/tools/repo.py`, `src/hipson/tools/packets.py`, `src/hipson/tools/memory.py`, tests.
- Tests: tool registration, duplicate rejection, input validation, `repo.scan`, `repo.changed_files`, `packet.review.create`, `packet.exec.create`, `memory.search`, `memory.add`.
- Acceptance criteria: tools wrap existing functions without changing CLI output; write tools obey generated-path policy.
- Risk: High.

#### PR: `feat(approvals): add tool risk policy and shell approval gates`

- Objective: centralize approval and sandbox policy for tool execution before the prompt/runtime loop can execute model-requested tools.
- Files touched: `src/hipson/approvals.py`, `src/hipson/sandbox.py`, `src/hipson/tools/registry.py`, shell tool implementation, tests.
- Tests: read auto allowed, generated-path write allowed, external approval required, exec approval required, dangerous blocked, sensitive path refused.
- Acceptance criteria: runtime cannot execute external/shell/dangerous tools without policy approval; approvals are persisted.
- Risk: High.

#### PR: `feat(prompt): add prompt assembler with memory and skill index`

- Objective: build bounded runtime prompts from policy, session context, repo facts, memory snippets, skill index, and tool specs.
- Files touched: `src/hipson/prompt.py`, `src/hipson/tools/skills.py`, `src/hipson/skills.py`, tests, docs.
- Tests: prompt budget truncation, skill index rendering, selected skill excerpt rendering, memory provenance labels, injection text remains data.
- Acceptance criteria: prompt assembler is deterministic under fixed inputs and can be tested without provider calls.
- Risk: High.

#### PR: `feat(runtime): add minimal hipson chat`

- Objective: add `hipson chat` and `hipson chat -q "..."` with fake-provider-testable agent loop.
- Files touched: `src/hipson/runtime.py`, `src/hipson/cli.py`, `src/hipson/session.py`, `src/hipson/prompt.py`, `src/hipson/tools/`, tests.
- Tests: non-interactive `-q` answer path, one read-only tool call, invalid tool rejection, persisted transcript, fake provider behavior.
- Acceptance criteria: MVP chat starts only after session, provider abstraction, tool registry, approval policy, and prompt assembler are in place; it works without MCP, scheduler, daemon, Telegram, or Discord; no external call occurs in tests.
- Risk: High.

#### PR: `feat(skills): add skill list/view/use runtime commands`

- Objective: expose skills as bounded reference material in runtime tools and CLI commands.
- Files touched: `src/hipson/tools/skills.py`, `src/hipson/skills.py`, `src/hipson/cli.py`, tests, docs.
- Tests: skill list metadata, view bounded text, missing skill, ignored generated dirs, packaged workflow skill access.
- Acceptance criteria: runtime can list/view skills without treating skill text as instructions that override runtime policy.
- Risk: Medium.

#### PR: `feat(learning): propose memory and skill candidates from sessions`

- Objective: add an approval-gated learning loop that proposes memory notes and useful skill references after sessions.
- Files touched: `src/hipson/learning.py`, `src/hipson/session.py`, `src/hipson/memory.py`, tests.
- Tests: proposal generation from fake session; no automatic persistence; redaction before proposal; approval writes memory.
- Acceptance criteria: model-derived learning never writes durable memory without approval.
- Risk: Medium/High.

#### PR: `feat(scheduler): add cron tick jobs`

- Objective: add local `hipson scheduler tick` or equivalent for due jobs without a daemon requirement.
- Files touched: `src/hipson/scheduler.py`, `src/hipson/session.py`, `src/hipson/cli.py`, tests.
- Tests: create/list due jobs, tick executes a safe read job, failure persisted, no background process required.
- Acceptance criteria: scheduler is opt-in and local; no external service required.
- Risk: Medium.

#### PR: `feat(gateway): add CLI gateway interface, leave Telegram/Discord for later`

- Objective: define gateway adapter boundary over runtime, starting with CLI only.
- Files touched: `src/hipson/gateway/__init__.py`, `src/hipson/gateway/cli.py`, `src/hipson/runtime.py`, tests.
- Tests: CLI gateway sends message to runtime; gateway cannot bypass approval policy; fake provider path.
- Acceptance criteria: future gateways can reuse runtime without duplicating tool/session/approval logic.
- Risk: Medium.

#### PR: `feat(mcp): add optional MCP bridge after internal tools are stable`

- Objective: expose stable internal tools over MCP only after the registry and approvals are mature.
- Files touched: future `src/hipson/gateway/mcp.py` or `src/hipson/mcp_bridge.py`, `src/hipson/tools/registry.py`, docs, tests.
- Tests: list tools, call safe read tool, approval-gated tools rejected/pending, no secret leakage.
- Acceptance criteria: MCP is optional and cannot bypass internal risk policy.
- Risk: High.

### 14.8 Relationship To Existing Hardening Plan

The existing hardening roadmap in sections 1-13 remains the foundation and should not be displaced. The runtime track should begin only after the router, tests, redaction, and provider boundary are stable enough to be used as internal runtime tools.

Recommended sequencing:

Phase A:

- Router correctness.
- Test split.
- Mutation survivor reduction.
- Provider boundary hardening.
- Config validation.
- Redaction/provider error hardening.

Phase B:

- Session store.
- Provider abstraction.
- Tool registry.
- Approval policy skeleton.
- Prompt assembler.
- Initial repo/memory/packet tools.

Phase C:

- Minimal `hipson chat`.
- Sandbox layer.
- Skill runtime list/view/use.

Phase D:

- Learning loop.
- Scheduler.
- Gateway adapters.
- Optional MCP bridge.

Strategic rule: do not build a persistent runtime by bypassing the CLI's current safety model. Build it by wrapping and strengthening the existing modules:

- `src/hipson/project.py` becomes repo-state tools.
- `src/hipson/packets.py` becomes packet-generation tools.
- `src/hipson/memory.py` becomes memory tools and/or a migration path into SQLite.
- `src/hipson/agents.py` becomes sidecar/provider-adjacent tools behind stricter approvals.
- `src/hipson/redaction.py` becomes a mandatory runtime boundary.
- `src/hipson/router.py` remains a cheap deterministic planner and can help select runtime modes.
- `src/hipson/skills.py` becomes skill metadata and reference loading, not instruction override.

Implementation source of truth: docs/PERSISTENT_AGENT_RUNTIME_SPEC.md now defines the exact session schema, provider protocol, tool contracts, prompt assembly rules, approval policy, implementation sequence, and test matrix. Keep section 14 aligned with that spec before starting runtime implementation.

When roadmap ordering differs, docs/PERSISTENT_AGENT_RUNTIME_SPEC.md is the source of truth for implementation order.
