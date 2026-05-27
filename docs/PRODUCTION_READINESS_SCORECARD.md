# Production Readiness Scorecard

## 1. Score Summary

Initial score before this repair pass: **88/100** for the local/provider-free MVP.

Final score after local runtime-router implementation: **98/100** for the local/provider-free MVP.

- Security and Safety: 24/25
- Correctness and Runtime Behavior: 20/20
- Test Quality and Fault Detection: 19/20
- CLI UX and Observability: 15/15
- Documentation and Truthfulness: 10/10
- Maintainability and Scope Control: 10/10

The score was capped below 95 before repair because the local MVP lacked a public safe end-to-end read-only tool execution path and max-tool-iteration visibility was incomplete. Those local/provider-free blockers are now fixed and verified. Real-provider readiness remains out of scope and is not claimed.

## 2. Security and Safety

Before repair:

- Chat uses a deterministic local router for supported safe read-only engineering tasks.
- Provider-specific chat remains explicit; unsupported local chat requests fail truthfully.
- Runtime tools use registry validation, approval policy, path policy, redaction, output contracts, and bounded persistence.
- No shell tool is registered by default.
- Real provider support is not implemented.

Repair target:

- Add only read-risk, no-approval `tool run` execution.
- Reject write/external/exec/dangerous tools by default.
- Keep fake/offline chat clearly labeled.

Verified result:

- `hipson tool run` only executes read-risk tools that do not require approval.
- Write-risk tools such as `packet.review.create` are rejected by default.
- Default `hipson chat -q "scan this repo..."` executes `repo.scan` locally through the runtime safety boundary.
- Fake/offline chat remains explicit.

## 3. Correctness and Runtime Behavior

Before repair:

- Runtime can execute provider-supplied tool calls in tests.
- CLI can inspect sessions/tools and approval-gated learning.
- CLI can run safe tools through the hardened boundary.
- Default chat can route supported provider-free local intents to safe read-only tools.

Repair target:

- Add public safe read-only tool execution.
- Add explicit fake/offline tool-call chat demo.
- Include bounded max-iteration context.

Verified result:

- `hipson tool run repo.changed_files '{"path":"."}' --json` succeeds locally.
- `hipson chat -q "scan this repo and propose the next safe PR"` executes `repo.scan` locally and returns a truthful deterministic answer.
- `hipson chat -q "show changed files"` executes `repo.changed_files` locally.
- `hipson chat --fake --fake-tool-call repo.changed_files --fake-tool-input '{"path":"."}' -q "check files"` exercises the runtime tool-call path and prints a bounded tool-call summary.
- Max-tool-iteration stops now include bounded attempted-tool context and persist skipped calls.

## 4. Test Quality and Fault Detection

Before repair:

- 209 tests pass.
- Runtime, tools, approvals, prompt, provider redaction, session, scheduler, MCP, and learning have focused tests.
- Mutation survivor triage remains incomplete.

Repair target:

- Add negative CLI tests for unsafe `tool run`.
- Add fake tool-call chat CLI tests.
- Add trajectory learning tests.

Verified result:

- Test count increased from 209 to 233 across the local/provider-free, real-agent completion, autonomous mutation-triage, and local-router passes.
- New tests cover safe `tool run`, unsafe/invalid/path-rejected tool runs, optional session persistence, fake tool-call chat, unsafe fake tool-call refusal, max-iteration visibility, and trajectory learning.
- Autonomous loop iteration 1 added selected survivor tests for approval path keys, sandbox skill-root paths, registry handler failure details, runtime rejection summaries, and sensitive path sanitization.
- Full mutmut survivor triage remains deferred and non-blocking for the local/provider-free MVP, but blocking before broad real-agent release readiness.

## 5. CLI UX and Observability

Before repair:

- `hipson session list/show/search`, `hipson tool list/show`, and `hipson learn propose/apply-memory` exist.
- `hipson tool run` is missing.
- `hipson chat --fake` has no explicit tool-call demo.

Repair target:

- Add `hipson tool run <name> <json>` for safe read-only tools.
- Add `hipson chat --fake-tool-call ... --fake-tool-input ...`.

Verified result:

- `hipson tool run` exists for safe read-only tools.
- `hipson chat` defaults to local deterministic router mode for supported safe tasks.
- `hipson chat --fake --fake-tool-call ...` exists and is explicitly fake/offline.
- Unsupported local chat requests fail with a bounded supported-intents message.

## 6. Documentation and Truthfulness

Before repair:

- README documents fake-only chat and learning workflow.
- Audit docs still mark `tool run`, FTS, durable approvals, and mutation triage as deferred.

Repair target:

- Update docs to mark local safe read-only `tool run` and fake tool-call demo as implemented if verified.
- Continue to state real-provider readiness is absent.

Verified result:

- README, audit context, backlog, persistent runtime spec, repair plan, and this scorecard now describe the local/provider-free status.
- Real-provider readiness and release readiness beyond local/provider-free MVP are not claimed.

## 7. Maintainability and Scope Control

Before repair:

- Runtime modules are small and dependency-light.
- The repair can be implemented in CLI/runtime/learning without changing providers or scheduler/MCP.

Repair target:

- Keep changes focused and stdlib-only.
- Avoid expanding tool surfaces beyond read-only execution.

## 8. Open P0/P1/P2 Findings

Before repair:

- P1: No public safe read-only tool execution path.
- P1: No explicit fake/offline tool-call chat demo.
- P1/P2: Max-tool-iteration visibility is incomplete.
- P2: Learning proposal quality is weak.
- P2: FTS-backed search remains future.
- P2: Durable approval records remain future.
- P1/P2: Mutation survivor triage remains open.

After repair:

- P0: none open in the local/provider-free MVP scope.
- P1: none open in the local/provider-free MVP scope.
- P1 before real-provider usage: focused mutmut survivor triage remains open.
- P2: FTS-backed/fallback search and durable approval records are implemented; keep regression tests and docs synchronized.
- P2: write/external/exec/dangerous `tool run` remains deferred until durable approval UX exists.
- P2: full mutmut survivor triage remains open for broader real-agent release readiness.

## 9. Final Decision

Status after implementation and verification: **production-ready local/provider-free MVP**.

This decision is scoped to the local/provider-free MVP only. Hipson is **not real-provider-ready** and should not be marketed as a real-provider Hermes competitor until provider adapter, durable approvals, prompt/tool-call hardening under real model outputs, and focused mutmut survivor triage are complete.
