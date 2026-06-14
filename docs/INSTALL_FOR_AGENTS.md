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

## Codex Rules

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
