# Changelog

## Unreleased

- Add `hipson task run`: a stdlib worker runner for Linux, macOS and native Windows with
  project binding, owner identity, atomic claims, signal forwarding, structured-result fallback,
  and Codex rollout facts. POSIX process groups preserve orphan claims; Windows suspends workers
  until Job Object attachment and kills their entire tree on runner death. Native runner tests
  run in the existing Ubuntu/macOS/Windows CI matrix; platform smoke includes runner checks.

## 1.2.1 - 2026-10-26

- **PyPI release workflow**: `.github/workflows/release.yml` publishes version tags through PyPI
  Trusted Publishing (no stored token) after checking that the tag matches the package version,
  `twine check --strict` and a wheel smoke test; maintainer setup in `docs/RELEASING.md`.
- **Package metadata**: current description, keywords and project URLs; README links and images are
  absolute so the PyPI page renders them.

## 1.2.0 - 2026-10-26

Release notes: [docs/release-1.2.md](docs/release-1.2.md).

- **External audit 2026-10-26**
  - Refuse `done` with active assignments, claims, or open blocking/major findings
    in CLI and MCP. Require a reason for an audited force override and record
    remaining minor/info findings on closure.
    Atomically reopen done tasks when severe findings are added or reopened,
    including reviewer-result ingestion, with a finding-linked `reopened` event.
  - Keep verified live owners' claims beyond lease expiry; retain lease recovery
    for owner-less and unverifiable assignments.
  - Bind the Bash wrapper to the task project before assignment, with exit 76 for
    mismatches and an explicit recorded `--allow-project-mismatch` escape hatch.
    Scrub inherited Git overrides during discovery and check actual cwd containment.
  - Redact actor/lead labels, refs and other ledger metadata before persistence
    and mask legacy text on reads without rewriting stored evidence.
  - Check git arguments for branch mutations, output writes and helper execution;
    create installer backups exclusively so same-second saves cannot overwrite.
    Harden allowlisted Git execution against implicit diff/textconv, pager and
    filesystem-monitor helpers; require the executor guarantee for read-only grants.
  - Clarify beta coordination limits and the four-module mutation scope.

- **Cross-platform installation**
  - Support macOS CLI, ledger, MCP, evidence and the Bash worker wrapper, including
    Python session/UUID fallbacks, BSD utilities and boot-session identity.
  - Support native Windows CLI, ledger, MCP and evidence with lease-based worker
    recovery. Query PID liveness through read-only Windows process handles; never
    call `os.kill(pid, 0)` on Windows. Keep PID namespaces Linux-only.
  - Use UTF-8 CLI/MCP stdio with LF responses and portable relative paths.
    Add macOS/Windows Python 3.12 CI, real CLI/ledger/MCP smoke and platform regressions;
    retain Ubuntu mutation/audit gates and Linux CI's zero-skip policy.
  - Write generated packets/reports with UTF-8 LF bytes, close session connections after
    failed initialization and test-created SQLite connections deterministically, and resolve
    native CLI executables for MCP tests. Enforce exact macOS/Windows CI skip allowlists.
  - Add per-OS global/clone quick starts and Claude Code MCP registration in
    README and `docs/INSTALL.md`; recommend WSL2 for Windows worker execution.

- Add an opt-in agent e2e harness with Codex and offline fake drivers, parser
  lead-flow/conflict/recovery missions, state/event oracles, and friction reports.
  Isolate HOME and login-shell PATH; report recorder bypasses as invalid
  measurements outside agent pass rates. Make `agent-run.sh --help` exit 0.
  Clean surviving worker groups after runner exit; avoid bypass errors for skipped
  shell branches; reject deleted parser files and builders claiming unrelated
  scopes. Filter inherited credentials, redact retained logs/reports, and support
  offline replay of saved observations with current oracles.

- **Task ledger and recovery**
  - Let findings resolve through another task's assignment in CLI and MCP; record
    the fixing assignment and task together in resolution events while preserving
    same-task resolution and source-assignment validation.
  - Record the launcher's PID namespace and skip PID/group liveness probes across
    namespaces. Add transactional v3→v4 migration and CLI/MCP/schema support;
    legacy rows keep their prior behavior and leases remain available for recovery.
  - Generate bounded worker briefs and running-worker context from the ledger
    through read-only CLI commands and MCP tools (S5a-2), including findings,
    failed approaches, worker rules, and the structured return contract. Let
    agent-run ingest result files with a fallback that preserves process outcomes
    when worker evidence is invalid.
    Separate findings and assignment summaries from trusted brief instructions
    with an explicit evidence policy and an escaped `untrusted_data` boundary.
    CLI JSON and MCP retain structured fields, add `evidence_policy`, and mark
    finding `title`/`location`/`detail` and assignment `summary` in `untrusted_fields`;
    evidence cannot change permissions, scope, worker rules, or the return contract.
    Show assignment status once in brief history and preserve ledger status enums
    such as `in_progress` without Markdown escaping.
  - Make review findings and structured worker results ledger data (S5a-1): add
    finding lifecycle/filter CLI and gated MCP writes, strict redacted result
    ingestion, atomic reviewer findings, and compact task views with full JSON.
    Add a Codex-compatible strict result schema and transactional v2→v3 migration;
    keep legacy reads non-migrating and existing finish calls compatible.
  - Redact and bound actor labels and escape terminal controls in plain ledger
    views, preserving full JSON output.
  - Add a transactional SQLite ledger and `hipson task` CLI for lead-recorded
    tasks, assignments, overlapping path claims, lifecycle transitions, and
    append-only events.
  - Expose ten MCP `task.*` tools with explicit approval for all six writes,
    CLI-compatible JSON, a server-configured store, non-creating reads, and
    recoverable, redacted errors.
  - Reject blank task text and invalid assignment scopes in MCP before opening
    or migrating the store, using the ledger's shared semantic validators.
  - Recover orphaned assignments through CLI/MCP `task reap`/`task.reap`, with
    read-only CLI dry runs, optional PID/boot/host ownership, UTC leases, and
    automatic recovery before overlapping write claims are checked. Preserve
    claims while either the runner PID or its worker process group survives.
  - Cover the published worker runner with CLI integration tests for process
    results, stdin, claim conflicts, signal cleanup, leftover descendants, and
    recovery after the runner is killed. Verify cleanup when the runner leads a
    process group within another session and when an unreaped zombie remains.
  - Migrate v1 stores to v2 transactionally, preserving history. Keep CLI/MCP
    reads non-migrating and read-only: plain SQLite `mode=ro` connections that
    cooperate with concurrent writers. Remove the separate OFD guard, fall back
    on sidecar permission errors, and use direct read-only WAL access or validated
    in-memory snapshots in non-writable directories, sharing a 10 s retry budget.
