# Two-Codex Workflow

## Roles

### Codex A: Architect
Architect owns context, task design, review, and final judgment.

Responsibilities:
- Scan the repo and identify conventions.
- Split work into small task packets.
- Define files to inspect and files allowed to edit.
- Review Executor diffs.
- Decide whether to accept, request fixes, or create the next task.

### Codex B: Executor
Executor owns implementation for one task packet.

Responsibilities:
- Inspect the requested files.
- Implement the smallest correct diff.
- Run verification commands.
- Report changes, reasoning, verification, and risks.

## Architect Task Packet
The Architect prepares a prompt with:
- Role
- Goal
- Context
- Files to inspect
- Files allowed to edit
- Constraints
- Acceptance criteria
- Verification commands
- Output format

Keep the packet independent enough that Executor does not need the full Architect chat.

## Executor Implementation
Executor should:
- Read before editing.
- Stay inside the allowed edit surface.
- Keep changes focused.
- Avoid unrelated refactors.
- Run the requested verification.
- Report exact commands and outcomes.

## Executor Report Back
Executor reports:
- What changed.
- Why it changed.
- Verification commands run and results.
- Remaining risks or blocked checks.
- Files changed.

## Architect Review
Architect reviews:
- `git diff`
- Executor report
- Test changes
- Behavior against acceptance criteria
- Security, data-loss, and compatibility risks

Architect then decides:
- Accept the diff.
- Request a fix with a smaller follow-up packet.
- Create the next implementation packet.

## Avoiding Context Pollution
- Do not paste unrelated chat history into Executor prompts.
- Prefer task packets over broad summaries.
- Keep old experiments, rejected ideas, and noisy logs out of active context.
- Use a context fold after each loop.

## Git Diff As Contract
Use `git diff` as the source of truth between sessions.

Recommended loop:
1. Architect writes task packet.
2. Executor implements.
3. Executor reports.
4. Architect inspects `git diff`.
5. Architect accepts, requests fixes, or creates the next task.

The report is helpful, but the diff is authoritative.

## Maintaining Progress Across Sessions
For long-running work, maintain `docs/progress.md` with:
- Goal
- Current status
- Decisions
- Completed work
- Verification
- Risks
- Next task packet

Keep it short enough that a fresh Codex session can quickly resume.

## Example Full Loop
1. User starts Codex A and says: "Use hipson-workflow in ARCHITECT_MODE. Scan this repo and create the first Executor prompt for adding password reset."
2. Architect scans package files, routes, auth modules, tests, and docs.
3. Architect writes a task packet for the first small step, such as adding a password reset request endpoint.
4. User opens Codex B in the same repo and pastes the task packet.
5. Executor inspects the allowed files, edits code, adds or updates tests if requested, and runs verification.
6. Executor reports changed files, commands run, failures if any, and remaining risks.
7. User returns to Codex A and says: "Review the last diff as Architect."
8. Architect runs or inspects `git diff`, checks the report against acceptance criteria, and reviews risks.
9. Architect either accepts the change, writes a fix prompt for Executor, or creates the next task packet.
