# Stabilization review of 1.6.0

Four independent read-only reviews (Codex gpt-6.1-sol, xhigh) of tag v1.6.0 (14856f9), recorded on ledger task
T-0025, each scoped to one area: **A** ledger and lifecycle, **B** process supervision, platforms, setup and
release, **C** security and trust boundaries, **D** workflow layer, CLI, tests and docs. The lead reproduced or
derived each finding from code before fixing it; every fix has a regression test (most in
`tests/test_stabilization.py`) that fails on the old code. Reviewer severities are kept; several "blocking"
ratings in C and D concern optional workflow features rather than the ledger.

Status: **fixed** (with test), **mitigated** (partial fix, follow-up named), **open** (not yet changed).

## A. Ledger and lifecycle (5 major, all fixed)

| ID | Finding | Status |
|---|---|---|
| A1 | Worker journal `git status`/`diff` could run repository clean/process filters after cleanup | fixed: filter drivers disabled, hooks off |
| A2 | `close --to review` did not recheck active assignments inside its transaction | fixed |
| A3 | A free-form `task event --type status` could forge the closure boundary and hide a late delivery | fixed: lifecycle event types are reserved; `decision` events show in `lead resume` |
| A4 | A lone surrogate in a worker result crashed the MCP server after finish committed | fixed: refused at ingestion, MCP output escapes as a fallback |
| A5 | Verification file inventories stored secret-shaped file names unredacted | fixed: redacted display paths, raw-path hash keeps freshness identity |

## B. Process supervision, platforms, setup and release

| ID | Finding | Status |
|---|---|---|
| B1 | `init`: an editor saving in the instant before atomic replace can be overwritten (blocking per review) | open: narrow window; rename-aside compare planned |
| B2 | Windows `.cmd`/`.bat` workers and checks reparsed `&`, `%`, `^` in arguments (blocking) | fixed: shared env-variable batch transport; also makes `npm`/`npx` checks work |
| B3 | Ctrl+C during `task verify`/`close`/`do` could orphan the running check | fixed: `supervise()` forwards signals and waits for cleanup |
| B4 | Claude registration does not record a `HIPSON_LEDGER`/`HIPSON_HOME`/`XDG_CONFIG_HOME` override | mitigated: init warns; recording the environment is a follow-up |
| B5 | Release could publish without CI passing on the tagged commit | fixed: release runs the CI matrix on the tag first |
| B6 | Manual release accepted a branch name as the tag input | fixed: must run from `refs/tags/<tag>` |
| B7 | Non-UTF-8 stderr from a successful client failed `init` | fixed |
| B8 | `init` next steps omitted `task ack` | fixed |

## C. Security and trust boundaries

| ID | Finding | Status |
|---|---|---|
| C1 | Read-only Git calls (scan, work, contract) could run filter drivers | fixed for scan/read paths (`prepare_read_only_command(cwd=...)`) |
| C2 | Workflow MCP tools read any path, e.g. `quality.report` sidecar `.env` | fixed: paths contained to the served project or the work plan's project; sensitive files refused |
| C3 | Skill lookup and scans followed symlinks out of the root | fixed |
| C4 | Sensitive tracked-file diff bodies stayed in scans/packets (= D02) | fixed: whole per-file sections omitted |
| C5 | Truncation before redaction could expose private-key bodies | fixed: redact before truncate, bounded reads |
| C6 | Provider redirects resent the Authorization header to another host or HTTP | fixed: provider HTTP never follows redirects |
| C7 | Stale quality artifacts can override failed verification and human gates (= D01) | open: see decision below |
| C8 | Verification commands inherited `GIT_DIR`/`GIT_WORK_TREE` and could check another tree | fixed: no inherited `GIT_*` for checks; workers lose repository-location variables |
| C9 | Session persistence skipped structured credential redaction | fixed |
| C10 | Agent installers can overwrite a concurrent user edit | open: reuse init's snapshot/lock/atomic replace |
| C11 | Malformed workflow MCP arguments terminated the server | fixed: errors stay per request (full schema validation for workflow tools is open) |
| C12 | Packet preflight read files unbounded, including FIFOs | fixed |
| C13 | Skill content could close its `untrusted_data` block | fixed for skills; sidecar `</packet>` framing open |

## D. Workflow layer, CLI, tests and docs

| ID | Finding | Status |
|---|---|---|
| D01 | Old quality gates override failed verification and a rejected decision; `release claim` can allow | open: decision below |
| D02 | Sensitive diff bodies in scans (= C4) | fixed |
| D03 | `route` recommendations allowed `$(...)` expansion | fixed: `shlex.quote` |
| D04 | `autopilot resume` skips project provider policy | open: decision below |
| D05 | Review kits continue after failed packet preflight | open |
| D06 | Legacy `verify run` timeouts leave descendants and drop output | open |
| D07 | Full/release verification profiles start `dev` servers | open |
| D08, D09 | Provider chat loses tool output/history; reports success after provider failure | open: decision below |
| D10 | `work --project P` records paths relative to the wrong directory | open |
| D11 | false-green e2e oracle accepted an unrelated failing command | fixed: exact command after delivery, plus a negative control |
| D12, D13 | Scheduler has no atomic job claim and binds no workspace | open: decision below |
| D14 | Untracked scan content had no aggregate bound or link-target check | fixed |
| D15-D23 | Minor CLI/session/learn/hermes/eval/diagnostic issues (read-only session open migrates, learn drops recent turns, unbounded message bodies, quoted verify args refused, resume ignores new decision, mixed JSON output, silent packet errors, hidden/absolute paths in evals, vacuous readiness) | open |
| D24 | Tests depended on the developer's PATH and Git config | fixed for fixture Git and the review-kit test |
| D25 | Init concurrency test can miss its race | open |

## Decision needed: the optional workflow layer

The ledger (`task`, `lead`, MCP `task.*`), `init` and verification are the product today. Reviewer D
recommends keeping and fixing the local primitives (`work`, `route`, `scan`, `packet`, `verify run`,
`evidence`/`audit`, `kit review`, `run`, `memory`, `skill`, `doctor`, `policy`, `sidecar`/`provider` as
explicit advisory integrations) and hiding or deprecating the experimental surfaces whose open findings above
are functional: `autopilot` (overlaps `kit review`, launches no worker), `release claim` (D01/C7),
`scheduler` (D12/D13), provider `chat`/runtime/gateway (D08/D09), `learn` and the Hermes bridge from the
primary path.
