# Install Hipson on Linux, macOS or Windows

Hipson needs Python 3.11+ and Git. The commands below ask uv to install/use Python 3.12.
No provider credentials are needed for CLI, task ledger, MCP or evidence commands.

| Platform | CLI, ledger, MCP, evidence, `hipson task run` | `examples/agent-run.sh` | Agent e2e harness |
|---|---|---|---|
| Linux | Supported | Supported | Supported |
| macOS | Supported | Supported with Bash and `python3` | Linux/WSL2 only |
| Windows native | Supported (Job Object worker containment) | Use WSL2 | Use WSL2 |
| Windows WSL2 | Supported | Supported | Supported |

## Linux

Install Git using your distribution's package manager if needed. Install uv, then the global CLI:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv tool install --python 3.12 git+https://github.com/Hipson47/Hipson
uv tool update-shell
cd /path/to/your/project
hipson init
```

## macOS

Use Terminal. If `git --version` is unavailable, run `xcode-select --install` and complete the
Command Line Tools installation. Then:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv tool install --python 3.12 git+https://github.com/Hipson47/Hipson
uv tool update-shell
cd /path/to/your/project
hipson init
```

`hipson task run` needs no Bash. The optional Bash wrapper supports stock macOS Bash 3.2 and BSD `ps`.
For that wrapper, ensure `python3` is on PATH
(`uv python install --default 3.12` and `uv python update-shell` if needed; reopen Terminal).
It uses Python's `os.setsid()` without a `setsid` executable and falls back to `uuidgen` or Python
without `/proc`. Boot identity comes from `sysctl -n kern.bootsessionuuid`.

## Windows native (PowerShell)

