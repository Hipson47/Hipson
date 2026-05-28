---
name: hipson-subagent-orchestration
description: Use when coordinating Codex subagents for non-trivial engineering work, including parallel codebase exploration, implementation workers, independent review, QA, security checks, or automation workflows in Hipson-managed repositories.
---

# Hipson Subagent Orchestration

Use this skill when the user asks for subagents, delegation, parallel agents, independent review, or when Hipson/global instructions explicitly authorize bounded subagent use for a non-trivial engineering task.

## Core Contract

- The main Codex agent owns planning, delegation, integration, verification, and final judgment.
- Spawn subagents only for concrete work that can run independently and materially improve quality or speed.
- Prefer read-only `explorer` agents for codebase mapping, risk discovery, test-gap analysis, logs, and research.
- Use `worker` agents only when the write scope is clear, bounded, and disjoint from other work.
- Use one independent reviewer after meaningful changes when correctness, safety, or regression risk is non-trivial.
- Do not delegate tiny one-file fixes, urgent critical-path blockers, or overlapping edits.
- Treat subagent output as advice until verified against files, diffs, commands, or screenshots.
- Close completed or abandoned subagent threads after their results have been integrated.

## Quick Workflow

1. Decide what the main agent should do locally first.
2. Split only parallelizable work into bounded subtasks.
3. Give each subagent role, goal, repo path, files to inspect, constraints, write scope if any, verification expectations, and exact output format.
4. Keep `fork_context=false` by default; pass a compact task packet instead of the whole thread.
5. Continue local non-overlapping work while subagents run.
6. Wait only when the result blocks the next step.
7. Review returned evidence, integrate useful findings, run final verification locally, and report what was verified.

## Role Defaults

- `explorer`: read-heavy discovery. No edits. Must cite files, commands, or observed evidence.
- `worker`: focused implementation. Must own a disjoint write scope and report files changed plus verification.
- `default` reviewer: read-only critique. Findings first, ordered by severity, with file/line evidence where possible.

## When To Read More

Read `references/subagent-orchestration.md` when you need the decision matrix, prompt templates, fan-out patterns, model/reasoning guidance, or failure-mode checklist.
