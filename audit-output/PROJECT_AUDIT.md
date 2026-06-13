# Hipson Project Audit

Audit root: `/home/hipson47/code/Hipson`  
Branch: `main`  
Remote: `origin git@github.com:Hipson47/Hipson.git`  
Audit date: 2026-05-29  
Mode: evidence-based review, no source-code changes

## Executive Summary

Hipson is a local-first Python CLI for AI-assisted engineering workflows. In the audited WSL checkout it has substantial implemented runtime behavior: repository scanning, packet generation, JSONL memory, SQLite sessions, deterministic local chat routing, safe read-only tool execution, approval records, session search, explicit learning apply, a fake/offline provider, an OpenAI-compatible provider adapter, sidecar routing, scheduler tick jobs, and optional MCP-style gateway code.

The local/provider-free feature set is real and locally verified. The audit observed 234 passing tests, successful Ruff, Mypy, configured Bandit, compileall, custom test runner, package build, installed-wheel smoke checks, `hipson doctor`, `hipson skill validate`, local chat router execution, `tool run`, session search, learning apply, sidecar dry-run, and scheduler tick.

The project should not yet claim broad production-stable or Hermes-style real-agent release readiness. The strongest blockers are incomplete mutation survivor triage, unverified live-provider behavior, and maturity language that is stronger than the evidence supports.

## Verdict

Hipson is credible as a local-first developer workflow CLI and runtime preview. It is not yet ready to claim full production-stable release status or fully verified Hermes-style real-provider agent readiness.

Recommended decision: **Option B: fix packaging/tests/provider validation first**, then ship a truthful local-first release.

## What Works

| Area | Status | Evidence |
|---|---:|---|
| Python package and CLI entry point | VERIFIED | `pyproject.toml`, `uv build`, temp-venv wheel install, installed `hipson --help` |
| Repo scan | VERIFIED | `uv run hipson scan .` and local chat `repo.scan` route |
| Deterministic local chat router | VERIFIED | `uv run hipson chat -q "scan this repo and propose the next safe PR"` executed `repo.scan` in local/router mode |
| Changed-files workflow | VERIFIED | `uv run hipson chat -q "show changed files"` and `hipson tool run repo.changed_files` |
| Safe read-only tool execution | VERIFIED | `repo.changed_files` and `repo.scan` passed; `packet.review.create` failed closed |
| SQLite sessions | VERIFIED | Temp session DB showed sessions, messages, tool calls, and approval records |
| Session search | VERIFIED | `hipson session search repo.scan --json` returned `search_backend: fts+fallback` and message/tool-call hits |
| Approval records | VERIFIED | `session show --json` displayed approval records for runtime/tool paths |
| Learning proposal and explicit memory apply | VERIFIED | `learn propose` generated a session-trajectory proposal; `learn apply-memory` wrote only when explicitly invoked |
| JSONL memory | VERIFIED | `memory --memory-dir ... add/search/list` passed |
| Fake/offline chat mode | VERIFIED | `hipson chat --fake` returned explicit fake/offline output |
| Provider fail-closed path | VERIFIED | Provider mode without `OPENROUTER_API_KEY` exited with a clear configuration error |
| Sidecar deterministic routing | VERIFIED | `sidecar route` returned local recommendations |
| Sidecar LLM preview | DRY_RUN_ONLY | `sidecar route --llm --llm-dry-run` printed a redacted request preview without a live call |
| Scheduler tick | VERIFIED / EXPERIMENTAL | Safe `repo.changed_files` job created/listed/ticked in temp SQLite DB |
| Skill validation | VERIFIED | `uv run hipson skill validate` passed for 51 skill files |
| Local quality gates | VERIFIED | Pytest, Ruff, Mypy, Bandit, compileall, custom runner passed |
| Build and installed package | VERIFIED | sdist/wheel built; temp-venv installed CLI smoke passed |

## What Is Partial

| Area | Status | Evidence |
|---|---:|---|
| Real provider adapter | PARTIAL | `src/hipson/providers/openai_compatible.py` exists; missing-key fail-closed and stub/unit behavior verified; no live smoke run |
| Provider-backed tool calling | PARTIAL | Runtime/provider tests use stubs/fakes; no live provider tool-call smoke authorized |
| Sidecar provider operation | PARTIAL / DRY_RUN_ONLY | Deterministic and dry-run paths verified; live OpenRouter sidecar not run |
| Mutation/fault-injection confidence | PARTIAL | `mutmut results` shows 184 survived, 286 not checked, and 148 timeout mutants |
| CI release confidence | PARTIAL | CI workflow is strong, but no GitHub Actions run was observed in this audit |
| Scheduler | PARTIAL | Tick-based local jobs work; it is not a daemon and should remain experimental |
| MCP/gateway | PARTIAL | Optional/internal adapter code and tests exist; external protocol compatibility not independently verified |
| Documentation alignment | PARTIAL | README is mostly current; historical audit docs still contain older states and old conclusions |

## What Is Broken Or Blocked

