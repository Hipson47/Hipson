# Runtime Implementation Audit — 2026-05

## 1. Executive Summary

The recent Persistent Agent Runtime work added substantial code: SQLite sessions, fake providers, tool registry, approvals/sandbox, prompt assembly, runtime loop, skill tools, learning proposals, scheduler, gateway, and an MCP-style bridge. The claimed local checks were largely reproducible: `uv run pytest -q` passed with 167 tests, `ruff`, `mypy`, configured Bandit, `compileall`, `scripts/run_tests.py`, `hipson doctor`, and `hipson skill validate` passed.

The implementation is not ready to trust as a real agent runtime. The CLI-visible `hipson chat -q "scan this repo..."` returns only `Fake provider response`; it does not exercise tool selection or repo scanning. The runtime API can execute injected tool calls in tests, but the public CLI is a fake-response smoke path. More importantly, `src/hipson/runtime.py:162` always passes `fake_provider=True` into the approval policy, so any future external tool registered into the runtime would be auto-allowed even when a real provider is injected. That is the largest safety issue before real-provider usage.

The security boundaries are partly real and partly skeletal. `ApprovalPolicy` checks risk levels and some path keys in `src/hipson/approvals.py:50-81`, and sandbox path checks reject traversal/sensitive paths in `src/hipson/sandbox.py:39-56`. However, the policy only checks `path`, `project`, `packet`, and `source` keys (`src/hipson/approvals.py:89`), while tools introduce other path-bearing inputs such as `memory_dir` and `root`. A manual audit probe confirmed `memory.search` with `memory_dir=str(Path.home())` is approved as read.

## 2. Scope and Method

This audit inspected the source of truth documents, current source, tests, and CLI behavior. The prior completion report was treated as unverified.

Commands used for baseline:

- `pwd`: `/home/hipson47/code/Hipson`
- `git branch --show-current`: `main`
- `git status --short`: existing dirty source/test changes plus untracked runtime/docs/test files.
- `git diff --stat`: only tracked modifications, 4 files and 424 insertions; this excludes the many untracked runtime files.
- `git ls-files --others --exclude-standard`: runtime docs, modules, and test files are untracked.
- `hipson route --task "audit persistent agent runtime implementation" --json`: recommended `review-packet`.

Evidence used:

- Contract: `docs/PERSISTENT_AGENT_RUNTIME_SPEC.md`
- Roadmap: `docs/PROJECT_DEVELOPMENT_PLAN.md`
- CLI docs: `README.md`
- Runtime source: `src/hipson/runtime.py`, `session.py`, `providers/`, `tools/`, `approvals.py`, `sandbox.py`, `prompt.py`, `learning.py`, `scheduler.py`, `gateway/`
- Tests: `tests/test_runtime.py`, `test_tools.py`, `test_approvals.py`, `test_prompt.py`, `test_skills.py`, `test_learning.py`, `test_scheduler.py`, `test_gateway.py`, `test_mcp.py`, and `tests/test_hipson_helpers.py`

## 3. Repository Snapshot

- Branch: `main`
- Dirty tracked files: `src/hipson/cli.py`, `src/hipson/router.py`, `src/hipson/skills.py`, `tests/test_hipson_helpers.py`
- Untracked docs: `docs/PERSISTENT_AGENT_RUNTIME_SPEC.md`, `docs/PROJECT_DEVELOPMENT_PLAN.md`
- Untracked runtime modules: `src/hipson/session.py`, `providers/`, `tools/`, `approvals.py`, `sandbox.py`, `prompt.py`, `runtime.py`, `learning.py`, `scheduler.py`, `gateway/`
- Untracked focused tests: `tests/test_approvals.py`, `test_gateway.py`, `test_learning.py`, `test_mcp.py`, `test_prompt.py`, `test_providers.py`, `test_runtime.py`, `test_scheduler.py`, `test_skills.py`, `test_tools.py`
- Ignored/generated files observed by `find`: `__pycache__` files from `compileall` and tests.

