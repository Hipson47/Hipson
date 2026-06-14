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
```

Default execution is provider-free. `--ai-profile <name>` prepares the advisory
sidecar path. A real provider-backed sidecar requires explicit `--run-sidecar`.

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

## MCP Skeleton

`hipson mcp serve --json` exposes a read-first catalog for future MCP clients:

- `contract.show`;
- `work.create`;
- `packet.preflight`;
- `verify.run`;
- `quality.report`;
- `evidence.append`;
- `audit.show`.

The skeleton does not hide provider calls. Provider-backed review remains an
explicit sidecar action.

## Human Gate

Hipson can automate local evidence collection. It cannot approve release,
security, destructive, credential, or irreversible decisions. Those remain human
gates even when local verification passes.