No verified core local-provider-free feature failed when invoked with correct syntax. Observed failures were expected fail-closed behavior or CLI usage mistakes.

| Area | Status | Evidence |
|---|---:|---|
| Unsupported local chat prompt | EXPECTED_FAIL_CLOSED | `hipson chat -q "write a novel about coffee"` exited 1 and listed supported local intents |
| Provider chat without API key | EXPECTED_FAIL_CLOSED | `env -u OPENROUTER_API_KEY hipson chat --provider openai-compatible ...` exited 1 |
| Write-risk tool through public `tool run` | EXPECTED_FAIL_CLOSED | `packet.review.create` rejected as blocked |
| Path traversal in tool input | EXPECTED_FAIL_CLOSED | `repo.changed_files {"path":"../"}` rejected |
| `memory search --json` | BROKEN_COMMAND_USAGE | Memory command does not accept `--json`; plain text search works |
| First scheduler create attempt | BROKEN_COMMAND_USAGE | Correct syntax requires `scheduler create --tool ... --input ...`; corrected command passed |
| Live provider smoke | UNVERIFIED | No credentials or live-network authorization used |
| pip-audit | UNVERIFIED | Skipped to avoid external advisory/network dependency during this local audit |

## What Is Overstated

| Claim | Status | Evidence |
|---|---:|---|
| `Development Status :: 5 - Production/Stable` | MISLEADING | `pyproject.toml` classifier conflicts with incomplete mutation triage and unverified live-provider behavior |
| “Stable 1.1” as broad production readiness | MISLEADING | Local CLI is strong, but release-grade mutation/provider validation is incomplete |
| Full Hermes-style real-agent readiness | MISLEADING if claimed | Real provider is explicit/stub-tested but live behavior is unverified; mutation closure is incomplete |
| Live OpenRouter sidecars are operationally proven | UNVERIFIED | Dry-run routing works; live sidecar calls were not made |

## Architecture Summary

Hipson is a Python CLI and local runtime for agent-assisted engineering work. Intended users are developers and AI coding-agent operators who want bounded packets, repo scans, local memory, safe tool execution, and auditable runtime sessions without requiring a provider by default.

Main modules:

- `src/hipson/cli.py`: CLI command tree.
- `src/hipson/project.py`: repository scanning and command discovery.
- `src/hipson/packets.py`: packet rendering.
- `src/hipson/runtime.py`: provider/local/fake runtime loop and tool execution.
- `src/hipson/local_router.py`: deterministic provider-free routing.
- `src/hipson/tools/registry.py`: tool schemas, risk levels, output contracts, bounded output.
- `src/hipson/approvals.py` and `src/hipson/sandbox.py`: approval and path/command policy.
- `src/hipson/session.py`: SQLite sessions, messages, tool calls, memories, jobs, approval records, FTS/fallback search.
- `src/hipson/providers/openai_compatible.py`: explicit OpenAI-compatible provider adapter.
- `src/hipson/learning.py`: approval-gated learning proposals and memory apply payloads.
- `src/hipson/agents.py`: sidecar routing and OpenRouter-style sidecar runner.
- `src/hipson/scheduler.py`: opt-in tick scheduler.
- `src/hipson/gateway/mcp.py`: optional/internal MCP-style bridge.

## Evidence Table

| Evidence | Result | Notes |
|---|---:|---|
| `pwd` | VERIFIED | `/home/hipson47/code/Hipson` |
| Git state | VERIFIED | Branch `main`; remote `origin git@github.com:Hipson47/Hipson.git`; only `audit-output/` untracked after audit |
| `uv run pytest -q` | PASS | `234 passed in 53.06s` |
| `uv run ruff check .` | PASS | All checks passed |
| `uv run mypy src/hipson` | PASS | Success in 34 source files |
| `uv run bandit -q -r src/hipson -c pyproject.toml` | PASS | Exit 0; warning text about `# nosec` comments only |
| `python -m compileall src/hipson scripts tests` | PASS | Exit 0 |
| `uv run python scripts/run_tests.py` | PASS | `234/234 tests passed` |
| `uv run hipson doctor` | PASS | Config readable; 51 skills checked; 0 failed |
| `uv run hipson skill validate` | PASS | 51 skill files OK |
| `uv build --out-dir /tmp/.../dist` | PASS | Built sdist and wheel; touched ignored `src/hipson.egg-info/` |
| Temp-venv wheel smoke | PASS | Installed CLI ran help, doctor, local chat, and `tool run` |
| `uv run mutmut results` | PARTIAL | 184 survived, 286 not checked, 148 timeout |

## Test / Build Results

| Check | Result |
|---|---:|
| Unit tests | PASS, 234 passed |
| Ruff | PASS |
| Mypy | PASS |
| Configured Bandit | PASS |
| Compileall | PASS |
| Custom runner | PASS, 234/234 |
| Doctor | PASS |
| Skill validation | PASS |
| Build sdist/wheel | PASS |
| Installed wheel smoke | PASS |
| pip-audit | SKIPPED |
| Full mutmut run | SKIPPED; existing results inspected and not green |

