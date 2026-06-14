# Hipson AI Quality Layer

Hipson should use AI as a quality multiplier, not as hidden automation. The core
loop remains local and auditable:

```text
route -> scan -> packet -> packet preflight -> AI quality pass -> verify -> quality report -> eval -> memory/handoff
```

The AI quality pass is optional and explicit. Hipson prepares bounded packet
context, chooses or accepts a model, shows a dry-run preview, and treats provider
output as advisory.

## Product Goal

Hipson should make AI-assisted work better than ordinary dev tooling by adding:

- model-aware second opinions;
- task-fit sidecar selection;
- bounded and redacted context;
- explicit free-model experimentation;
- verification and memory discipline around every AI output.
- finding-level adjudication for sidecar claims;
- local evals for hallucinated files, mismatched commands, and missing evidence.

This is different from a standard CLI because Hipson is not just running local
commands. It is shaping how humans and agents collaborate on code quality.

## User Control

Users can keep the default provider-free workflow:

```bash
hipson work --task "review current diff for test gaps"
```

Users can opt into the free OpenRouter lane:

```bash
hipson work --task "review current diff for test gaps" --free-ai
```

Users can choose a specific model:

```bash
hipson work --task "review release risk" --ai-model openrouter/free
hipson sidecar run --agent reviewer_cheap --packet runs/review-packet.md --model openrouter/free
```

After a sidecar run, users can keep adjudication local:

```bash
hipson quality report --work runs/work.json --verify runs/verify.json --sidecar runs/sidecar.md
hipson quality eval --packet runs/review-packet.md --sidecar runs/sidecar.md --verify runs/verify.json
```

## Free Model Lane

Free models are useful for low-stakes quality passes:

- test gap brainstorming;
- packet clarity critique;
- first-pass implementation review;
- documentation review;
- summary and handoff drafts.

They should not be used for:

- sensitive customer context;
- secrets or broad logs;
- final security approval;
- release signoff;
- live-provider readiness claims;
- decisions that override local tests or human review.

## Trust Contract

Every AI quality pass must preserve these rules:

- no provider call happens unless the user runs the sidecar command;
- the dry-run preview comes first;
- packets are bounded and redacted;
- free/model-selected output is advisory;
- local diff, tests, and human review remain authoritative;
- any durable memory or handoff must summarize verified outcomes, not raw model
  confidence.
- finding IDs, sidecar model metadata, and provider output excerpts stay
  separated from local verification evidence.

## Roadmap

See `docs/AI_DEV_FIRST_EXPANSION_PLAN.md` for the broader AI-dev-first product
roadmap that connects this quality layer to model profiles, subagent curation,
verification evidence, audit export, and workflow packs.

Near-term:

- keep `hipson work --free-ai` and `--ai-model` as explicit opt-ins;
- add curated free sidecars such as `reviewer_free` and `coder_review_free`;
- document when free models are appropriate;
- keep free models excluded from default routing.
- use `hipson provider doctor` to check provider and agent readiness without
  sending repository data.

Next:

- add model profile presets for quality, cost, speed, long-context, UI review,
  and security review;
- keep improving `hipson quality report` finding adjudication;
- expand `hipson quality eval` into fixture-backed golden packet evals.