Important note: `git diff --stat` under-reports the implementation because untracked files are not included.

## 4. Verification Commands and Results

Passed:

- `uv run pytest -q`: `167 passed in 26.60s`
- `uv run ruff check .`: passed
- `uv run mypy src/hipson`: `Success: no issues found in 32 source files`
- `uv run bandit -q -r src/hipson -c pyproject.toml`: passed
- `python -m compileall src/hipson`: passed
- `uv run python scripts/run_tests.py`: `167/167 tests passed`
- `uv run hipson doctor`: passed; reported `hipson_home: /home/hipson47/.config/hipson` and sidecar env found
- `uv run hipson skill validate`: passed; 50 skills checked

Failed or intentionally skipped:

- `uv run bandit -q -r src/hipson`: failed with existing low-severity B404/B603 findings in `src/hipson/project.py:13` and `src/hipson/project.py:47`. The configured run passes because `pyproject.toml` skips B404/B603/B607.
- Optional focused mutation check skipped. Reason: mutmut creates mutation artifacts and is heavier than this audit-only pass; current `pyproject.toml` mutation config does not include the new runtime modules.
- Live provider/network checks skipped by requirement.

CLI smoke:

- `uv run hipson --help`: shows commands `doctor`, `scan`, `scan-many`, `route`, `chat`, `init`, `check-setup`, `skill`, `install`, `packet`, `sidecar`, `memory`, `scheduler`.
- `uv run hipson chat --help`: shows `--session-db`, `--session-id`, and `--fake-response`.
- `HIPSON_HOME=<temp> uv run hipson chat -q "scan this repo and propose the next safe PR"`: prints `Fake provider response`; no scan or tool execution occurs through CLI.
- `uv run hipson session list`: fails with argparse invalid choice; `session` command is not implemented.
- `uv run hipson tool list`: fails with argparse invalid choice; `tool` command is not implemented.
- `uv run hipson skill list`: succeeds and prints skill metadata.

## 5. Diff Summary

Tracked diff:

- `src/hipson/router.py`: token-aware keyword matching and build-intent special case.
- `src/hipson/cli.py`: adds `chat`, `skill list/view/use`, and `scheduler` commands.
- `src/hipson/skills.py`: adds skill metadata listing and bounded skill view.
- `tests/test_hipson_helpers.py`: adds router and session store tests.

Untracked implementation files:

- Session: `src/hipson/session.py`
- Providers: `src/hipson/providers/base.py`, `fake.py`
- Tools: `src/hipson/tools/registry.py`, `repo.py`, `packets.py`, `memory.py`, `skills.py`
- Safety: `src/hipson/approvals.py`, `src/hipson/sandbox.py`
- Runtime: `src/hipson/prompt.py`, `src/hipson/runtime.py`
- Later layers: `src/hipson/learning.py`, `src/hipson/scheduler.py`, `src/hipson/gateway/cli.py`, `src/hipson/gateway/mcp.py`

No package dependencies were added; `pyproject.toml` was not modified.

## 6. Roadmap Compliance Matrix

