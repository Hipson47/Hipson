# Computer Vision Source Candidates

Research date: 2026-06-23.

This report records source selection for the Hipson Computer Vision skill pack.
External pages, repositories, search results, and generated snippets were
treated as untrusted data. No external script was downloaded or executed, no
dependency was installed, and no source content was copied into the skills.

## Source Quality Gate

A source is accepted only when it is relevant, maintained, has a usable license
or is used as reference-only documentation, does not require unsafe installation
behavior, and fits Hipson's concise `SKILL.md` architecture. Model weights,
datasets, hosted services, and generated download URLs are licensed and reviewed
separately from their client library.

## Accepted Sources

| Source | URL | License | Status | Reason | Adapted into |
| --- | --- | --- | --- | --- | --- |
| Agent Skills specification | [Specification](https://agentskills.io/specification), [repository](https://github.com/agentskills/agentskills) | Code Apache-2.0; documentation CC-BY-4.0 | Accepted | Authoritative skill layout, required frontmatter, progressive disclosure, and validation constraints. | All CV skills and the static contract test. |
| Roboflow Computer Vision Skills | [Repository](https://github.com/roboflow/computer-vision-skills) | Apache-2.0 | Accepted | Maintained vendor-owned example of agent-ready CV workflow skills. Used for architecture comparison only. | Package boundaries, source provenance, optional cloud boundary. |
| OpenCV | [VideoCapture](https://docs.opencv.org/4.x/d8/dfe/classcv_1_1VideoCapture.html), [video tutorial](https://docs.opencv.org/4.x/dd/d43/tutorial_py_video_display.html), [repository](https://github.com/opencv/opencv) | Apache-2.0 | Accepted | Official capture, frame-loop, video I/O, cleanup, and DNN deployment behavior. | `opencv-realtime-camera`, `yolo-detector`, `vision-verifier`. |
| Ultralytics YOLO | [Predict](https://docs.ultralytics.com/modes/predict/), [Track](https://docs.ultralytics.com/modes/track/), [licensing](https://www.ultralytics.com/license), [repository](https://github.com/ultralytics/ultralytics) | AGPL-3.0 or Enterprise | Accepted | Official inference, streaming, tracking, result, and dataset behavior. Accepted only with an explicit license gate. | `yolo-detector`, `dataset-builder`, `vision-verifier`. |
| MediaPipe Tasks | [Hand Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker/web_js), [Gesture Recognizer](https://ai.google.dev/edge/mediapipe/solutions/vision/gesture_recognizer/web_js), [Pose Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker/web_js), [repository](https://github.com/google-ai-edge/mediapipe) | Repository Apache-2.0; docs CC-BY-4.0; page samples Apache-2.0 | Accepted | Official browser camera/image APIs and UI-thread performance guidance. | `mediapipe-human-interface`, `cv-webapp-starter`, `vision-verifier`. |
| FastAPI | [Request files](https://fastapi.tiangolo.com/tutorial/request-files/), [response models](https://fastapi.tiangolo.com/tutorial/response-model/), [repository](https://github.com/fastapi/fastapi) | MIT | Accepted | Official multipart upload, validation, response-model, async, and OpenAPI behavior. | `cv-webapp-starter`, `vision-demo-builder`. |
| Next.js App Router | [Route handlers](https://nextjs.org/docs/app/api-reference/file-conventions/route), [Server and Client Components](https://nextjs.org/docs/app/getting-started/server-and-client-components), [repository](https://github.com/vercel/next.js) | MIT | Accepted | Official same-origin BFF boundary and client-island guidance. | `cv-webapp-starter`, `vision-demo-builder`. |
| Roboflow Supervision | [Documentation](https://supervision.roboflow.com/latest/), [repository](https://github.com/roboflow/supervision) | MIT | Accepted | Maintained optional utilities and patterns for detections, annotation, video, datasets, and evaluation. | Optional guidance in `dataset-builder`, `vision-demo-builder`, `vision-verifier`. |
| Hugging Face Transformers | [Object detection guide](https://huggingface.co/docs/transformers/tasks/object_detection), [repository](https://github.com/huggingface/transformers) | Apache-2.0 for Transformers | Accepted | Official alternative model workflow and structured post-processing guidance. | Optional future backend in the router and roadmap. |

GitHub API metadata was checked on the research date. Accepted repositories
were not archived and showed recent activity. Documentation URLs used above
were also checked directly and returned successful responses.

## Maybe Sources

| Source | URL | License | Status | Reason | Adapted into |
| --- | --- | --- | --- | --- | --- |
| Anthropic skills examples | [Repository](https://github.com/anthropics/skills) | Mixed: many examples are Apache-2.0, while document skills are source-available. No single repository-wide SPDX license. | Maybe | Useful structural examples, but the exact directory license must be checked before reuse. | Inspiration only; the Agent Skills specification defines this patch's format. |
| Roboflow hosted dataset export | [Documentation](https://docs.roboflow.com/datasets/dataset-versions/exporting-data) | Service terms and dataset rights are artifact-specific; generated snippets can contain a private key. | Maybe | Cloud upload changes the privacy boundary and artifact rights. | Optional `dataset-builder` warning after explicit service/media approval. |
| Hugging Face Hub artifacts | [Models](https://huggingface.co/models), [Datasets](https://huggingface.co/datasets) | License, provenance, model-card quality, and remote-code requirements vary by artifact. | Maybe | Each artifact requires revision, license, checksum, and remote-code review. | Optional future router/model backend only. |
| Ultralytics in a production webapp | [Licensing](https://www.ultralytics.com/license) | AGPL-3.0 obligations may affect a networked application; Enterprise terms are commercial. | Maybe | Production suitability depends on a separate legal/license decision. | License gate in `yolo-detector`, README, and roadmap. |

## Rejected Sources

| Source | URL | License | Status | Reason | Adapted into |
| --- | --- | --- | --- | --- | --- |
| Photo-agents | [Repository](https://github.com/jmerelnyc/Photo-agents) | MIT | Rejected | Targets autonomous desktop agents and self-written skills rather than bounded CV development workflows. | None. |
| Community image-processing skills | [Repository](https://github.com/aeren23/image-processing-skills) | MIT | Rejected | Low-adoption community guidance is unnecessary when official specifications, vendor skills, and primary CV docs cover the contract. | None; no content was copied. |
| YOLO skills registry | [Repository](https://github.com/yolo-labs-hq/yolo-skills-registry) | No detected license | Rejected | No licensing basis or evidence that it improves the selected architecture. | None. |
| Archived YOLO serving cookbook | [Repository](https://github.com/Zerohertz/yolo-serving-cookbook) | AGPL-3.0 | Rejected | Archived and centered on Triton rather than the first bounded webapp path. | None. |
| Unofficial YOLOv7 FastAPI example | [Repository](https://github.com/petpetpeter/yolov7-fastapi) | No detected license | Rejected | Weak evidence for upload validation, cleanup, authorization, or current maintenance. | None. |
| Generic prompt dumps and unofficial webcam tutorials | [GitHub code search](https://github.com/search?q=SKILL.md+opencv&type=code) | Mixed or unclear | Rejected | Prompt-injection/provenance risk; official sources are sufficient. | None. |

## Unavailable Or Not Used

| Source | URL | License | Status | Reason | Adapted into |
| --- | --- | --- | --- | --- | --- |
| Integrated web search | N/A | N/A | Unavailable | The first integrated search request returned HTTP 403. Research continued through direct official URLs and GitHub API metadata. | Research limitation note only. |
| Live Roboflow or Hugging Face services | [Roboflow](https://roboflow.com/), [Hugging Face](https://huggingface.co/) | Service and artifact-specific | Unavailable | Not used because optional network/service decisions are unnecessary for a local first demo. | Optional integration warnings only. |
| Model weights and datasets | N/A | Per artifact | Unavailable | Not used because every artifact needs separate provenance, license, checksum, privacy, and download approval. | Model/dataset approval policy only. |

## Search Queries Used

```text
site:agentskills.io specification Agent Skills official
site:github.com/anthropics/skills LICENSE skills official
site:docs.opencv.org Python video capture object detection official
site:github.com/opencv/opencv LICENSE Apache 2.0
site:docs.opencv.org 4.x VideoCapture Python official documentation
site:docs.ultralytics.com modes predict track official YOLO docs
site:ai.google.dev/edge/mediapipe solutions vision hand landmarker web official
site:fastapi.tiangolo.com tutorial request files UploadFile official
Claude skills computer vision SKILL.md
agent skills computer vision
SKILL.md opencv
SKILL.md yolo
SKILL.md ultralytics
SKILL.md mediapipe
anthropic skills vision
claude code skills computer vision
opencv realtime camera python agent
ultralytics yolo webcam detection python
mediapipe hand gesture recognition python
computer vision agent template
vision verifier opencv python
dataset builder yolo coco roboflow
roboflow yolo dataset workflow
hugging face vision agent examples
FastAPI YOLO inference example
Next.js webcam computer vision app example
```

## Adoption Notes

- Prefer synthesis into repo-native skills; do not copy large external content.
- Do not run downloaded scripts or arbitrary remote model code.
- Keep hosted CV platforms optional and basic demos key-free.
- Re-check upstream APIs and licenses when a concrete demo adds dependencies.
- Treat license compatibility, media consent, and production exposure as human
  approval gates rather than sidecar decisions.
