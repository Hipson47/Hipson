# Hipson Skill Library

Hipson keeps a curated local library of public agent skills in `skills/external/`.
The first batch focuses on design quality, frontend testing, Figma workflows,
GitHub review workflows, deployment, documentation, and security planning.

Hipson also includes local wrapper skills for Hipson-specific workflows:

- `skills/hipson-premium-ui-ux/`: default entrypoint for screenshot-backed
  premium UI/UX review.
- `skills/hipson-visual-experience-director/`: visual direction, art direction,
  motion interaction design, Codex implementation briefs, and visual QA.
- `skills/hipson-hyperframes-video/`: optional HyperFrames website/document to
  video brief and timeline QA workflow.

## Installed Sources

| Source | Project path | Focus |
|---|---|---|
| OpenAI curated Codex skills | `skills/external/openai-curated/` | Figma, screenshots, browser testing, GitHub, security, docs, deployments |
| Vercel Labs agent skills | `skills/external/vercel-labs/` | React, web design, composition, transitions, Vercel deployment |
| Anthropic agent skills | `skills/external/anthropic/` | frontend design, brand, theme, testing, artifacts, documents, MCP |

Source links:
- <https://github.com/openai/skills>
- <https://github.com/vercel-labs/agent-skills>
- <https://github.com/anthropics/skills>

## Runtime Model

There are two copies with different jobs:

- `skills/external/`: versioned project reference for Hipson packets, sidecar prompts,
  audits, and future packaging.
- `~/.codex/skills`: active Codex skill install on this machine.

After installing new global skills, restart Codex so the runtime skill list refreshes.

## UI/UX And Motion Agent Usage

For premium frontend review, use the `premium_ui_ux` sidecar together with
these skills as source material:

- `skills/hipson-premium-ui-ux`
- `skills/external/anthropic/frontend-design`
- `skills/external/anthropic/brand-guidelines`
- `skills/external/anthropic/theme-factory`
- `skills/external/vercel-labs/web-design-guidelines`
- `skills/external/vercel-labs/react-best-practices`
- `skills/external/openai-curated/screenshot`
- `skills/external/openai-curated/playwright-interactive`
- `skills/external/openai-curated/figma-implement-design`
- `skills/external/openai-curated/figma-generate-design`

Use `visual_experience_director` when the task needs a new direction, motion
system, studio-mode concept, interactive hero plan, implementation brief, or
visual QA acceptance criteria. Prefer `premium_ui_ux` for screenshot-backed
review of an already rendered UI.

Use `hyperframes_video_director` only for HyperFrames or rendered video
composition workflows such as website-to-video, launch videos, animated case
studies, pitch videos, or social shorts. Do not route normal static UI design
there.

Good UI/UX packets should include:
- screenshot paths or embedded screenshots;
- desktop and mobile viewport sizes;
- current implementation files;
- target brand and audience;
- explicit acceptance criteria for visual quality, accessibility, and responsive behavior.

## Subagent Prompting

All non-trivial subagent prompts should be assembled from skills, not improvised
as generic task descriptions. Use:

- `skills/hipson-gpt/skill_system-prompt-architect.md` for prompt structure;
- `skills/hipson-gpt/skill_agentic-rag-orchestration.md` for multi-agent workflow;
- the relevant domain skills from this library for the actual task.

A good packet names the selected skills, gives bounded repo evidence, states the
agent role, defines verification expectations, and requires an evidence-backed
report with file paths, commands, screenshots, or diffs where applicable.

## Supply-Chain Policy

Do not blindly install community "awesome" lists into active runtime directories.
Use them as discovery indexes, then vendor only specific skill folders after review.

Before adding a new source:
- inspect `SKILL.md`, scripts, and any referenced assets;
- check whether it requests secrets, external network access, or destructive actions;
- prefer official or vendor-owned repos for default installs;
- avoid duplicate skills unless the workflow is clearly better.
