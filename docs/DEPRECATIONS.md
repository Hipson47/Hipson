# Deprecations

Hipson's product is the lead's task ledger (`hipson task`, `hipson lead`, MCP `task.*` / `lead.resume`),
`hipson init` and the local workflow primitives (`work`, `route`, `scan`, `packet`, `verify run`,
`evidence`, `audit`, `kit review`, `run`, `memory`, `skill`, `doctor`, `policy`, `session`, `tool`,
`sidecar`, `provider`, `quality`). The experimental surfaces below had open functional defects in the 1.6
stabilization review ([STABILIZATION-1.6.md](STABILIZATION-1.6.md)) and overlap the ledger.

Since 1.7 they are **deprecated**: hidden from `hipson --help`, the lead quick reference, the agent
contract and MCP; still callable, with a one-line warning on stderr (stdout and JSON output are
unchanged); **removed in 2.0**.

| Command | Why | Use instead |
|---|---|---|
| `hipson autopilot` | Duplicates `kit review`; `implement` prepares a packet and launches no worker; `resume` skipped provider policy (D04) | `hipson kit review`; `hipson task do` or `task open --check ... --start` + `task run` for implementation |
| `hipson release claim` (and MCP `release.claim`, removed) | Stale quality artifacts could override failed verification and a rejected decision (D01/C7); it is not a release gate | `hipson task close` (fresh declared checks, acknowledged results, no open blocking/major findings) and the owner's own release approval |
| `hipson scheduler` | No atomic job claim, jobs bind no workspace (D12/D13) | Your own scheduler (cron, CI) running ordinary Hipson commands in the right directory |
| `hipson chat` (runtime, gateway) | Provider chat lost tool output and history, reported success after provider failure (D08/D09) | A coding agent (Claude Code, Codex) with the `hipson` MCP server |
| `hipson learn` | Dropped the end of longer sessions (D16) | `hipson memory add` for durable lessons |
| `hipson hermes` | Status bridge outside the ledger; mixed JSON output (D20) | The ledger (`task now`, `task events`); the bridge stays callable for existing integrations until 2.0 |

The evidence/audit gate fix (D01/C7) also applies to `audit show` and `kit review`, which stay. Moving from
`autopilot review` to `kit review`: both apply the project policy's `denied_paths` and prompt-required operations
(`--approve-operation`); `kit review` runs a provider sidecar only with explicit `--run-sidecar --ai-profile`,
whereas autopilot's `local_only` check refused sidecars outright.