| Step | Status | Evidence | Gaps | Risk | Next action |
|---|---|---|---|---|---|
| 1. Router token matching | Implemented with caveat | `src/hipson/router.py:90-180`; tests in `tests/test_hipson_helpers.py:1959` | Build special-case runs before review rules, so phrases like "build review workflow" may route to exec unexpectedly. | Medium | Add more route fixtures for mixed build/review/security wording. |
| 2. SQLite session store | Implemented basic store | `src/hipson/session.py:16-97`, `115-245`; tests in `tests/test_hipson_helpers.py:1446` | FTS tables are created but not populated; no session CLI; no search API. | Medium | Add session CLI/search and FTS triggers or remove FTS claim. |
| 3. Fakeable provider interface | Implemented fake-only | `src/hipson/providers/base.py:11-50`, `fake.py:10-52`; tests in `tests/test_providers.py` | No real primary chat provider adapter; sidecar OpenRouter still separate and not hardened. | High | Add redacted provider adapter only after approval/path issues are fixed. |
| 4. Tool registry and wrappers | Partially implemented | `src/hipson/tools/registry.py:46-84`, wrappers in `repo.py`, `packets.py`, `memory.py`, `skills.py` | Output contracts are not structurally validated; no `packet.exec.create`, `memory.add`, sidecar tools, or shell tool. | High | Harden contracts and bound/redact outputs before expanding tools. |
| 5. Approvals/sandbox | Partially implemented, risky | `src/hipson/approvals.py:50-81`, `sandbox.py:39-56` | Path-bearing keys are incomplete; runtime hardcodes `fake_provider=True`; approval metadata is not fully persisted. | High | Make approval context explicit and schema-aware per tool. |
| 6. Prompt assembler | Implemented basic deterministic assembler | `src/hipson/prompt.py:11-87`; tests in `tests/test_prompt.py` | Tool summaries/session summary are not enclosed as untrusted data; no session history compaction. | Medium | Add prompt snapshot for malicious tool summaries/provider text. |
| 7. Minimal `hipson chat` | Partially implemented | `src/hipson/runtime.py:45-120`; CLI in `src/hipson/cli.py:228-246`; tests in `tests/test_runtime.py` | Public CLI always uses `FakeProvider.with_text`, so no CLI tool-call path; rejected tool calls are not clearly returned to user. | High | Add explicit offline/fake mode UX and real provider-disabled error path. |
| 8. Skills list/view/use | Implemented | `src/hipson/skills.py:82-170`, `src/hipson/tools/skills.py`; tests in `tests/test_skills.py` | `skill use` is a payload helper, not integrated into prompt selection flow. | Medium | Keep as reference-only; add docs and safer root/path policy. |
| 9. Learning proposals | Stub/facade, proposal-only | `src/hipson/learning.py:45-120`; tests in `tests/test_learning.py` | No approval/apply path; heuristic always maps generic "skill" to `hipson-workflow`. | Medium | Add explicit approval command before any durable writes. |
| 10. Scheduler tick jobs | Partially implemented, outside MVP | `src/hipson/scheduler.py:23-121`; CLI in `src/hipson/cli.py:296-350` | Not cron; no validation of timestamps; `--approved` is just a CLI flag, not an approval record. | Medium | Keep disabled/experimental until approval metadata is formalized. |
| 11. Gateway adapter | Implemented thin adapter | `src/hipson/gateway/cli.py:11-35`; tests in `tests/test_gateway.py` | Only CLI adapter class; no public gateway command beyond `chat`. | Low | Keep thin; do not add network gateways yet. |
| 12. MCP bridge | Stub/facade only | `src/hipson/gateway/mcp.py:18-104`; tests in `tests/test_mcp.py` | Not an MCP server/protocol bridge; no transport; non-read tools always rejected. | Medium | Treat as internal adapter, not MCP feature, until stable. |

## 7. Architecture Integrity Review

The target architecture is present as modules, but several modules are shallow.

Coherent parts:

- `src/hipson/runtime.py:45-120` orchestrates session creation, prompt assembly, provider completion, tool-call handling, persistence, and bounded iterations.
- Provider abstraction is separate from sidecar/OpenRouter: `src/hipson/providers/base.py` has dataclasses/protocol, while `src/hipson/agents.py` keeps sidecar OpenRouter code.
- Registry dispatch is centralized in `src/hipson/tools/registry.py:61-84`.
- Runtime checks approval before `registry.run` in `src/hipson/runtime.py:162-174`.

Integrity gaps:

