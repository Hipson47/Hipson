# Command Log

Working directory for this rerun: `/home/hipson47/code/Hipson`.

Secrets and credential-like fixture values are redacted or summarized. No live provider calls were made.

## Root And Inventory

| Command | Result | Summary |
|---|---:|---|
| `pwd` | PASS | `/home/hipson47/code/Hipson` |
| `ls -la` | PASS | Python project with `src/`, `tests/`, `docs/`, `.github/`, `pyproject.toml`, `uv.lock` |
| `git status --short` | PASS | `?? audit-output/` |
| `git branch --show-current` | PASS | `main` |
| `git remote -v` | PASS | `origin git@github.com:Hipson47/Hipson.git` |
| `command -v hipson || true` | PASS | `/home/hipson47/.local/bin/hipson` |
| `find . -maxdepth 2 ... config files` | PASS | Found Python packaging, docs, runs, templates, skills, audit-output |
| `find src/hipson -maxdepth 3 -type f` | PASS | Runtime, providers, tools, gateway, sessions, learning modules present |
| `find tests -maxdepth 2 -type f` | PASS | 12 test files present |
| `find docs -maxdepth 1 -type f` | PASS | Audit, scorecard, runtime spec, roadmap docs present |

## Source / Claim Inspection

| Command | Result | Summary |
|---|---:|---|
| `uv run hipson route --task "audit current Hipson README claims and release readiness" --json` | PASS | Advisory route recommended review packet workflow |
| `sed -n '1,260p' pyproject.toml` | PASS | Version 1.1.0; no runtime deps; dev tools; production/stable classifier; mutmut config |
| `sed -n '1,280p' README.md` | PASS | README claims stable local-first CLI, runtime preview, provider adapter, sidecars, learning |
| `sed -n '1,260p' .github/workflows/ci.yml` | PASS | CI config includes Ruff, Mypy, Bandit, pip-audit, tests, mutmut, build, wheel smoke |
| `rg -n ... README.md docs pyproject.toml src tests .github` | PASS | Found current and historical provider/runtime/mutation/readiness claims |
| `sed` key source files | PASS | Inspected CLI, runtime, local router, registry, provider adapter, approvals, sandbox, session, learning |

## Static And Test Verification

| Command | Result | Important Output |
|---|---:|---|
| `uv run pytest -q` | PASS | `234 passed in 53.06s` |
| `uv run ruff check .` | PASS | `All checks passed!` |
| `uv run mypy src/hipson` | PASS | `Success: no issues found in 34 source files` |
| `uv run bandit -q -r src/hipson -c pyproject.toml` | PASS | Exit 0; warning text about `# nosec` comment words only |
| `python -m compileall src/hipson scripts tests` | PASS | Compileall completed |
| `uv run python scripts/run_tests.py` | PASS | `234/234 tests passed` |

## CLI Smoke Verification

