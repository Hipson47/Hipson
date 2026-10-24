# Agent Provider Model

## Goal
Give Hipson cheap sidecar intelligence without wasting main-context tokens.

## Layers
1. Native Codex subagents: best for local repo reads, bounded implementation, and diff review.
2. OpenRouter sidecars: best for cheap paid second opinions on prepared packets.
3. Local RAG memory: best for durable project knowledge and reusable decisions.

## OpenRouter Sidecars
The source checkout defaults to `src/hipson/assets/config/agents.json` (bundled in
installed packages); `config/agents.json` is the tracked mirror. Select a custom
file explicitly with `hipson sidecar --config PATH ...`.

Run:

```bash
hipson sidecar list
hipson sidecar run --agent reviewer_lite --packet runs/review-packet.md --dry-run
```

After packet preflight and content review, omit `--dry-run` for an explicit
provider call. `scripts/hipson_agents.py` remains a compatibility wrapper.

Keep secrets in exported environment variables or ignored local env files:

```bash
mkdir -p ~/.config/hipson
cp .env.example .env
```

Do not store real provider keys in `config/`. For local development, use the
current working directory's ignored `.env`. For shared configuration, use
`<Hipson home>/agents.env` (normally `~/.config/hipson/agents.env`), or select a
file with `HIPSON_AGENTS_ENV` or `hipson sidecar --env PATH ...`. Exported values
win; absent an explicit env file, the working-directory `.env` precedes the
shared file. Hipson home honors `HIPSON_HOME`, then `XDG_CONFIG_HOME/hipson`.

## Guardrails
- Send packets, not full repos.
- Redact secrets before sending anything to external providers.
- `scripts/hipson_agents.py` redacts common key/token/password patterns before API calls.
- Packets are capped by `--max-packet-chars` to control cost and context size.
- Sensitive paths such as `.env`, `.ssh`, and `.config` are refused as packet inputs.
- Sidecars are read-only.
- Sidecar reports are advisory.
- Save outputs in `runs/` and summarize only useful findings back into chat.
- Treat `runs/` and `scans/` as generated local artifacts; do not commit them by default.

## Good Sidecar Tasks
- Test gap analysis.
- Premium UI/UX critique for screenshot-backed frontend packets.
- Security checklist review.
- Prompt/task-packet critique.
- Changelog or progress summarization.
- Second opinion on a small architecture decision.

For design and frontend packets, use the [skill library](skill-library.md) to
choose relevant vendored skill excerpts from `skills/external/`. Sidecars do not
read the repository themselves. The current runner sends text only: include
needed snippets, file context, and visual observations from local screenshot
review. Screenshot paths alone do not send image contents to the provider.

## Bad Sidecar Tasks
- Direct repo edits.
- Broad source ingestion.
- Secret handling.
- Final approval.
- Anything requiring local tool execution.

## Local RAG Memory
Use local memory when prior decisions, handoffs, risks, or repo-specific facts
should survive across sessions without sending broad context to providers:
- `memory/notes.jsonl`: durable facts and decisions.
- `memory/sources.jsonl`: file/path provenance.
- `hipson memory add`: store a redacted note.
- `hipson memory search`: retrieve relevant notes.
- `hipson memory list`: inspect recent notes.
- Optional embeddings provider later.

Keep memory compact. Store decisions and handoffs, not transcripts.

## Model Routing
Use [model routing](model-routing.md) for configured sidecar roles and routing
controls. Its dated pricing snapshot is historical, not a live cost guarantee.
