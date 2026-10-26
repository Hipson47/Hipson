# Testing Hipson on a Mac

Thanks for testing. This takes about 5 minutes and changes nothing outside one folder and the uv tool cache.

## 1. Prerequisites (once)

Open **Terminal** and check Git:

```bash
git --version
```

If macOS offers to install the Command Line Tools, accept and wait for it to finish (or run
`xcode-select --install`). Then install [uv](https://docs.astral.sh/uv/), which also provides Python:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Close and reopen Terminal so `uv` is on your PATH, then check `uv --version`.

## 2. Run the smoke test

```bash
git clone https://github.com/Hipson47/Hipson.git
cd Hipson
bash scripts/platform_smoke.sh
```

The script installs Hipson into `Hipson/.venv`, uses a throwaway task ledger in a temporary folder
and checks, in order:

- the CLI starts and `hipson doctor` passes;
- the macOS boot identity used for crash recovery is readable;
- the README quick start (open, assign, board, finish, review, done) and `hipson task brief`;
- the worker wrapper: a second writer on a busy path is refused with exit code 75, a live worker
  survives `hipson task reap`, and a killed worker's claim is released;
- the MCP server answers `initialize`, `tools/list` and a read tool over stdio;
- both test suites (`scripts/run_tests.py` and `pytest`), about 2-4 minutes.

It ends with `RESULT: N passed, M failed`.

## 3. Send back the report

Send the file `Hipson/platform-smoke-report.txt` (it contains the macOS, Python, bash and uv
versions plus the result of each check, no personal data beyond your macOS version).
If something failed, the report includes the last lines of output for that check.

## 4. Optional: try it as a real tool

```bash
uv tool install --python 3.12 git+https://github.com/Hipson47/Hipson
uv tool update-shell   # then reopen Terminal
hipson --help
```

With [Claude Code](https://docs.claude.com/en/docs/claude-code) installed, register the MCP server in
any project folder:

```bash
claude mcp add --transport stdio hipson -- hipson mcp serve --project "$PWD" --stdio
claude mcp get hipson
```

## Cleanup

```bash
rm -rf Hipson                         # the clone and its .venv
uv tool uninstall hipson              # only if you did step 4
```
