# Real Agent Readiness Scorecard

## 1. Score Summary

Current score after the Hermes-style real-agent completion pass: 93/100.

Hipson now has an explicit OpenAI-compatible primary runtime provider adapter, safe read-only public tool execution, durable approval records, searchable redacted session history, approval-gated learning, and credential-free tests for provider success/failure/tool-call paths. It is not 100/100 because live provider smoke was intentionally skipped and the configured mutmut run still has untriaged safety-adjacent survivors/timeouts.

Final decision: not 100/100 yet. Do not claim unrestricted release readiness until the open P1 findings in section 10 are closed.

## 2. Provider Runtime

Score: 14/15.

- Pass: `hipson chat -q ...` fails closed when no provider is configured.
- Pass: explicit fake/offline mode remains available through `--fake`.
- Pass: `hipson chat --provider openai-compatible ...` is explicit and uses provider-free defaults otherwise.
- Pass: the provider adapter has HTTPS-by-default URL policy, explicit local HTTP opt-in, redacted/bounded transport errors, and strict tool-call argument parsing.
- Pass: unit tests use stub transports and require no live network or credentials.
- Gap: live provider smoke remains manual and was not run in this pass.

## 3. Tool Calling

Score: 15/15.

- Pass: provider-compatible tool descriptors are generated from the registry.
- Pass: real-provider-style tool calls are parsed into `ProviderToolCall` records.
- Pass: runtime tool calls execute through registry input validation, path policy, approval policy, output contract validation, redaction, and bounded persistence.
- Pass: `hipson tool run` gives a public read-only/no-approval execution path through the same safety boundary.
- Pass: malformed, unsafe, blocked, failed, and max-iteration tool calls remain visible and auditable.

## 4. Approval and Safety

Score: 14/15.

- Pass: approval records are persisted for runtime, scheduler, and manual tool-run decisions.
- Pass: unsafe risk classes fail closed by default.
- Pass: sensitive path, traversal, and per-tool path policies are covered by regression tests.
- Pass: no shell execution tool is registered by default.
- Gap: there is not yet an interactive human approval UX for write/external/exec tools; those paths remain intentionally narrow or blocked.

## 5. Session and Memory

Score: 12/12.

- Pass: SQLite sessions persist messages, tool calls, approval records, jobs, memories, and skill runs.
- Pass: `hipson session list/show/search` exposes bounded, redacted observability.
- Pass: search covers messages, tool-call summaries, and memory summaries, with FTS where SQLite supports it and safe fallback behavior otherwise.
- Pass: persistence helpers redact and bound message, tool, provider-error, and approval metadata.

## 6. Learning Loop

Score: 9/10.

- Pass: `hipson learn propose` creates approval-gated memory and skill-reference proposals from session trajectory.
- Pass: memory proposals include request/outcome/tool-call provenance and deterministic IDs.
- Pass: `hipson learn apply-memory` requires explicit user action and redacts/bounds persisted notes.
- Gap: duplicate suppression, richer skill draft generation, and explicit skill apply remain future work.

## 7. CLI UX and Observability

Score: 10/10.

- Pass: `chat`, `tool list/show/run`, `session list/show/search`, and `learn propose/apply-memory` are documented and tested.
- Pass: public commands fail closed and keep output bounded/redacted.
- Pass: fake/offline behavior is clearly labeled and cannot be mistaken for a default real provider.

## 8. Test Quality and Mutation

Score: 9/13.

- Pass: full pytest, ruff, mypy, configured Bandit, compileall, custom runner, doctor, and skill validation passed after the implementation.
- Pass: provider adapter tests cover success, URL policy, redacted/bounded HTTP errors, malformed tool arguments, and runtime integration with a stub transport.
- Pass: runtime/tool/session/scheduler tests cover durable approval records and safe persistence.
- Gap: `timeout 300s uv run mutmut run || true` did not complete the configured 2,219-mutant set. Last observed progress was 1,965/2,219 with 1,643 killed, 130 timeouts, and 192 survivors; `uv run mutmut results || true` still lists survivors/not-checked mutants in `agents`, `approvals`, `prompt`, `sandbox`, `tools.registry`, `redaction`, `router`, and `runtime`.

## 9. Documentation Truthfulness

Score: 10/10.

- Pass: README documents explicit provider configuration, fail-closed default chat, fake/offline mode, session/tool observability, bounded approval records, and approval-gated learning.
- Pass: audit context/backlog and runtime spec were updated to reflect the current implementation without claiming live-provider or unrestricted release readiness.

## 10. Open Findings

- P1: complete focused mutmut survivor triage in smaller batches for approval/path/redaction/registry/prompt/runtime safety logic.
- P1: run manual live-provider smoke only with explicit user permission and disposable credentials before external release.
- P2: design an interactive human approval UX for write/external/exec tools.
- P2: improve learning with duplicate suppression and richer skill draft/apply workflows while keeping explicit approval gates.

## 11. Final Decision

Not 100/100 yet.

Hipson is substantially closer to a Hermes-style real AI engineering agent runtime: it now has an explicit real-provider adapter and an end-to-end, auditable tool-calling path tested without network or credentials. It should be treated as a strong local-first real-agent candidate, not as final release-ready 100/100 software, until the P1 mutation triage and manual live-provider smoke are closed.
