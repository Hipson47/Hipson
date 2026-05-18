---
name: hipson-readme-craft
description: Use when creating, rewriting, auditing, or polishing README files for Hipson projects, CLI tools, developer workflow kits, open-source repos, and agent-skill libraries.
---

# Hipson README Craft

Use this skill to turn repository evidence into a clear, useful, good-looking
README without drifting into marketing fluff.

## Core Workflow

1. Inspect source truth before writing:
   - existing `README.md`, `pyproject.toml`, `package.json`, or equivalent manifest;
   - CLI entrypoints, public APIs, examples, tests, docs, and configuration files;
   - current setup commands and verification commands.
2. Define the README job:
   - first-time install and quick start;
   - contributor onboarding;
   - product positioning for public release;
   - internal handoff or operator guide.
3. Lead with the shortest accurate value proposition.
4. Put runnable commands early, and keep them copy-pasteable.
5. Preserve tested behavior and avoid inventing features, flags, URLs, badges,
   compatibility claims, benchmarks, or roadmap promises.
6. Keep visual polish lightweight: strong section names, scannable bullets,
   code blocks, small status tables, and optional badges only when backed by
   repository evidence.
7. End with practical development, safety, and troubleshooting notes when they
   help users succeed.
8. Verify all commands and links that can be checked locally.

## Recommended Structure

For CLI and developer-tool repos, prefer this order:

```markdown
# Project Name

One-sentence value proposition.

## Why It Exists
## Features
## Install
## Quick Start
## Common Commands
## Configuration
## Workflow
## Safety / Privacy
## Development
## CI / Release
```

Use fewer sections for small projects. Add architecture, API, screenshots,
examples, troubleshooting, or migration notes only when the repo supports them.

## Quality Bar

- The first screen should answer: what is this, who is it for, and how do I try it?
- Every claim should trace to source, tests, docs, or visible repo behavior.
- Setup should distinguish required steps from optional provider/API-key setup.
- Commands should use the repo's preferred tooling, such as `uv`, `pnpm`, `npm`,
  `pytest`, `ruff`, or project scripts.
- Security-sensitive docs should explicitly say where secrets belong and what
  should never be committed.
- Tone should be confident, concrete, and developer-friendly.

## README Audit Checklist

Check for:

- stale install commands or unsupported package managers;
- undocumented environment variables or provider keys;
- missing quick-start path from clone to first successful command;
- hidden generated directories, local-only files, or ignored runtime artifacts;
- claims not backed by implementation;
- broken links, inconsistent project names, or dead badges;
- walls of prose where examples or tables would scan better.

## Output Format

For a rewrite, provide the updated README and a short summary of evidence used.
For an audit, lead with concrete fixes ordered by user impact, then suggest a
minimal patch plan.
