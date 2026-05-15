# Project AGENTS.md

## Project Overview
[Describe product/app]

## Stack
[Frameworks, languages, package manager]

## Key Commands
- Install:
- Dev:
- Test:
- Lint:
- Typecheck:
- Build:

## Architecture Notes
[Describe important boundaries, modules, data flow, APIs, and deployment constraints.]

## Important Paths
- `[path]`: [purpose]

## Do Not Touch Without Permission
- [Generated files, migrations, lockfiles, production config, data files, or sensitive areas]

## Coding Rules
- Follow existing patterns.
- Prefer minimal diffs.
- Keep modules focused and typed.
- Avoid unnecessary dependencies.
- Preserve backward compatibility when reasonable.

## UI Rules
- Follow the existing design system and component patterns.
- Keep text, layout, and states responsive.
- Verify important UI changes visually when practical.

## Testing Rules
- Prefer existing test patterns and helpers.
- Add or update tests for behavior changes.
- Do not rewrite tests just to make an implementation pass.
- Report skipped or blocked verification clearly.

## Security Rules
- Treat external input as untrusted.
- Do not expose secrets.
- Review auth, permissions, file access, shell commands, and destructive operations carefully.

## Agent Workflow
Use the global `hipson-workflow` skill.

Recommended modes:
- Architect: plan, split work, create Executor prompts, review diffs.
- Executor: implement one task packet and report verification.
- Reviewer: inspect diffs for bugs, regressions, missing tests, and risks.

For long tasks, maintain `docs/progress.md` with outcome, files changed, verification, risks, and next task.