Install [Git for Windows](https://git-scm.com/downloads/win). Install uv:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Reopen PowerShell so the installer PATH change takes effect, then:

```powershell
uv tool install --python 3.12 git+https://github.com/Hipson47/Hipson
uv tool update-shell
$env:Path = "$(uv tool dir --bin);$env:Path"
Set-Location C:\path\to\your\project
hipson init
```

`hipson.exe` runs directly; it needs no `cmd /c` or Bash wrapper. Use the Windows Claude Code
installation here. The CLI and MCP wire format use UTF-8; MCP accepts CRLF requests and emits LF responses.

Native Windows supports the worker runner, task commands, SQLite coordination, MCP and concurrent
evidence appends. Run and record a worker without Bash:

```powershell
hipson task open --project . --title "Parser check" --objective "Verify parser" --json
hipson task run --task T-0001 --actor codex --role builder --mode rw --scope src --lease-seconds 3600 -- python -c "print('worker check')"
hipson task board
```

Before creating a worker, the runner joins a guardian Job Object. The child inherits its
membership during process creation, so runner death also cleans up a suspended child still
in startup. A nested worker Job supports cleanup before finish; the guardian handle remains
open until interpreter exit. `KILL_ON_JOB_CLOSE` terminates the tree if the runner is killed.
Ctrl+C and Ctrl+Break forward Ctrl+Break to the worker's process group, followed by bounded
Job termination when needed. Leftovers stop before the runner records finish and releases claims.
The Linux agent e2e harness still needs WSL2.

Use the task/assignment IDs printed by your commands. `hipson task reap` recovers expired leases.
Windows PID probes use query-only handles (`OpenProcess` + `GetExitCodeProcess`), never signals.
They do not probe POSIX worker groups; `task run` contains Windows descendants with its Job Object.
Windows boot identity is unavailable: after a reboot,
a reused live PID can keep a claim active until its lease expires or the lead explicitly finishes it.
Access denied or unknown probe errors conservatively keep claims. Leases must cover the work duration;
an expired lease can release a claim even if a worker is still running.

## Windows through WSL2

In an administrator PowerShell:

```powershell
wsl --install -d Ubuntu
```

Restart if requested and finish Ubuntu's first-run setup. In Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y git curl
mkdir -p ~/code
cd ~/code
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv tool install --python 3.12 git+https://github.com/Hipson47/Hipson
uv tool update-shell
cd ~/code/your-project
hipson init
```

Install/run Claude Code, Hipson and workers in the same WSL distro. Keep active repositories under
`~/code`, rather than `/mnt/c`. WSL uses its own Linux home and ledger, separate from Windows.

## Clone instead of installing the global CLI

After the OS-specific uv setup, these commands work in Bash, zsh and PowerShell:

```text
git clone https://github.com/Hipson47/Hipson.git
cd Hipson
uv sync --python 3.12 --no-dev
uv run --no-dev hipson init --project /path/to/your/project
```

For Linux/macOS/WSL2, activate the environment so the wrapper can find `hipson` and `python3`:

```bash
source .venv/bin/activate
hipson init --project /path/to/your/project
```

For native Windows, use the executable directly, with no activation script policy changes:

```powershell
.\.venv\Scripts\hipson.exe init --project C:\path\to\your\project
```

`hipson task run --help` is available in global installs and clones on every OS.
The optional legacy Bash wrapper is available in a source clone. Launch it with
`bash examples/agent-run.sh --help`; `hipson` and `python3` must be on PATH.
For development, use `uv sync --all-extras` to install the test/static-analysis tools too.

## One-command lead setup

```text
hipson init [--project PATH] [--client auto|claude|codex|none] [--scope project|user] [--dry-run] [--yes] [--json]
```

Run from your target project, or pass `--project`. Setup checks Python, Git, the Hipson version,
platform support and the configured ledger location. It creates or migrates the ledger and registers
the absolute installed Hipson executable and project path. No provider credentials are required.

- `--client auto` (default) selects both Claude Code and Codex when their CLIs are on PATH.
  With neither available it fails without applying changes. `--client codex` can configure Codex
  even without its CLI; `--client none` sets up only the ledger.
- Claude discovery reads `<project>/.mcp.json` and the user/local scopes in `.claude.json` directly.
  Planning never runs `claude mcp get` or `list`, which can connect to existing servers.
  After confirmation, registration uses `claude mcp add --transport stdio --scope ...`.
  Default project scope writes `<project>/.mcp.json`; `--scope user` writes
  `~/.claude.json` (or `$CLAUDE_CONFIG_DIR/.claude.json`). Windows `claude.cmd` is supported.
  See [Claude Code MCP scopes](https://code.claude.com/docs/en/mcp#mcp-installation-scopes).
- Codex always uses user config: `$CODEX_HOME/config.toml`, default `~/.codex/config.toml`.
  Setup appends `[mcp_servers.hipson]` while preserving every existing byte. `--scope` controls
  Claude only. See [Codex MCP configuration](https://developers.openai.com/codex/mcp/).
  Setup also inspects `.codex/config.toml` layers from the repository root to the target project,
  including currently absent files; incompatible effective project overrides stop setup.
- An existing `hipson` registration reports **already configured** only when its scope, resolved
  executable, arguments (including the resolved `--project` path), and enabled stdio transport match
  the requested setup. A different command, project, extra argument or disabled entry is a **conflict**.
  Environment overrides must equal the current setup environment; a different ledger or other changed
  variable is a conflict. Codex tool filters must leave `task.open` and `task.board` available.
  If a Claude user entry conflicts with the default project scope, setup plans a project-scoped
  override and preserves the user entry. This still requires confirmation and a backup before edits.
  Claude local entries take precedence over project entries; project entries take precedence over user
  entries. A conflicting entry at the target scope or a higher scope stops setup before any writes.
  Remove or rename that entry explicitly and rerun, or select its scope when it already has the desired
  command and project. Codex conflicts also stop setup; use a separate `CODEX_HOME` for another project
  or explicitly remove/rename its conflicting table. `--yes` never authorizes replacing a conflict.
  A project-scoped Claude entry may need approval on the client's first use.
- Before config edits, setup creates an exclusive `*.backup-YYYYMMDD-HHMMSS` backup, adding `-N`
  on collision. Ledger migrations also get a SQLite snapshot backup, including committed WAL data.
  Backups are private from creation (0600 on POSIX); incomplete copies are removed.
  Cross-process OS locks in `~/.hipson/init-locks` serialize validation, backup and edits. Lock files
  remain for reuse; the OS releases the locks on process exit. Config identity and content are
  rechecked against the plan, including after backup. Codex writes use a private staging file beside
  the config and atomic replacement, preserving its mode and existing bytes/line endings. Resulting
  configs are parsed and validated; a failing action is restored from its backup (or a new file removed).
  An unrelated concurrent edit is retained and reported for manual recovery. Invalid/incompatible
  configs, changed discovery sources and backup errors stop setup with a nonzero exit.

`hipson init --dry-run` prints the planned commands, their working directory, backup policy and exact
UTF-8 TOML addition. It performs read-only discovery; it creates no ledger, config or backups and does
not start MCP verification. Non-interactive setup without `--yes` prints the plan and exits nonzero
before applying changes. On a TTY, only `y`/`yes` confirms; `--json` keeps prompts on stderr.
Already configured setups need no further consent when no edits are planned.

For unattended setup: `hipson init --yes --json`. The JSON report contains checks, planned/applied/skipped
actions (or a conflict/failed action), backup paths, verification and next steps. Setup is resumable: if an action
fails, earlier successful actions remain recorded in the report and a rerun skips matching registrations.
After applying setup, Hipson verifies each effective registration's command, arguments, environment
and tool filters with an MCP initialize + tools/list handshake. Client commands and verification run
in POSIX process groups or Windows Jobs; timeout/interruption stops their descendants, including `.cmd` launchers.
This verifies the local server; the client's trust settings may still require approval.

Restart the lead client to load its MCP config, then open your first real task:

```text
hipson task open --project . --title "My first task" --objective "Describe the change" --json
hipson task run --task T-0001 --actor worker --role builder --mode rw --scope . -- python -c "print('worker check')"
hipson task board
```

Use the returned task id in place of `T-0001`. The setup report prints these next steps using your project
path; setup itself does not add a placeholder task. `hipson init` replaces the old progress-template command;
existing progress files are untouched.

## Storage and recovery

The task ledger is local to the user, shared across repositories:

| Setting | Linux, macOS, WSL2 | Native Windows |
|---|---|---|
| Default config | `~/.config/hipson` | `%USERPROFILE%\.config\hipson` |
| Default ledger | `~/.config/hipson/ledger.sqlite` | `%USERPROFILE%\.config\hipson\ledger.sqlite` |
| Override config | `HIPSON_HOME`, then `XDG_CONFIG_HOME/hipson` | Same variables, native paths |
| Override ledger | `HIPSON_LEDGER` | `HIPSON_LEDGER` |

For example: `export HIPSON_LEDGER="$PWD/ledger.sqlite"` (Bash/zsh), or
`$env:HIPSON_LEDGER = "$($PWD.Path)\ledger.sqlite"` (PowerShell).
Core config files and artifacts use UTF-8. Evidence defaults to the project's `runs/evidence.jsonl`;
`--ledger-dir` overrides its directory. Task ledger and evidence are separate stores.
Evidence appends use POSIX `flock`, or an atomic directory lock on Windows. A crashed Windows evidence
writer may leave `.evidence.lock.d`; appends time out with its path. Remove a stale lock only after
confirming no writer is active.

Linux recovery checks boot and PID-namespace identities; macOS checks boot-session identity.
PID namespaces are Linux-only. See [TASK_LEDGER.md](TASK_LEDGER.md) for the owner and lease contract.

## Verify the installation

Run `hipson init --dry-run`, then `hipson init` and check that MCP verification passed.
Run `hipson doctor`, then open your first task as above (the syntax works on every OS).
For Claude Code, run `claude mcp get hipson`, then `/mcp` and check that `task.board` is available.
For Codex, use `codex mcp list` after restarting the client.
Task write tools require `approved=true`.

If Claude Code cannot find the global CLI, run `uv tool update-shell`, restart its terminal,
or rerun `hipson init --dry-run` to inspect the absolute executable path it will register.

CI runs both test runners, static checks, builds and a real installed CLI/MCP stdio smoke on
Ubuntu (Python 3.11/3.12), macOS and Windows (Python 3.12). Windows CI runs the real process-query
regression and native Job Object tree cleanup plus Ctrl+C/Ctrl+Break forwarding. All platforms
run the native runner contract tests; platform APIs also have mocked error-path coverage.
The agent e2e harness explicitly skips outside Linux/WSL2; Linux CI still requires zero skipped tests. Both test runners
fail macOS/Windows CI on any skip outside the exact reason allowlist in `tests/support.py`:

- macOS and Windows: Linux-only e2e, subreaper/zombie inspection and process-group recovery controls.
- Windows only: Bash/POSIX worker wrapper, directory-mode permission controls, and symlink creation
  when Developer Mode or the symlink privilege is unavailable.

Missing test tools or other unexpected prerequisites fail CI on every supported OS. Windows
read-only WAL tests emulate ACL denial where needed: `chmod` cannot deny directory creation, and
SQLite's [Win32 shared-memory open](https://github.com/sqlite/sqlite/blob/version-3.50.4/src/os_win.c#L4004)
uses `OPEN_ALWAYS` even with `readonly_shm=1`. POSIX uses native directory permissions; both retain
assertions that denied reads create no files and preserve database/WAL bytes.
Actual native execution results come from those CI jobs; a Linux run alone cannot verify macOS or Windows.

Install commands follow the official [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/)
and [tool guide](https://docs.astral.sh/uv/guides/tools/). MCP registration follows
[Claude Code's stdio guide](https://code.claude.com/docs/en/mcp#option-3-add-a-local-stdio-server).
