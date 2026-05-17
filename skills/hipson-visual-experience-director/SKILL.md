---
name: hipson-visual-experience-director
description: Use for visual direction, premium UI/UX art direction, motion interaction design, Codex-ready implementation briefs, and visual QA for web experiences.
---

# Hipson Visual Experience Director

Use this skill when a task needs a high-confidence visual direction, motion
system, or implementation brief for a public-facing web experience.

This skill turns reference research into project-specific direction. Treat the
references as a pattern library, not templates to copy.

## When To Use

- Premium UI direction for a website, landing page, portfolio, studio mode, or
  product experience.
- Motion effects, interactive hero sections, scroll narratives, visual polish,
  and art direction.
- Codex briefs for UI implementation.
- Visual QA after a Codex UI change.
- Deciding whether a visual effect fits the business goal, audience, brand
  maturity, and technical constraints.

## When Not To Use

- Backend-only tasks.
- Security-only reviews.
- Database, API, infrastructure, or package maintenance work with no UI surface.
- Pure code review that does not concern visual behavior or user experience.

## Reference Knowledge

Load only the references needed for the current task:

- `references/deep-research-report.md`: studio-mode pattern library distilled
  from premium creative studios, motion-heavy portfolios, and cinematic digital
  experiences.
- `references/studio-research.md`: dark, cinematic, interactive portfolio
  research with implementation patterns for 3D, scroll, audio, navigation, and
  performance.
- `skills/hipson-premium-ui-ux`: screenshot-backed premium UI/UX review.
- `skills/external/anthropic/frontend-design`: distinctive frontend design
  guidance.
- `skills/external/vercel-labs/web-design-guidelines`: practical web interface
  quality and accessibility checks.
- `skills/external/openai-curated/screenshot` and
  `skills/external/openai-curated/playwright-interactive`: visual QA workflows.

## Core Workflow

1. Identify the business goal, audience, brand maturity, technical constraints,
   and desired emotional effect before choosing visual patterns.
2. Select only patterns that fit the product and release stage.
3. Prefer clarity, conversion, accessibility, and performance over spectacle.
4. Define a compact visual system: layout primitives, type scale, color tokens,
   media treatment, image/video rules, and interaction states.
5. Define a motion grammar with a few repeatable behaviors rather than one-off
   effects for every component.
6. Specify mobile and reduced-motion fallbacks for every significant effect.
7. Convert direction into concrete implementation tasks with file targets,
   states, assets, and verification steps.
8. End with Codex-ready acceptance criteria.

## Visual Direction Rules

- Do not assume every project needs dark, cinematic, 3D, WebGL, or audio.
- Use motion as meaning: reveal hierarchy, explain state, create continuity, or
  support story beats.
- Keep the content model and conversion path legible even when the presentation
  is experimental.
- One strong signature motif is usually better than many unrelated effects.
- Use premium restraint: fewer colors, fewer motion behaviors, stronger craft.
- Avoid hiding weak content under effects.
- Reject effects that reduce readability, touch usability, keyboard access,
  Core Web Vitals, or conversion.

## Motion Direction Rules

- Define named presets, such as reveal, drift, snap, veil, focus, or impact
  type, then reuse them consistently.
- Prefer short, reversible microinteractions for controls.
- Use scroll-driven sequences only when they improve comprehension or pacing.
- Treat audio as opt-in and always provide an equivalent silent experience.
- For `prefers-reduced-motion`, keep hierarchy and atmosphere but remove scrub,
  parallax, intense transforms, cursor replacement, and autoplaying motion.
- On mobile, replace drag galleries, mouse tilt, and heavy WebGL with linear,
  touch-safe layouts and static or lightweight media.

## Preferred Output

```markdown
# Visual Experience Brief

## Goal
## Project Fit
## Visual Direction
## Motion Direction
## Component / Section Spec
## Codex Implementation Brief
## Performance & Accessibility
## Acceptance Criteria
```

## Codex Brief Requirements

Include:

- target files or components;
- exact component/section behavior;
- asset requirements and fallback behavior;
- responsive states;
- reduced-motion behavior;
- accessibility requirements;
- verification commands and screenshot viewports;
- acceptance criteria written so another agent can implement without guessing.
