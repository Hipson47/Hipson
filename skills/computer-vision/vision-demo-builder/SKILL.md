---
name: vision-demo-builder
description: Use when packaging an existing Computer Vision pipeline as a reproducible local CLI or lightweight demo with explicit artifacts and verification.
---

# Vision Demo Builder

## Purpose

Package an already selected CV pipeline into a small, reproducible demo. Own the
demo entry point, configuration, sample invocation, artifact layout, operator
README, and acceptance checklist without redefining the model or web trust
boundary.

## Use When

- A working CV pipeline needs a local CLI, bounded preview, or lightweight demo.
- The deliverable needs run commands, sample configuration, JSON, annotations,
  screenshots, FPS logs, or a demo README.
- A prerecorded fixture must make the demo usable without camera hardware.

## Do Not Use When

- The model/task is undecided; use `cv-project-router` first.
- The primary deliverable is a Next.js/FastAPI application; pair with
  `cv-webapp-starter` for its HTTP and browser boundaries.
- The request is production deployment, training infrastructure, or a broad
  application rewrite.

## Inputs

- Existing inference/capture adapter and its input/output contracts.
- Target audience, platform, run mode, acceptance criteria, and time budget.
- Approved dependencies/model assets and deterministic fixture.
- Required JSON, annotated media, screenshot, video, metrics, and retention rules.

## Default Stack

- Existing project stack first; otherwise a small Python CLI around the pipeline.
- OpenCV preview only for an explicitly local GUI demo.
- Static image/prerecorded video mode available alongside any camera mode.
- Environment variables or config files for non-secret options; no credentials
  required for the baseline demo.

## Workflow

1. Inspect the repository and reuse its CLI, configuration, logging, output,
   testing, and packaging conventions. Avoid new frameworks for a small demo.
2. Freeze the upstream frame and prediction contracts before adding presentation
   code. Keep inference logic out of UI callbacks.
3. Define one clear entry point with bounded inputs, output directory, overwrite
   policy, timeout/frame limit, CPU default, and optional device selection.
4. Provide a deterministic fixture path. Make camera, GPU, hosted API, and model
   download modes explicit options rather than startup side effects.
5. Emit machine-readable JSON as the source of truth. Derive annotations,
   screenshots, or video from that result and make media retention opt-in.
6. Write copy-pasteable setup/run commands, expected outputs, stop behavior,
   privacy notes, license gates, and troubleshooting for missing model/camera.
7. Keep generated artifacts under a bounded ignored directory with collision-safe
   names. Never overwrite source media by default.
8. End with `vision-verifier`; record which local, camera, browser, GPU, and live
   checks actually ran.

## Output Contract

Produce:

- a focused demo entry point and configuration contract;
- a README with prerequisites, approved provisioning, run/stop commands,
  expected files, privacy, licensing, and troubleshooting;
- versioned JSON plus optional derived annotation/screenshot/video references;
- deterministic fixture and test plan, subject to media approval;
- verification report with passed, failed, skipped, and unknown checks.

## Verification

- Start from a clean environment using the documented, approved dependencies.
- Run a deterministic image/video fixture and validate JSON and artifact paths.
- Test invalid input, missing model, unavailable camera/device, timeout,
  cancellation, existing output, and cleanup.
- Confirm the baseline does not require a secret, hosted service, camera, or GPU.
- Review any visual artifact at its intended size and record manual QA separately
  from automated checks.

## Failure Modes

- Setup requires an undocumented download or secret: stop and update the
  provisioning boundary before claiming the demo is reproducible.
- Camera/GPU absent: use the fixture/CPU path and report the optional mode skipped.
- Output is only a rendered image: add versioned JSON so behavior is testable.
- Demo framework obscures the pipeline: remove or isolate the framework layer
  rather than duplicating inference logic.
- Generated media cannot be cleaned safely: disable retention by default.

## Safety Notes

- A demo is not a production deployment or accuracy evaluation. State its limits.
- Never bundle secrets, private media, customer data, weights, datasets, or caches.
- Validate input/output paths and do not expose a local demo to a network without
  authentication, authorization, rate limiting, upload limits, and review.
- Do not execute downloaded installers, notebooks, model code, or shell scripts.
