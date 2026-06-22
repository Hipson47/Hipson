---
name: yolo-detector
description: Use when designing or verifying Ultralytics YOLO detection, segmentation, tracking, or pose inference with licensed models and stable JSON outputs.
---

# YOLO Detector

## Purpose

Define a reproducible YOLO inference boundary for detection, segmentation,
tracking, or pose. Keep model configuration, preprocessing, postprocessing,
result serialization, licensing, and verification explicit.

## Use When

- The task explicitly selects Ultralytics YOLO or requires its supported modes.
- An image, video, camera, or API workflow needs structured predictions.
- A demo must emit both machine-readable results and optional annotations.

## Do Not Use When

- A browser-side MediaPipe landmark task satisfies the requirement.
- The model family is undecided; use `cv-project-router` first.
- The task is training orchestration, identity recognition, or a medical claim.

## Inputs

- Mode: detect, segment, track, or pose.
- Model identifier, exact library/model version, source, license, checksum, and
  approved cache location.
- Image/frame contract, class allowlist, confidence and IoU thresholds, and
  device policy.
- Required JSON fields, coordinate space, annotation policy, and latency target.

## Default Stack

- Python, Ultralytics, OpenCV, and NumPy in a demo-owned environment.
- CPU baseline and a small pinned model only after explicit download approval.
- Absolute pixel `xyxy` boxes in JSON, with source dimensions included.
- OpenCV capture delegated to `opencv-realtime-camera` and web boundaries
  delegated to `cv-webapp-starter`.

## Workflow

1. Confirm the selected YOLO task and reject modes not supported by the pinned
   model. Record Ultralytics AGPL-3.0 or Enterprise licensing implications.
2. Require explicit approval before adding the dependency or downloading
   weights. Never execute model-repository scripts or arbitrary remote code.
3. Pin library and model revisions, record source/checksum/cache location, and
   keep weights outside Git.
4. Define image size, color order, confidence, IoU/NMS, class filters, maximum
   detections, device selection, warm-up, and deterministic seed where relevant.
5. Convert library-specific results at one adapter boundary. Validate finite
   confidence values, class identifiers, source dimensions, and clipped boxes.
6. For tracking, state tracker configuration and lifecycle; track IDs are
   session-local and must not be treated as identity.
7. Make annotated media optional and derived from normalized JSON, not a second
   source of truth.
8. End with `vision-verifier` using a licensed static fixture and expected
   structural assertions rather than brittle exact detections alone.

## Output Contract

Emit versioned JSON with this minimum shape:

```json
{
  "schema_version": "1.0",
  "task": "object-detection",
  "source": {"width": 1280, "height": 720},
  "model": {"id": "approved-model", "version": "pinned-version"},
  "detections": [
    {
      "id": "det-1",
      "class_id": 0,
      "label": "person",
      "confidence": 0.93,
      "bbox": {
        "x_min": 120.4,
        "y_min": 80.1,
        "x_max": 460.7,
        "y_max": 690.0,
        "coordinate_space": "pixel"
      }
    }
  ],
  "timing_ms": {"preprocess": 0.0, "inference": 0.0, "postprocess": 0.0}
}
```

Segmentation, pose, and tracking extensions must be versioned and documented.
Artifact references must be opaque relative paths or authorized URLs, never
local filesystem paths.

## Verification

- Validate JSON against the application schema, including empty detections,
  finite numbers, coordinate bounds, and stable class mappings.
- Run CPU inference on a licensed fixture after model availability is approved.
- Test corrupt input, unsupported mode, missing weights, offline cache miss,
  timeout, and unavailable accelerator fallback.
- Separate model-load, warm-up, preprocess, inference, and postprocess timing.
- Review annotated output manually only as additional evidence; do not replace
  structural tests with a screenshot.

## Failure Modes

- Weight download or cache miss without approval: stop and report the exact
  artifact required; do not trigger a hidden network download.
- Accelerator unavailable: fall back to the declared CPU baseline or fail
  clearly when the acceptance criterion requires acceleration.
- Empty detections: return a valid empty list, not a transport error.
- Invalid coordinates or NaN confidence: reject the result at the adapter
  boundary and report the affected frame/request.

## Safety Notes

- Review Ultralytics licensing before commercial, closed-source, or networked
  deployment. Library licensing does not grant rights to every model or dataset.
- Never accept arbitrary model paths, URLs, pickles, or remote code from a
  request. Use a server-side allowlist.
- Do not label persistent tracks as people or identities. Do not infer sensitive
  attributes from detections.
- Keep weights, uploads, annotations, private media, and output videos out of Git.