- Runtime passes `fake_provider=True` unconditionally to approvals (`src/hipson/runtime.py:162`). This couples approval safety to an assumption that all runtime providers are fake.
- `hipson chat` always constructs `FakeProvider.with_text(args.fake_response)` (`src/hipson/cli.py:240-242`), so the CLI does not expose model/tool behavior.
- Registry does not enforce approvals itself; every caller must remember to use `ApprovalPolicy`. Runtime, scheduler, and MCP do; direct registry callers do not.
- Scheduler and MCP were implemented before the runtime is production-worthy, even though the spec marks them future/optional.

## 8. Session Store Review

Strengths:

- Uses stdlib `sqlite3` (`src/hipson/session.py:5-7`).
- Creates required tables: sessions, messages, tool_calls, memories, skill_runs, jobs (`src/hipson/session.py:16-97`).
- Enables foreign keys on open (`src/hipson/session.py:106-110`).
- Redacts message content and metadata before persistence (`src/hipson/session.py:149-155`).
- Redacts tool input/output/error before persistence (`src/hipson/session.py:202-207`).
- Tests verify schema, CRUD, redaction, and foreign keys in `tests/test_hipson_helpers.py:1446-1537`.

Gaps:

- FTS tables are created (`src/hipson/session.py:325-342`) but no triggers or insert path populate them. FTS search is therefore only a placeholder.
- `add_tool_call` can persist full tool outputs. `repo.scan` returns `markdown` in `src/hipson/tools/repo.py:51-55`, and runtime stores `result.output` directly at `src/hipson/runtime.py:204-214`. This conflicts with the spec's "no full repo dumps in SQLite" rule.
- No row-level constraints on `role`, `status`, `risk_level`, or `approval_status`.
- No session list/show/search CLI despite spec listing them as "Next".

Manual probe:

- A temp runtime calling injected `repo.scan` persisted output keys `['artifact', 'changed_files', 'commands', 'markdown']`; `markdown_len` was 429 in a tiny temp repo. Larger repos/diffs can be much larger.

## 9. Provider Review

Strengths:

- Fake provider supports deterministic text, tool calls, and failures in `src/hipson/providers/fake.py:17-52`.
- `ProviderError` redacts message/detail in `src/hipson/providers/base.py:34-46`.
- Provider tests cover deterministic text/tool/failure behavior in `tests/test_providers.py`.
- Runtime tests use fake providers and require no network.

Gaps:

- No real primary chat provider exists.
- CLI cannot configure a real provider; it always uses `FakeProvider.with_text`.
- Sidecar provider path still allows `http` and `https` schemes (`src/hipson/agents.py:226-229`) and includes raw HTTP response body in errors (`src/hipson/agents.py:249-251`). This was already known in the docs, but remains unfixed.
- Provider outputs are redacted before session persistence, but assistant text is returned directly as user-facing output, not enclosed or labeled as untrusted.

## 10. Tool Registry Review

Strengths:

- `ToolSpec`, `ToolResult`, and `ToolContext` exist in `src/hipson/tools/registry.py:28-54`.
- Duplicate registration and unknown tools are rejected (`src/hipson/tools/registry.py:65-74`).
- Basic input validation exists (`src/hipson/tools/registry.py:87-100`).
- Initial tools include `repo.scan`, `repo.changed_files`, `memory.search`, `packet.review.create`, `skill.list`, `skill.view` (`tests/test_tools.py:42-58`).

Gaps:

- Output contracts are only checked for JSON serializability, not shape. `ToolRegistry.run` only calls `_ensure_json_serializable(result.output)` at `src/hipson/tools/registry.py:83`.
- Input schema is a custom mini-schema, not JSON Schema; it has no bounds, enums, nested validation, or list item typing.
- `repo.scan` returns full markdown (`src/hipson/tools/repo.py:51-55`) and may persist large diff context.
- `memory.search` accepts `memory_dir` and resolves it without sandbox checks (`src/hipson/tools/memory.py:56-61`).
- `packet.review.create` is a write tool but `approval_required=False` in `src/hipson/tools/packets.py:25-27`. The policy allows generated/docs writes, but the metadata is confusing.