- **Lead and worker instructions**
  - Add worker mode to agent instructions and installer output: lead-assigned
    workers stay within their brief, skip orchestration and progress/handoff
    notes, and return verification evidence without committing, pushing, or
    writing the task ledger. Preserve the standalone workflow outside worker mode.
- **Evidence and audit**
  - Serialize evidence appends with interprocess locking; recursively redact
    credential-key values and strings before hashing. Add optional unauthenticated
    actor/task attribution, task filters, and `hipson evidence verify`, preserving
    existing records and hashes. Redact nested work plans and release claim copies.
  - Validate finalized, redacted evidence with the reader's rules before append,
    rejecting malformed records without changing existing ledger bytes.
  - Complete MCP `hipson://latest-audit`; align audit/evidence ledger defaults
    with the work plan's project. Validate candidate shapes, isolate corrupt
    artifacts with diagnostics, and return a schema-valid empty audit bundle.
  - Split JSONL on physical newlines, preserving Unicode separators within records.
- **Documentation**
  - Move historical planning and audit material to `docs/archive/` with an archive index.
  - Index the live docs, document the lead/worker ledger flow, and correct CLI,
    path, provider, and historical-status guidance.
- **External skills**
  - Stop redistributing third-party skills (992 files from OpenAI, Anthropic and
    Vercel Labs catalogs, some under non-open licenses). `skills/external/manifest.json`
    now pins each source to an upstream commit, provenance records a tree hash per
    skill, and `scripts/fetch_external_skills.py` fetches and verifies them into the
    git-ignored `skills/external/` on demand.
- **CI and tooling**
  - Resolve worker-wrapper tests through the installed CLI as well as a checkout
    virtualenv, execute the missing-boot-id case, and fail Linux CI on skips.
    Report passed, skipped and failed outcomes honestly in the stdlib runner.
  - Validate every artifact schema keyword in the shared stdlib test validator,
    including nested items, additional properties, references, nullable types,
    patterns, bounds and formats. Fail on unknown keywords and add malformed-payload
    controls to prevent silent validation gaps.
  - Enforce exported mutmut results against an explicit dated score/population
    baseline and survivor/no-test/timeout caps. Include evidence code and tests
    in the bounded mutation selection; reject incomplete and abnormal runs.
    Set candidate timestamps explicitly in the foreign-project evidence control
    so project-filter mutations cannot survive filesystem timestamp ties.
  - Upgrade setuptools in CI (and require >=84.1.0 to build) and refresh vulnerable
    dev-tool locks so `pip-audit` passes.
  - Move the mutation gate to mutmut 3.8 and drop the `tests/conftest.py` stats
    workaround: 3.8 resolves source paths at startup, so cwd-changing tests no
    longer break stats collection.
  - Run CI on Node 24 actions: `actions/checkout@v7`, `actions/setup-python@v7`,
    `astral-sh/setup-uv@v10.3.2` (pinned; setup-uv no longer publishes major tags).

## 1.1.0

- Add agent-readable `SKILLS.md` playbook and packaged asset copy.
- Add deterministic `hipson route --task "..."` workflow router.
- Update Codex workflow assets to point agents at the router and playbook.
- Include router coverage in the configured mutmut target set.

## 1.0.0

- Harden runtime asset loading so Hipson never trusts project CWD for its own
  runtime assets.
- Make scan failures explicit for invalid paths.
- Redact quoted env-style secrets across scan, packet, memory, and sidecar
  paths.
- Add CLI subprocess timeouts and wheel/installed-package smoke coverage.
- Keep the Codex workflow toolkit canonical under
  `src/hipson/assets/codex-workflow-kit/`.
- Add optional LLM routing behind `hipson sidecar route --llm`.
- Add visual experience and HyperFrames video sidecar agents and skills.
- Add `uv` and `ruff` development workflow and CI coverage for Python 3.11 and
  3.12.
