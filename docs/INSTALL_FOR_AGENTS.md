# Install For Agents

Install Hipson once, then install managed instructions for the agent surfaces
you use:

```bash
hipson install agents --all --dry-run
hipson install agents --all --apply
```

Targeted installs are available:

```bash
hipson install agents --codex --dry-run
hipson install agents --cursor --dry-run
hipson install agents --claude --dry-run
hipson install agents --mcp --dry-run
```

Apply mode preserves existing files by merging a Hipson managed marker block and
creating backups before changes. Codex installs also generate hook/rule
templates under the Codex hooks directory. Templates are not force-enabled.

## Lead and Worker Modes

Enter worker mode when the task brief says you are a worker for a lead, contains `Worker rules:`,
or when `HIPSON_ROLE=worker` is set. A task or assignment id alone does not make you a worker.
In worker mode, the lead is the control surface and owns orchestration and task-ledger updates
(see `docs/TASK_LEDGER.md`). Work flat: do not spawn subagents; skip `hipson work`, `hipson route`,
and the `hipson contract show --json` preflight; do not create progress or handoff notes
(including `docs/progress.md`); do not commit or push; do not write the task ledger.
Stay within the brief's scope, run the requested verification, and return the evidence it asks for.
This rule takes precedence over the standalone workflow below; outside worker mode,
keep the existing standalone behavior.

## Codex Rules (Standalone)

The managed Codex block tells Codex to:

- call `hipson contract show --json` before non-trivial repo work;
- create a work plan with `hipson work`;
- run packet preflight before provider-backed sidecars;
- verify locally before claiming success;
- append evidence and show audit for handoff;
- use the human gate for release, security, destructive, credential, and
  irreversible actions.

## Project Policy

Project policy can live at `.hipson/policy.json` or `.hipson/policy.yaml`.

```bash
hipson policy show --json
hipson policy validate
```

The policy controls default workflow, denied paths, prompt-required operations,
local-only defaults, release gates, and agent integration settings. Deny/allow
path conflicts fail validation.

## Health Check

After installing, check agent readiness:

```bash
hipson doctor --agent-surfaces --json
```

For MCP clients, v0 provides a catalog and a minimal stdio surface:

```bash
hipson mcp serve --catalog
hipson mcp serve --stdio
```

The stdio server exposes read-first tools for contract, policy, work planning,
packet preflight, quality report, audit, run status, and run validation. Local
execution/write tools such as `verify.run`, `evidence.append`,
`handoff.create`, and `release.claim` require explicit `approved=true` tool
arguments.

The central ledger also exposes `task.show`, `task.events`, `task.list`, and
`task.board`, plus gated `task.open`, `task.assign`, `task.finish`, `task.reap`,
`task.event`, and `task.status` tools. See [Task Ledger](TASK_LEDGER.md#mcp-stdio-s2)
for the Windows/WSL launch command and argument contracts.
