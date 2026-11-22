# Agent UX baseline

Run from the checkout being measured with the project dependencies installed. The measurements use local Git repositories and a temporary ledger. The resume scenario uses the fake shell driver on Linux or WSL2.

```sh
export UV_OFFLINE=1
uv run python scripts/agent_e2e.py --scenario resume --driver fake
# Use report.json from the directory printed by the harness.
uv run python scripts/agent_ux_measure.py \
  --resume-e2e-report .agent-e2e/RUN_DIRECTORY/report.json \
  --output docs/evals/agent-ux-baseline-1.7.0.json
```

The 1.7.0 artifact uses 22 live tasks across three projects. Twelve tasks share the repository with 1,050 generated inventory files. Three tasks have been idle for ten days. The measurement script removes the temporary repositories and ledger when it exits.

When measuring a later checkout, choose a new `--output` path. For surface measurements alone, omit `--resume-e2e-report`. Pass `--db PATH` to read an existing ledger. `task show` then uses the live task with the largest last recorded inventory by file count, falling back to terminal tasks if there are no live tasks. Use `--task ID` to select it explicitly. Omit `--output` to print the report.

Use `uv run python scripts/agent_ux_measure.py --projection` to measure the S1 projection core
directly against the fixture. It measures both list orders and one task without CLI or MCP dispatch.
The fixture checks require all 22 tasks in each list, at most 32,000 bytes for board, 48,000 for now,
and 4,000 for the heaviest compact task across the fixture. All three reads must make zero
`task_verification.project_state` calls.

The S0 fixture has three verification records across its 22 checks. All three report
`recorded: "ok"`. Two report `current: "not_checked"` and the older inventory revision reports
`current: "stale"`. The other 19 checks report `recorded: "missing"` and `current: "not_checked"`.
Parity tests compare each fixture task to the full view and exercise the same-HEAD edit limit
with a targeted record. The compact view reports `gate: "not_evaluated"`.

## Reading the report

- `schema_version` describes the measurement report format. `hipson_version` and `git_revision` identify the Hipson code and checkout HEAD. `git_dirty` records whether the checkout has changes when measurement begins. `ledger_source` is `fixture` or `provided`, and `task_show_id` identifies the task used for `task.show`.
- `reads` contains `full` and `compact` measurements for `task.board`, `task.now`, `lead.resume`, and `task.show`. The script checks CLI help and MCP tool schemas for `detail` and passes it explicitly where supported. A missing surface is `null`. If neither surface supports a mode, `available` is `false`. The `full` entry uses the default on 1.7.0.
- In `--projection` mode, `projection` supplies `bytes`, `project_state_calls`, and `wall_seconds` for `task.board`, `task.now`, and `task.show`. The byte count uses the UTF-8 output of `task_projection.dumps` without a trailing newline. Board also reports `largest_task_bytes` and `check_states` counts grouped by `recorded` then `current` for the full tasks on the measured page.
- `cli_json` and `cli_text` count UTF-8 stdout bytes, including the final newline and any JSON indentation. `mcp_json` counts UTF-8 bytes of the JSON text returned by `tools/call` before JSON-RPC envelope encoding.
- Each surface has its own `project_state_calls` and `wall_seconds`. The count covers calls to the full `task_verification.project_state` fingerprint. Resume's separate stat-only Git checks contribute to elapsed time. Wall time includes command parsing or request dispatch, reads and rendering. Both entry points run in-process with one sample per surface and mode. Fixture setup and process startup are outside the timed calls. Compare timings on the same machine.
- `resume_e2e` holds `scenario`, `driver`, the oracle result `passed`, and `measurement_valid` for the harness recording. `hipson_invocations` counts audited CLI calls. `read_commands` counts successful workflow reads by command, and `task_history_lookups` counts reads beyond `lead resume` and `task now`. `used_lead_resume`, `used_task_now`, and `first_hipson_command` describe how the driver found its context. `help_lookups` and `docs_lookups` count matching transcript commands.
- `resume_e2e.metrics.hipson_output_bytes` counts the UTF-8 stdout and stderr emitted by audited Hipson CLI calls during the run, before log redaction. `hipson_read_output_bytes` counts the subset from successful workflow reads. Both exclude setup output, including the resume text embedded in the mission. These are output sizes at the recorder, with shell redirection or filtering downstream. `token_usage` is `null` for the fake driver.
- The other harness metrics record transcript `total_commands` and `turns`, complete run `duration_seconds`, nonzero Hipson exits in `failing_invocations`, and whether the driver read a failure journal before starting recovery. The journal field is `null` when no recovery is observed.
