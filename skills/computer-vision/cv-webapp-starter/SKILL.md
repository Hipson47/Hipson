---
name: cv-webapp-starter
description: Use when starting a Computer Vision web application with Next.js, FastAPI, browser MediaPipe, typed APIs, bounded uploads, and private media handling.
---

# Computer Vision Webapp Starter

## Purpose

Define the first production-shaped web boundary around a CV pipeline. Choose
between a Next.js frontend/BFF with FastAPI inference and browser-only MediaPipe,
then specify typed contracts, upload/camera limits, auth, artifacts, privacy, and
verification before scaffolding code.

## Use When

- A browser uploads images to a Python/OpenCV/model pipeline.
- Next.js needs a same-origin route handler/BFF in front of FastAPI.
- A browser camera drives MediaPipe gesture or pose UI.
- OpenAPI-generated TypeScript clients and Problem Details errors are required.

## Do Not Use When

- The deliverable is only a local Python script or dataset operation.
- The CV model/task is undecided; route it first.
- Live high-rate video streaming is assumed without a latency, privacy, and
  transport design.

## Inputs

- Existing frontend/backend stack, auth model, deployment topology, and API style.
- Input modality, maximum bytes/dimensions/pixels/duration, accepted decoded
  formats, timeout, concurrency, and retention.
- Inference contract, allowlisted model, expected JSON, annotated artifact rules,
  and browser overlay coordinate requirements.
- Privacy, logging, data residency, threat model, and local/offline expectations.

## Default Stack

- Next.js App Router with Server Components by default and a small client camera
  island only where interaction requires it.
- Next.js route handler/BFF for same-origin browser traffic and server-side auth.
- FastAPI async endpoint, Pydantic v2 response models, and OpenAPI as the source
  of truth for generated TypeScript clients.
- OpenCV decode plus a server-side allowlisted model for uploads.
- Browser-side MediaPipe for supported gesture/pose tasks, with no frame upload.

## Workflow

1. Inspect the repository's routing, auth, API client generation, error, test,
   upload, and configuration conventions before choosing the architecture.
2. Use Next.js BFF -> FastAPI for server inference. Permit direct browser ->
   FastAPI only for an explicit localhost prototype with narrow CORS and no
   secrets. Use browser MediaPipe for supported low-latency gesture/pose UI.
3. Define `POST /v1/detections` as multipart image input. Ignore the supplied
   filename; stream a byte limit; decode and validate signature, format,
   dimensions, pixel count, timeout, and concurrency.
4. Keep model selection in server configuration. Reject request-provided model
   paths, URLs, pickles, arbitrary parameters, and remote code.
5. Return Pydantic-validated, versioned detection JSON. Use `application/problem+json`
   with stable `code` and `request_id` for errors. Add liveness separately from
   model-readiness checks.
6. Export OpenAPI and generate the TypeScript client. Do not hand-maintain a
   duplicate detection response type.
7. Store uploads only in isolated temporary storage when memory processing is
   unsuitable. Clean up on success, error, timeout, disconnect, and cancellation.
   Serve derived artifacts through opaque, authorized, expiring identifiers.
8. Enforce authentication/authorization at the API/data boundary when users or
   private artifacts exist. Add rate, request-size, timeout, and concurrency
   limits before any non-local exposure.
9. For camera UI, request permission after user action, show active/stop state,
   stop tracks on navigation, and provide non-camera controls.
10. End with `vision-verifier`; keep webcam streaming/WebRTC as a later,
    separately threat-modeled extension.

## Output Contract

The architecture output must include:

- component/data-flow decision and rejected alternatives;
- Next.js route/client boundaries and FastAPI endpoint/model boundaries;
- OpenAPI generation command/path and generated-client ownership;
- multipart limits, auth/rate limits, timeout/concurrency, retention, cleanup,
  cache-control, and artifact authorization;
- versioned response JSON with source dimensions, model metadata, detections,
  timing, request ID, and optional expiring artifact references;
- Problem Details error schema and liveness/readiness behavior;
- test matrix and privacy/license approvals.

## Verification

- Test valid image, empty result, malformed multipart, wrong content, truncated
  image, oversized bytes, excessive pixels, decompression-bomb case, timeout,
  concurrency limit, disconnect, and cleanup.
- Validate FastAPI output against Pydantic/OpenAPI and compile the generated
  TypeScript client against frontend usage.
- Verify authorization on inference and private artifact access, not only in the
  Next.js proxy. Verify rate and size limits at the effective public boundary.
- Confirm responses and artifacts use `Cache-Control: no-store` where private.
- Verify browser camera permission denial, stop/unmount cleanup, unsupported
  browser, worker failure, and keyboard/touch fallback.

## Failure Modes

- Backend blocks the async loop during inference: isolate bounded CPU/GPU work
  using the repository's worker/executor pattern and enforce concurrency.
- Proxy buffers an unbounded upload: reject early at both public and inference
  boundaries and stream where supported.
- OpenAPI/client drift: regenerate in CI or fail a checked contract-diff test.
- Temporary files or artifacts survive failure: treat cleanup as a failed check
  and keep retention disabled.
- Browser camera or model asset unavailable: preserve upload/manual controls and
  show an accessible bounded error state.

## Safety Notes

- Treat every route handler, API endpoint, upload, artifact URL, and camera as a
  public attack surface even in an early prototype.
- Never expose keys, model paths, local filenames, private artifact URLs, or
  internal errors to the browser. Do not log uploaded media.
- Do not stream webcam frames as base64 JSON. Threat-model WebRTC or sampled
  upload separately before implementation.
- Keep secrets server-side and media local/no-retention by default. Production,
  licensing, privacy, and deployment decisions remain human approval gates.
