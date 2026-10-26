# Install Hipson on Linux, macOS or Windows

Hipson needs Python 3.11+ and Git. The commands below ask uv to install/use Python 3.12.
No provider credentials are needed for CLI, task ledger, MCP or evidence commands.

| Platform | CLI, ledger, MCP, evidence | `examples/agent-run.sh` | Agent e2e harness |
|---|---|---|---|
| Linux | Supported | Supported | Supported |
| macOS | Supported | Supported with Bash and `python3` | Linux/WSL2 only |
| Windows native | Supported, leases/explicit finish for worker recovery | Use WSL2 | Use WSL2 |
| Windows WSL2 | Supported | Supported | Supported |

## Linux

Install Git using your distribution's package manager if needed. Install uv, then the global CLI:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv tool install --python 3.12 git+https://github.com/Hipson47/Hipson
uv tool update-shell
hipson --help
hipson doctor
```

In the project you want Claude Code to manage, register the installed stdio server:

```bash
claude mcp add --transport stdio hipson -- hipson mcp serve --project "$PWD" --stdio
claude mcp get hipson
```

## macOS

Use Terminal. If `git --version` is unavailable, run `xcode-select --install` and complete the
Command Line Tools installation. Then:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv tool install --python 3.12 git+https://github.com/Hipson47/Hipson
uv tool update-shell
hipson --help
hipson doctor
claude mcp add --transport stdio hipson -- hipson mcp serve --project "$PWD" --stdio
claude mcp get hipson
```

The wrapper supports stock macOS Bash 3.2 and BSD `ps`. Ensure `python3` is on PATH
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
hipson --help
hipson doctor
claude mcp add --transport stdio hipson -- hipson mcp serve --project "$($PWD.Path)" --stdio
claude mcp get hipson
```

`hipson.exe` runs directly; it needs no `cmd /c` or Bash wrapper. Use the Windows Claude Code
installation here. The CLI and MCP wire format use UTF-8; MCP accepts CRLF requests and emits LF responses.

Native Windows supports task commands, SQLite coordination, MCP and concurrent evidence appends.
It does not support the Bash worker wrapper or the Linux agent e2e harness. For manually managed
workers, set a bounded lease and explicitly finish each assignment:

```powershell
hipson task open --project . --title "Parser check" --objective "Verify parser" --json
hipson task assign --task T-0001 --actor codex --role builder --mode rw --scope src --lease-seconds 3600
hipson task board
hipson task finish --assignment A-0001 --result passed --summary "Worker exited 0; lead verification pending"
```

Use the task/assignment IDs printed by your commands. `hipson task reap` recovers expired leases.
Windows PID probes use query-only handles (`OpenProcess` + `GetExitCodeProcess`), never signals.
They cannot check surviving POSIX worker groups. Windows boot identity is unavailable: after a reboot,
a reused live PID can keep a claim active until its lease expires or the lead explicitly finishes it.
Access denied or unknown probe errors conservatively keep claims. Leases must cover the work duration;
an expired lease can release a claim even if a worker is still running.

## Windows through WSL2 (recommended for workers)

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
hipson --help
hipson doctor
claude mcp add --transport stdio hipson -- hipson mcp serve --project "$PWD" --stdio
claude mcp get hipson
```

Install/run Claude Code, Hipson and workers in the same WSL distro. Keep active repositories under
`~/code`, rather than `/mnt/c`. WSL uses its own Linux home and ledger, separate from Windows.

## Clone instead of installing the global CLI

After the OS-specific uv setup, these commands work in Bash, zsh and PowerShell:

```text
git clone https://github.com/Hipson47/Hipson.git
cd Hipson
uv sync --python 3.12 --no-dev
uv run --no-dev hipson --help
uv run --no-dev hipson doctor
```

For Linux/macOS/WSL2, activate the environment so the wrapper can find `hipson` and `python3`:

```bash
source .venv/bin/activate
claude mcp add --transport stdio hipson -- "$PWD/.venv/bin/hipson" mcp serve --project "$PWD" --stdio
```

For native Windows, use the executable directly, with no activation script policy changes:

```powershell
.\.venv\Scripts\hipson.exe --help
claude mcp add --transport stdio hipson -- "$($PWD.Path)\.venv\Scripts\hipson.exe" mcp serve --project "$($PWD.Path)" --stdio
```

For a global install, the wrapper is available in a source clone, not installed as a CLI command.
Launch it from that clone with `bash examples/agent-run.sh --help`; `hipson` and `python3` must be on PATH.
For development, use `uv sync --all-extras` to install the test/static-analysis tools too.

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

Run `hipson --help`, `hipson doctor`, then `hipson task open`, `hipson task assign` and
`hipson task board` as in the Windows example (the same task command syntax works on every OS).
Run `claude mcp get hipson`, then `/mcp` in Claude Code and check that `task.board` is available.
Task write tools require `approved=true`.

If Claude Code cannot find the global CLI, run `uv tool update-shell`, restart its terminal,
or register an absolute executable path: `"$(command -v hipson)"` on POSIX,
`"$((Get-Command hipson).Source)"` in PowerShell. Register from the target project directory.

CI runs both test runners, static checks, builds and a real installed CLI/MCP stdio smoke on
Ubuntu (Python 3.11/3.12), macOS and Windows (Python 3.12). Windows CI runs the real process-query
regression; all platforms also test its error paths with mocked ctypes. The agent e2e harness
explicitly skips outside Linux/WSL2; Linux CI still requires zero skipped tests. Both test runners
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
