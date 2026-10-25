"""Regression coverage for untrusted text in plain ledger views."""

import json
from contextlib import closing
from pathlib import Path

from hipson.ledger import Ledger
from hipson.redaction import REDACTION
from tests.test_hipson_helpers import run_cli_inprocess
from tests.test_ledger_findings import _result


def test_plain_ledger_actor_redaction_and_bounds(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    secret = "sk-" + "a" * 30
    actor = f"reviewer {secret}"
    long_actor = "worker " + "x" * 500 + secret
    with closing(Ledger(db)) as store:
        task = store.open_task(project=tmp_path, title="Review", objective="Check display", lead=actor)["id"]
        store.assign(task, actor=actor, role="reviewer", mode="ro")
        long_assignment = store.assign(task, actor=long_actor, role="reviewer", mode="ro")["id"]
        store.add_event(task, actor=actor, type="note", summary="Ready")
        expected = [store.show(task), store.board(), store.list_tasks(), store.events(task)]

    commands = (("show", "--task", task), ("board",), ("list",), ("events", "--task", task))
    for command, payload in zip(commands, expected, strict=True):
        rc, stdout, stderr = run_cli_inprocess("task", "--db", str(db), *command)
        assert rc == 0, stderr
        assert secret not in stdout and REDACTION in stdout
        if command[0] in ("show", "board"):
            assignment_line = next(line for line in stdout.splitlines() if long_assignment in line)
            displayed_actor = assignment_line.split(f"{long_assignment} ", 1)[1].split(":reviewer/ro", 1)[0]
            assert len(displayed_actor) == 220 and displayed_actor.endswith("[truncated to 220 chars]")
        rc, stdout, stderr = run_cli_inprocess("task", "--db", str(db), *command, "--json")
        assert rc == 0, stderr
        assert stdout == json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def test_plain_ledger_views_escape_worker_controls_and_preserve_json(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    unsafe = "text\x1b[2J\x1b[H\x00\x07\b\t\n\r\x7f\x85\x9b\u202e café"
    safe = r"text\u001b[2J\u001b[H\u0000\u0007\b\t\n\r\u007f\u0085\u009b\u202e café"
    report = _result()
    report["summary"] = f"summary {unsafe}"
    report["findings"] = [{"severity": "minor", "title": f"title {unsafe}",
                           "detail": f"detail {unsafe}", "location": f"location {unsafe}"}]
    with closing(Ledger(db)) as store:
        task = store.open_task(project=tmp_path, title=f"task {unsafe}", objective=f"objective {unsafe}",
                               lead=f"lead {unsafe}")["id"]
        assignment = store.assign(task, actor=f"reviewer {unsafe}", role="reviewer", mode="ro")["id"]
        store.finish(assignment, result="passed", summary="Reviewed", worker_result=report)
        store.assign(task, actor=f"active {unsafe}", role="reviewer", mode="ro")
        cursor = store.events(task)[-1]["seq"]
        store.add_event(task, actor=f"actor {unsafe}", type="note", summary=f"event {unsafe}")
        cases = (
            (("show", "--task", task), store.show(task),
             (f"reviewer {safe}", f"summary {safe}", f"title {safe}", f"location {safe}")),
            (("board",), store.board(), (f"active {safe}", r"task text\u001b[2J\u001b[H")),
            (("list",), store.list_tasks(), (f"task {safe}", f"objective {safe}", f"lead {safe}")),
            (("events", "--task", task, "--after", str(cursor)), store.events(task, after=cursor),
             (f"actor {safe}", f"event {safe}")),
            (("finding", "list", "--task", task), store.findings(task=task),
             (f"title {safe}", f"detail {safe}", f"location {safe}")),
        )

    for command, payload, fragments in cases:
        rc, stdout, stderr = run_cli_inprocess("task", "--db", str(db), *command)
        assert rc == 0, stderr
        assert all(char.isprintable() or char == "\n" for char in stdout), repr(stdout)
        for fragment in fragments:
            assert fragment in stdout, (command, fragment, stdout)
        rc, stdout, stderr = run_cli_inprocess("task", "--db", str(db), *command, "--json")
        assert rc == 0, stderr
        assert stdout == json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
