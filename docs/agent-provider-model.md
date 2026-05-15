# Agent Provider Model

## Goal
Give Hipson cheap sidecar intelligence without wasting main-context tokens.

## Layers
1. Native Codex subagents: best for local repo reads, bounded implementation, and diff review.
2. OpenRouter sidecars: best for cheap paid second opinions on prepared packets.
3. Local RAG memory: best for durable project knowledge and reusable decisions.

## OpenRouter Sidecars
Configured in `config/agents.json`.

Run:

```bash
python3 scripts/hipson_agents.py list
python3 scripts/hipson_agents.py run --agent reviewer_lite --packet /tmp/review-packet.md
```

Secrets live outside the repo:

```bash
mkdir -p ~/.config/hipson
cp .env.example .env
```

Do not store real provider keys in `config/`. For local development, use repo-root `.env`. For one shared config across many repos, use `~/.config/hipson/agents.env` or set `HIPSON_AGENTS_ENV`.

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

For design and frontend packets, use `docs/skill-library.md` to choose the relevant
vendored skill excerpts from `skills/external/`. Sidecars do not read the repository
by themselves, so include the needed snippets, screenshots, and file context in the
packet.

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
Use `docs/model-routing.md` as the source of truth for choosing sidecar models by task difficulty and cost.
