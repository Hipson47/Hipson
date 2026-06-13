# Claims Matrix

Status vocabulary: VERIFIED, PARTIAL, UNVERIFIED, DRY_RUN_ONLY, MOCK_OR_STUB, BROKEN, MISLEADING, OUT_OF_SCOPE.

| Claim | Source | Status | Evidence | Risk | Suggested README Wording |
|---|---|---:|---|---:|---|
| Hipson is a local-first developer workflow CLI | README, pyproject | VERIFIED | WSL source inspection and CLI smoke commands | P3 | "Hipson is a local-first Python CLI for AI-assisted engineering workflows." |
| Hipson has no required runtime dependencies | README, pyproject | VERIFIED | Empty project dependencies; temp wheel smoke worked | P3 | "Hipson has no required runtime dependencies beyond Python." |
| Hipson is Production/Stable | `pyproject.toml` classifier | MISLEADING | Mutation results incomplete; live provider unverified | P1 | "Hipson is a local-first runtime preview / beta until release gates close." |
| Hipson 1.1 is stable | README | PARTIAL / MISLEADING | Local CLI paths pass; release-grade gates incomplete | P1 | "The local CLI is functional; provider and mutation gates remain release work." |
| Repository scanning works | README, CLI help | VERIFIED | `uv run hipson scan .` passed | P3 | "Scan repositories using local Git and project metadata." |
| Packet generation works | README, CLI help, source | VERIFIED | Source and command help inspected; CI config includes packet smoke | P3 | "Generate bounded review and executor packets." |
| JSONL memory works | README, CLI help | VERIFIED | Correct `hipson memory --memory-dir ... add/search/list` commands passed | P3 | "Store and search local JSONL memory notes." |
| SQLite sessions exist | README, source | VERIFIED | `session list/show/search` with temp DB passed | P3 | "Persist sessions in SQLite and inspect them locally." |
| Local deterministic chat router exists | README, runtime source | VERIFIED | `hipson chat -q "scan this repo..."` ran local/router `repo.scan` | P2 | "Default chat supports deterministic provider-free routing for supported safe intents." |
| Default chat is a real model-backed agent | Not current | OUT_OF_SCOPE / MISLEADING if implied | Default local router is deterministic, not an LLM | P1 | "Default chat is provider-free deterministic routing, not model reasoning." |
| Fake/offline mode exists | README, CLI help | VERIFIED | `hipson chat --fake` returned fake/offline output | P3 | "Use `--fake` for deterministic offline smoke tests." |
| Real provider adapter exists | README, provider source | PARTIAL | `openai_compatible.py` exists; stub tests and missing-key fail-closed verified | P1 | "Experimental OpenAI-compatible adapter is explicit opt-in and stub-tested." |
| Live provider readiness is proven | Any implied release claim | UNVERIFIED | No live credentials/network call authorized or run | P1 | "Live provider smoke is manual and not required for local operation." |
| Provider errors are redacted and bounded | README, source/tests | PARTIAL / VERIFIED BY TESTS | Source/tests cover redaction; no live error observed | P2 | "Provider errors are redacted/bounded by adapter code and covered by tests." |
| Provider URLs are HTTPS-only by default | README, provider source/tests | VERIFIED BY SOURCE/TESTS | URL policy inspected; tests present | P2 | "Remote provider URLs are HTTPS-only unless explicit local test mode is enabled." |
| Provider tool calls go through registry and approvals | README, runtime source/tests | PARTIAL | Runtime source/tests exist; no live provider call run | P1 | "Provider-requested tool calls are parsed and validated through Hipson's safety pipeline in tests." |
| Tool registry exists | README, source | VERIFIED | `src/hipson/tools/registry.py`, `tool list`, `tool show` | P3 | "Tools are registered with risk, approval, schema, contract, and path-policy metadata." |
| `hipson tool run` supports safe read-only execution | README, CLI | VERIFIED | `repo.changed_files` and `repo.scan` passed through CLI | P2 | "Run read-risk/no-approval tools through `hipson tool run`." |
| Write-risk tools are blocked by default | README, CLI | VERIFIED | `packet.review.create` rejected through `tool run` | P1 | "Write, external, exec, and dangerous tools are rejected by default." |
| Path traversal is blocked | Safety docs/source | VERIFIED | `repo.changed_files {"path":"../"}` rejected | P1 | "Tool path inputs are checked by path policy." |
| Approval records exist | README, session source | VERIFIED | `session show --json` showed approval records | P2 | "Tool decisions are recorded as approval records in sessions." |
| Scheduler works as opt-in tick runner | README, scheduler source | PARTIAL | Temp scheduler create/list/tick passed for safe read tool | P2 | "Scheduler is experimental, opt-in, and tick-based." |
| Scheduler is an autonomous daemon | Not current | OUT_OF_SCOPE | No daemon behavior observed | P1 | "Scheduler is not a daemon." |
| MCP/gateway support exists | README/docs/source | PARTIAL | Optional adapter source/tests exist; external integration not verified | P2 | "MCP-style gateway is optional/internal." |
| Sidecar deterministic routing works | README, CLI | VERIFIED | `sidecar route` passed | P3 | "Sidecar routing can recommend local workflow roles." |
| Sidecar LLM dry-run works | README, CLI | DRY_RUN_ONLY | `sidecar route --llm --llm-dry-run` produced preview | P2 | "Dry-run previews provider sidecar requests without making a call." |
| Sidecar live OpenRouter calls work | README/config | UNVERIFIED | No live call authorized or run | P1 | "Live sidecar calls require explicit configuration and manual validation." |
| Skills are installed and valid | README, skills | VERIFIED | `skill validate` passed for 51 skill files | P3 | "Bundled skills validate locally." |
| CI quality gates exist | `.github/workflows/ci.yml` | VERIFIED BY CONFIG | Workflow inspected | P2 | "CI is configured to run tests, static checks, build, and mutation." |
| CI currently passes | Not directly observed | UNVERIFIED | No remote CI result checked | P2 | "Run and record CI before release." |
| Mutation testing is configured | pyproject, CI | VERIFIED | `pyproject.toml` mutmut config and `mutmut results` output | P2 | "Focused mutation testing is configured." |
| Mutation closure is complete | Audit docs/goals if implied | BROKEN / PARTIAL | `mutmut results`: 184 survived, 286 not checked, 148 timeout | P1 | "Mutation survivor triage remains open." |
| Package build works | pyproject/build | VERIFIED | `uv build --out-dir /tmp/...` passed | P2 | "Source and wheel builds pass locally." |
| Installed package works | Build smoke | VERIFIED | Temp venv wheel install and CLI smoke passed | P2 | "Installed-wheel smoke passed in local audit." |
| No secrets committed | Security scan | VERIFIED WITH LIMITS | No real secrets observed; fake/test fixtures and ignored env files only | P1 | "No real committed credential was observed in this audit." |
| Visual/HyperFrames sidecars are product-ready | README/config | UNVERIFIED / PARTIAL | Config/skills exist; no real media workflow verified | P3 | "Visual sidecar routing exists as optional workflow configuration." |

