# Platform compatibility

Hipson's core needs Python 3.11+ and Git. It needs no WSL, Bash, Docker or particular shell, and no particular AI
provider. Native Windows, macOS and Linux are first-class targets; WSL2 is one more Linux environment, kept
separate from Windows (its own home, ledger, logins and agent configuration).

Evidence levels: **CI** = runs in GitHub Actions on that OS for every push (both test runners plus the clean-wheel
smoke, `scripts/platform_smoke.py`); **live** = a real agent run on that OS, with the report in
[evals/](evals/); **unverified** = expected to work, not executed here.

## Core

| Capability | Linux | macOS | Windows native | Notes |
| --- | --- | --- | --- | --- |
| Install from a clean wheel, `hipson --help`, `doctor` | CI | CI | CI; also a local install in a path with a space and `ü` | `hipson.exe` console entry point on Windows |
| `hipson init` (ledger, MCP registration for Claude/Codex) | CI | CI | CI | PowerShell-quoted next steps on Windows |
| MCP stdio server | CI | CI | CI | UTF-8; CRLF requests accepted, LF replies |
| Task ledger (SQLite), concurrent writers, claims | CI | CI | CI | |
| `task run` / `verify` / `close` / `do`: containment | CI (process groups, Linux subreaper) | CI (process groups) | CI (Job Objects, guardian Job) | |
| Cancellation | CI (SIGINT/SIGTERM/SIGHUP) | CI | CI (Ctrl+C/Ctrl+Break, Job termination) | |
| Crash recovery (`task reap`) | CI (boot id, `/proc`) | CI (boot UUID) | CI (query-only PID handles, leases) | Windows has no boot id: after a reboot a reused PID keeps a claim until its lease ends |
| `task flow` with immutable review snapshots | CI (smoke) | CI (smoke) | CI (smoke) | Snapshot links need symlink privilege on Windows, otherwise the snapshot fails closed |
| Git integration (fingerprints, snapshots, CRLF) | CI | CI | CI | Snapshots copy `core.autocrlf`/`core.eol` |
| Paths with spaces and non-ASCII | CI | CI | CI | Smoke work directory `hipson smoke ü 日本 …` |
| Worker wrapper | `hipson task run` (CI); `examples/agent-run.sh` optional | same | `hipson task run` (CI); the Bash wrapper is not needed | |

## Native agent runtimes (`hipson agent runtimes` / `agent exec`)

`hipson agent runtimes` reports how each runtime on PATH would execute: `native`, `batch-shim` (a Windows
`.cmd`/`.bat` launcher) or `wsl-forwarded` (a Windows launcher that runs `wsl.exe`).

| Runtime | Linux (WSL2 here) | macOS | Windows native |
| --- | --- | --- | --- |
| Claude Code | live 2.1.319: instructions, skills, hooks, subagents, plan-mode reviewer, resume | unverified | unverified (not installed on the test machine). Native `claude.exe` expected to work; an npm `claude.cmd` is refused because its JSON-schema argument cannot pass through cmd.exe |
| Codex | live 0.169.0: all probes, including resume with `sandbox_mode` | unverified | **live 0.157.1** (`codex.exe`): builder + check, instructions, skill, read-only reviewer, session resume, Hipson = manual ([report](evals/native-probe-2026-11-19-codex-windows.json)). That CLI rejects `gpt-6.2-sol` and `gpt-6.1-sol` ("not supported", a CLI version issue); `gpt-6-sol` worked. npm `codex.cmd` runs through `batch_command` |
| Antigravity | live 1.5.12 (plan mode is not read-only) | unverified | not installed natively here; the only `agy` on PATH is a WSL-forwarding launcher, which `agent exec` refuses: run Hipson inside WSL for it, or install a native Windows build |

Windows and WSL stay apart: a WSL-forwarding launcher runs the WSL installation with WSL's login, configuration
and paths, so Hipson never runs it as a Windows worker and never translates a Windows workspace into WSL.

## Scoped to Linux/WSL2 (deep evaluation, not core)

| Tool | Why | Portable replacement |
| --- | --- | --- |
| `scripts/agent_e2e.py` (agent e2e harness) | `prctl` subreaper, `/proc` zombie inspection, `fcntl`, Linux process groups | none yet; the clean-wheel smoke covers the user-facing lifecycle on every OS |
| `scripts/flow_compare.py` (offline and `--live`) | builds fixtures with the e2e harness | none yet |
| `scripts/native_probe.py` Claude hook probe | the probe's hook uses descriptor-relative no-follow opens | the Codex and Antigravity probes run on Windows |
| Mutation gates | CI runs them on Linux 3.12 only | not needed per OS: they test pure ledger logic |

## Known platform limits

- Windows: no boot identity (leases bound recovery after a reboot); symlinks need Developer Mode or the
  privilege; `task open --check 'TEXT'` parses POSIX-style, so give Windows paths with forward slashes or use
  `-- COMMAND ARGS` forms; the default config home is `~/.config/hipson` on every OS (`HIPSON_HOME` overrides).
- macOS: descendant tracking after a process leaves its group is best effort.
- Windows `python3` is often the Microsoft Store alias, which runs no Python: write checks with `python` there.
