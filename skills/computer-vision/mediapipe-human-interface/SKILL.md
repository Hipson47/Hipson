---
name: mediapipe-human-interface
description: Use when building browser or Python hand, gesture, face, or pose interactions with smoothing, semantic events, and privacy-preserving media handling.
---

# MediaPipe Human Interface

## Purpose

Turn MediaPipe landmarks or gesture results into stable, accessible UI events
without treating body data as identity. Prefer browser execution for supported
interactive tasks so frames remain on-device and latency stays low.

## Use When

- Hands, gestures, face landmarks, or pose landmarks control a user interface.
- A browser camera interaction can run locally with MediaPipe Tasks.
- Temporal smoothing, debounce, confidence, and loss-of-tracking behavior need
  a documented contract.

## Do Not Use When

- The task is general object detection, dataset labeling, face recognition, or
  identity tracking.
- The task claims medical, diagnostic, employment, emotion, or biometric
  conclusions from landmarks.
- Server-side frame upload is proposed without a requirement that justifies the
  added privacy boundary.

## Inputs

- Landmark/task type and exact model asset source, version, license, and cache.
- Image or video mode, target FPS, maximum subjects, and confidence thresholds.
- Semantic UI event map, debounce/smoothing windows, cooldown, and neutral state.
- Browser support, worker strategy, camera permission UX, and reduced-motion or
  keyboard alternatives.

## Default Stack

- MediaPipe Tasks for Web in a Next.js client component for gesture/pose UI.
- Web Worker or bounded sampling when synchronous inference would block the UI.
- Python MediaPipe only when the rest of a local Python pipeline requires it.
- Local-only frames, no retention, CPU baseline, and explicit keyboard/touch
  fallbacks for every gesture action.

## Workflow

1. Decide browser versus Python from privacy, latency, device, and integration
   constraints. Document why media crosses a process or network boundary.
2. Request approval before adding packages or model assets. Pin asset URLs or
   vendored files with license and integrity metadata; do not execute downloads.
3. Define camera start as an explicit user action with active state, stop
   control, permission-denied state, and cleanup on navigation/unmount.
4. Normalize landmarks and confidence into a small adapter contract. Do not
   expose raw task-library objects across the application.
5. Define smoothing, hysteresis, debounce, cooldown, missing-landmark timeout,
   handedness/orientation handling, and a neutral event.
6. Emit semantic events such as `select`, `next`, or `stop`; keep UI state
   transitions testable without a live camera.
7. Move repeated inference off the main UI thread or sample frames when needed.
   Measure end-to-end event latency rather than model call time alone.
8. Verify with deterministic result fixtures, then use a short consented camera
   smoke check only when the target environment provides one.

## Output Contract

Return:

- runtime and model-asset decision with license and privacy boundary;
- normalized landmark/result shape and semantic event schema;
- confidence, smoothing, debounce, cooldown, and neutral-state rules;
- camera lifecycle, worker/sampling strategy, and accessibility fallback;
- latency/FPS evidence and skipped hardware checks;
- no raw frame, private path, or biometric identifier fields.

## Verification

- Unit-test landmark-to-event mapping using synthetic normalized fixtures.
- Test threshold boundaries, jitter, repeated gestures, lost landmarks,
  multiple subjects, mirrored input, and the neutral state.
- Verify camera tracks stop and workers/listeners terminate on stop and unmount.
- Verify permission denial, unsupported browser, asset-load failure, and slow
  device behavior have accessible fallbacks.
- Run keyboard/touch controls independently of MediaPipe.

## Failure Modes

- Permission denied or no camera: keep the application usable with alternative
  controls and explain the missing capability.
- Model asset unavailable/offline: fail into a bounded disabled state; do not
  repeatedly fetch or silently switch models.
- UI thread stalls: reduce sampling, move work to a worker, or lower complexity;
  report the measured tradeoff.
- Landmark loss/jitter: emit neutral state after the documented timeout and
  avoid stale or repeated actions.

## Safety Notes

- Treat face, hand, pose, and persistent track data as sensitive even when no
  image is stored.
- Do not upload or retain frames by default. Do not log raw landmarks or camera
  identifiers.
- Never infer identity, health, emotion, protected attributes, or intent from
  landmarks.
- Preserve explicit consent, active-camera visibility, stop control, and
  accessible non-camera alternatives.
