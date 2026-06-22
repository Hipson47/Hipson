# Hipson Computer Vision Skill Pack

This package routes and verifies bounded Computer Vision work without adding CV
libraries to Hipson itself. It is designed for local experiments first, then
web applications that use a Next.js App Router frontend/BFF with a FastAPI
Python inference service or browser-side MediaPipe where that is the safer
runtime.

## Package Entry Point

Start with `cv-project-router` when the input, model family, runtime, or output
is not already fixed. Keep the selected stack small and always finish
implementation work with `vision-verifier`.

```bash
hipson skill view cv-project-router --root .
hipson skill list --root . --query computer-vision
```

The skills are recursively discoverable by Hipson. Automatic CV keyword routing
is not part of this package; select the router explicitly until runtime routing
is implemented in a separately scoped change.

## Skills

| Skill | Use it for |
| --- | --- |
| `cv-project-router` | Select a bounded CV workflow from modality, task, runtime, privacy, and output constraints. |
| `opencv-realtime-camera` | Own image/video/webcam acquisition, frame loops, FPS measurement, and cleanup. |
| `yolo-detector` | Design Ultralytics YOLO detection, segmentation, tracking, or pose inference and normalized outputs. |
| `mediapipe-human-interface` | Build hands, gesture, face, or pose interactions with smoothing and privacy controls. |
| `dataset-builder` | Extract frames, define labels, split data, convert formats, and validate provenance. |
| `vision-verifier` | Verify imports, fixtures, schemas, model readiness, camera fallbacks, cleanup, and performance claims. |
| `vision-demo-builder` | Package an existing CV pipeline as a reproducible local demo. |
| `cv-webapp-starter` | Define Next.js/FastAPI or browser-only CV application boundaries and contracts. |

Hipson limits active skills to five. Typical stacks stay below that limit:

- image upload detector: `yolo-detector`, `cv-webapp-starter`, `vision-verifier`;
- local webcam detector: `opencv-realtime-camera`, `yolo-detector`,
  `vision-demo-builder`, `vision-verifier`;
- gesture UI: `mediapipe-human-interface`, `cv-webapp-starter`,
  `vision-verifier`;
- dataset frame extraction: `opencv-realtime-camera`, `dataset-builder`,
  `vision-verifier`.

## First Experiments

1. **Image upload object detector**: Next.js handles same-origin browser traffic,
   FastAPI validates a bounded multipart image, and YOLO returns typed detection
   JSON plus an optional short-lived annotated artifact.
2. **Local webcam YOLO detector**: OpenCV owns capture and cleanup, YOLO owns
   inference, and a prerecorded fixture proves the pipeline without a camera.
3. **Hand gesture web controller**: MediaPipe runs in the browser, emits
   debounced semantic events, and does not upload frames by default.
4. **Pose checker**: explicit geometry and confidence rules produce heuristic,
   non-medical feedback with a recorded failure state for missing landmarks.
5. **Dataset frame extractor**: deterministic sampling creates collision-safe
   files, a provenance manifest, and leakage-resistant split candidates.

The image upload detector is the recommended first serious web application. It
has deterministic fixtures and a smaller privacy and latency surface than live
video streaming.

## Web Application Direction

For server-side models, prefer:

```text
Browser -> Next.js route handler/BFF -> FastAPI /v1/detections
        -> OpenCV decode -> model inference -> JSON + expiring artifact
```

FastAPI/Pydantic is the API source of truth. Export OpenAPI and generate the
TypeScript client rather than maintaining duplicate browser types. Keep model
selection server-side and allowlisted. For gesture and pose interactions that
MediaPipe supports in the browser, keep frames on-device and use a client-only
interactive island.

Do not use base64 JSON as a webcam streaming protocol. Keep local webcam YOLO
as a Python demo first; treat WebRTC or sampled-frame upload as a later,
separately threat-modeled capability.

## Dependency Policy

- Hipson keeps zero CV runtime dependencies. Each future demo owns its pinned
  manifest and lockfile.
- Dependency additions, model downloads, and external services require explicit
  human approval.
- Use `opencv-python-headless` on servers and `opencv-python` only for GUI
  demos; do not install both in one environment.
- CPU is the baseline. GPU/CUDA is an optional documented profile.
- Record every model's source, version or revision, license, checksum, and cache
  location outside Git.
- Review Ultralytics AGPL-3.0 or Enterprise terms before production or
  closed-source network use.
- Roboflow and Hugging Face integrations are optional. Keep keys server-side
  and review every hosted model and dataset license separately.

## Privacy And Repository Safety

- Default to local processing and no media retention.
- Request camera access only after explicit user action and provide a visible
  active-camera indicator and stop control.
- Bound upload bytes, decoded dimensions, pixel count, duration, processing
  time, and concurrency; validate decoded content rather than trusting a MIME
  header or filename.
- Do not log frames, original filenames, local paths, landmarks, or private
  artifact URLs.
- Do not commit uploads, private screenshots, datasets, labels, weights,
  generated videos, caches, or secrets.
- Do not imply identity recognition, biometric classification, surveillance,
  or medical diagnosis.

## Example Hipson Prompts

```text
Use cv-project-router to plan an image-upload object detector with a Next.js
frontend and FastAPI backend. Keep media local, define the OpenAPI response, and
end with vision-verifier.
```

```text
Use opencv-realtime-camera, yolo-detector, vision-demo-builder, and
vision-verifier to design a local webcam demo with a prerecorded fallback and
measured FPS. Do not download weights without approval.
```

```text
Use mediapipe-human-interface and cv-webapp-starter to design a browser-only
hand gesture controller with smoothing, a stop control, and no frame uploads.
```

## Verification Checklist

- Run `hipson skill doctor --root . --json`.
- Run `pytest -q tests/test_computer_vision_skills.py`.
- Confirm all eight `SKILL.md` files have the required sections.
- Confirm JSON examples parse and do not expose filesystem paths.
- Review `source-candidates.md` before adopting a library or hosted artifact.
- Run `git diff --check` and review only the bounded CV package diff.
- State camera, GPU, browser, model-download, and live-service checks as skipped
  unless they actually ran in the target environment.
