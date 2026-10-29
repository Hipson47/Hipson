"""Actor routing: registry validation, rejections, prior vs history, determinism, recording and capability tags."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest

from hipson import actor_routing, ledger, task_verification
from tests.json_schema import schema_errors
from tests.test_hipson_helpers import REPO_ROOT
from tests.test_ledger import assert_strict_schema
from tests.test_ledger_mcp import _assert_error, _call, _payload
from tests.test_task_verification import _project

SCHEMA = json.loads((REPO_ROOT / "schemas" / "actor-route.schema.json").read_text(encoding="utf-8"))


def _actor(actor_id: str, vendor: str, **extra: object) -> dict[str, object]:
    return {"id": actor_id, "vendor": vendor, "capabilities": ["code_write", "code_review", "web_research"], **extra}


def _registry(tmp_path: Path, *actors: dict[str, object], **top: object) -> dict[str, object]:
    path = tmp_path / "actors.json"
    path.write_text(json.dumps({"version": "test-1", "min_sample": 3, "actors": list(actors), **top}))
    return actor_routing.load_registry(path)


def _route(registry, store=None, **request):
    route = actor_routing.recommend(registry, store, **{"needs": ["code_write"], **request})
    assert not schema_errors(SCHEMA, route), schema_errors(SCHEMA, route)
    return route


def test_a_missing_registry_is_a_clear_error_not_a_silent_default(tmp_path):
    with pytest.raises(actor_routing.RoutingError, match="No actor registry at .*print-example"):
        actor_routing.load_registry(tmp_path / "absent.json")
    env = {**os.environ, "HIPSON_HOME": str(tmp_path), "HIPSON_LEDGER": str(tmp_path / "ledger.sqlite"),
           "PYTHONPATH": str(REPO_ROOT / "src")}
    run = subprocess.run([sys.executable, "-P", "-m", "hipson.cli", "route", "actor", "--needs", "code_write"],
                         env=env, capture_output=True, text=True, timeout=60)
    assert run.returncode == 2 and "No actor registry" in run.stderr and run.stdout == ""


def test_an_invalid_registry_reports_every_problem(tmp_path):
    path = tmp_path / "actors.json"
    path.write_text(json.dumps({"min_sample": 0, "extra": 1, "actors": [
        {"id": "a", "vendor": "", "capabilities": ["code_write", "telepathy"], "priors": {"code_write": 2}},
        {"id": "a", "vendor": "v", "capabilities": [], "max_sensitivity": "top", "roles": ["boss"]}]}))
    with pytest.raises(actor_routing.RoutingError) as raised:
        actor_routing.load_registry(path)
    message = str(raised.value)
    for problem in ("unknown key 'extra'", "version must be", "min_sample must be", "vendor must be",
                    "unknown capability 'telepathy'", "prior for 'code_write' must be between 0 and 1",
                    "duplicate id 'a'", "capabilities must be a non-empty list", "max_sensitivity must be",
                    "roles must be a list"):
        assert problem in message, problem
    path.write_text("{not json")
    with pytest.raises(actor_routing.RoutingError, match="not valid JSON"):
        actor_routing.load_registry(path)


def test_the_shipped_example_is_valid_and_ranks_no_vendor_above_another(tmp_path):
    path = tmp_path / "actors.json"
    path.write_text(actor_routing.example_registry())
    registry = actor_routing.load_registry(path)
    assert {entry["default_prior"] for entry in registry["actors"]} == {0.5}
    assert all(entry["priors"] == {} for entry in registry["actors"])
    route = _route(registry, probe=False)
    assert {item["score"] for item in route["candidates"]} == {0.5}
    assert [item["actor"] for item in route["candidates"]] == ["agy", "claude", "codex"]  # Ties: cost, then id.


def test_missing_capability_role_mode_sensitivity_and_availability_are_rejected_with_reasons(tmp_path):
    secret = {"max_sensitivity": "secret"}
    registry = _registry(
        tmp_path,
        _actor("writer", "v1", capabilities=["creative_writing"], **secret),
        _actor("reader", "v2", modes=["ro"], roles=["reviewer"], **secret),
        _actor("cloud", "v3"),  # max_sensitivity defaults to internal.
        _actor("off", "v4", available=False, **secret),
        _actor("ghost", "v5", binary="no-such-binary-hipson-test", **secret),
        _actor("ok", "v6", **secret),
    )
    route = _route(registry, mode="rw", sensitivity="confidential")
    reasons = {item["actor"]: item["reasons"] for item in route["rejected"]}
    assert reasons == {"writer": ["missing capability code_write"],
                       "reader": ["does not take the builder role", "does not work in rw mode"],
                       "cloud": ["not allowed confidential work (max internal)"],
                       "off": ["marked unavailable in the registry"],
                       "ghost": ["unavailable: no-such-binary-hipson-test is not on PATH"]}
    assert route["recommended"] == "ok" and [item["actor"] for item in route["candidates"]] == ["ok"]
    # Without the PATH probe the binary is not checked.
    unprobed = _route(registry, mode="rw", sensitivity="confidential", probe=False)
    assert [item["actor"] for item in unprobed["candidates"]] == ["ghost", "ok"]
    with pytest.raises(actor_routing.RoutingError, match="unknown capability telepathy"):
        _route(registry, needs=["telepathy"])
    with pytest.raises(actor_routing.RoutingError, match="name at least one capability"):
        actor_routing.recommend(registry, None, needs=[])


def _store(tmp_path: Path) -> ledger.Ledger:
    return ledger.Ledger(tmp_path / "ledger.sqlite")


def _outcome(store: ledger.Ledger, task: str, actor: str, result: str, *, role: str = "builder",
             needs: tuple[str, ...] = ("code_write",)) -> str:
    assignment = store.assign(task, actor=actor, role=role, mode="ro", needs=list(needs))["id"]
    store.finish(assignment, result=result, summary="done")
    store.ack(assignment)
    return assignment


def test_the_same_vendor_reviewer_rule_is_enforced(tmp_path):
    registry = _registry(tmp_path, _actor("codex", "openai"), _actor("codex-mini", "openai"),
                         _actor("claude", "anthropic"))
    with closing(_store(tmp_path)) as store:
        task = store.open_task(project=tmp_path, title="Review", objective="Review")["id"]
        _outcome(store, task, "codex", "passed")
        _outcome(store, task, "local-script", "passed")  # Not in the registry: vendor unknown.
        route = _route(registry, store, role="reviewer", task=task, needs=["code_review"])
    reasons = {item["actor"]: item["reasons"] for item in route["rejected"]}
    assert reasons == {
        "codex": ["same vendor (openai) as builder codex; a reviewer must be independent"],
        "codex-mini": ["same vendor (openai) as builder codex; a reviewer must be independent"]}
    assert route["recommended"] == "claude"
    assert route["notes"] == [f"builder local-script of {task} is not in the registry; its vendor is unknown"]
    assert route["history_basis"]["builder_vendors"] == {"codex": "openai"}
    # A builder request is not limited by the rule.
    assert _route(registry, needs=["code_review"])["rejected"] == []


def test_history_below_the_minimum_sample_keeps_the_prior_and_above_it_overrides(tmp_path):
    registry = _registry(tmp_path, _actor("strong", "v1", priors={"code_write": 0.9}),
                         _actor("steady", "v2", priors={"code_write": 0.6}))
    with closing(_store(tmp_path)) as store:
        tasks = [store.open_task(project=tmp_path, title=f"T{index}", objective="o")["id"] for index in range(4)]
        for task in tasks[:2]:
            _outcome(store, task, "strong", "failed")
        below = _route(registry, store)
        strong = next(item for item in below["candidates"] if item["actor"] == "strong")
        part = strong["components"][0]
        assert part["history"]["samples"] == 2 and part["history"]["score"] == 0.0
        assert part["history_weight"] == 0 and part["score"] == 0.9 and below["recommended"] == "strong"
        _outcome(store, tasks[2], "strong", "failed")  # The third sample reaches min_sample = 3.
        above = _route(registry, store)
        strong = next(item for item in above["candidates"] if item["actor"] == "strong")
        assert strong["components"][0]["history_weight"] == 2.0
        assert strong["score"] == round(0.9 / 3, 4) and above["recommended"] == "steady"
        # A tag the history does not carry leaves the prior alone; other roles are separate history.
        _outcome(store, tasks[3], "strong", "failed", role="reviewer")
        other = _route(registry, store, needs=["web_research"])
        assert next(item for item in other["candidates"] if item["actor"] == "strong")["score"] == 0.5


def test_history_counts_scope_violations_rework_and_the_first_verification(tmp_path):
    project = _project(tmp_path)
    with closing(_store(tmp_path)) as store:
        task = store.open_task(project=project, title="Build", objective="Build")["id"]
        first = _outcome(store, task, "codex", "passed")
        store.connection.execute("UPDATE assignments SET changes = ? WHERE id = ?", (json.dumps(
            {"counts": {"out_of_scope": {"created": 1, "modified": 0, "deleted": 0}}}), first))
        store.connection.commit()
        task_verification.run(tmp_path / "ledger.sqlite", task, [sys.executable, "-c", "raise SystemExit(1)"])
        _outcome(store, task, "codex", "passed")  # A later builder on the same task: the first was reworked.
        task_verification.run(tmp_path / "ledger.sqlite", task, [sys.executable, "-c", "pass"])
        records = actor_routing.outcomes(store, role="builder")
    summary = actor_routing.history(records, "codex", "code_write")
    assert summary["samples"] == 2 and summary["scope_violation_rate"] == 0.5 and summary["rework_rate"] == 0.5
    assert summary["first_pass_verify_rate"] == 0.5 and summary["verified_samples"] == 2
    assert summary["success_rate"] == 0.5 and summary["score"] == 0.25  # 0.5 success - 0.5 * 0.5 rework.
    assert summary["median_duration_seconds"] is not None


def test_output_is_deterministic_and_ties_break_on_cost_then_id(tmp_path):
    registry = _registry(tmp_path, _actor("b", "v1", cost_tier=1), _actor("a", "v2", cost_tier=1),
                         _actor("c", "v3", cost_tier=0))
    with closing(_store(tmp_path)) as store:
        task = store.open_task(project=tmp_path, title="T", objective="o")["id"]
        _outcome(store, task, "a", "passed")
        first, second = _route(registry, store), _route(registry, store)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert [item["actor"] for item in first["candidates"]] == ["c", "a", "b"]


def _cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("HIPSON_")}
    env.update(HIPSON_HOME=str(tmp_path), HIPSON_LEDGER=str(tmp_path / "ledger.sqlite"),
               PYTHONPATH=str(REPO_ROOT / "src"))
    return subprocess.run([sys.executable, "-P", "-m", "hipson.cli", *args], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=60)


def test_cli_task_tags_routing_and_the_recorded_event(tmp_path):
    _registry(tmp_path, _actor("codex", "openai", priors={"web_research": 0.8}), _actor("claude", "anthropic"))
    opened = _cli(tmp_path, "task", "open", "--project", str(tmp_path), "--title", "Research", "--objective", "o",
                  "--needs", "web_research,code_write", "--needs", "web_research", "--json")
    assert opened.returncode == 0, opened.stderr
    task = json.loads(opened.stdout)["id"]
    assert json.loads(opened.stdout)["needs"] == ["web_research", "code_write"]
    bad = _cli(tmp_path, "task", "open", "--project", str(tmp_path), "--title", "x", "--objective", "o",
               "--needs", "Web Research")
    assert bad.returncode != 0 and "lower-case names" in bad.stderr
    routed = _cli(tmp_path, "route", "actor", "--task", task, "--json")  # Tags default to the task's.
    assert routed.returncode == 0, routed.stderr
    route = json.loads(routed.stdout)
    assert route["request"]["needs"] == ["web_research", "code_write"] and route["recommended"] == "codex"
    assert not schema_errors(SCHEMA, route)
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite", read_only=True)) as store:
        event = store.events(task)[-1]
    assert event["type"] == "routed" and event["seq"] == route["recorded_event"]
    assert event["summary"].startswith("route builder/ro needs web_research,code_write: recommended codex (0.65)")
    assert event["refs"] == ["codex", route["registry"]["sha256"][:12]]
    text = _cli(tmp_path, "route", "actor", "--needs", "code_write", "--no-probe")
    assert text.returncode == 0 and "Recommended: claude" in text.stdout and "Recommendation only" in text.stdout
    unrecorded = _cli(tmp_path, "route", "actor", "--task", task, "--no-record", "--json")
    assert json.loads(unrecorded.stdout)["recorded_event"] is None
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite", read_only=True)) as store:
        assert [item["type"] for item in store.events(task)].count("routed") == 1
        with pytest.raises(ledger.LedgerError, match="reserved"):
            store.add_event(task, actor="worker", type="routed", summary="forged")
    example = _cli(tmp_path, "route", "actor", "--print-example")
    assert example.returncode == 0 and json.loads(example.stdout)["version"] == "example-1"
    legacy = _cli(tmp_path, "route", "--task", "review the docs", "--json")
    assert legacy.returncode == 0, legacy.stderr  # The workflow router keeps working.


def test_task_run_records_its_own_tags_or_the_tasks(tmp_path):
    project = _project(tmp_path)
    with closing(_store(tmp_path)) as store:
        task = store.open_task(project=project, title="T", objective="o", needs=["code_write"])["id"]
        store.set_status(task, to="in_progress", actor="claude")
    env = {key: value for key, value in os.environ.items() if key not in ("HIPSON_TASK", "HIPSON_ASSIGNMENT", "HIPSON_ROLE")}
    env.update(HIPSON_LEDGER=str(tmp_path / "ledger.sqlite"), PYTHONPATH=str(REPO_ROOT / "src"))
    for extra in ([], ["--needs", "browser,computer_use"]):
        run = subprocess.run([sys.executable, "-P", "-m", "hipson.cli", "task", "run", "--task", task, "--actor",
                              "codex", "--role", "explorer", *extra, "--", sys.executable, "-c", "pass"],
                             cwd=project, env=env, capture_output=True, text=True, timeout=120)
        assert run.returncode == 0, run.stderr
    refused = subprocess.run([sys.executable, "-P", "-m", "hipson.cli", "task", "run", "--task", task, "--actor",
                              "codex", "--role", "explorer", "--needs", "BAD", "--", sys.executable, "-c", "pass"],
                             cwd=project, env=env, capture_output=True, text=True, timeout=120)
    assert refused.returncode == 64 and "lower-case names" in refused.stderr
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite", read_only=True)) as store:
        view = store.show(task)
    assert [item["needs"] for item in view["assignments"]] == [["code_write"], ["browser", "computer_use"]]
    assert_strict_schema("task.schema.json", view)


def test_mcp_route_actor_is_read_only_unless_record_is_approved(tmp_path):
    _registry(tmp_path, _actor("codex", "openai"))
    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=tmp_path, title="T", objective="o", needs=["code_write"])["id"]
    with patch.dict(os.environ, {"HIPSON_HOME": str(tmp_path)}):
        route = _payload(_call(tmp_path, "route.actor", ledger_db=db, task=task))
        assert route["recommended"] == "codex" and route["recorded_event"] is None
        _assert_error(_call(tmp_path, "route.actor", ledger_db=db, task=task, record=True), "approved")
        recorded = _payload(_call(tmp_path, "route.actor", ledger_db=db, task=task, record=True, approved=True))
    assert recorded["recorded_event"] is not None
    with closing(ledger.Ledger(db, read_only=True)) as store:
        assert [item["type"] for item in store.events(task)].count("routed") == 1


def test_v9_ledgers_open_read_only_and_migrate_to_empty_tags(tmp_path):
    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=tmp_path, title="Old", objective="Old ledger")["id"]
        store.assign(task, actor="codex", role="builder", mode="ro")
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("ALTER TABLE assignments DROP COLUMN needs")
        connection.execute("ALTER TABLE tasks DROP COLUMN needs")
        connection.execute("UPDATE schema_version SET version = 9")
        connection.commit()
    with closing(ledger.Ledger(db, read_only=True)) as store:
        view = store.show(task)
        assert view["needs"] == [] and view["assignments"][0]["needs"] is None
        assert actor_routing.outcomes(store, role="builder") == []
    with closing(ledger.Ledger(db)) as store:
        assert store.connection.execute("SELECT version FROM schema_version").fetchone()[0] == ledger.SCHEMA_VERSION == 10
        assert store.show(task)["needs"] == []


def test_review_fixes_ids_vendors_member_types_weights_and_unknown_tasks(tmp_path):
    path = tmp_path / "actors.json"
    for bad, problem in (
        ({"id": "codex ", "vendor": "openai", "capabilities": ["code_write"]}, "without surrounding whitespace"),
        ({"id": "a", "vendor": "v", "capabilities": [{}]}, "capabilities must be a non-empty list of names"),
        ({"id": "a", "vendor": "v", "capabilities": ["code_write"], "roles": [[]]}, "roles must be a list"),
        ({"id": "a", "vendor": "v", "capabilities": ["code_write"], "modes": [{}]}, "modes must be a list"),
    ):
        path.write_text(json.dumps({"version": "x", "actors": [bad]}))
        with pytest.raises(actor_routing.RoutingError, match=problem):
            actor_routing.load_registry(path)
    for weight in (1e309, float("nan"), 0, 10**400):
        path.write_text(json.dumps({"version": "x", "history_weight": weight,
                                    "actors": [{"id": "a", "vendor": "v", "capabilities": ["code_write"]}]}))
        with pytest.raises(actor_routing.RoutingError, match="history_weight must be a finite number"):
            actor_routing.load_registry(path)
    # Vendors compare case-insensitively, so "OpenAI" cannot review its own "openai" builder.
    registry = _registry(tmp_path, _actor("codex", "openai"), _actor("gpt-reviewer", " OpenAI "),
                         _actor("claude", "anthropic"))
    with closing(_store(tmp_path)) as store:
        task = store.open_task(project=tmp_path, title="T", objective="o")["id"]
        _outcome(store, task, "codex", "passed")
        route = _route(registry, store, role="reviewer", task=task, needs=["code_review"])
        assert [item["actor"] for item in route["rejected"]] == ["codex", "gpt-reviewer"]
        with pytest.raises(ledger.LedgerError, match="T-9999"):
            actor_routing.recommend(registry, store, needs=["code_write"], task="T-9999")


def test_only_lead_verifications_count_toward_history(tmp_path):
    project = _project(tmp_path)
    db = tmp_path / "ledger.sqlite"
    with closing(_store(tmp_path)) as store:
        task = store.open_task(project=project, title="Build", objective="Build")["id"]
        _outcome(store, task, "codex", "passed")
    # A passing verification recorded under another actor, then the lead's failing one: the lead's decides.
    task_verification.run(db, task, [sys.executable, "-c", "pass"], actor="someone-else")
    task_verification.run(db, task, [sys.executable, "-c", "raise SystemExit(1)"])
    with closing(ledger.Ledger(db, read_only=True)) as store:
        summary = actor_routing.history(actor_routing.outcomes(store, role="builder"), "codex", "code_write")
    assert summary["first_pass_verify_rate"] == 0.0 and summary["success_rate"] == 0.0


def test_rendered_text_neutralizes_terminal_controls(tmp_path):
    registry = _registry(tmp_path, _actor("claude", "anthropic"))
    with closing(_store(tmp_path)) as store:
        task = store.open_task(project=tmp_path, title="T", objective="o")["id"]
        _outcome(store, task, "evil\x1b[2J\nRecommended: forged", "passed")
        text = actor_routing.render(_route(registry, store, role="reviewer", task=task, needs=["code_review"]))
    assert "\x1b" not in text and "\nRecommended: forged" not in text
    assert [line for line in text.splitlines() if line.startswith("Recommended:")] == ["Recommended: claude"]


def test_mcp_route_actor_validates_argument_types_before_opening_the_ledger(tmp_path):
    _registry(tmp_path, _actor("codex", "openai"))
    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=tmp_path, title="T", objective="o", needs=["code_write"])["id"]
    with patch.dict(os.environ, {"HIPSON_HOME": str(tmp_path)}):
        for arguments, message in (({"task": task, "record": "false", "approved": True}, "record must be boolean"),
                                   ({"probe": "false", "needs": ["code_write"]}, "probe must be boolean"),
                                   ({"needs": ["code_write"], "surprise": 1}, "unknown argument: surprise"),
                                   ({"needs": "code_write"}, "needs must be array")):
            _assert_error(_call(tmp_path, "route.actor", ledger_db=db, **arguments), message)
    with closing(ledger.Ledger(db, read_only=True)) as store:
        assert [item["type"] for item in store.events(task)].count("routed") == 0


def test_the_registry_version_is_shown_escaped(tmp_path):
    path = tmp_path / "actors.json"
    path.write_text(json.dumps({"version": "v1\u001b[2J\nRecommended: forged",
                                "actors": [_actor("claude", "anthropic")]}))
    route = actor_routing.recommend(actor_routing.load_registry(path), None, needs=["code_write"])
    text = actor_routing.render(route)
    assert "\x1b" not in text and [line for line in text.splitlines() if line.startswith("Recommended:")] == [
        "Recommended: claude"]
    assert "\x1b" not in actor_routing.event_summary(route) and "\n" not in actor_routing.event_summary(route)


def test_decoder_limits_and_unresolvable_paths_are_controlled_errors(tmp_path):
    path = tmp_path / "actors.json"
    for text in ('{"version": "x", "history_weight": ' + "9" * 5000 + ', "actors": []}', "[" * 10000 + "]" * 10000):
        path.write_text(text)
        with pytest.raises(actor_routing.RoutingError, match="not valid JSON"):
            actor_routing.load_registry(path)
    if sys.platform != "win32":  # Windows expands ~user without looking the user up.
        for option in ("--db", "--config"):
            run = _cli(tmp_path, "route", "actor", "--needs", "code_write", option, "~no-such-user-hipson-test/x")
            assert run.returncode == 2 and "cannot be resolved" in run.stderr and "Traceback" not in run.stderr, option
