# AI Full-Stack Dev Workflow

Hipson is the local-first control plane for AI-native full-stack development. It
does not replace the coding agent. It gives the agent a bounded workflow between
the repository and optional model-backed review.

The core loop is:

```text
current diff -> work plan -> packet -> preflight -> optional sidecar -> verify -> quality report/eval -> evidence -> audit
```

## Default Flow

Use this when an AI developer or coding agent needs to move from an open diff to
an auditable handoff.

```bash
hipson contract show --json
hipson work --task "review current diff for full-stack regressions" --project . --include-diff --write-packet --packet-output runs/review-packet.md --work-output runs/work.json
hipson packet preflight runs/review-packet.md -o runs/review-packet.preflight.json --json
hipson verify run --work runs/work.json --limit 1 -o runs/verify.json --json
hipson quality report --work runs/work.json --verify runs/verify.json -o runs/quality.json --json
hipson evidence append --work runs/work.json --verification runs/verify.json --quality-report runs/quality.json
hipson audit show --work runs/work.json --json
```

This flow is provider-free by default. It does not need API keys. The output is
local evidence, not model confidence.

## Optional AI Review Pass

Sidecars are optional and advisory. Use them only after packet preflight and a
human review of packet contents.

```bash
hipson sidecar run --agent reviewer_free --packet runs/review-packet.md --model openrouter/free --dry-run
# Optional real provider call:
# hipson sidecar run --agent reviewer_free --packet runs/review-packet.md --model openrouter/free -o runs/sidecar.md
hipson quality report --work runs/work.json --verify runs/verify.json --sidecar runs/sidecar.md -o runs/quality.json --json
hipson quality eval --project . --packet runs/review-packet.md --sidecar runs/sidecar.md --verify runs/verify.json -o runs/quality-eval.json --json
hipson evidence append --work runs/work.json --verification runs/verify.json --quality-report runs/quality.json --quality-eval runs/quality-eval.json
hipson audit show --work runs/work.json --json
```

Do not treat a passed verification artifact as proof that sidecar findings are
verified. Sidecar findings remain advisory until checked against local files,
tests, and human review.

## Full-Stack Usage Pattern

For frontend work:

- Use `hipson work` to bound the task and identify changed files.
- Generate a review or executor packet with explicit scope.
- Verify with repo-native checks such as lint, typecheck, tests, build, and any
  visual QA commands available in the project.
- Treat visual or UX sidecar findings as review input, not approval.

For backend work:

- Keep API, auth, data, migration, payment, and upload changes under explicit
  human review.
- Verify with tests, type checks, migrations in dry-run mode where available,
  and security-relevant local checks.
- Record evidence before making release or handoff claims.

For cross-stack work:

- Split packets if frontend, backend, and data changes need different reviewers.
- Keep provider-backed review on bounded packets, not the whole repository.
- Use `evidence append` and `audit show` to preserve what was verified and what
  remains unknown.

## Release Semantics

Hipson separates four gates:

- `verification_gate`: local commands passed or failed.
- `sidecar_eval_gate`: optional sidecar output was evaluated for local conflicts
  or remains unverified.
- `human_decision_gate`: final human outcome is pending, passed, or blocked.
- `release_claim_gate`: release claims are blocked unless local verification,
  sidecar status, and human decision support them.

The human remains final authority for release, security, destructive actions,
and other high-risk decisions.
