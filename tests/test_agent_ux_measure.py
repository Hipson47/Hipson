"""Offline measurement contracts shared by pytest and the standalone runner."""

import json
from contextlib import closing
from datetime import UTC, datetime, timedelta
from unittest.mock import call, patch

from hipson import __version__
from hipson.ledger import Ledger
from hipson.task_projection import dumps, project_list
from scripts import agent_ux_measure
from tests.fixtures.agent_ux_ledger import build
from tests.test_task_projection import _assert_gate_parity


def test_agent_ux_fixture_and_report(tmp_path):
    fixture = build(tmp_path / "fixture")
    with closing(Ledger(fixture.db, read_only=True)) as store:
        tasks = store.list_tasks()
        assert len(tasks) == 22 and len({task["project"] for task in tasks}) == 3
        assert all(task["status"] in {"open", "in_progress", "review"} and task["checks"] for task in tasks)
        assert sum(task["status"] == "review" for task in tasks) == 2

        heavy = store.show(fixture.heavy_task)
        inventory = heavy["verification"]["current"]["files"]
        assert 270_000 <= len(json.dumps(inventory).encode("utf-8")) <= 330_000
        assert all(item["tracked"] for item in inventory)
        assert heavy["verification"]["checks"][0]["freshness"] == "current"
        stale = next(task for task in tasks if task["title"] == "Recheck the previous inventory revision")
        stale_verification = store.show(stale["id"])["verification"]
        assert stale_verification["latest"]["exit_code"] == 0
        assert stale_verification["checks"][0]["freshness"] == "stale"

        assert {finding["severity"] for finding in store.findings(status="open")} == {"blocking", "major"}
        assignment_states = {
            (assignment["status"], assignment["ack_state"])
            for task in tasks if task["title"] in {"Keep the fixture marker assigned", "Acknowledge the fixture delivery"}
            for assignment in store.show(task["id"])["assignments"]
        }
        assert assignment_states == {("active", "not_required"), ("passed", "unacknowledged")}

        cutoff = datetime.now(UTC) - timedelta(days=7)
        dormant = [task for task in tasks if datetime.fromisoformat(task["updated_at"]) < cutoff]
        assert len(dormant) == 3 and {task["status"] for task in dormant} == {"open", "in_progress"}
        for task in dormant:
            assert all(datetime.fromisoformat(event["timestamp"]) < cutoff for event in store.events(task["id"]))
            assert not store.show(task["id"])["assignments"] and not store.verifications(task["id"])

    output = tmp_path / "measurements.json"
    assert agent_ux_measure.main(["--db", str(fixture.db), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report.keys() == {"schema_version", "hipson_version", "git_revision", "git_dirty", "ledger_source",
                             "task_show_id", "reads", "resume_e2e"}
    assert report["schema_version"] == 1 and report["hipson_version"] == __version__
    assert len(report["git_revision"]) in {40, 64} and int(report["git_revision"], 16) > 0
    assert isinstance(report["git_dirty"], bool)
    assert report["ledger_source"] == "provided" and report["resume_e2e"] is None
    assert report["task_show_id"] == fixture.heavy_task
    assert report["reads"].keys() == {"task.board", "task.now", "lead.resume", "task.show"}
    channels = {"cli_json", "mcp_json", "cli_text"}
    for read in report["reads"].values():
        assert read.keys() == {"full", "compact"}
        assert read["full"]["available"] is True
        assert all(read["full"][channel]["project_state_calls"] > 0 for channel in channels)
        for measurement in read.values():
            assert measurement.keys() == {"available", *channels}
            stats = [measurement[channel] for channel in channels if measurement[channel] is not None]
            assert measurement["available"] == bool(stats)
            for channel in stats:
                assert channel.keys() == {"bytes", "project_state_calls", "wall_seconds"}
                assert 0 < channel["bytes"] < 25_000_000
                assert channel["project_state_calls"] >= 0 and channel["wall_seconds"] >= 0

    board = report["reads"]["task.board"]
    if __version__ == "1.7.0" and not board["compact"]["available"]:
        assert all(not read["compact"]["available"] for read in report["reads"].values())
        assert 1_000_000 <= board["full"]["mcp_json"]["bytes"] <= 10_000_000
        assert board["full"]["mcp_json"]["project_state_calls"] == 3
        assert board["full"]["cli_json"]["project_state_calls"] == 3
        assert 0 < board["full"]["cli_text"]["bytes"] < 10_000

    assert agent_ux_measure.main(["--db", str(fixture.db), "--projection", "--output", str(output)]) == 0
    compact = json.loads(output.read_text(encoding="utf-8"))
    assert compact["git_revision"] == report["git_revision"] and compact["git_dirty"] == report["git_dirty"]
    assert compact["task_show_id"] == fixture.heavy_task
    budgets = {"task.board": 32_000, "task.now": 48_000, "task.show": 4_000}
    assert compact["projection"].keys() == budgets.keys()
    for name, budget in budgets.items():
        stats = compact["projection"][name]
        assert 0 < stats["bytes"] <= budget
        assert stats["project_state_calls"] == 0 and stats["wall_seconds"] >= 0
    with closing(Ledger(fixture.db, read_only=True)) as store:
        full = {task["id"]: store.show(task["id"]) for task in tasks}
        for kind in ("task.board", "task.now"):
            projection = project_list(store, kind=kind)
            by_id = {task["id"]: task for task in projection["tasks"]}
            assert set(by_id) == {task["id"] for task in tasks}
            assert projection["truncated"] is False and projection["omitted"] == 0
            assert len(dumps(projection).encode("utf-8")) <= budgets[kind]
            assert max(len(dumps(task).encode("utf-8")) for task in by_id.values()) <= budgets["task.show"]
            for task_id, projected in by_id.items():
                _assert_gate_parity(full[task_id], projected)
            assert by_id[fixture.heavy_task]["checks"][0]["head_matches"] is True
            assert by_id[stale["id"]]["checks"][0]["head_matches"] is False
            assert by_id[stale["id"]]["checks"][0]["state"] == "stale"


def test_agent_ux_detail_is_explicit_on_supported_surfaces():
    with patch.object(agent_ux_measure, "_cli", side_effect=["  --detail {full,compact}", "{}", "full", "{}", "compact"]) as cli, \
            patch.object(agent_ux_measure, "_tool_text", return_value="{}") as mcp:
        result = agent_ux_measure._measure_read("task.show", "T-0001", mcp_detail=True)
    show = ["task", "show", "--task", "T-0001", "--detail"]
    assert cli.call_args_list == [call(["task", "show", "--help"]),
                                  call([*show, "full", "--json"]), call([*show, "full"]),
                                  call([*show, "compact", "--json"]), call([*show, "compact"])]
    assert mcp.call_args_list == [call("task.show", {"task": "T-0001", "detail": detail})
                                  for detail in ("full", "compact")]
    assert all(result[detail]["available"] for detail in ("full", "compact"))