## 11. Approval and Sandbox Review

Strengths:

- Risk levels are represented by `RiskLevel` in `src/hipson/tools/registry.py:11`.
- `ApprovalPolicy.evaluate` implements read/write/external/exec/dangerous decisions in `src/hipson/approvals.py:50-81`.
- Sensitive paths, traversal, broad home/profile paths, and workspace boundaries are checked in `src/hipson/sandbox.py:39-56`.
- Tests cover core decisions in `tests/test_approvals.py`.

Critical gaps:

- Runtime hardcodes `fake_provider=True` in `src/hipson/runtime.py:162`. If real provider support is later injected, external tools would still be treated as fake-provider approved.
- Approval path checks only inspect keys `path`, `project`, `packet`, and `source` (`src/hipson/approvals.py:89`). Tools use other path-bearing keys such as `memory_dir`, `root`, and `output`.
- Manual probe: approval for `memory.search` with `memory_dir=str(Path.home())` returned `allowed=True` and reason `Read allowed after sandbox checks`.
- Runtime persists only `approval_status`, not the full decision metadata from `ApprovalDecision.to_metadata()` (`src/hipson/runtime.py:204-214`, `217-239`).

## 12. Prompt Assembler Review

Strengths:

- Stable system prefix exists in `src/hipson/prompt.py:11-16`.
- Current request is enclosed as `<untrusted_data>` (`src/hipson/prompt.py:43`, `62-63`).
- Tool specs include names, descriptions, risk, approval, input schema, and output contract (`src/hipson/prompt.py:70-78`).
- Redaction and section/total caps exist (`src/hipson/prompt.py:58-87`).
- Prompt tests cover deterministic output, redaction, truncation, and no tool calls in `tests/test_prompt.py`.

Gaps:

- Session summary/tool summaries are inserted as plain text, not enclosed as untrusted data (`src/hipson/prompt.py:44`).
- Runtime does not include historical session messages, only tool summaries (`src/hipson/runtime.py:129-138`).
- There is no test for malicious tool result summaries being injected into the next provider prompt.

## 13. Runtime Loop Review

Implemented behavior:

- Creates or loads a session (`src/hipson/runtime.py:122-127`).
- Persists user messages (`src/hipson/runtime.py:48`).
- Assembles prompt (`src/hipson/runtime.py:129-138`).
- Calls provider abstraction (`src/hipson/runtime.py:56-72`).
- Persists assistant message (`src/hipson/runtime.py:78-84`).
- Rejects unknown tools (`src/hipson/runtime.py:148-158`).
- Checks approval before execution (`src/hipson/runtime.py:160-174`).
- Persists tool calls/results (`src/hipson/runtime.py:193-239`).
- Bounded max iterations (`src/hipson/runtime.py:54`, `94-102`).

Gaps:

- CLI `chat` is fake-response only (`src/hipson/cli.py:240-242`); `hipson chat -q "scan..."` does not scan.
- Invalid tool/input rejections are persisted, but the final user answer may be `Fake provider response` rather than an explicit rejection. Tests assert persistence but not useful user-facing failure text (`tests/test_runtime.py:59-92`).
- `ProviderRequest.messages` contains only one system message with assembled prompt (`src/hipson/runtime.py:57-70`), not a normal role-separated chat history.
- No real provider failure/no-provider configuration path exists beyond injected fake failure.
- Learning proposals are not integrated into runtime after final answers.

## 14. CLI Behavior Review

Observed CLI:

- `hipson chat` exists.
- `hipson chat -q` exists.
- `hipson skill list/view/use` exists.
- `hipson scheduler create/list/tick` exists.

Missing CLI:

- `hipson session list`: absent; argparse invalid choice.
- `hipson tool list`: absent; argparse invalid choice.

Product behavior:

