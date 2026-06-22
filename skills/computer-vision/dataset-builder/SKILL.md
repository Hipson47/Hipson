---
name: dataset-builder
description: Use when extracting, labeling, splitting, converting, validating, or versioning Computer Vision datasets with provenance, privacy, and leakage controls.
---

# Computer Vision Dataset Builder

## Purpose

Create reproducible CV dataset artifacts from owned or licensed media. Make
provenance, class definitions, annotation formats, split strategy, validation,
augmentation, versioning, and privacy decisions explicit before training.

## Use When

- Extracting frames from video or images from an approved source.
- Defining or converting YOLO, COCO, or another documented annotation format.
- Creating train/validation/test splits, class maps, dataset manifests, or
  quality reports.

## Do Not Use When

- Media ownership, consent, license, or intended use is unknown.
- The task is model training/inference without dataset changes.
- The request asks to scrape private media, bypass access controls, or infer
  sensitive labels.

## Inputs

- Source inventory, ownership/consent/license, retention, and allowed uses.
- Dataset task, class ontology, inclusion/exclusion rules, and annotation format.
- Sampling policy, duplicate policy, split groups, ratios, seed, and version ID.
- Image constraints, labeling tool/export source, augmentation policy, and
  destination outside the Hipson repository.

## Default Stack

- Python, OpenCV, NumPy, and standard JSON/CSV/YAML parsers in a project-owned
  environment.
- Local filesystem and content hashes for the baseline; Roboflow, Supervision,
  and Hugging Face remain optional integrations.
- YOLO or COCO only when the downstream model contract requires it.
- Group-aware deterministic splits and augmentation after splitting.

## Workflow

1. Inventory sources and record owner, license, consent, collection method,
   allowed use, retention, and sensitive-content handling. Stop if rights are
   unresolved.
2. Freeze a versioned class map and annotation policy with positive, negative,
   ignored, occluded, truncated, and ambiguous examples.
3. Extract frames deterministically by timestamp, interval, or scene rule. Use
   collision-safe identifiers derived from stable source IDs, never private paths.
4. Validate decoding, dimensions, channels, corrupt files, exact/near duplicates,
   empty labels, unknown classes, normalized coordinate bounds, and polygon shape.
5. Split by source group, subject, site, session, or video before augmentation to
   prevent adjacent frames or related subjects from leaking across partitions.
6. Apply augmentation only to training data and record the transform policy,
   random seed, tool/library version, and whether labels were transformed.
7. Convert formats through one deterministic adapter and round-trip a sample.
   Preserve the canonical source annotations separately.
8. Write a manifest with hashes, counts, class distribution, split statistics,
   rejected items, schema version, and unresolved quality risks.
9. End with `vision-verifier`; do not claim dataset suitability from file counts
   alone.

## Output Contract

Produce a manifest containing:

- dataset name/version, schema, task, class map, and annotation format;
- source/provenance references without secret URLs or private paths;
- file/annotation hashes, accepted/rejected counts, and rejection reasons;
- split algorithm, group key, ratios, seed, and per-split class statistics;
- augmentation policy and tool versions;
- validation results, known bias/coverage gaps, license, and retention notes.

Dataset files, labels, and derived media stay outside Git unless the user has
explicitly approved small, licensed, non-sensitive fixtures.

## Verification

- Parse every annotation and validate class IDs, dimensions, coordinates,
  polygons, keypoints, and empty-label policy.
- Assert split disjointness by content hash and declared group key.
- Check deterministic reproduction with the same source manifest and seed.
- Round-trip a representative conversion sample and visualize a bounded,
  consented sample for human review.
- Report class imbalance, corrupt/duplicate counts, missing provenance, and
  skipped bias/quality checks separately.

## Failure Modes

- Missing rights or provenance: block ingestion and record the unresolved source.
- Corrupt media or invalid labels: quarantine with a reason; do not silently
  repair the canonical annotation.
- Leakage detected: rebuild splits from the correct group key before training.
- Cloud export includes a token/private URL: redact it, invalidate the artifact,
  and regenerate through a server-side secret boundary.
- Insufficient class coverage: report the gap rather than compensating with
  uncontrolled duplication or augmentation.

## Safety Notes

- Treat faces, bodies, homes, plates, documents, and location metadata as
  sensitive. Define redaction and deletion workflows before processing.
- Never commit datasets, labels tied to private media, credentials, generated
  download URLs, caches, or model weights.
- Hosted uploads require explicit approval, consent, data-residency review, and
  server-side secret handling.
- Do not create identity, protected-attribute, medical, or surveillance labels
  without a separately approved legal, ethical, and safety process.
