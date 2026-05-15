# AI Coding Workflow

## Spec-Driven Development
- Start with observable behavior, not implementation guesses.
- Convert user intent into acceptance criteria before editing.
- Identify inputs, outputs, errors, persistence, UI states, and compatibility concerns.
- Keep the spec small enough that one Executor can finish and verify it.

## Delegate -> Review -> Own
- Delegate bounded implementation work with a clear task packet.
- Review the resulting diff, not only the report.
- Own the final decision as Architect. If the diff is risky, request changes or split the next task smaller.

## TDD-AI Protocol
1. Human criteria: capture the behavior the user wants.
2. AI tests from criteria: create tests that express the criteria when the task calls for tests.
3. Implementation: make the smallest production change that satisfies the tests and criteria.
4. Verification: run the relevant test command and report the exact result.

## Implementation Without Changing Tests
- If tests already describe the desired behavior, do not edit them during implementation.
- If tests fail because they reveal real outdated expectations, stop and explain before rewriting them.
- If the task explicitly includes test updates, keep test changes separate and easy to review.

## Minimal Diff Discipline
- Edit only files required for the task.
- Preserve public APIs unless the task requires a contract change.
- Avoid formatting churn, import reshuffling, and unrelated cleanup.
- Prefer small named helpers over broad rewrites.

## Avoiding Self-Confirming Tests
- Tests should fail against the old behavior for the right reason.
- Do not write tests that mirror implementation internals.
- Prefer user-visible behavior, API contracts, data boundaries, and regression cases.
- Include at least one negative or edge case when risk justifies it.

## Handling Long Tasks
- Split work into task packets that each produce a reviewable diff.
- Keep a running context summary instead of relying on chat history.
- Record decisions when they affect architecture, data, security, or compatibility.

## Updating `docs/progress.md`
Use `docs/progress.md` for multi-step work when the repo has docs or the user wants durable handoff.

Recommended sections:
- Goal
- Current status
- Decisions
- Completed tasks
- Verification
- Risks
- Next task packet

Keep it concise. Update it after meaningful progress, not after every command.
