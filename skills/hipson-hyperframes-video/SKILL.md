---
name: hipson-hyperframes-video
description: Use for optional HyperFrames video composition workflows, website-to-video planning, product videos, launch videos, animated case studies, social shorts, and deterministic timeline QA.
---

# Hipson HyperFrames Video

Use this skill when the user wants a video composition, website-to-video
workflow, launch video, product intro, animated case study, pitch video, social
short, or rendered MP4-style output using HyperFrames.

Do not merge this workflow into normal web UI design. HyperFrames work is a
separate video composition path.

## What HyperFrames Is

HyperFrames is a deterministic HTML composition workflow for rendered video.
The composition is written as plain HTML/CSS/JavaScript, previewed locally, and
rendered to video with Node.js and FFmpeg tooling where available.

Use it when a web-like layout should become a timed video: product launch,
feature explainer, changelog recap, case-study animation, pitch asset, or social
short.

## When Not To Use

- Normal static UI design.
- Backend, API, database, or security-only tasks.
- Pure React component implementation.
- One-off image generation.
- Any task where the user has not asked for video, timeline, render, MP4, or
  HyperFrames.

## Cold Start

When the user only describes the video, ask for or infer:

- audience and goal;
- platform and aspect ratio;
- duration and FPS;
- source copy;
- visual references or brand constraints;
- desired mood;
- audio or silent/caption-first delivery;
- output format and deadline.

Then produce a video brief before implementation.

## Warm Start

When the user provides a URL, page, PDF, transcript, changelog, CSV, or copy:

1. Extract the story beats.
2. Choose what can be shown as text, product UI, chart, screenshot, or motion
   card.
3. Define scenes with timestamps.
4. Identify assets that must be local, generated, exported, or omitted.
5. Produce a HyperFrames implementation brief.

## Core HyperFrames Rules

- Use plain HTML compositions, not React or Vue, unless the user explicitly
  asks for a framework.
- The root composition element must include `data-composition-id`,
  `data-width`, and `data-height`.
- Timed elements use `class="clip"`, `data-start`, `data-duration`, and
  `data-track-index`.
- GSAP timelines should be paused and registered on `window.__timelines`.
- Avoid `Math.random()` and async timeline setup.
- Keep video elements muted; use separate audio elements.
- Prefer deterministic rendering over live network data or runtime randomness.
- Node.js 22+ and FFmpeg are required for local rendering.
- Use preview, render, and lint commands where available.

## Video Brief Format

```markdown
# HyperFrames Video Brief

## Goal
## Source Material
## Format
Aspect ratio, duration, FPS, platform.

## Creative Direction
Mood, visual style, type, color, motion, audio.

## Scene Breakdown
Scene-by-scene timing, visuals, captions, motion, audio.

## HyperFrames Implementation Notes
HTML structure, data attributes, GSAP timeline, assets, commands.

## Codex Brief
Concrete implementation task.

## QA Checklist
Timing, captions, contrast, audio, render quality, determinism.
```

## Codex Implementation Brief Requirements

Include:

- composition id;
- width, height, FPS, and duration;
- target folder and file names;
- scene timing table;
- required assets and fallback assets;
- text/caption source;
- exact animation/timeline expectations;
- preview, lint, and render commands;
- local output path, normally ignored by git;
- QA checklist for deterministic rendering.

## QA Checklist

- Root element has required composition data attributes.
- Every timed element has deterministic `data-start`, `data-duration`, and
  `data-track-index`.
- Timelines are paused and registered on `window.__timelines`.
- No `Math.random()` or async setup affects final timing.
- Video elements are muted and audio is separate.
- Captions are readable at target platform size.
- Contrast is sufficient.
- Timing matches the scene breakdown.
- Rendered output is reviewed for dropped frames, clipping, bad crops, and
  audio sync.
- Generated media remains out of git unless intentionally approved.

## Repo Hygiene

Use local output folders such as `hyperframes-output/`, `exports/`, or `tmp/`
for renders and generated assets. Do not commit large generated MP4, WebM, MOV,
WAV, MP3, SRT, or VTT files unless the user explicitly approves them as source
assets.
