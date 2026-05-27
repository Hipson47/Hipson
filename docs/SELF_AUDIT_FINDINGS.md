# Hipson Self-Audit Findings

## 1. Executive Summary

Hipson's current working tree contains a broad persistent-runtime hardening diff: explicit fake chat mode, SQLite sessions, provider/fake abstractions, tool registry contracts, path-aware approvals, prompt separation, scheduler, and an optional MCP-style adapter. Local Hipson commands show the core CLI is usable. Later local-router work superseded the original fake/fail-closed default for supported provider-free engineering tasks.

The highest-value remaining repair package is focused mutation/fault-injection survivor triage for runtime-critical safety boundaries. The current tests cover many negative paths, but `docs/AUDIT_CONTEXT_FOR_HIPSON.md` records a time-boxed mutmut run that timed out with partial survivors in approvals, sandbox, registry, provider, prompt, and runtime helpers. Before real-provider work, those helpers need more requirement-level tests that catch small logic inversions.

## 2. Current Verified State

- Repository: `/home/hipson47/code/Hipson` on branch `main`.
- Working tree: existing unstaged runtime/provider/tool/scheduler/test/docs changes plus untracked `tests/test_session.py`; this self-audit preserves those changes as the current baseline.
- `uv run hipson doctor`: passed; reported Python 3.12.3, Hipson 1.1.0, git ok, Codex home `/home/hipson47/.codex-app`, Hipson home `/home/hipson47/.config/hipson`, optional sidecar env found, assets ok, 50 skills checked and 0 failed.
- `uv run hipson skill validate`: passed; 50 `SKILL.md` files checked, 0 failed.
- Original pre-router smoke: `uv run hipson chat -q "scan this repo"` failed closed with `No chat provider is configured...`. Later local-router smoke now executes supported safe repo-scan requests locally.
- `HIPSON_HOME=<temp> uv run hipson chat --fake -q "offline runtime smoke"`: succeeded with `Fake/offline mode: Fake provider response`.
- `uv run hipson scheduler --help`: shows opt-in `create`, `list`, and `tick` commands, not a daemon.
- `uv run pytest -q`: passed, 201 tests.
- `uv run ruff check .`: passed.
- `uv run mypy src/hipson`: passed.
- `uv run bandit -q -r src/hipson -c pyproject.toml`: passed.
- `python -m compileall src/hipson`: passed.
- `uv run python scripts/run_tests.py`: passed, 201/201.
- `timeout 180s uv run mutmut run --max-children 2`: timed out with exit 124 after reaching roughly 1,597/2,219 mutants; last observed progress showed roughly 1,381 killed and 216 surviving. Generated `mutants/` artifacts were removed afterward.

## 3. Hipson Command Results

- `uv run hipson route --task "audit Hipson persistent runtime safety, tests, docs, and CLI observability" --json`: returned `mode: review`, `risk: normal`, `recommended_skill: review-packet`, and recommended `hipson scan . --include-diff` plus review-packet generation.
- `uv run hipson scan . --include-diff`: succeeded and reported the large current diff across runtime, approvals, sandbox, provider, prompt, session, scheduler, MCP, tools, tests, and docs.
- `uv run hipson packet review . --title "Hipson Self-Audit" --scope "current runtime safety and observability gaps" --include-diff -o runs/self-audit-review-packet.md`: succeeded and wrote `runs/self-audit-review-packet.md`.
- `uv run hipson memory list`: succeeded and reported `No memory notes found.`
- `uv run hipson skill list`: succeeded and listed packaged/external skills.
- `uv run hipson sidecar route --task "audit runtime safety and repair plan" --risk security --limit 3`: succeeded using deterministic routing and returned `architect_max`, `architect_strong`, and `reviewer_cheap`.

## 4. Source/Test/Docs Drift

