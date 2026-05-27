# Hermes-Style Real Agent Completion Plan

## 1. Verified Current State

Verified on 2026-05-27 from `/home/hipson47/code/Hipson` on branch `main`.

- Working tree was clean before this plan.
- `uv run pytest -q` passed with 216 tests.
- `uv run ruff check .`, `uv run mypy src/hipson`, configured Bandit, `compileall`, `scripts/run_tests.py`, `hipson doctor`, and `hipson skill validate` passed.
- `hipson chat -q ...` fails closed without a provider.
- `hipson chat --fake ...` works in explicit fake/offline mode.
- `hipson tool run repo.changed_files '{"path":"."}' --json` works through the read-only tool boundary.
- `hipson session list`, `hipson tool list`, and `hipson learn --help` exist.

Post-implementation verification update:

- `uv run pytest -q` passed with 223 tests after the real-agent completion changes.
- `uv run python scripts/run_tests.py` passed with 223/223 tests.
- `uv run ruff check .`, `uv run mypy src/hipson`, configured Bandit, `compileall`, `hipson doctor`, and `hipson skill validate` passed.
- `hipson chat --provider openai-compatible ...` now exists as an explicit provider mode and remains fail-closed when the configured API key is missing.
- Unit tests cover the OpenAI-compatible adapter with stub transports, including runtime tool-call integration without network or credentials.

## 2. Real-Agent Gap Analysis

Hipson now has a dependency-free OpenAI-compatible provider adapter for the primary runtime, but it is not yet a fully release-cleared real-provider deployment.

Current gaps:

- Live provider smoke remains manual and was skipped by requirement because no provider credentials or live network calls were used.
- Provider tool-call behavior is covered by stubbed OpenAI-compatible transport and fake providers, not by a live model.
- Approval decisions are durable first-class approval records, but there is no interactive human approval UX for write/external/exec tools yet.
- Mutation/fault-injection triage remains incomplete; the configured mutmut run timed out before checking every mutant and still reported survivors/timeouts.

## 3. Selected Scope

This pass targets production-readiness for a local-first real-agent runtime without live credential-dependent verification:

- Add a dependency-free OpenAI-compatible chat provider adapter for the primary runtime.
- Keep provider-free defaults and fail-closed missing config behavior.
- Add explicit CLI provider mode for `hipson chat`.
- Ensure provider error bodies are redacted and bounded.
- Keep real-provider tests stubbed and network-free.
- Add durable approval records to the session store and runtime/tool CLI.
- Improve session search so messages, tool-call summaries, and memories are searchable with FTS when available and a documented fallback when not.
- Update readiness documentation honestly.

## 4. Non-Goals

- No live provider smoke test without explicit user-provided credentials and permission.
- No unrestricted shell execution.
- No scheduler daemon.
- No required MCP dependency.
- No automatic memory or skill application.
- No broad rewrite of the runtime, prompt assembler, or tool registry.

## 5. Architecture Changes

Planned changes:

- Add `hipson.providers.openai_compatible` as the primary runtime provider adapter.
- Reuse the existing provider protocol: `ProviderRequest`, `ProviderResponse`, and `ProviderToolCall`.
- Keep sidecar provider code separate from primary runtime provider code while sharing the same URL and redaction safety rules where practical.
- Extend `SessionStore` with first-class approval records.
- Extend session search with FTS-backed lookup when available and bounded redacted fallback queries.

## 6. Safety Model

The runtime remains fail-closed by default.

- Missing provider config must not create a fake provider implicitly.
- Real provider use must be explicit.
- Provider output and provider tool calls are untrusted data.
- Tool calls must continue through registry input validation, path policy, approval policy, output contract validation, redaction, and bounded persistence.
- Provider-visible tool result summaries must remain bounded and redacted.
- Durable approval records must be redacted and auditable.

## 7. Implementation Sequence

1. Add provider adapter and provider tests with a stub HTTP transport.
2. Wire explicit CLI real-provider mode without changing fail-closed default behavior.
3. Add durable approval record storage and integrate runtime/tool CLI persistence.
4. Improve session search over messages, tool calls, and memories.
5. Add focused runtime/provider/approval/session tests.
6. Update docs and readiness scorecard.
7. Run targeted and full verification.

## 8. Tests To Add Or Update

- Provider adapter accepts HTTPS config and rejects unsafe/malformed URLs.
- Provider adapter redacts and bounds HTTP and transport errors.
- Stubbed provider response returns text without network.
- Stubbed provider tool call response parses into `ProviderToolCall`.
- `hipson chat --provider openai-compatible` fails closed when config is missing.
- Runtime provider tool calls still execute through the hardened path.
- Approval records are persisted for runtime and CLI tool runs.
- Session search finds messages, tool-call summaries, and approved memories without leaking secrets.

## 9. Verification Plan

Run targeted checks first:

- `uv run pytest tests/test_providers.py tests/test_runtime.py tests/test_session.py tests/test_tools.py -q`
- `uv run pytest -q -k "provider or runtime or tool or approval or session or learn or learning or redaction or prompt"`

Then run full verification:

- `uv run pytest -q`
- `uv run ruff check .`
- `uv run mypy src/hipson`
- `uv run bandit -q -r src/hipson -c pyproject.toml`
- `python -m compileall src/hipson`
- `uv run python scripts/run_tests.py`
- `uv run hipson doctor`
- `uv run hipson skill validate`

## 10. Review Loop

After implementation, inspect `git diff`, run CLI smoke checks, update the scorecard, and record any blocker that prevents 100/100. Do not claim real-provider production readiness if verification relies on untested assumptions or live credentials.

## 11. Rollback Notes

The new provider adapter should be isolated and removable without changing fake/offline runtime behavior. Session schema changes must be idempotent and backward-compatible for existing SQLite files.

## 12. Deferred Work

- Optional live provider smoke with explicit user permission and credentials.
- Full focused mutmut survivor triage.
- Durable human approval UI beyond persisted policy decisions.
- Rich provider-specific tool role round-tripping if future providers need it.