| Command | Result | Important Output |
|---|---:|---|
| `uv run hipson doctor` | PASS | Config readable; 51 skills checked; 0 failed |
| `uv run hipson skill validate` | PASS | 51 skill files OK |
| `uv run hipson --help` | PASS | CLI command tree printed |
| `uv run hipson route --task "audit current Hipson README claims" --json` | PASS | Advisory route returned review mode |
| `uv run hipson scan .` | PASS | Local project scan produced expected scan report |
| `uv run hipson chat -q "scan this repo and propose the next safe PR" --session-db /tmp/.../runtime.sqlite` | PASS | Local/router mode executed `repo.scan`; no provider output |
| `uv run hipson chat -q "show changed files" --session-db /tmp/.../runtime.sqlite` | PASS | Local/router mode executed `repo.changed_files` |
| `uv run hipson chat --fake -q "offline runtime smoke" --session-db /tmp/.../runtime.sqlite` | PASS | Explicit fake/offline response |
| `uv run hipson chat -q "write a novel about coffee" --session-db /tmp/.../runtime.sqlite` | EXPECTED_FAIL_CLOSED | Exit 1; unsupported local-router intent listed supported intents |
| `env -u OPENROUTER_API_KEY uv run hipson chat --provider openai-compatible -q "hello" --session-db /tmp/.../runtime.sqlite` | EXPECTED_FAIL_CLOSED | Exit 1; provider key missing |
| `uv run hipson tool list` | PASS | Registered tools listed |
| `uv run hipson tool show repo.changed_files` | PASS | Tool metadata displayed |
| `uv run hipson tool run repo.changed_files '{"path":"."}' --json --session-db /tmp/.../runtime.sqlite` | PASS | Read-only tool executed and persisted |
| `uv run hipson tool run repo.scan '{"path":".","include_diff":false}' --json --session-db /tmp/.../runtime.sqlite` | PASS | Read-only scan tool executed |
| `uv run hipson tool run packet.review.create '{"project":".","title":"x"}' --json` | EXPECTED_FAIL_CLOSED | Exit 1; write-risk tool rejected |
| `uv run hipson tool run repo.changed_files '{"path":"../"}' --json` | EXPECTED_FAIL_CLOSED | Exit 1; path traversal rejected |
| `uv run hipson session list --session-db /tmp/.../runtime.sqlite --json` | PASS | Sessions listed |
| `uv run hipson session search repo.scan --session-db /tmp/.../runtime.sqlite --json` | PASS | `search_backend: fts+fallback`; message/tool-call hits |
| `uv run hipson session show <session-id> --session-db /tmp/.../runtime.sqlite --json` | PASS | Session messages and metadata shown |
| `uv run hipson learn propose --session-id <session-id> --session-db /tmp/.../runtime.sqlite --json` | PASS | Learning proposal generated |
| `uv run hipson learn apply-memory --session-id <session-id> --proposal-id <proposal-id> --memory-dir /tmp/.../memory --json` | PASS | Memory applied only after explicit command |
| `uv run hipson memory --memory-dir /tmp/... add/search/list ...` | PASS | JSONL memory add/search/list passed |
| `uv run hipson memory --memory-dir /tmp/... search "Audit" --json` | FAILED_USAGE | `--json` is not supported on memory search |
| `uv run hipson sidecar route --task "security review of release diff" --risk security` | PASS | Deterministic sidecar recommendations |
| `uv run hipson sidecar route ... --llm --llm-dry-run` | DRY_RUN_ONLY | Redacted provider request preview; no live call |
| `uv run hipson install codex --dry-run` | DRY_RUN_ONLY | Dry-run completed |
| `uv run hipson scheduler --session-db /tmp/... create repo.changed_files '{"path":"."}'` | FAILED_USAGE | Correct syntax requires `--tool` and `--input` |
| `uv run hipson scheduler --session-db /tmp/... create --tool repo.changed_files --input '{"path":"."}'` | PASS | Job created |
| `uv run hipson scheduler --session-db /tmp/... list --json` | PASS | Pending job listed |
| `uv run hipson scheduler --session-db /tmp/... tick --json` | PASS | Job completed |

## Build And Installed Package Smoke

| Command | Result | Important Output |
|---|---:|---|
| `uv build --out-dir /tmp/.../dist` | PASS | Built `hipson-1.1.0.tar.gz` and wheel |
| `python -m venv /tmp/.../venv` | PASS | Temporary virtualenv created |
| `/tmp/.../venv/bin/pip install /tmp/.../dist/hipson-*.whl` | PASS | Wheel installed |
| Installed `hipson --help` | PASS | CLI entry point worked outside checkout |
| Installed `hipson doctor` | PASS | Doctor ran from installed package |
| Installed `hipson chat -q "show changed files" --session-db /tmp/...` | PASS | Local router worked in temp git repo |
| Installed `hipson tool run repo.changed_files '{"path":"."}' --json` | PASS | Tool execution worked from installed wheel |

## Security / Mutation / External Checks

| Command | Result | Summary |
|---|---:|---|
| `git status --short --ignored=matching build src/hipson.egg-info audit-output` | PASS | Shows `?? audit-output/` and ignored `src/hipson.egg-info/` |
| `uv run mutmut results || true` | PARTIAL | Results include survivors, timeouts, and not-checked mutants |
| `uv run mutmut results | ... count statuses` | PARTIAL | `{'survived': 184, 'not checked': 286, 'timeout': 148}` |
| `find . -maxdepth 3 \( -name '.env' ... \)` | PASS | Found only `./config/providers.example.env` |
| Secret-pattern `rg` scan excluding generated/vendor areas | PASS_WITH_FIXTURES | Found fake/test secret-like fixtures and docs references; no real committed credential observed |
| `git check-ignore -v .env config/providers.env src/hipson.egg-info build dist` | PASS | `.env`, provider env, egg-info, and dist are ignored |
| Full `uv run mutmut run` | SKIPPED | Existing results already show mutation closure incomplete; full run is costly |
| `uv run pip-audit` | SKIPPED | Avoided external advisory/network dependency in local audit |
| Live provider smoke | SKIPPED | No credentials or explicit live-network authorization |
| Publish/deploy/release commands | SKIPPED | Out of scope and unsafe for audit |

