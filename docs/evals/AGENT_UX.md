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

## S2 compact reads

Run the surface measurement offline from the S2 checkout.

```sh
UV_OFFLINE=1 uv run python scripts/agent_ux_measure.py --output docs/evals/agent-ux-1.8-s2.json
```

| Read | 1.7.0 full CLI JSON (bytes) | S2 bounded CLI JSON (bytes) | 1.7.0 full MCP JSON (bytes) | S2 bounded MCP JSON (bytes) | Full fingerprints per read, before → after |
| --- | ---: | ---: | ---: | ---: | ---: |
| `task board` | 5,614,904 | 27,657 | 4,391,961 | 27,657 | 3 → 0 |
| `task now` | 5,812,325 | 27,655 | 4,382,200 | 27,655 | 3 → 0 |
| `lead resume` | 11,853,633 | 28,848 | 8,778,936 | 28,848 | 3 → 0 |
| `task show` | 766,795 | 2,322 | 623,094 | 2,322 | 1 → 0 |
| `task show --task T-0001 --section verification` | — | 7,533 | — | 7,533 | — → 0 |

The [1.7.0 baseline](agent-ux-baseline-1.7.0.json) and [S2 report](agent-ux-1.8-s2.json) use the same
22-task fixture. On S2, explicitly requesting full detail produced the same byte sizes as the
baseline. Compact resume derives its context from the projection snapshot and uses the HEAD
observations in its now page. Compact sizes include the evidence markers. The verification section
measurement uses the default five-record limit and produces 7,533 bytes in either CLI mode. Its
single record shows the first 20 of 1,051 files, including the tracked fixture marker in the
inventory total.

## S3 compact resume with next actions

```sh
export UV_OFFLINE=1
uv run python scripts/agent_e2e.py --scenario resume --driver fake --resume-compact
uv run python scripts/agent_e2e.py --scenario resume --driver fake --resume-compact json
uv run python scripts/agent_ux_measure.py \
  --resume-e2e-report .agent-e2e/TEXT_RUN_DIRECTORY/report.json \
  --output docs/evals/agent-ux-1.8-s3.json
uv run python - .agent-e2e/JSON_RUN_DIRECTORY/report.json <<'PY'
import json
import sys
from pathlib import Path
from scripts.agent_ux_measure import _resume_e2e

path = Path('docs/evals/agent-ux-1.8-s3.json')
report = json.loads(path.read_text())
report['resume_e2e_json'] = _resume_e2e(Path(sys.argv[1]))
path.write_text(json.dumps(report, indent=2) + '\n')
PY
```

| Lead resumes mid-task | Hipson calls | Output bytes | Read bytes | Oracle |
| --- | ---: | ---: | ---: | --- |
| 1.7.0 full text | 5 | 7,339 | 3,265 | Passed |
| S3 compact text | 6 | 6,654 | 2,625 | Passed |
| S3 compact JSON (informational) | 6 | 10,069 | 6,040 | Passed |

Compact text saves 685 bytes and makes one more call than the 1.7.0 reference. The fixture's first
delivery carries runner activity and a populated journal, so the suggested activity read precedes
acknowledgment in both compact formats. The call-count target remains unmet.

The owner accepted this result on 2026-11-22. A worker's activity and failure journal are read before
its result is acknowledged, even though that adds a call. Five calls is the minimum for this
scenario, so the 1.7.0 reference, which hard-codes the IDs and skips that read, cannot be beaten on
calls. P0-2's "fewer calls" criterion is therefore waived for this scenario. "Fewer bytes" is met.

On the larger S0 fixture, the original S3 actions included nine verification calls and one activity
read. The repaired list contains one acknowledgment, two verification calls, two task reads, and
two findings reads. Bounded CLI and MCP JSON sizes with actions included are 29,101 bytes for now
and 29,852 bytes for resume. Both make zero full fingerprints. [S3 report](agent-ux-1.8-s3.json)

## Reading the report

- `schema_version` describes the measurement report format. `hipson_version` and `git_revision` identify the Hipson code and checkout HEAD. `git_dirty` records whether the checkout has changes when measurement begins. `ledger_source` is `fixture` or `provided`, and `task_show_id` identifies the task used for `task.show`.
- `reads` contains `full` and `compact` measurements for `task.board`, `task.now`, `lead.resume`, and `task.show`. The script checks CLI help and MCP tool schemas for `detail` and passes it explicitly where supported. A missing surface is `null`. If neither surface supports a mode, `available` is `false`. The `full` entry uses the default on 1.7.0.
- The S2 report also records `section_read`, measured separately with the same fixture and the default verification section limit, through both CLI modes and MCP.
- In `--projection` mode, `projection` supplies `bytes`, `project_state_calls`, and `wall_seconds` for `task.board`, `task.now`, and `task.show`. The byte count uses the UTF-8 output of `task_projection.dumps` without a trailing newline. Board also reports `largest_task_bytes` and `check_states` counts grouped by `recorded` then `current` for the full tasks on the measured page.
- `cli_json` and `cli_text` count UTF-8 stdout bytes, including any final newline and JSON indentation. Compact CLI JSON omits the trailing newline. `mcp_json` counts UTF-8 bytes of the JSON text returned by `tools/call` before JSON-RPC envelope encoding.
- Each surface has its own `project_state_calls` and `wall_seconds`. The count covers calls to the full `task_verification.project_state` fingerprint. Resume's separate stat-only Git checks contribute to elapsed time. Wall time includes command parsing or request dispatch, reads and rendering. Both entry points run in-process with one sample per surface and mode. Fixture setup and process startup are outside the timed calls. Compare timings on the same machine.
- `resume_e2e` holds `scenario`, `driver`, the oracle result `passed`, and `measurement_valid` for the harness recording. `hipson_invocations` counts audited CLI calls. `read_commands` counts successful workflow reads by command, and `task_history_lookups` counts reads beyond `lead resume` and `task now`. `used_lead_resume`, `used_task_now`, and `first_hipson_command` describe how the driver found its context. `help_lookups` and `docs_lookups` count matching transcript commands.
- The S3 report stores the compact text run in `resume_e2e` and the informational compact JSON run in `resume_e2e_json`.
- `resume_e2e.metrics.hipson_output_bytes` counts the UTF-8 stdout and stderr emitted by audited Hipson CLI calls during the run, before log redaction. `hipson_read_output_bytes` counts the subset from successful workflow reads. Both exclude setup output, including the resume text embedded in the mission. These are output sizes at the recorder, with shell redirection or filtering downstream. `token_usage` is `null` for the fake driver.
- The other harness metrics record transcript `total_commands` and `turns`, complete run `duration_seconds`, nonzero Hipson exits in `failing_invocations`, and whether the driver read a failure journal before starting recovery. The journal field is `null` when no recovery is observed.
