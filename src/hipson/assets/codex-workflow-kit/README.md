# Codex Workflow Kit

Portable instructions and templates for structured Codex work across repositories.
The kit installs global operating rules, the `hipson-workflow` skill, and a
project-level `AGENTS.md` template.

## Installed Files
- `~/.codex/AGENTS.md` - global Codex behavior.
- `~/.codex/skills/hipson-workflow/SKILL.md` - the main AI-assisted workflow skill.
- `~/.codex/skills/hipson-workflow/references/` - compact workflow references.
- `~/.codex/skills/hipson-subagent-orchestration/SKILL.md` - bounded subagent delegation workflow.
- `templates/repo-AGENTS.md` - project-level rules template.

## Installation
Run from this package directory:

```bash
chmod +x install.sh
./install.sh
```

The installer:
- creates missing directories;
- backs up an existing `~/.codex/AGENTS.md`;
- backs up an existing `hipson-workflow` skill directory;
- copies the current kit files into the Codex home.

## Usage
In any repository, ask Codex to build a local work brief for non-trivial work:

```text
Run hipson work --task "implement parser fix"
```

Useful prompts:

```text
Use the Hipson SKILLS.md playbook for this task
Create an Executor prompt for this task
Review the last diff as Architect
Generate repo-specific AGENTS.md
Fold context and write the next task packet
Use hipson-workflow in EXECUTOR_MODE
```

## Two-Session Workflow
Use two separate Codex sessions in the same repository.

1. Session A: Architect, Reviewer, or Mentor.
2. Session B: Executor or Implementer.
3. Architect scans the repo and creates a small task packet.
4. Executor receives the task packet.
5. Executor makes the change and runs verification.
6. Architect reviews the resulting diff.
7. Architect accepts, requests fixes, or creates the next packet.

The git diff is the contract between sessions. Executor reports are useful, but
the diff remains the source of truth.

## Project Rules
In any project directory:

```bash
cp /path/to/Hipson/src/hipson/assets/codex-workflow-kit/templates/repo-AGENTS.md ./AGENTS.md
```

Then fill in:
- project description;
- stack;
- commands;
- important paths;
- testing, UI, and security rules.

## Typical Flow
Architect session:

```text
Use hipson-workflow in ARCHITECT_MODE. Scan this repo and create the first Executor prompt for adding [feature].
```

Executor session:

```text
Use hipson-workflow in EXECUTOR_MODE.

[paste the task packet from Architect]
```

After implementation, return to Architect:

```text
Review the last diff as Architect.
```

## Troubleshooting
- If Codex cannot find the skill, check `~/.codex/skills/hipson-workflow/SKILL.md`.
- If global rules do not load, check `~/.codex/AGENTS.md`.
- If the installer lacks permissions, run it from a directory where your user can write.
- If a project has its own `AGENTS.md`, those rules may refine the global workflow.
- If project commands are unknown, ask Architect to scan the repo and generate project rules.
