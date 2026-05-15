---
name: hipson-premium-ui-ux
description: Use for premium UI/UX review and implementation planning in Hipson frontend projects, especially screenshot-backed landing pages, forms, visual polish, accessibility, responsive behavior, and design-agent packets.
---

# Hipson Premium UI/UX

Use this skill when a Hipson task needs frontend work to feel polished, deliberate,
and production-ready rather than merely functional.

## Core Workflow

1. Inspect the actual rendered UI, not only code.
2. Capture desktop and mobile screenshots, or ask for them if unavailable.
3. Review visual quality before implementation details:
   - hierarchy, typography, spacing, density;
   - image quality, crop, aspect ratio, and focal point;
   - color balance, contrast, and brand fit;
   - form controls, focus states, keyboard behavior, and errors;
   - responsive layout and touch ergonomics.
4. Identify the smallest implementation changes that raise perceived quality.
5. Verify with browser screenshots and relevant tests/build commands.
6. For sidecars, include screenshots, viewport sizes, changed files, and relevant
   excerpts from `skills/external/`.

## Recommended References

Load only the references needed for the task:

- `skills/external/anthropic/frontend-design`: visual design and frontend UX critique.
- `skills/external/anthropic/brand-guidelines`: brand consistency, voice, and visual fit.
- `skills/external/anthropic/theme-factory`: palette and theme generation.
- `skills/external/vercel-labs/web-design-guidelines`: practical web design rules.
- `skills/external/vercel-labs/react-best-practices`: React implementation quality.
- `skills/external/openai-curated/screenshot`: screenshot capture workflows.
- `skills/external/openai-curated/playwright-interactive`: browser QA workflows.
- `skills/external/openai-curated/figma-implement-design`: Figma-to-code implementation.
- `skills/external/openai-curated/figma-generate-design`: code-to-Figma or screen design.

## Premium Review Rubric

Call out issues that make the UI feel cheap or unfinished:

- default native controls where a custom control is expected;
- pixelated, stretched, low-resolution, or poorly cropped images;
- one-note palettes or colors that fight the product domain;
- weak heading scale, cramped vertical rhythm, or inconsistent alignment;
- hidden focus states, poor contrast, missing labels, or awkward keyboard flows;
- responsive breakpoints that merely shrink instead of re-composing the layout;
- decorative elements that do not serve hierarchy or comprehension.

## Packet Template For `premium_ui_ux`

```markdown
## Goal
[What should feel premium and production-ready]

## Audience / brand
[Who this is for, what tone the interface should have]

## Screenshots
- [desktop screenshot path and viewport]
- [mobile screenshot path and viewport]

## Relevant files
- [path]

## Current concern
[What looks/feels wrong now]

## Skill excerpts to apply
- [short excerpts or paths from skills/external]

## Output wanted
1. Critical visual issues
2. UX/accessibility issues
3. Concrete implementation recommendations
4. Verification checklist
```

## Guardrails

- Do not accept visual changes without rendered verification.
- Do not add decorative complexity to hide weak layout fundamentals.
- Do not introduce new UI dependencies unless the existing stack cannot support
  the interaction or accessibility requirement cleanly.
- Prefer targeted polish over broad redesigns unless the current screen cannot
  meet the product goal.
