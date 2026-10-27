# Hipson 1.5.0

Hipson 1.5.0 closes the gap that published evidence ranks first for lead/worker coding agents:
**false completion**, where work is accepted on a worker's word. A task now reaches `review` or
`done` only with a fresh, passing verification that the lead ran on the current state of the
project. See [docs/ROADMAP.md](ROADMAP.md) for the research behind the order.

Status: **beta**.

## Highlights

- **`hipson task verify --task T -- CMD`** (MCP `task.verify`): the lead runs a check in the task's
  project. Hipson records the command, exit code, duration, HEAD revision and a content-based
  working-tree fingerprint, plus a bounded, redacted output tail. It runs without a shell, with a
  timeout and process containment, and returns the command's exit code.
- **Fresh-verification rule**: `review` and `done` require a passing verification recorded by the
  task's lead after the latest finished builder result, still matching HEAD and the working-tree
  content. Missing, failing or stale evidence is refused with the exact reason (for example which
  file changed); `--force --reason` remains the audited override.
- **Fingerprint semantics**: content of tracked files (eol-normalized like git, so clean CRLF
  checkouts on Windows stay clean) plus untracked non-ignored files. Git-ignored paths and known
  tool caches (`__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.coverage`, …) are
  excluded; staging alone does not change it; a commit does. No repository-configured helper
  (hooks, filters, pagers, fsmonitor) runs while it is computed.
- **Lead-only evidence**: verification is refused inside a worker context (`HIPSON_ROLE=worker` or
  `HIPSON_ASSIGNMENT`), and a fingerprint that had to fall back to file metadata (very large files)
  is recorded as partial and never satisfies the rule without an override.

## Measured

Three new agent e2e scenarios target false completion: a worker that exits 0 while tests fail
(`false-green`), a worker that edits the tests (`altered-tests`) and a verification made stale by a
later change (`stale-verify`). With a real Codex lead (gpt-6.1-sol) the full suite of nine scenarios
passed 9/9 in one run, and the three new ones 6/6 over two runs. Small samples: a smoke signal, not
a benchmark.

## Upgrade notes

- Ledger schema v6; tasks already in `review` or `done` keep their status.
- Closing a task now needs `hipson task verify` first. The README quick start shows the step
  (`hipson task verify --task T-0001 -- git diff --check`).
