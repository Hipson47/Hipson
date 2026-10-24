# Next Decision Brief

## Context

Hipson is functional as a local-first CLI and runtime preview in `/home/hipson47/code/Hipson`. The current codebase has strong local verification results, but it should not yet be marketed as broadly production-stable or fully Hermes-style real-agent complete.

## Option A: Polish And Release Local-First CLI

### Pros

- Builds on verified working behavior: scan, packets, local router, read-only tool run, sessions, learning proposals.
- Fastest path to a useful public release.
- Avoids blocking on live provider work.

### Cons

- Requires README/package metadata repositioning before release.
- Mutation survivor triage remains a credibility gap.
- Users may still expect a real model-backed agent unless messaging is very clear.

### Engineering Focus

Small to medium.

- Tighten README and package classifiers.
- Add release checklist.
- Run full CI.
- Document known limits.

## Option B: Fix Packaging / Tests / Provider Validation First

### Pros

- Best balance of credibility and speed.
- Directly addresses P1 release blockers.
- Improves confidence before any public stable claim.

### Cons

- Slower than a docs-only polish pass.
- Requires focused mutation work and provider smoke design.
- May uncover additional safety bugs.

### Engineering Focus

Medium.

- Reclassify maturity.
- Triage high-risk mutmut survivors.
- Add missing security/fault-injection tests.
- Add manual live-provider smoke checklist.
- Record a clean CI run.

## Option C: Reposition As Experimental Agent Toolkit

### Pros

- Very honest about current state.
- Avoids overpromising real-agent readiness.
- Lets advanced users evaluate the runtime safely.

### Cons

- Less attractive as a polished release.
- May understate the local CLI capabilities that already work.
- Could slow adoption if messaging is too cautious.

### Engineering Focus

Small.

- Rewrite positioning.
- Keep current code.
- Publish as experimental preview only after CI.

## Option D: Pause And Refactor Architecture

### Pros

- Opportunity to simplify provider/runtime/scheduler boundaries before expansion.
- Can reduce future integration debt.

### Cons

- Not justified by current local verification results.
- Delays a useful local-first release.
- High risk of broad rewrites without immediate product value.

### Engineering Focus

Large.

- Architecture review.
- Boundary redesign.
- Migration plan and extensive regression tests.

## Recommended Choice

Choose **Option B: Fix Packaging / Tests / Provider Validation First**.

Hipson is close enough to a credible local-first release that a broad refactor is not the right next move. The next package should close the release truthfulness gap: adjust maturity claims, triage high-risk mutation survivors, and add a controlled provider validation story that does not require live network calls in tests.

## Suggested Next Package

Title: `release: harden local-first release claims and mutation gates`

Acceptance criteria:

- `pyproject.toml` maturity classifier matches actual readiness.
- README says local-first runtime preview unless all release gates pass.
- High-risk mutmut survivors in approvals, sandbox, prompt, redaction, registry, runtime, providers, and learning are triaged.
- New tests cover any high-risk survivor that can affect approval, path safety, redaction, bounded output, prompt isolation, provider errors, or tool execution.
- Live provider smoke is documented as optional/manual and never part of unit tests.
- CI passes and result is recorded.

