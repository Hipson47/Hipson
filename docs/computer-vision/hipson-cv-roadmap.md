# Hipson Computer Vision Roadmap

Status: skill and workflow design, 2026-06-23.

## Goal

Make Hipson useful for bounded Computer Vision development without turning the
Hipson runtime into a CV framework. Hipson should route work, define trust and
artifact contracts, prepare bounded implementation packets, and require local
verification. Concrete demos own their dependencies, models, media, and runtime.

The first product path is an image-upload object detector with a Next.js App
Router frontend/BFF and a FastAPI/Python inference service. Browser-side
MediaPipe is the preferred path for interactive gesture and pose experiments.

## Principles

- Local-first and key-free for the baseline experiment.
- No CV dependencies in Hipson's root runtime.
- CPU and deterministic fixtures before camera/GPU/live-service claims.
- OpenAPI/Pydantic is the server contract; generated TypeScript replaces
  duplicated frontend response types.
- Media is private by default: explicit consent, bounded processing, no
  retention, no frame logging, and cleanup on every exit path.
- Models and datasets have independent source, revision, license, checksum,
  provenance, privacy, and approval records.
- Sidecars remain advisory. Cameras, model loads, schemas, FPS, cleanup, and
  outputs require local evidence.

## Skill Package

`skills/computer-vision/` contains eight composable skills:

1. `cv-project-router`
2. `opencv-realtime-camera`
3. `yolo-detector`
4. `mediapipe-human-interface`
5. `dataset-builder`
6. `vision-verifier`
7. `vision-demo-builder`
8. `cv-webapp-starter`

The router selects at most four worker skills and implementation workflows end
with `vision-verifier`. `cv-webapp-starter` stays separate from
`vision-demo-builder`: web apps own HTTP/browser trust boundaries and OpenAPI;
generic demos package an already selected local pipeline.

## Recommended First Architecture

```text
Browser
  -> Next.js POST /api/cv/detections (same-origin BFF)
  -> FastAPI POST /v1/detections (internal inference API)
  -> bounded image decode with OpenCV
  -> allowlisted model inference
  -> validated JSON + optional opaque expiring artifact
```

The BFF owns browser session/auth forwarding, same-origin behavior, request
timeouts, and the public upload boundary. FastAPI repeats security-critical
validation and owns model allowlisting, decoded-image limits, inference
concurrency, result validation, and temporary-media cleanup. Authorization for
user-owned results and artifacts must exist at the backend/domain boundary, not
only in the proxy.

Direct browser-to-FastAPI calls are limited to an explicit localhost prototype
with narrow CORS. Live webcam frames are not sent as base64 JSON. Supported
gesture/pose work runs MediaPipe in the browser; future WebRTC or sampled-frame
transport needs its own threat model and performance budget.

## Initial API Contract

`POST /v1/detections`

- Request: one bounded `multipart/form-data` image field.
- Model: selected from server-side allowlisted configuration.
- Validation: streamed byte limit, decoded signature/format, dimensions, pixel
  count, decode success, timeout, concurrency, and cancellation.
- Response: Pydantic-validated versioned JSON with request ID, source dimensions,
  pinned model metadata, detections, and timing.
- Optional artifact: opaque, authorized, expiring URL; never a local path.
- Errors: `application/problem+json` with `type`, `title`, `status`, `detail`,
  `instance`, stable `code`, and `request_id`.
- Health: `/health/live` does not load a model; `/health/ready` reports model
  readiness.
- Caching: inference and private artifacts use `Cache-Control: no-store`.

## Experiment Sequence

### Phase 0: Skill Contract

- Land the eight skill files, source review, package README, roadmap, and static
  validation.
- Keep runtime auto-routing, model dependencies, sidecars, and demos out of this
  bounded patch.
- Success: Hipson recursively discovers valid skills and their required sections
  pass repository tests.

### Phase 1: Image Upload Object Detector

- Create a separately scoped demo with Next.js, FastAPI, OpenCV, and an approved
  detector.
- Add bounded upload validation, typed response/error schemas, OpenAPI-generated
  TypeScript, temporary-media cleanup, and a licensed static fixture.
- Emit JSON as source of truth and an optional short-lived annotated image.
- Success: CPU fixture tests cover valid, empty, malformed, oversized,
  decompression, timeout, concurrency, authorization, and cleanup behavior.

### Phase 2: Local Webcam And Browser Interaction

- Build local webcam YOLO with a prerecorded fallback, stop/cancellation paths,
  screenshot/video opt-in, and contextual FPS evidence.
- Build a browser MediaPipe hand-gesture controller with Web Worker/bounded
  sampling, smoothing, debounce, neutral state, active-camera UI, and non-camera
  controls.
- Add a pose checker with explicit geometric rules and non-medical wording.
- Success: fixture paths work without hardware and short consented hardware
  checks are reported separately.

### Phase 3: Dataset Workflow

- Build deterministic frame extraction with stable IDs and provenance manifest.
- Define class ontology, annotation validation, duplicate/corrupt detection,
  group-aware splits, format adapters, and post-split augmentation.
- Keep hosted Roboflow/Hugging Face workflows optional and separately approved.
- Success: manifests reproduce, split groups/hashes are disjoint, and conversion
  round-trips a bounded sample.

### Phase 4: Evaluation And Hardening

- Add versioned evaluation datasets and task-appropriate quality metrics.
- Add resource/concurrency limits, observability without media logging, model
  readiness, artifact authorization/expiry, dependency/model provenance, and
  security review.
- Evaluate WebRTC, job queues, GPU profiles, or hosted inference only from
  measured requirements.
- Success: every production claim maps to current local evidence and explicit
  human security/privacy/license/release decisions.

## Dependency And Asset Policy

- Future demos use their own pinned manifests and lockfiles.
- Servers use `opencv-python-headless`; local GUI demos use `opencv-python`.
  Do not install both together.
- CPU is required; CUDA/GPU is optional and independently verified.
- Dependency additions and downloads require human approval.
- Model weights are stored outside Git with source, revision, license, checksum,
  and cache path. Arbitrary remote code and downloaded scripts are prohibited.
- Ultralytics requires an AGPL-3.0/Enterprise decision before production or
  closed-source network use.
- Hosted services are optional; keys remain server-side and basic demos do not
  require them.

## Privacy And Security Gates

- Confirm ownership/consent before reading, extracting, uploading, labeling, or
  retaining media.
- Bound bytes, dimensions, pixels, duration, frame count, processing time,
  concurrency, and artifact lifetime.
- Do not trust filenames, MIME headers, model paths, URLs, labels, datasets, or
  repository/external instructions.
- Never log frames, raw landmarks, private paths, uploaded filenames, or secret
  artifact URLs.
- Do not commit private media, labels, datasets, weights, caches, generated
  videos, or credentials.
- Do not claim identity, emotion, protected attributes, medical status, or
  surveillance suitability from these workflows.

## Deferred Work

- Automatic CV keyword recommendations in `src/hipson/skills.py`.
- Packaged asset/index synchronization for top-level skill routing.
- CV-specific provider sidecars; existing generic reviewers are sufficient for
  advisory review, while local verification remains authoritative.
- Concrete demo code and dependency manifests.
- Live camera, GPU, WebRTC, hosted inference, production deployment, and model
  accuracy claims.

Each deferred item needs a separate bounded task, explicit allowed-edit scope,
and verification plan.