- `hipson chat -q "scan this repo and propose the next safe PR"` prints `Fake provider response` with no scan. This preserves provider-free default but is misleading as an MVP runtime command.
- `README.md` does not document `chat`, `scheduler`, or new runtime constraints; it still describes Hipson as Stable 1.1 local-first CLI with sidecars and packets.

## 15. Skills Review

Strengths:

- `find_skill_files` ignores generated/cache trees in `src/hipson/skills.py:25-27`.
- Skill metadata listing is implemented in `src/hipson/skills.py:82-103`.
- Skill viewing is bounded/redacted and enclosed as untrusted data in `src/hipson/skills.py:106-170`.
- Tests cover listing, bounded view, missing skill, packaged workflow skill, and tool wrappers in `tests/test_skills.py`.

Gaps:

- `skill use` only emits a JSON payload; it is not integrated into runtime prompt selection.
- CLI `skill list` prints full descriptions for all skills, including large external skill descriptions. That is not a secret issue, but it is noisy.
- Root/path policy is split between generic approval and `tools/skills.py`; the generic policy does not know about the `root` key.

## 16. Learning Review

Strengths:

- `propose_from_session` returns proposals and does not persist durable memory (`src/hipson/learning.py:45-59`).
- Proposals include source refs and approval metadata (`src/hipson/learning.py:19-42`).
- Tests assert proposal-only behavior and no memory rows written (`tests/test_learning.py:7-26`).

Gaps:

- No approval/apply command exists.
- Skill proposal heuristic maps any generic mention of "skill" or "workflow" to `hipson-workflow` (`src/hipson/learning.py:140-152`).
- No integration with runtime final loop; spec says propose candidates only with approval, but runtime does not propose them at all.

## 17. Scheduler Review

Strengths:

- Scheduler is explicit tick-only, no daemon (`src/hipson/scheduler.py:52-57`).
- Uses registry and approval policy before execution (`src/hipson/scheduler.py:68-87`).
- Blocks external/exec/dangerous jobs (`src/hipson/scheduler.py:73-75`).
- Tests cover due jobs, future jobs, failures, and CLI create/list/tick in `tests/test_scheduler.py`.

Gaps:

- It is not a cron scheduler; `schedule` is stored but unused (`src/hipson/scheduler.py:40-47`).
- `run_after` is a raw string compared lexicographically in SQL (`src/hipson/session.py:260-270`); no timestamp validation.
- CLI `--approved` is a boolean flag, not a durable approval workflow (`src/hipson/cli.py:506-512`).
- Approval decisions are not persisted as structured metadata.

## 18. Gateway Review

The CLI gateway is a thin adapter over runtime: `src/hipson/gateway/cli.py:29-35` calls `runtime.run` and returns answer/session/tool count. It does not duplicate tool or approval logic. Tests cover message pass-through and approval non-bypass in `tests/test_gateway.py`.

No Telegram, Discord, or network gateway exists. This matches the spec's non-goals.

## 19. MCP Bridge Review

The MCP bridge is not an MCP server or protocol implementation. It is an internal adapter class:

- Lists read/no-approval tools by default (`src/hipson/gateway/mcp.py:23-27`, `72-73`).
- Calls registry through approval policy (`src/hipson/gateway/mcp.py:37-53`).
- Rejects non-read tools even with `approved=True` (`src/hipson/gateway/mcp.py:47-48`).
- Tests cover list, read tool call, write rejection, sensitive path rejection, unknown tool in `tests/test_mcp.py`.

This is safer than exposing too much, but it should not be marketed as "MCP implemented".

## 20. Test Quality Review

Meaningful tests:

- Runtime persistence, invalid tool/input, provider failure, max iterations: `tests/test_runtime.py`.
- Approval matrix and sandbox sensitive/traversal paths: `tests/test_approvals.py`.
- Prompt redaction/truncation/untrusted request: `tests/test_prompt.py`.
- Scheduler write refusal and due/future jobs: `tests/test_scheduler.py`.
- MCP read-only exposure and write rejection: `tests/test_mcp.py`.
- Existing redaction/packet/sidecar tests remain substantial in `tests/test_hipson_helpers.py`.