- `README.md` documents core 1.1 commands and sidecars but does not document `hipson chat`, `hipson scheduler`, runtime SQLite behavior, or fake-only chat limitations.
- `docs/PERSISTENT_AGENT_RUNTIME_SPEC.md` still labels scheduler, gateway, MCP bridge, and learning as future/proposed while implementation files now exist in `src/hipson/scheduler.py`, `src/hipson/gateway/`, and `src/hipson/learning.py`.
- `docs/AUDIT_FINDINGS_BACKLOG.md` records fixed P0/P1 hardening items and an open P1 mutation-survivor triage item; this matches the current next repair package.

## 5. Runtime Safety Findings

### F-001
- Severity: P1
- Title: Focused mutation survivor triage remains open for runtime-critical helpers.
- Evidence: `docs/AUDIT_CONTEXT_FOR_HIPSON.md` records `timeout 180s uv run mutmut run` exiting 124 after generating 2,199 mutants, with partial survivors/no-tests/not-checked/timeouts in `hipson.approvals`, `hipson.sandbox`, `hipson.tools.registry`, `hipson.agents`, `hipson.prompt`, and `hipson.runtime`. `pyproject.toml` configures those runtime-critical paths under `[tool.mutmut]`.
- Why it matters: Logic inversions in approval, path, contract, redaction, prompt, or runtime rejection helpers can undermine safety while ordinary happy-path tests still pass.
- Recommended fix: Add requirement-level tests for high-risk helpers first; rerun focused mutation in smaller batches later.
- Tests to add: Provider URL/redaction helper tests, runtime provider-tool payload and multi-rejection tests, registry type/contract boundary tests, sandbox symlink/sensitive/generated-path tests.
- Status: partially fixed-in-this-pass. New focused tests were added, but full survivor triage remains open because the mutation run timed out and partial results still show survivors/not-checked mutants.

### F-002
- Severity: P2
- Title: Runtime observability CLI remains incomplete.
- Evidence: `src/hipson/cli.py` defines `chat`, `skill`, `scheduler`, `memory`, `packet`, `sidecar`, and core commands, but no top-level `session` or `tool` commands. `docs/PERSISTENT_AGENT_RUNTIME_SPEC.md` marks `hipson session list/show/search` and `hipson tool list` as Next.
- Why it matters: Developers cannot inspect runtime sessions or registry state through first-class read-only commands, making debugging harder.
- Recommended fix: Add read-only `hipson session list/show` and `hipson tool list` after safety tests are stronger.
- Tests to add: CLI tests using temp SQLite DB and bounded/redacted output.
- Status: deferred

## 6. Provider/Sidecar Findings

### F-003
- Severity: P1
- Title: Provider/sidecar hardening has targeted tests but direct helper mutation coverage should be strengthened.
- Evidence: `src/hipson/agents.py` centralizes `validate_provider_base_url`, `bounded_redacted_provider_text`, and `escape_untrusted_data_delimiters`; `tests/test_hipson_helpers.py` has integrated provider tests, but the mutmut results recorded in `docs/AUDIT_CONTEXT_FOR_HIPSON.md` still identified provider-adjacent survivors.
- Why it matters: Provider error redaction and URL validation are the main boundary before any real-provider sidecar usage.
- Recommended fix: Add direct regression tests for HTTPS acceptance, remote HTTP rejection, explicit localhost HTTP opt-in, malformed schemes, error-body redaction/truncation, and untrusted delimiter escaping.
- Tests to add: Direct tests in `tests/test_hipson_helpers.py`.
- Status: fixed-in-this-pass for direct URL/redaction/delimiter helper coverage; full provider mutation survivor triage remains part of F-001.

## 7. Prompt/Injection Findings

### F-004
- Severity: P1
- Title: Prompt separation is implemented, but mutation survivor triage should pin structural guarantees.
- Evidence: `src/hipson/prompt.py` returns system/user messages via `assemble_prompt_messages`; dynamic content is wrapped in `<untrusted_data>` blocks and delimiter escaping exists. `tests/test_prompt.py` covers system/user separation and delimiter escaping.
- Why it matters: Small changes to role separation or delimiter escaping would be high-impact once a real provider adapter exists.
- Recommended fix: Keep direct prompt-injection tests and extend runtime tests that inspect the actual `ProviderRequest` sent to fake providers.
- Tests to add: Runtime provider request tools/message structure assertions.
- Status: partially fixed-in-this-pass. Runtime now has a provider-request structure assertion for role-separated messages and stable tool descriptors; prompt mutation survivor triage remains part of F-001.

