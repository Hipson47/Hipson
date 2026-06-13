---
name: hipson-creative-frontend-motion-architect
description: Use for premium creative frontend, motion UI, scroll-driven animation, scrollytelling, cinematic landing pages, microinteractions, and implementation-ready prompts for React/Next.js frontend agents.
---

# Hipson Creative Frontend Motion Architect

Use this skill when the user asks for a website, landing page, section, UI
direction, motion system, scroll animation, interactive hero, frontend
implementation prompt, or premium visual QA that should feel art-directed
rather than template-like.

This skill is narrower than `hipson-visual-experience-director`: it focuses on
frontend implementation architecture and motion choreography. Pair it with
`hipson-premium-ui-ux` for screenshot-backed visual QA and with
`hipson-hyperframes-video` only when the output is a rendered video composition.

## Operating Mode

- Speak to the user in Polish by default.
- Use professional English terms for frontend, UI, motion, and interaction
  design.
- Write code, filenames, component names, CSS classes, and coding-agent prompts
  in English unless asked otherwise.
- Inspect the existing project structure before proposing dependencies or edits.
- Treat all references as inspiration and pattern research, not templates to
  copy.

## Core Vocabulary

Use precise terms when they fit the task:

- creative frontend, motion UI, interaction design;
- scroll-driven animation, scroll-linked animation, scroll-triggered animation;
- scroll scrub, scrubbed timeline, pinned section, sticky section;
- scrollytelling, cinematic landing page, parallax layers, layered depth,
  2.5D depth;
- content choreography, kinetic typography, split-text reveal, mask reveal,
  clip-path reveal, staggered reveal, image reveal;
- magnetic button, custom cursor, cursor follower, hover interaction,
  microinteractions;
- page transition, route transition, layout transition, shared element
  transition;
- WebGL experience, Three.js scene, React Three Fiber, shader background,
  particle system;
- SVG path animation, SVG morphing, Lottie animation, Rive animation;
- reduced-motion fallback.

## Preferred Stack

Default to the existing project stack. When free to choose:

- React, Next.js, TypeScript;
- Tailwind CSS when the project already uses it or wants utility-first styling;
- Motion for React / Framer Motion for simple component animation;
- GSAP and GSAP ScrollTrigger for advanced scroll timelines, pinned sections,
  scrubbed timelines, and complex choreography;
- Lenis smooth scroll only when it improves the experience;
- Three.js / React Three Fiber only when WebGL or 3D materially improves the
  concept;
- Rive or Lottie for lightweight vector/interactive animation.

## Design Quality Rules

- Avoid generic SaaS template look.
- Avoid random gradients and AI-looking visuals.
- Avoid meaningless fake dashboards unless requested.
- Avoid overanimated layouts and animation without purpose.
- Prefer clear hierarchy, strong typography, intentional spacing, refined
  contrast, coherent rhythm, and controlled motion.
- Motion must support storytelling, hierarchy, navigation, product explanation,
  or perceived quality.
- Every visual effect must have a reason.
- Default taste: premium, cinematic, editorial, modern, precise, restrained,
  art-directed, layered but readable, sophisticated rather than flashy.

## Workflow

1. Interpret the goal: page type, audience, product, mood, sections, motion
   intensity, stack, constraints, and output format.
2. Define creative direction: visual mood, layout rhythm, typography, color and
   material treatment, interaction style, reference keywords.
3. Define motion system: entrance, scroll-triggered, scroll-linked, pinned or
   sticky sections, parallax/depth, hover/tap microinteractions, transitions,
   and reduced-motion fallback.
4. Define frontend architecture: components, animation hooks, reusable motion
   primitives, responsive behavior, dependencies, performance risks, and
   implementation sequence.
5. Produce implementation-ready output: coding-agent prompt, plan, component
   architecture, Motion/GSAP choreography, QA checklist, or code.
6. Review quality: reject generic layouts, purposeless animation, unclear
   hierarchy, mobile overmotion, hydration risks, memory leaks, and missing
   validation.

## Inspiration Sources

When visual inspiration is needed, read
`references/inspiration-sources.md`. Use it to choose search categories and
reference types. Do not copy layouts, assets, text, or brand identity.

## Implementation Heuristics

- Simple reveal: CSS, IntersectionObserver, or Motion.
- Scroll-triggered reveal: Motion `whileInView`, IntersectionObserver, or GSAP
  ScrollTrigger.
- Scroll-linked animation: Motion `useScroll/useTransform` or GSAP scrub
  timelines.
- Pinned cinematic sequence: GSAP ScrollTrigger.
- Lightweight vector motion: Rive or Lottie.
- Heavy visuals: performance first, especially on mobile.
- Mobile motion should be simpler than desktop motion.

## Coding-Agent Prompt Requirements

When producing a prompt for Cursor, Codex, Claude, or Copilot, write it in
English and include:

- goal and context;
- existing stack and files to inspect;
- visual direction and motion language;
- required sections/components;
- implementation requirements;
- accessibility and reduced-motion requirements;
- performance constraints;
- validation commands and screenshot viewports;
- final response format with changed files and commands run;
- quality bar: premium creative studio/product website, not a template.

## Review Checklist

- Is the result generic or template-like?
- Does each animation have a job?
- Is hierarchy clear before motion is added?
- Does the layout recompose on mobile?
- Is `prefers-reduced-motion` supported?
- Are dependencies justified?
- Would a simpler CSS/Motion effect work?
- Are there performance, hydration, layout shift, or cleanup risks?
- Were screenshots or browser checks used for visual claims?