Weak or self-confirming tests:

- `tests/test_runtime.py::test_chat_cli_query_uses_fake_provider_and_temp_db` asserts the CLI prints a configured fake string. It does not test tool calls through CLI.
- `tests/test_gateway.py::test_cli_gateway_cannot_bypass_runtime_approval_policy` expects final answer `Fake provider response`, which confirms rejected tool errors are not user-facing.
- Tool registry tests do not verify output contract shape; they only verify JSON-serializable output.
- Scheduler tests do not cover `--approved` write execution or timestamp parsing failures.
- No tests prove FTS data is searchable.
- No tests cover `memory_dir` path bypass.
- No tests cover runtime with a non-fake provider object plus an external tool.

No assertion-free tests were found in new focused files, but several tests assert implementation-shaped internals rather than user-visible contracts.

## 21. Security Threat Model Review

| Threat | Current mitigation | Evidence | Missing tests | Residual risk |
|---|---|---|---|---|
| Prompt injection from user request | Current request enclosed as untrusted data. | `src/hipson/prompt.py:43`, `62-63`; `tests/test_prompt.py:60` | Malicious tool summary/provider output in session summary. | Medium |
| Malicious skill content | Skill view encloses content as untrusted data. | `src/hipson/skills.py:117`, `169-170`; `tests/test_skills.py:36` | Runtime-selected skill excerpt injection. | Medium |
| Provider error leakage | `ProviderError` redacts; sidecar still leaks raw HTTP body. | `src/hipson/providers/base.py:39-46`; `src/hipson/agents.py:249-251` | Real provider adapter tests. | High |
| Secrets in diffs/env | Existing redaction/sensitive scan tests. | `src/hipson/project.py:399-431`; helper tests around redaction. | Tool output persistence with real repo diff. | High |
| Sensitive path access | Sandbox blocks common path keys. | `src/hipson/sandbox.py:39-56` | `memory_dir`, `root`, future tool-specific path keys. | High |
| Path traversal | Sandbox blocks `..`. | `src/hipson/sandbox.py:42-43`; `tests/test_approvals.py:40` | Tool-specific bypass keys. | Medium |
| Tool poisoning | Registry rejects unknown/bad input. | `src/hipson/tools/registry.py:70-100` | Output contract mismatch, malicious result summaries. | Medium |
| Unsafe shell execution | No shell tool implemented. | No `shell.run` registered in `build_default_registry`. | Future shell tool policy. | Low now, high later |
| Accidental secret persistence | Session store redacts content/json/error. | `src/hipson/session.py:149-155`, `202-207` | Large tool output redaction edge cases, private key JSON. | Medium |
| Unbounded context growth | Prompt caps exist. | `src/hipson/prompt.py:36-37`, `81-87` | Tool output persistence and summary injection caps. | Medium |
| Sidecar data leakage | Existing packet guards, but provider errors raw. | `src/hipson/agents.py:353-400`, `249-251` | HTTPS-only and redacted HTTP body tests. | High |
| Gateway approval bypass | Gateway uses runtime. | `src/hipson/gateway/cli.py:29-35` | Future network gateway auth. | Low now |
| MCP approval bypass | MCP bridge calls approval policy. | `src/hipson/gateway/mcp.py:42-53` | Real MCP transport tests. | Medium |
| Scheduler unsafe execution | Blocks external/exec/dangerous; approval policy used. | `src/hipson/scheduler.py:73-87` | Approved write job audit metadata. | Medium |
| Learning-loop poisoning | Proposal-only, no writes. | `src/hipson/learning.py:45-59` | Approval/apply path, provenance validation. | Medium |

## 22. Documentation Drift