## 8. Session/Persistence Findings

### F-005
- Severity: P2
- Title: FTS setup exists, but search/population behavior is not implemented.
- Evidence: `src/hipson/session.py` creates `messages_fts` and `memories_fts` in `_setup_fts`, but no public search API populates or queries those tables. `docs/AUDIT_CONTEXT_FOR_HIPSON.md` lists no FTS search/population test.
- Why it matters: The spec mentions session search, but current FTS is only a placeholder; claiming search readiness would be misleading.
- Recommended fix: Either implement FTS-backed session search in a later observability PR or label FTS as schema placeholder only.
- Tests to add: Future temp-DB session search tests or explicit placeholder tests.
- Status: deferred

## 9. CLI Observability Findings

### F-006
- Severity: P2
- Title: Scheduler approval remains a simple boolean.
- Evidence: `src/hipson/scheduler.py` stores `approved` in job payload and blocks `external`, `exec`, and `dangerous` jobs, but does not persist a durable approval actor/reason record.
- Why it matters: Scheduler jobs remain opt-in and bounded, but audit trails are not strong enough for higher-risk scheduled work.
- Recommended fix: Keep scheduler read/low-risk only until durable approvals are designed.
- Tests to add: Existing scheduler tests should continue to block dangerous jobs even with `approved=True`; future tests should cover durable approval metadata.
- Status: deferred

## 10. Test Quality and Mutation Findings

### F-007
- Severity: P1
- Title: Current tests need more direct fault-injection coverage for helper-level logic.
- Evidence: `tests/test_runtime.py`, `tests/test_approvals.py`, `tests/test_tools.py`, `tests/test_prompt.py`, `tests/test_mcp.py`, `tests/test_scheduler.py`, and `tests/test_session.py` cover many negative paths, but partial mutmut results still showed high-risk helper survivors and timeouts.
- Why it matters: Safety boundaries rely on compact helper functions where surviving mutants often mean missing assertion pressure.
- Recommended fix: Add tests that assert observable behavior for helper outputs and edge cases instead of mirroring implementation internals.
- Tests to add: Runtime tool payload shape, rejection summary cap, registry union/list/null type checks, direct sandbox symlink/sensitive/write checks, provider redaction helper tests.
- Status: partially fixed-in-this-pass. These tests were added and pass; remaining low-level mutmut survivors still need smaller-batch triage.

## 11. Documentation Findings

### F-008
- Severity: P3
- Title: Docs need reconciliation after code settles.
- Evidence: `README.md` omits runtime commands; `docs/PERSISTENT_AGENT_RUNTIME_SPEC.md` still describes some implemented modules as future/proposed; `docs/AUDIT_FINDINGS_BACKLOG.md` has current status notes but remains an audit backlog, not user-facing runtime docs.
- Why it matters: Users and future agents need a truthful line between implemented fake/offline runtime pieces and future real-provider readiness.
- Recommended fix: Do a docs/spec alignment pass after mutation/fault-injection triage.
- Tests to add: None.
- Status: deferred

## 12. Prioritized Backlog

1. P1: Rerun focused mutation in smaller module/function batches and triage high-risk survivors that remain in approvals, sandbox, registry, provider, prompt, and runtime helpers.
2. P2: Add direct tests or document equivalent survivors for low-risk mutation results in sidecar scoring/routing helper code.
3. P2: Add read-only runtime observability commands for sessions and tool registry state.
4. P2: Decide whether FTS search is a real session feature or a schema placeholder.
5. P2: Replace scheduler boolean approval with durable approval metadata before expanding scheduler scope.
6. P3: Reconcile README/spec/audit docs with actual fake/offline runtime status.
