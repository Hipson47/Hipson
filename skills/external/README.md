# External Skill Library

This directory vendors selected public agent skills for Hipson workflows.

The project copy is useful for:
- auditability: the exact instructions are versioned with Hipson;
- packet building: sidecar agents can be given relevant skill excerpts;
- portability: project agents can use the same source material even before a global Codex install;
- design quality: UI/UX agents have explicit visual, interaction, and testing standards.

Runtime note: Codex discovers active skills from the Codex skills directory, usually
`~/.codex/skills`. The same selected skills have also been installed there on this
machine. Restart Codex to load newly installed global skills.

## Sources

- `openai-curated/`: selected skills from <https://github.com/openai/skills>
- `vercel-labs/`: selected skills from <https://github.com/vercel-labs/agent-skills>
- `anthropic/`: selected skills from <https://github.com/anthropics/skills>

## Selection Policy

Installed by default:
- design and UI/UX review skills;
- Figma and screenshot-driven implementation workflows;
- frontend testing and browser inspection workflows;
- React and web composition guidance;
- deployment helpers for common hosting targets;
- code review, GitHub PR, Sentry, security, and documentation workflows.

Not installed by default:
- novelty/demo skills;
- platform-specific skills unrelated to current Hipson work;
- skills focused on handling provider tokens directly;
- duplicate skills when an equivalent safer source was already installed.

## High-Value UI/UX Stack

Use these first for public-facing frontend work:
- `anthropic/frontend-design`
- `anthropic/brand-guidelines`
- `anthropic/theme-factory`
- `vercel-labs/web-design-guidelines`
- `openai-curated/screenshot`
- `openai-curated/playwright-interactive`
- `openai-curated/figma-implement-design`
- `openai-curated/figma-generate-design`
- `openai-curated/figma-create-design-system-rules`

For Hipson sidecars, pair these with the `premium_ui_ux` agent and include screenshots,
viewport sizes, target user, brand constraints, and the exact files changed.