## Security Findings

| ID | Severity | Finding | Evidence | Recommended Action |
|---|---:|---|---|---|
| S-001 | P1 | Mutation assurance is incomplete in security-critical modules | `mutmut results`: 184 survived, 286 not checked, 148 timeout | Run focused mutation triage and add tests for high-risk survivors |
| S-002 | P1 | Broad production-stable claim is not supported by safety evidence | `pyproject.toml` production/stable classifier plus incomplete mutation results | Reclassify as beta/local-first preview until gates close |
| S-003 | P1 | Live provider behavior is unverified | Provider fail-closed verified; no live network/credentials used | Keep live provider readiness unclaimed; add manual smoke checklist |
| S-004 | P2 | Sidecar live-provider behavior is unverified | `sidecar route --llm --llm-dry-run` only previews | Mark live sidecars as optional/manual |
| S-005 | P2 | Scheduler/MCP should remain limited in docs | Source and tests show opt-in/internal behavior | Avoid marketing as full daemon/MCP product |
| S-006 | P2 | Secret-like fixtures exist in tests/docs as redaction examples | Secret scan found fake `sk-test...`, fake passwords, fake private-key blocks | Keep fixtures clearly fake; avoid raw values in public docs |

No committed real credential was observed in this audit. `.env`, `config/providers.env`, `dist/`, and `src/hipson.egg-info/` are ignored.

## Packaging / Release Findings

| ID | Severity | Finding | Evidence | Recommended Action |
|---|---:|---|---|---|
| P-001 | P1 | Package metadata overstates maturity | `Development Status :: 5 - Production/Stable` | Change classifier before public release |
| P-002 | P2 | Build touches ignored source-tree metadata | `uv build` writes `src/hipson.egg-info/` | Keep ignored, or isolate build cleanup in release process |
| P-003 | P2 | CI not independently observed | `.github/workflows/ci.yml` inspected; local equivalent mostly passed | Require clean GitHub CI before tag |
| P-004 | P2 | `pip-audit` not locally verified | Skipped during audit | Run in controlled CI/release environment |

## Documentation Accuracy Findings

| ID | Severity | Finding | Evidence | Recommended Action |
|---|---:|---|---|---|
| D-001 | P1 | Maturity language is too strong | README says “Stable 1.1”; pyproject says production/stable | Reword to local-first runtime preview or beta |
| D-002 | P2 | Historical audit docs contain stale claims | Older docs still discuss fake-only chat and absent session/tool commands | Add current-state index or label historical docs |
| D-003 | P2 | Provider docs need sharper live-vs-stub distinction | Adapter is stub-tested; live provider smoke skipped | Say “stub-tested, live smoke manual” |
| D-004 | P3 | Some CLI syntax is easy to misuse | Memory `--memory-dir` placement and scheduler `--tool/--input` syntax | README proposal includes exact examples |

## Risk Ranking

### P0

No P0 issue was verified.

### P1

- Incomplete mutation survivor triage in security-critical runtime boundaries.
- Overstated production/stable package positioning.
- Live provider and live sidecar behavior unverified.

### P2

- Remote CI not observed.
- Scheduler and MCP/gateway remain limited/experimental.
- Build creates ignored source-tree metadata.
- Historical docs drift.
- CLI ergonomics around some subcommands.

### P3

- Ignored cache/build artifacts are present.
- README can be more concise and more release-oriented.

## Recommended Next Actions

1. Reposition release language to “local-first CLI/runtime preview” or beta until mutation/provider gates close.
2. Run focused mutmut triage in small batches for approvals, sandbox, redaction, prompt, registry, runtime, providers, agents, session, and learning.
3. Add tests for high-risk survivors before adding new features.
4. Add a manual live-provider smoke checklist that never runs in unit tests and never logs secrets.
5. Run and record clean GitHub CI before tagging.
6. Keep scheduler, MCP, and live sidecars documented as optional/experimental.

## Do Not Claim Yet

- Broad production/stable release readiness.
- Hermes-style real-agent completeness.
- Live provider readiness.
- Live provider-backed tool-calling readiness.
- Mutation/fault-injection closure.
- Full MCP server compliance.
- Autonomous shell execution.
- CI green status unless a real CI run is observed.

## Safe To Claim Now

- Hipson is a dependency-light Python CLI for local-first AI-assisted engineering workflows.
- Hipson can scan repositories and generate bounded workflow packets.
- Hipson has a deterministic local chat router for supported safe intents.
- Hipson can run safe read-only tools through registry and approval policy.
- Hipson persists runtime sessions, tool calls, and approval records in SQLite.
- Hipson supports session list/show/search with FTS plus fallback behavior in this checkout.
- Hipson supports explicit approval-gated learning proposal and memory apply commands.
- Hipson has an explicit OpenAI-compatible provider adapter that fails closed when credentials are missing.
- Local tests and static checks passed in this WSL audit.

