# Agent Autopilot Layer

Hipson Agent Autopilot Layer v0 turns the CLI control plane into an
agent-discoverable workflow layer. The coding agent remains Codex, Cursor,
Claude Code, or an MCP client. Hipson supplies the local contract, packet
boundary, verification evidence, policy, and handoff artifacts.

## Default Loop

```text
agent bootstrap -> contract -> work plan -> packet -> preflight -> verify -> quality report -> evidence -> audit -> summary
```

The local-first command is:

```bash
hipson autopilot review --task "review current diff" --json
```

For bounded implementation work, use an executor packet with explicit edit
scope:

```bash
hipson autopilot implement --task "implement bounded parser fix" --allowed-edit src/hipson/parser.py,tests --verification "git diff --check" --json
```

It writes:

```text
runs/<work_id>/
  contract.json
  work.json
  review-packet.md
  preflight.json
  verify.json
  quality.json
  evidence.jsonl
  audit.json
  summary.md
  manifest.json
  handoff.json
  handoff.md
```

Default execution is provider-free. `--ai-profile <name>` prepares the advisory
sidecar path. A real provider-backed sidecar requires explicit `--run-sidecar`.

Runs can be resumed without rebuilding the packet or work plan:

```bash
hipson autopilot resume --run runs/<work_id> --rerun-step verify --json
```

`--rerun-step` accepts `contract`, `preflight`, `verify`, `quality`,
`quality_eval`, `evidence`, `audit`, `summary`, `handoff`, or `manifest`, and
can be repeated. This is for repairing or refreshing missing/stale artifacts
without repeating the full workflow.

## Run Control

After any review/autopilot run:

```bash
hipson run status --run runs/<work_id> --json
hipson run validate --run runs/<work_id> --json
hipson run handoff --run runs/<work_id> --json
```

`run status` gives agents a compact current-state payload. `run validate`
checks required files and JSON artifact kinds. `run handoff` writes
`handoff.json` and `handoff.md` for the next agent.

Release/security claims are evaluated separately from verification:

```bash
hipson release claim --run runs/<work_id> --claim "release readiness" --human-decision approved --json
```

The claim is allowed only when audit evidence has a passed verification gate,
a passed release claim gate, and the claim command records an approved human
decision. Otherwise it writes a blocked claim with concrete reasons.

## Agent Bootstrap

Agents can discover Hipson through:

```bash
hipson agent bootstrap --target codex --json
hipson agent bootstrap --target cursor --json
hipson agent bootstrap --target claude --json
```

The bootstrap artifact is `hipson.agent_bootstrap`. It reports the detected
project, contract availability, recommended first command, installed surfaces,
warnings, policy summary, and fallback commands.

## MCP Stdio

`hipson mcp serve --catalog` exposes a read-first catalog for MCP clients, and
`hipson mcp serve --stdio` starts a minimal line-delimited JSON-RPC stdio
server. The current tool set is:

- `contract.show`;
- `policy.show`;
- `work.create`;
- `packet.preflight`;
- `verify.run`;
- `quality.report`;
- `evidence.append`;
- `audit.show`.
- `run.status`;
- `run.validate`;
- `handoff.create`;
- `release.claim`.

Read-first tools are provider-free. `verify.run` and `evidence.append` are gated
and require `approved=true` in the tool arguments before they execute local
commands or write evidence. `handoff.create` and `release.claim` are also gated
because they write run artifacts. Provider-backed review remains an explicit
sidecar action; MCP never hides provider calls.

## Project Policy Enforcement

Autopilot checks `.hipson/policy.json` or `.hipson/policy.yaml` before running.
Invalid policy blocks the run. `denied_paths` block autopilot when the current
git diff touches protected files. `local_only: true` blocks provider-backed
sidecars even if `--run-sidecar` is passed. Prompt-required operations must be
explicitly approved through the CLI.
For implementation runs, `--allowed-edit` is checked against policy
`denied_paths` and `allowed_paths` before the run bundle is created.

## Agent Surfaces Doctor

Use:

```bash
hipson doctor --agent-surfaces --json
```

It reports whether Codex, Cursor, Claude, and MCP notes are installed, whether
the current project policy is valid, whether the agent contract is available,
and the recommended next command.

## Human Gate

Hipson can automate local evidence collection. It cannot approve release,
security, destructive, credential, or irreversible decisions. Those remain human
gates even when local verification passes.
