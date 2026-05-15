# Verification Checklist

## Rule
Do not claim verified unless the command or manual check was actually run. If a check is skipped, state why.

## Checks
- Existing tests: run the narrowest relevant existing tests first, then broader tests when risk justifies it.
- New tests: add or update tests when behavior changes and the project has a test pattern.
- Lint: run the project lint command when available.
- Typecheck: run the project typecheck command when available.
- Build: run the build command for changes that can affect packaging, routing, bundling, or deployment.
- Security review: inspect external input, auth, secrets, permissions, file paths, shell commands, and data deletion paths.
- Manual smoke test: exercise the changed behavior when automated coverage is insufficient.
- UI visual check: for UI changes, inspect desktop and mobile states where practical.

## Command Discovery
Look for commands in:
- `package.json`
- `Makefile`
- `justfile`
- `Taskfile.yml`
- `pyproject.toml`
- `tox.ini`
- `pytest.ini`
- CI workflows
- repo docs

Do not invent commands. If no command exists, say so.

## Verification Report Template
```markdown
## Verification
- `[command]`: passed
- `[command]`: failed, [short reason]
- `[manual check]`: passed, [what was checked]

## Not verified
- [Check]: [reason]

## Security notes
- [Relevant risk reviewed or none found]
```

## Failure Handling
- If a verification command fails because of your changes, fix the issue and rerun it.
- If it fails because of pre-existing or environment issues, report the evidence and avoid hiding the failure.
- If verification is too expensive or destructive, ask before running it.
