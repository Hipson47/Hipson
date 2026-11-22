# Hipson 1.8.0

Hipson is a tool for coding agents. Its interface is the MCP and CLI contract agents use to see
current work, act on evidence and recover from refusals. Version 1.8 adds bounded reads and
concrete next calls to that interface. The lead still owns verification, review and acceptance.

Status: **beta**, release candidate measured on 2026-11-22.

## Highlights

- **Compact reads.** Pass `--detail compact` on the CLI or `detail: "compact"` in MCP for board,
  now, resume and task show. Compact JSON is versioned, marks worker evidence and carries
  continuation calls. Task show can page verification, assignments, findings and events.
- **Next actions and recovery.** Compact now and resume rank up to ten `next_actions` with MCP
  arguments and CLI commands. Refusals carry stable codes and, where applicable, fix calls.
  MCP adds an `error_schema: 1` item, CLI JSON sends the envelope to stderr, and CLI text prints
  `fix:` suggestions.
- **Stale work and cleanup.** Compact reads flag open or in-progress work after seven days of
  ledger inactivity by default. Use `hipson task cleanup --project PATH` or `task.cleanup`
  before removing a project or worktree. It reads task, assignment and claim records, reports
  omitted counts, and refuses while nonterminal tasks or active assignments remain.
- **Opt-in lead profile.** Use `hipson mcp serve --stdio --profile lead` or register it with
  `hipson init --mcp-profile lead`. The profile exposes 14 tools and defaults the four task
  reads to compact. Known tools outside the profile get a coded refusal.
- **Usage per task.** Task show and its assignments section report runtime tokens and ledger
  elapsed time, with attempts by role, repair rounds, token totals grouped by runtime source
  and reasons when usage is missing.

The CLI and default `full` MCP profile still use full detail in 1.8. Those reads will default to compact
detail in 1.9. The tool profile and read detail are separate settings. See the [changelog](../CHANGELOG.md).

## Measurements

These UTF-8 byte counts compare 1.7.0 full reads with 1.8.0 RC compact reads on the same
22-task fixture.

| Read | CLI JSON, 1.7.0 → RC | MCP JSON, 1.7.0 → RC | CLI text, 1.7.0 → RC | Full fingerprints, 1.7.0 → RC |
| --- | ---: | ---: | ---: | ---: |
| `task board` | 5,614,904 → 30,497 | 4,391,961 → 30,497 | 1,350 → 2,264 | 3 → 0 |
| `task now` | 5,812,325 → 32,704 | 4,382,200 → 32,704 | 4,024 → 3,580 | 3 → 0 |
| `lead resume` | 11,853,633 → 33,455 | 8,778,936 → 33,455 | 4,043 → 4,001 | 3 → 0 |
| `task show` | 766,795 → 2,441 | 623,094 → 2,441 | 538 → 455 | 1 → 0 |

The complete MCP `tools/list` reply was 16,223 bytes for 34 tools on 1.7.0. The RC replies
are 18,463 bytes for 35 tools in `full` and 5,954 bytes for 14 tools in `lead`.

| Fake-driver resume | Hipson calls | Output bytes | Read bytes | Oracle |
| --- | ---: | ---: | ---: | --- |
| 1.7.0 full text | 5 | 7,339 | 3,265 | Passed |
| 1.8.0 RC compact text | 6 | 7,037 | 2,753 | Passed |
| 1.8.0 RC compact JSON (informational) | 6 | 10,763 | 6,478 | Passed |

The owner waived the fewer-calls target for this scenario. The 1.7.0 reference hard-codes
the IDs and skips activity. The compact runs take a sixth call to read the worker's activity
and failure journal before acknowledging the result. Compact text reads 512 fewer bytes.
See the [measurement method and owner decision](evals/AGENT_UX.md).

## Upgrade from 1.7

```bash
uv tool upgrade hipson        # or: pip install --upgrade hipson
hipson init --client none     # backs up the ledger and migrates schema 11 to 12
```

With 1.8 installed, any write open of a schema 11 ledger first takes a SQLite backup next to
the ledger, named `<ledger>.v11-backup-<timestamp>`, then migrates it. `hipson init` keeps its
own backup as well. Reads can open the older ledger before migration.

Hipson 1.7.0 refuses a schema 12 ledger. Upgrade every Hipson that shares it together, including
other virtual environments, installations that your MCP clients start, and other machines that
sync the ledger. Restart the lead client after upgrading.

To go back, stop every Hipson that uses the ledger. Delete the ledger's `-wal` and `-shm`
files if present, then copy the pre-migration backup over the ledger file before starting
1.7 again. All writes made after the backup are lost.

## Known limitations

- Compact reads show recorded evidence and may leave current check status unevaluated. Use
  lead verification for a fresh result and read the relevant task section for the full evidence.
  Cleanup is a point-in-time read. A later ledger write can change its result.
- Runtime token reporting depends on the adapter and runtime. Missing counters remain unknown.
  Usage is recorded as evidence and has no effect on gates. The RC measurements use a fake
  driver. Updated live Codex, Claude Code and Antigravity usage probes are pending with the lead.
- The Windows Python 3.12 CI test `test_a_redacted_review_finding_is_still_repairable` has
  intermittently failed while copying `core.autocrlf` into the review snapshot. The Git helper
  uses a five-second timeout, discards stderr and makes one attempt. The observed failure is
  insufficient to distinguish a timeout from a Git error, and the test passed locally on Linux.
