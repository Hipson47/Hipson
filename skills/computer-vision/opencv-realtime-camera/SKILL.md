---
name: opencv-realtime-camera
description: Use when building or reviewing OpenCV image, video, or webcam frame loops with deterministic fallbacks, performance measurement, and reliable cleanup.
---

# OpenCV Realtime Camera

## Purpose

Own bounded frame acquisition and lifecycle behavior for images, video files,
and local cameras. Produce a reusable frame source that model-specific skills can
consume without duplicating capture, timing, error, or cleanup logic.

## Use When

- A Python workflow reads a webcam, video file, image sequence, or single image.
- A demo needs FPS measurement, frame sampling, preview, recording, or capture
  fallback behavior.
- OpenCV color conversion, resizing, or frame timestamps need explicit rules.

## Do Not Use When

- Browser `getUserMedia` and MediaPipe own the full pipeline.
- The task is model selection, annotation, dataset splitting, or HTTP upload.
- A still-image library already satisfies the requirement without a frame loop.

## Inputs

- Source kind and allowlisted camera index or bounded local file path.
- Expected width, height, frame rate, sampling interval, and stop condition.
- Required color space and preprocessing contract.
- Preview/output policy, retention policy, and deterministic fixture path.
- Target OS, headless/GUI environment, and CPU/GPU assumptions.

## Default Stack

- Python, NumPy, and `opencv-python` for an explicitly local GUI demo.
- `opencv-python-headless` for servers, CI, and non-GUI processing.
- Monotonic timing, bounded queues, CPU baseline, and a prerecorded fixture.
- One OpenCV wheel variant per environment; never install GUI and headless
  variants together.

## Workflow

1. Inspect existing dependency and media-path conventions. Request approval
   before adding OpenCV or changing a lockfile.
2. Define one frame-source interface for image, video, and camera inputs. Never
   accept an arbitrary remote URL or shell-expanded path as a camera source.
   Resolve local paths and enforce approved-root containment after symlink
   resolution before opening a source or output.
3. Open the source, check `isOpened`, validate every `read` result, and reject
   empty or unexpectedly large frames before downstream work.
4. Make resize, aspect-ratio, channel order, dtype, normalization, and timestamp
   behavior explicit. Do not let capture and model code disagree about BGR/RGB.
5. Bound runtime with a stop key, cancellation signal, frame count, or timeout.
   Avoid an unbounded producer when inference is slower than capture.
6. Measure warm-up separately and report processed FPS plus dropped/skipped
   frames over a defined interval.
7. Put capture release, writer release, and window teardown in reliable cleanup
   paths that run on success, error, and cancellation.
8. Provide a static image or prerecorded video fallback for CI and machines
   without a camera.

## Output Contract

Return:

- source configuration with sensitive paths redacted;
- frame contract: shape, dtype, color space, timestamp, and resize policy;
- stop, timeout, backpressure, and cleanup behavior;
- optional artifact paths under an approved generated-output directory;
- processed-frame count, dropped/skipped count, timing window, and FPS;
- commands/checks run and hardware checks skipped.

## Verification

- Run the loop against a small, licensed fixture and assert end-of-stream and
  cleanup behavior.
- Test invalid source, failed open, failed read, empty frame, cancellation, and
  output-write failure.
- Verify frame shape/color/timestamp at the model boundary.
- If a camera is available, run a short bounded smoke check; otherwise mark it
  skipped and keep the fixture check authoritative.
- Verify headless execution does not call GUI APIs.

## Failure Modes

- Camera unavailable or permission denied: report the device error and switch to
  the declared fixture; do not probe unrelated devices indefinitely.
- Decode failure or corrupt frame: stop or skip according to an explicit error
  budget and report counts.
- Inference cannot keep up: use bounded buffering or intentional sampling and
  report dropped frames.
- Writer codec unavailable: preserve JSON/metrics, fail the optional artifact,
  and do not claim video output succeeded.

## Safety Notes

- Request camera permission explicitly and show active/stop state in interactive
  demos. Do not start capture in the background.
- Default to no recording. Never log frames, private filenames, or local paths.
- Validate output paths, avoid overwrites by default, and keep private media,
  generated frames, and videos out of Git.
- Do not install platform packages, camera drivers, or codecs without approval.