- `docs/PERSISTENT_AGENT_RUNTIME_SPEC.md:31` still describes runtime modules as proposed future modules, while the working tree now contains implementations.
- `README.md` does not document `hipson chat`, `scheduler`, runtime DB behavior, or that `chat` is fake-only.
- Spec CLI contract lists `hipson session list` and `hipson tool list` as "Next"; CLI smoke confirms both are absent. This is not a failure of MVP, but it must not be described as complete.
- `docs/PROJECT_DEVELOPMENT_PLAN.md` still frames scheduler/MCP as optional/future in places, but the implementation already adds `scheduler` and an MCP-style adapter.

## 23. Top Risks

1. **P0: `hipson chat` is misleading as a runtime command.** It always uses `FakeProvider.with_text`; the smoke request to scan the repo returns `Fake provider response`.
2. **P0: Runtime approval context hardcodes `fake_provider=True`.** `src/hipson/runtime.py:162` would auto-allow external-risk tools if they are registered later, even with a real provider object.
3. **P1: Tool outputs can be persisted too broadly.** Runtime persists `result.output` directly; `repo.scan` includes markdown scan output.
4. **P1: Path sandbox is not schema-aware.** `memory_dir` is not checked by `ApprovalPolicy`; manual probe approved `Path.home()`.
5. **P1: Sidecar provider hardening remains undone.** `src/hipson/agents.py` still allows `http` and includes raw HTTP error body.

## 24. Recommended Next Steps

1. Add a hard runtime mode boundary: fake/offline mode must be explicit, and real-provider runtime must fail closed until provider, approvals, and tool contracts are hardened.
2. Fix `runtime.py` approval context: pass true provider/tool execution context; never hardcode `fake_provider=True`.
3. Make tool contracts schema-aware, including path-bearing fields, output bounds, and persistence policies per tool.
4. Add session/tool CLI read-only commands before expanding runtime behavior: `hipson session list/show`, `hipson tool list`.
5. Harden sidecar provider boundaries: HTTPS-only default, no raw HTTP body in errors, redacted provider failures.
6. Add negative tests for `memory_dir`, malicious tool summaries, full scan persistence bounds, and real-provider-like injected provider behavior.
7. Split implementation into PR-sized chunks before merge; scheduler and MCP bridge should be delayed or marked experimental.

## 25. Open Questions

- Should `hipson chat` default to fake/no-provider mode, or fail unless `--fake` is explicit?
- What is the intended default session DB retention and location?
- Which tools are allowed to persist full output versus summaries/artifact paths only?
- Should generated writes under `runs/`, `scans/`, and `docs/` be auto-approved for model-initiated tool calls?
- How should approvals be represented in CLI and persisted in `tool_calls` metadata?
- Is the MCP bridge intended to become a real protocol server or remain an internal adapter?
- Should scheduler exist before runtime/provider safety is production-ready?

## Scores

- Spec compliance: **6/10**. The module names and many MVP behaviors exist, but CLI `chat` is fake-only, session/tool commands are absent, scheduler/MCP are premature, and approval metadata/persistence rules are incomplete.
- Architecture quality: **6/10**. The shape is coherent and dependency-light, but boundaries are still too implicit: approvals are caller-enforced, provider identity is hardcoded as fake, and tool persistence is not policy-aware.
- Test quality: **6/10**. The suite is broad and passes, with useful failure tests, but key tests are self-confirming and miss path-key bypasses, output-contract enforcement, CLI tool-call behavior, and non-fake provider safety.
- Security posture: **5/10**. Redaction and sandbox foundations exist, but path coverage gaps, raw sidecar provider error bodies, broad tool output persistence, and fake-provider approval hardcoding block trust.
- Runtime readiness: **4/10**. The runtime is a testable skeleton, not a dependable user-facing agent loop.
- Real-provider ready: **no**. Approval context, provider error handling, prompt/tool-output boundaries, and provider adapter are not ready.
- Release ready: **no**. The implementation should be split and hardened before merging or documenting as available.
