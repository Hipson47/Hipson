# Stabilization review of 1.6.0

Four independent read-only reviews (Codex gpt-6.1-sol, xhigh) of tag v1.6.0 (14856f9), recorded on ledger task
T-0025, each scoped to one area: **A** ledger and lifecycle, **B** process supervision, platforms, setup and
release, **C** security and trust boundaries, **D** workflow layer, CLI, tests and docs. The lead reproduced or
derived each finding from code before fixing it; every fix has a regression test (most in
`tests/test_stabilization.py`) that fails on the old code. Reviewer severities are kept; several "blocking"
ratings in C and D concern optional workflow features rather than the ledger.

Status: **fixed** (with test), **mitigated** (partial fix, follow-up named), **deprecated** (the command is
hidden since 1.7 and removed in 2.0, see [DEPRECATIONS.md](DEPRECATIONS.md)), **open** (not yet changed).
Items marked *1.7* were resolved after the owner's decision on 2026-10-28 (ledger tasks T-0061..T-0063).

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
| B1 | `init`: an editor saving in the instant before atomic replace can be overwritten (blocking per review) | fixed (1.7): rename-aside compare, no-clobber install, rename fallback without hard links |
| B2 | Windows `.cmd`/`.bat` workers and checks reparsed `&`, `%`, `^` in arguments (blocking) | fixed: shared env-variable batch transport; also makes `npm`/`npx` checks work |
| B3 | Ctrl+C during `task verify`/`close`/`do` could orphan the running check | fixed: `supervise()` forwards signals and waits for cleanup |
| B4 | Claude registration does not record a `HIPSON_LEDGER`/`HIPSON_HOME`/`XDG_CONFIG_HOME` override | fixed (1.7): the registration records the three overrides and setup checks them |
| B5 | Release could publish without CI passing on the tagged commit | fixed: release runs the CI matrix on the tag first |
| B6 | Manual release accepted a branch name as the tag input | fixed: must run from `refs/tags/<tag>` |
| B7 | Non-UTF-8 stderr from a successful client failed `init` | fixed |
| B8 | `init` next steps omitted `task ack` | fixed |

## C. Security and trust boundaries

| ID | Finding | Status |
|---|---|---|
| C1 | Read-only Git calls (scan, work, contract) could run filter drivers | fixed for scan/read paths (`prepare_read_only_command(cwd=...)`) |
| C2 | Workflow MCP tools read any path, e.g. `quality.report` sidecar `.env` | fixed: every path and a work plan's own `project` must resolve inside the served project (plan contents never widen it); run artifacts must be regular files in their run; the plan's `project` must be absolute and its `packet.path` contained; sensitive files refused. Behavior change: one MCP server per project (`hipson init` already registers it that way). Not a sandbox: `verify.run` (approval required) executes the plan's commands with the user's privileges, and what those commands read is outside this boundary |
| C3 | Skill lookup and scans followed symlinks out of the root | fixed |
| C4 | Sensitive tracked-file diff bodies stayed in scans/packets (= D02) | fixed: whole per-file sections omitted |
| C5 | Truncation before redaction could expose private-key bodies | fixed: redact before truncate, bounded reads |
| C6 | Provider redirects resent the Authorization header to another host or HTTP | fixed: provider HTTP never follows redirects |
| C7 | Stale quality artifacts can override failed verification and human gates (= D01) | fixed (1.7): gates derive from verification and the decision; mismatched reports are ignored; `release` deprecated |
| C8 | Verification commands inherited `GIT_DIR`/`GIT_WORK_TREE` and could check another tree | fixed: checks and workers lose repository-selecting and config-injecting variables (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_CONFIG*`, ...); identity and transport (`GIT_AUTHOR_*`, `GIT_SSH_COMMAND`) stay |
| C9 | Session persistence skipped structured credential redaction | fixed |
| C10 | Agent installers can overwrite a concurrent user edit | fixed (1.7): installers use init's snapshot, lock and atomic write |
| C11 | Malformed workflow MCP arguments terminated the server | fixed: type, key and arithmetic errors (e.g. `1e309`) stay per request; full schema validation for workflow tools is open |
| C12 | Packet preflight read files unbounded, including FIFOs | fixed |
| C13 | Skill content could close its `untrusted_data` block | fixed (skills; sidecar `<packet>` framing in 1.7) |

A first fix-verification review of the branch found eight incomplete fixes (forged work-plan projects and
linked run artifacts for C2, keys longer than the bounded read for C5, `1e309` for C11, identity variables for
C8, skill validation for C3, file names with spaces for C4, negative limits for C12); all eight were fixed
with regression tests before the second verification.

## D. Workflow layer, CLI, tests and docs

| ID | Finding | Status |
|---|---|---|
| D01 | Old quality gates override failed verification and a rejected decision; `release claim` can allow | fixed (1.7), see C7 |
| D02 | Sensitive diff bodies in scans (= C4) | fixed |
| D03 | `route` recommendations allowed `$(...)` expansion | fixed: `shlex.quote` |
| D04 | `autopilot resume` skips project provider policy | deprecated (1.7) |
| D05 | Review kits continue after failed packet preflight | fixed (1.7) |
| D06 | Legacy `verify run` timeouts leave descendants and drop output | fixed (1.7): contained supervisor |
| D07 | Full/release verification profiles start `dev` servers | fixed (1.7): finite scripts only |
| D08, D09 | Provider chat loses tool output/history; reports success after provider failure | deprecated (1.7) |
| D10 | `work --project P` records paths relative to the wrong directory | fixed (1.7) |
| D11 | false-green e2e oracle accepted an unrelated failing command | fixed: exact command after delivery, plus a negative control |
| D12, D13 | Scheduler has no atomic job claim and binds no workspace | deprecated (1.7) |
| D14 | Untracked scan content had no aggregate bound or link-target check | fixed |
| D15-D23 | Minor CLI/session/learn/hermes/eval/diagnostic issues (read-only session open migrates, learn drops recent turns, unbounded message bodies, quoted verify args refused, resume ignores new decision, mixed JSON output, silent packet errors, hidden/absolute paths in evals, vacuous readiness) | 1.7: fixed D15, D17, D18, D19, D21, D22, D23 (provider doctor); D16 `learn`, D20 `hermes` and the scheduler/hermes parts of D23 deprecated |
| D24 | Tests depended on the developer's PATH and Git config | fixed for fixture Git and the review-kit test |
| D25 | Init concurrency test can miss its race | fixed (1.7): observed lock contention plus a negative control |

## Decision (taken 2026-10-28): the optional workflow layer

The ledger (`task`, `lead`, MCP `task.*`), `init` and verification are the product today. Reviewer D
recommends keeping and fixing the local primitives (`work`, `route`, `scan`, `packet`, `verify run`,
`evidence`/`audit`, `kit review`, `run`, `memory`, `skill`, `doctor`, `policy`, `sidecar`/`provider` as
explicit advisory integrations) and hiding or deprecating the experimental surfaces whose open findings above
are functional: `autopilot` (overlaps `kit review`, launches no worker), `release claim` (D01/C7),
`scheduler` (D12/D13), provider `chat`/runtime/gateway (D08/D09), `learn` and the Hermes bridge from the
primary path.

The owner accepted this recommendation on 2026-10-28: the listed surfaces are deprecated in 1.7 and the kept
primitives' findings are fixed. The 1.7 work also found and fixed one issue the reviews missed: supervisors
Hipson spawns in the project directory could import a project module named like the standard library
(e.g. `json.py`); they now run with `python -P`.
