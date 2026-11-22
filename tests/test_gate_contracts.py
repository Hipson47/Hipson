"""Exact contracts of the review/done gates: verification_state, close_task and status transitions.

These run in-process with a fixed project state, so they are fast enough for mutation testing of the ledger core.
"""

import hashlib
import itertools
import json
import shlex
from contextlib import closing
from unittest.mock import patch

import pytest

from hipson import ledger, task_verification
from hipson.task_verification import argv_identity, verification_state

LEAD = "lead"
CONTEXT = {"role": None, "assignment": None, "task": None}
STATE = {"revision": "a" * 40, "subject": "s", "fingerprint": "f" * 64, "dirty": False, "error": None,
         "files": [{"path": "x.py", "state": "100644:" + "1" * 64, "index": None, "tracked": True}],
         "generated_files": []}
CHECK = ["pytest", "-q"]


def _record(**overrides):
    record = {"id": "V-" + "1" * 32, "actor": LEAD, "context": dict(CONTEXT), "exit_code": 0, "command": CHECK,
              "argv_sha256": argv_identity(CHECK), "builder_finished_seq": 5, "state_changed": False,
              "cleanup_confirmed": True, "coverage": "complete", "metadata_fallback_paths": [], **STATE}
    record.update(overrides)
    return record


def _state(records, current=STATE, seq=5, checks=None):
    return verification_state(records, current, seq, LEAD, checks)


def test_a_fresh_passing_record_is_accepted_without_and_with_declared_checks():
    for checks in (None, [CHECK]):
        state = _state([_record()], checks=checks)
        assert state["passing"] and state["freshness"] == "current" and state["reasons"] == []
        assert state["accepted"] == "V-" + "1" * 32 and state["accepted_ids"] == ["V-" + "1" * 32]
    assert _state([_record()], checks=[CHECK])["checks"] == [{
        "command": CHECK, "record": "V-" + "1" * 32, "passing": True, "freshness": "current", "reasons": []}]
    assert _state([_record()])["checks"] == []


def test_every_refusal_reason_is_exact():
    cases = [
        (_record(actor="worker"), "verification was not recorded by the task lead in a lead context", "unknown"),
        (_record(context={"role": "worker", "assignment": None, "task": None}),
         "verification was not recorded by the task lead in a lead context", "unknown"),
        (_record(context={"role": None}), "verification was not recorded by the task lead in a lead context", "unknown"),
        (_record(exit_code=2), "failing verification: exit 2", "failing"),
        (_record(builder_finished_seq=4), "stale: verification predates latest finished builder assignment", "stale"),
        (_record(revision="b" * 40), f"stale: HEAD moved; before={'b' * 40} after={'a' * 40}", "stale"),
        (_record(cleanup_confirmed=False), "verification process cleanup was not confirmed", "unknown"),
        (_record(state_changed=True, changes=["x.py: a", "y.py: b"]),
         "stale: project changed during verification: x.py: a; y.py: b", "stale"),
        (_record(state_changed=True), "stale: project changed during verification or cleanup was not confirmed", "stale"),
        (_record(coverage="partial"), "partial content coverage (metadata fallback): ", "partial"),
        (_record(metadata_fallback_paths=["big.bin"]), "partial content coverage (metadata fallback): big.bin", "partial"),
        (_record(fingerprint=None, error="boom"), "unavailable working-tree fingerprint: boom", "unknown"),
    ]
    for record, reason, freshness in cases:
        state = _state([record])
        assert state["reasons"] == [reason] and not state["passing"], (record, state["reasons"])
        assert state["freshness"] == freshness and state["accepted"] is None and state["accepted_ids"] == []


def test_stale_working_tree_is_reported_per_file_or_by_fingerprint_for_legacy_records():
    changed = {**STATE, "fingerprint": "e" * 64,
               "files": [{"path": "x.py", "state": "100644:" + "2" * 64, "index": None, "tracked": True}]}
    assert _state([_record()], current=changed)["reasons"] == [
        f"stale: working tree changed since verification: x.py: state before=100644:{'1' * 64} "
        f"after=100644:{'2' * 64}"]
    legacy = {key: value for key, value in _record().items() if key != "files"}
    assert _state([legacy], current=changed)["reasons"] == [
        f"stale: working tree changed; before={'f' * 64} after={'e' * 64}"]
    assert _state([legacy])["passing"]  # A legacy record with the same fingerprint stays fresh.
    many = {**STATE, "files": [{"path": f"f{i:02}.py", "state": "100644:" + "3" * 64, "index": None, "tracked": True}
                               for i in range(22)]}
    reason = _state([_record()], current=many)["reasons"][0]
    assert reason.count("state before=") == 20 and reason.endswith("; 3 more changed paths")


def test_missing_verifications_and_per_check_selection():
    assert _state([]) == {**_state([]), "freshness": "missing", "passing": False,
                          "reasons": ["missing lead verification; run hipson task verify"], "latest": None}
    other = ["ruff", "check"]
    state = _state([_record()], checks=[CHECK, other])
    assert not state["passing"] and state["freshness"] == "missing"
    assert state["reasons"] == ["check `ruff check`: missing: never verified; run hipson task verify "
                                "(no command runs all checks)"]
    passed = _record(id="V-" + "2" * 32)
    failed = _record(id="V-" + "3" * 32, exit_code=1)
    assert not _state([passed, failed], checks=[CHECK])["passing"]  # The latest run of the check decides.
    assert _state([failed, passed], checks=[CHECK])["accepted_ids"] == ["V-" + "2" * 32]
    shown_only = _record(argv_sha256=argv_identity(["pytest"]))  # Same display, different full argv.
    assert not _state([shown_only], checks=[CHECK])["passing"]


def test_argv_identity_is_the_sha256_of_ascii_json_including_non_ascii_arguments():
    argv = ["pytest", "-k", "zażółć"]
    expected = hashlib.sha256(json.dumps(argv, ensure_ascii=True).encode("ascii")).hexdigest()
    assert argv_identity(argv) == expected and argv_identity(["pytest", "-k", "zazolc"]) != expected


def _store(tmp_path):
    store = ledger.Ledger(tmp_path / "ledger.sqlite")
    task = store.open_task(project=tmp_path, title="Gate", objective="Gate contracts", lead=LEAD)["id"]
    return store, task


def _verified(store, task):
    seq = store._builder_finished_seq(task)
    store._record_verification(_record(id="V-" + "9" * 32, task=task, builder_finished_seq=seq))


def _error(fn, message, code, error=ledger.LedgerError):
    with pytest.raises(error) as refusal:
        fn()
    assert message in str(refusal.value)
    assert refusal.value.code == code
    return refusal.value


def _exact(fn, message, code, error=ledger.LedgerError):
    refusal = _error(fn, message, code, error)
    assert str(refusal) == message
    return refusal


def _assert_gate_refusal(refusal, reason_codes, *, fix=()):
    assert refusal.code == reason_codes[0]
    assert refusal.reason_codes == reason_codes
    assert refusal.fix == list(fix)


def _activity_fix(assignment, db):
    return {
        "tool": "task.activity",
        "arguments": {"assignment": assignment},
        "cli": f"hipson task activity --assignment {assignment} --db {shlex.quote(str(db))} --json",
    }


def _fix(tool, args, flags, db, suffix=""):
    command = {"task.findings": "task finding list", "task.finding_update": "task finding update"}
    cli = f"hipson {command.get(tool, tool.replace('.', ' '))}{flags} --db {shlex.quote(str(db))} --json{suffix}"
    return {"tool": tool, "arguments": args, "cli": cli}


def _verify_fix(task, db, *, command=None, explicit=False):
    args = {"task": task, "actor": LEAD, "approved": True}
    if command:
        args["command"] = command
    suffix = " -- " + shlex.join(command) if command else " -- COMMAND" if explicit else ""
    return _fix("task.verify", args, f" --task {task} --actor {LEAD}", db, suffix)


def _ack_fixes(assignment, db):
    return [_activity_fix(assignment, db),
            _fix("task.ack", {"assignment": assignment, "actor": LEAD, "approved": True},
                 f" --assignment {assignment} --actor {LEAD}", db)]


def _finding_fixes(task, finding, db):
    return [_fix("task.findings", {"task": task, "status": "open"}, f" --task {task} --status open", db),
            _fix("task.finding_update", {"finding": finding, "actor": LEAD, "approved": True},
                 f" --finding {finding} --actor {LEAD}", db, " --status STATUS")]


def _transition_fixes(task, db, targets):
    return [_fix("task.show", {"task": task, "detail": "compact"}, f" --task {task} --detail compact", db),
            *(_fix("task.status", {"task": task, "to": to, "actor": LEAD, "approved": True},
                   f" --task {task} --to {to} --actor {LEAD}", db) for to in targets)]


def test_close_task_from_open_records_every_step_without_overrides(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        store, task = _store(tmp_path)
        with closing(store):
            _verified(store, task)
            view = store.close_task(task, actor=LEAD, summary="all green")
            assert view["status"] == "done"
            events = store.events(task)
            statuses = [event["summary"] for event in events if event["type"] == "status"]
            assert [summary.split(";")[0] for summary in statuses] == [
                "started by close", "all green", "review -> done: all green"]
            assert not any(event["type"].endswith("_override") for event in events)
            assert all(event["actor"] == LEAD for event in events if event["type"] == "status")


def test_close_to_review_ignores_done_only_blockers_but_not_active_work(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        store, task = _store(tmp_path)
        with closing(store):
            store.set_status(task, to="in_progress", actor=LEAD)
            done = store.assign(task, actor="w", role="reviewer", mode="ro")["id"]
            store.finish(done, result="passed", summary="unread")
            major = store.add_finding(task, actor="r", severity="major", title="Open major")["id"]
            _verified(store, task)
            assert store.close_task(task, to="review", actor=LEAD)["status"] == "review"
            refusal = _error(lambda: store.close_task(task, actor=LEAD), "unacknowledged result",
                             "close.results_unacknowledged", ledger.CloseRefusedError)
            assert str(refusal).startswith(f"Cannot close {task}: ") and len(refusal.reasons) == 2
            _assert_gate_refusal(refusal, ["close.results_unacknowledged", "close.findings_open"],
                                 fix=_ack_fixes(done, store.path) + _finding_fixes(task, major, store.path))
            refusal = _error(lambda: store.close_task(task, to="review", actor=LEAD),
                             "close works from open, in_progress or review",
                             "close.status_not_allowed", ledger.CloseRefusedError)
            _assert_gate_refusal(refusal, ["close.status_not_allowed"])
            _error(lambda: store.close_task(task, to="cancelled", actor=LEAD), "Close target must be review or done",
                   "close.target_invalid")
            _error(lambda: store.close_task(task, actor=" "), "actor must not be empty", "input.text_required")


def test_done_lists_remaining_minor_findings_and_blocks_on_severe_ones(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        store, task = _store(tmp_path)
        with closing(store):
            store.set_status(task, to="in_progress", actor=LEAD)
            minor = store.add_finding(task, actor="r", severity="minor", title="Small nit")["id"]
            info = store.add_finding(task, actor="r", severity="info", title="FYI")["id"]
            _verified(store, task)
            store.set_status(task, to="review", actor=LEAD)
            blocking = store.add_finding(task, actor="r", severity="blocking", title="Stop")["id"]
            refusal = _error(lambda: store.set_status(task, to="done", actor=LEAD),
                             f"open blocking finding {blocking}: Stop", "close.findings_open", ledger.GateRefusedError)
            _assert_gate_refusal(refusal, ["close.findings_open"], fix=_finding_fixes(task, blocking, store.path))
            store.update_finding(blocking, actor=LEAD, status="fixed", resolution_note="done")
            view = store.set_status(task, to="done", actor=LEAD, summary="ship")
            event = store.events(task)[-1]
            assert view["status"] == "done" and event["summary"].startswith("review -> done: ship")
            assert f"remaining minor/info findings: {minor} [minor]: Small nit; {info} [info]: FYI" in event["summary"]
            assert minor in event["refs"] and info in event["refs"] and "V-" + "9" * 32 in event["refs"]


def test_invalid_transitions_name_allowed_targets_and_the_close_hint(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        store, task = _store(tmp_path)
        with closing(store):
            refusal = _error(lambda: store.set_status(task, to="done", actor="invoker"),
                   f"Invalid task transition for {task}: open -> done (allowed: cancelled, in_progress); "
                   f"hipson task close --task {task} verifies and closes in one step", "status.transition_not_allowed")
            assert refusal.fix == _transition_fixes(task, store.path, ["in_progress"])
            refusal = _error(lambda: store.set_status(task, to="review", actor=LEAD),
                   f"Invalid task transition for {task}: open -> review (allowed: cancelled, in_progress)",
                   "status.transition_not_allowed")
            assert refusal.fix == _transition_fixes(task, store.path, ["in_progress"])
            store.set_status(task, to="cancelled", actor=LEAD, summary="dropped")
            assert store.events(task)[-1]["summary"] == "open -> cancelled: dropped"
            refusal = _error(lambda: store.set_status(task, to="open", actor=LEAD), "(allowed: none (terminal))",
                             "status.transition_not_allowed")
            assert refusal.fix == _transition_fixes(task, store.path, [])


def test_close_task_refuses_unverified_work_with_every_reason_and_closes_from_review(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        store, task = _store(tmp_path)
        with closing(store):
            store.set_status(task, to="in_progress", actor=LEAD)
            active = store.assign(task, actor="w", role="builder", mode="ro")["id"]
            with pytest.raises(ledger.CloseRefusedError) as refusal:
                store.close_task(task, actor=LEAD)
            assert refusal.value.reasons == ["missing lead verification; run hipson task verify",
                                             f"active assignment {active}: wait for it, or hipson task reap --task {task}"]
            _assert_gate_refusal(refusal.value, ["verify.lead_missing", "close.assignment_active"],
                                 fix=[_verify_fix(task, store.path, explicit=True)])
            with pytest.raises(ledger.CloseRefusedError) as refusal:
                store.close_task(task, to="review", actor=LEAD)
            assert refusal.value.reasons[-1].startswith(f"active assignment {active}")
            _assert_gate_refusal(refusal.value, ["verify.lead_missing", "close.assignment_active"],
                                 fix=[_verify_fix(task, store.path, explicit=True)])
            store.finish(active, result="passed", summary="ok")
            store.ack(active, note="read")
            _verified(store, task)
            assert store.close_task(task, to="review", actor=LEAD)["status"] == "review"
            with pytest.raises(ledger.CloseRefusedError) as refusal:
                store.close_task(task, to="review", actor=LEAD)
            assert refusal.value.reasons == [
                f"{task} is review; close works from open, in_progress or review (use --to done)"]
            _assert_gate_refusal(refusal.value, ["close.status_not_allowed"])
            assert store.close_task(task, actor=LEAD)["status"] == "done"  # From review, one transition only.
            assert [event["summary"].split(";")[0] for event in store.events(task) if event["type"] == "status"][-2:] == [
                "in_progress -> review", "review -> done"]
            with pytest.raises(ledger.CloseRefusedError) as refusal:
                store.close_task(task, actor=LEAD)
            assert refusal.value.reasons == [f"{task} is done; close works from open, in_progress or review"]
            _assert_gate_refusal(refusal.value, ["close.status_not_allowed"])


def test_done_blockers_exact_texts(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        store, task = _store(tmp_path)
        with closing(store):
            store.set_status(task, to="in_progress", actor=LEAD)
            live = store.assign(task, actor="w", role="builder", mode="rw", scope=["src"])["id"]
            ended = store.assign(task, actor="v", role="reviewer", mode="ro")["id"]
            store.finish(ended, result="passed", summary="x")
            blocking = store.add_finding(task, actor="r", severity="blocking", title="B")["id"]
            store.add_finding(task, actor="r", severity="minor", title="m")
            view = store.show(task)
            assert ledger.done_blockers(view) == [
                f"active assignment {live}: wait for it, or hipson task reap --task {task}",
                f"unacknowledged result {ended}: read it (hipson task show --task {task}), then "
                f"hipson task ack --assignment {ended}",
                f"open blocking finding {blocking}: fix it, then hipson task finding update --finding {blocking} "
                f"--to fixed --actor {LEAD}",
            ]
            stale = {**view, "assignments": [item for item in view["assignments"] if item["id"] != live]}
            assert f"held claims: hipson task reap --task {task}" in ledger.done_blockers(stale)
            assert not any(item.startswith("held claims") for item in ledger.done_blockers(view))


def test_validate_checks_limits_are_exact():
    assert ledger.validate_checks([["c", str(index)] for index in range(16)]) == [["c", str(i)] for i in range(16)]
    _error(lambda: ledger.validate_checks([["c", str(index)] for index in range(17)]),
           "Acceptance checks must be a list of at most 16 argv lists", "checks.list_invalid")
    _error(lambda: ledger.validate_checks("pytest"), "Acceptance checks must be a list of at most 16 argv lists",
           "checks.list_invalid")
    assert ledger.validate_checks([["c"] * 64]) == [["c"] * 64]
    for bad in ([["c"] * 65], [[]], ["pytest -q"], [None]):
        _error(lambda bad=bad: ledger.validate_checks(bad),
               "Each acceptance check must be a nonempty argv list of at most 64 items", "checks.argv_invalid")
    assert ledger.validate_checks([["c", "x" * 4096]]) == [["c", "x" * 4096]]
    for bad in ("x" * 4097, "a\x1fb", "a\x7fb", 3):
        _error(lambda bad=bad: ledger.validate_checks([["c", bad]]),
               "Acceptance check arguments must be strings of at most 4096 characters without control characters",
               "checks.argument_invalid")
    assert ledger.validate_checks([["c", "a b", "\x80"]]) == [["c", "a b", "\x80"]]  # Space and 0x80 are fine.
    _error(lambda: ledger.validate_checks([[" ", "pytest"]]), "An acceptance check needs a command name",
           "checks.command_required")
    assert ledger.validate_checks([["a", " "]]) == [["a", " "]]
    _error(lambda: ledger.validate_checks([["c", "token=ghp_" + "a" * 36]]),
           "Acceptance checks must not contain secret-shaped text; pass secrets via the environment",
           "checks.argument_sensitive")
    assert ledger.validate_checks([["a"], ["b"], ["a"]]) == [["a"], ["b"]]
    assert ledger.validate_checks([("a", "b")]) == [["a", "b"]]


def test_set_checks_contract(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        store, task = _store(tmp_path)
        with closing(store):
            view = store.set_checks(task, checks=[CHECK], actor=LEAD)
            assert view["checks"] == [CHECK]
            assert store.events(task)[-1]["summary"] == "acceptance checks: pytest -q"
            _error(lambda: store.set_checks(task, checks=[], actor=LEAD),
                   f"Replacing declared acceptance checks of {task} requires --reason", "checks.replace_reason_required")
            _error(lambda: store.set_checks(task, checks=[], actor=LEAD, reason="  "), "requires --reason",
                   "checks.replace_reason_required")
            store.set_checks(task, checks=[], actor=LEAD, reason=" manual ")
            event = store.events(task)[-1]
            assert event["type"] == "checks_set" and event["actor"] == LEAD
            assert event["summary"] == "acceptance checks: none declared; previously: pytest -q; reason: manual"
            store.set_checks(task, checks=[CHECK], actor=LEAD)  # Declaring on an empty list needs no reason.
            _error(lambda: store.set_checks(task, checks=[CHECK], actor=" "), "actor must not be empty",
                   "input.text_required")
            store.set_status(task, to="cancelled", actor=LEAD)
            _error(lambda: store.set_checks(task, checks=[], actor=LEAD, reason="x"),
                   f"Cannot change acceptance checks of cancelled task {task}", "checks.task_terminal")


def test_done_refusal_lists_every_blocker_and_force_records_an_exact_override(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        store, task = _store(tmp_path)
        with closing(store):
            store.set_status(task, to="in_progress", actor=LEAD)
            _verified(store, task)
            store.set_status(task, to="review", actor=LEAD)
            live = store.assign(task, actor="w", role="builder", mode="rw", scope=["src"])["id"]
            unread = store.assign(task, actor="v", role="reviewer", mode="ro")["id"]
            store.finish(unread, result="passed", summary="x")
            major = store.add_finding(task, actor="r", severity="major", title="M")["id"]
            refusal = _error(lambda: store.set_status(task, to="done", actor=LEAD),
                             f"Cannot mark {task} done: active assignment {live} (rw); claim {live}:src; "
                             f"open major finding {major}: M; unacknowledged result {unread}",
                             "close.assignment_active", ledger.GateRefusedError)
            _assert_gate_refusal(refusal, ["close.assignment_active", "close.claims_held", "close.findings_open",
                                           "close.results_unacknowledged"], fix=_finding_fixes(task, major, store.path) + _ack_fixes(unread, store.path))
            _error(lambda: store.set_status(task, to="done", actor=LEAD, force=True), "Override reason must not be empty",
                   "input.text_required")
            view = store.set_status(task, to="done", actor=LEAD, force=True, reason="ship anyway")
            assert view["status"] == "done"
            override, status = store.events(task)[-2:]
            assert override["type"] == "done_override" and override["actor"] == LEAD
            assert override["summary"] == (f"Reason: ship anyway; blockers: active assignment {live} (rw); "
                                           f"claim {live}:src; open major finding {major}: M; "
                                           f"unacknowledged result {unread}")
            assert override["refs"] == [live, major, unread]
            assert status["type"] == "status" and status["summary"].startswith("review -> done")
            assert "remaining minor/info findings: none" in status["summary"]


def test_forced_review_without_verification_is_recorded_as_overridden(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        store, task = _store(tmp_path)
        with closing(store):
            store.set_status(task, to="in_progress", actor=LEAD)
            refusal = _error(lambda: store.set_status(task, to="review", actor=LEAD),
                             f"Cannot mark {task} review: missing lead verification; run hipson task verify; next: "
                             f"hipson task close --task {task} (runs declared checks), or hipson task verify --task "
                             f"{task} -- COMMAND (MCP: task.close / task.verify)",
                             "verify.lead_missing", ledger.GateRefusedError)
            _assert_gate_refusal(refusal, ["verify.lead_missing"], fix=[_verify_fix(task, store.path, explicit=True)])
            view = store.set_status(task, to="review", actor=LEAD, force=True, reason="manual check")
            assert view["status"] == "review"
            override, status = store.events(task)[-2:]
            assert override["type"] == "review_override" and override["refs"] == []
            assert override["summary"] == "Reason: manual check; blockers: missing lead verification; run hipson task verify"
            assert status["summary"] == ("in_progress -> review; lead verification: OVERRIDDEN: missing lead "
                                         "verification; run hipson task verify; acceptance checks: none declared")
            assert status["refs"] == []


def test_a_final_snapshot_change_inside_the_transaction_is_refused_or_audited(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)) as state:
        store, task = _store(tmp_path)
        with closing(store):
            store.set_status(task, to="in_progress", actor=LEAD)
            _verified(store, task)
            moved = {**STATE, "revision": "c" * 40}
            state.side_effect = itertools.chain([dict(STATE)], itertools.repeat(moved))  # Stale only in the final view.
            refusal = _error(lambda: store.set_status(task, to="review", actor=LEAD),
                             f"Cannot mark {task} review: stale: HEAD moved", "verify.head_moved", ledger.GateRefusedError)
            _assert_gate_refusal(refusal, ["verify.head_moved"], fix=[_verify_fix(task, store.path, command=CHECK)])
            assert store.show(task)["status"] == "in_progress"
            state.side_effect = itertools.chain([dict(STATE)], itertools.repeat(moved))
            view = store.set_status(task, to="review", actor=LEAD, force=True, reason="moved during close")
            assert view["status"] == "review"
            override, status = store.events(task)[-2:]
            assert override["type"] == "review_override"
            assert override["summary"].startswith("Reason: moved during close; final verification blockers: stale: HEAD moved")
            assert "final lead verification OVERRIDDEN: stale: HEAD moved" in status["summary"]
            assert "V-" + "9" * 32 not in status["refs"]


def test_per_check_freshness_and_messages_are_exact():
    failing = _state([_record(exit_code=1)], checks=[CHECK])
    assert failing["checks"][0]["freshness"] == "failing" and failing["freshness"] == "failing"
    assert failing["reasons"] == ["check `pytest -q`: failing verification: exit 1"]
    two = _state([_record(metadata_fallback_paths=["b.bin", "a.bin"])])
    assert two["reasons"] == ["partial content coverage (metadata fallback): a.bin; b.bin"]
    errored = _state([_record()], current={**STATE, "fingerprint": None, "error": "current broke"})
    assert errored["reasons"] == ["unavailable working-tree fingerprint: current broke"]
    unknown = _state([_record()], current={**STATE, "fingerprint": None, "error": None})
    assert unknown["reasons"] == ["unavailable working-tree fingerprint: unknown"]
    assert _state([_record()])["untrusted_fields"] == [
        "latest.actor", "latest.context", "latest.metadata_fallback_paths", "latest.command", "latest.label",
        "latest.subject", "latest.output_tail", "latest.files", "latest.generated_files", "latest.changes",
        "current.subject", "current.files", "current.generated_files"]
    assert _state([_record()])["current"] == STATE


def test_changes_compare_by_raw_path_key_in_display_order():
    def item(path, digit, key=None):
        entry = {"path": path, "state": "100644:" + digit * 64, "index": None, "tracked": True}
        return {**entry, "key": key} if key else entry
    before = {"files": [item("b.py", "1"), item("a.py", "1"), item("[REDACTED].txt", "1", key="k" * 64)]}
    after = {"files": [item("b.py", "2"), item("a.py", "2"), item("[REDACTED].txt", "1", key="k" * 64)]}
    assert task_verification._changes(before, after) == [
        f"a.py: state before=100644:{'1' * 64} after=100644:{'2' * 64}",
        f"b.py: state before=100644:{'1' * 64} after=100644:{'2' * 64}"]
    renamed = {"files": [item("[REDACTED].txt", "1", key="j" * 64)]}
    # Same display path, different raw paths: two changes (their relative order is not part of the contract).
    assert sorted(task_verification._changes({"files": [item("[REDACTED].txt", "1", key="k" * 64)]}, renamed)) == [
        "[REDACTED].txt: state before=100644:" + "1" * 64 + " after=None",
        "[REDACTED].txt: state before=None after=100644:" + "1" * 64]
    tag = {"files": [item("<b>.py", "1")]}
    assert task_verification._changes(tag, {"files": []}) == ["&lt;b&gt;.py: state before=100644:" + "1" * 64 + " after=None"]


def test_more_changed_paths_note_starts_after_twenty():
    def files(count, digit):
        return [{"path": f"f{i:02}.py", "state": "100644:" + digit * 64, "index": None, "tracked": True}
                for i in range(count)]
    for count, suffix in ((20, False), (21, True)):
        reason = _state([_record(files=files(count, "1"))], current={**STATE, "files": files(count, "2")})["reasons"][0]
        assert reason.endswith("; 1 more changed paths") is suffix and reason.count("state before=") == 20


def test_final_snapshot_is_rechecked_for_done_too_and_messages_are_exact(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)) as state:
        store, task = _store(tmp_path)
        with closing(store):
            store.set_status(task, to="in_progress", actor=LEAD)
            _verified(store, task)
            store.set_status(task, to="review", actor=LEAD)
            moved = {**STATE, "revision": "c" * 40}
            state.side_effect = itertools.chain([dict(STATE)], itertools.repeat(moved))
            refusal = _exact(lambda: store.set_status(task, to="done", actor=LEAD),
                             f"Cannot mark {task} done: stale: HEAD moved; before={'a' * 40} after={'c' * 40}",
                             "verify.head_moved", ledger.GateRefusedError)
            _assert_gate_refusal(refusal, ["verify.head_moved"], fix=[_verify_fix(task, store.path, command=CHECK)])
            assert store.show(task)["status"] == "review"
            state.side_effect = None
            state.return_value = dict(STATE)
            store.set_status(task, to="done", actor=LEAD)
            _exact(lambda: store.set_checks(task, checks=[], actor=LEAD, reason="x"),
                   f"Cannot change acceptance checks of done task {task}", "checks.task_terminal")
            _exact(lambda: store.close_task(task, to="cancelled", actor=LEAD), "Close target must be review or done",
                   "close.target_invalid")
            refusal = _exact(lambda: store.close_task(task, actor=LEAD),
                             f"Cannot close {task}: {task} is done; close works from open, in_progress or review",
                             "close.status_not_allowed", ledger.CloseRefusedError)
            _assert_gate_refusal(refusal, ["close.status_not_allowed"])
            _exact(lambda: ledger.validate_checks([[" "]]), "An acceptance check needs a command name",
                   "checks.command_required")
            _exact(lambda: ledger.validate_checks([["c", "token=ghp_" + "a" * 36]]),
                   "Acceptance checks must not contain secret-shaped text; pass secrets via the environment",
                   "checks.argument_sensitive")


def test_review_and_done_summaries_and_refs_are_exact(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        store, task = _store(tmp_path)
        with closing(store):
            store.set_checks(task, checks=[CHECK, ["ruff", "check"]], actor=LEAD)
            store.set_status(task, to="in_progress", actor=LEAD)
            seq = store._builder_finished_seq(task)
            store._record_verification(_record(id="V-" + "4" * 32, task=task, builder_finished_seq=seq))
            store._record_verification(_record(id="V-" + "5" * 32, task=task, builder_finished_seq=seq,
                                               command=["ruff", "check"], argv_sha256=argv_identity(["ruff", "check"])))
            view = store.set_status(task, to="review", actor=LEAD)
            event = store.events(task)[-1]
            assert event["summary"] == (f"in_progress -> review; lead verification: V-{'4' * 32}, V-{'5' * 32}; "
                                        "acceptance checks: pytest -q; ruff check")
            assert event["refs"] == [f"V-{'4' * 32}", f"V-{'5' * 32}"]
            assert view["revision"] == store.show(task)["revision"] and view["updated_at"] == store.show(task)["updated_at"]
            store.set_status(task, to="cancelled", actor=LEAD)
            assert store.events(task)[-1]["summary"] == "review -> cancelled"


# Change observation records (1.7 slice 1): exact contracts so the ledger-core mutation gate covers them.
def _changes(status="changes", **extra):
    counts = {group: {"created": 0, "modified": 0, "deleted": 0} for group in ("in_scope", "out_of_scope", "unattributed")}
    return {"status": status, "counts": counts, "paths": [], "omitted": 0, "truncated": False, **extra}


def _record_changes_contract(tmp_path):
    store, task = _store(tmp_path)
    with closing(store):
        store.set_status(task, to="in_progress", actor=LEAD)
        assignment = store.assign(task, actor="w", role="builder", mode="rw", scope=["src"])["id"]
        for bad in ({"status": "fine"}, ["status"], {}):
            _exact(lambda bad=bad: store.record_changes(assignment, bad),
                   "Change observation must be an object with a known status", "observation.status_invalid")
        _exact(lambda: store.record_changes(assignment, _changes()), f"Assignment {assignment} is still active",
               "assignment.active")
        store.finish(assignment, result="passed", summary="ok")
        record = _changes(paths=[{"path": f"p{index}", "change": "created", "attribution": "out_of_scope",
                                  "evidence": "presence"} for index in range(22)], omitted=3)
        record["counts"]["out_of_scope"]["created"] = 22
        record["counts"]["unattributed"]["modified"] = 2
        view = store.record_changes(assignment, record, [
            {"title": "Outside", "detail": "d", "location": "p0"}, {"title": "Second", "detail": "e", "location": ""}])
        assert view["changes"] == {**record, "incomplete_reasons": []}  # Normalized on read.
        found = store.findings(task=task)
        assert [(item["severity"], item["title"], item["source_assignment"])
                for item in found] == [("major", "Outside", assignment), ("major", "Second", assignment)]
        event = store.events(task)[-1]
        listed = "; ".join(f"out_of_scope created p{index}" for index in range(20))
        assert (event["type"], event["actor"]) == ("observed", "hipson")
        assert event["summary"] == (f"{assignment}: changes changes; 22 outside scope, 2 unattributed; paths: {listed}"
                                    f" (+5 more in task show --json); findings {found[0]['id']}, {found[1]['id']}")
        assert event["refs"] == [assignment, found[0]["id"], found[1]["id"]]
        _exact(lambda: store.record_changes(assignment, _changes()),
               f"Assignment {assignment} already has a change observation", "observation.already_recorded")
        other = store.assign(task, actor="w", role="reviewer", mode="ro", scope=[])["id"]
        store.finish(other, result="passed", summary="ok")
        store.record_changes(other, _changes("incomplete"))
        assert store.events(task)[-1]["summary"] == (
            f"{other}: changes incomplete; 0 outside scope, 0 unattributed; incomplete, not a clean result")
        third = store.assign(task, actor="w", role="reviewer", mode="ro", scope=[])["id"]
        store.finish(third, result="passed", summary="ok")
        clean = _changes("clean")
        clean["counts"]["ignored"] = {"created": 0, "modified": 0, "deleted": 0}
        store.record_changes(third, clean)
        assert store.events(task)[-1]["summary"] == f"{third}: changes clean; 0 outside scope, 0 unattributed"
        assert store.events(task)[-1]["refs"] == [third]
        fourth = store.assign(task, actor="w", role="reviewer", mode="ro", scope=[])["id"]
        store.finish(fourth, result="passed", summary="ok")
        ignored = _changes(paths=[{"path": "report.html", "change": "created", "attribution": "ignored",
                                   "evidence": "presence"}], omitted=5)
        ignored["counts"]["ignored"] = {"created": 1, "modified": 2, "deleted": 3}
        store.record_changes(fourth, ignored)
        assert store.events(task)[-1]["summary"] == (
            f"{fourth}: changes changes; 0 outside scope, 0 unattributed, 6 ignored; "
            "paths: ignored created report.html (+5 more in task show --json)")
        assert store.events(task)[-1]["refs"] == [fourth]


def _overlapping_writers_contract(tmp_path):
    store, task = _store(tmp_path)
    with closing(store):
        elsewhere = store.open_task(project=tmp_path / "other", title="Other", objective="Other project", lead=LEAD)["id"]
        store.set_status(task, to="in_progress", actor=LEAD)
        store.set_status(elsewhere, to="in_progress", actor=LEAD)
        mine = store.assign(task, actor="w", role="builder", mode="rw", scope=["src"])["id"]
        before = store.assign(task, actor="w", role="builder", mode="rw", scope=["a"])["id"]
        during = store.assign(task, actor="w", role="builder", mode="rw", scope=["b"])["id"]
        after = store.assign(task, actor="w", role="builder", mode="rw", scope=["c"])["id"]
        reader = store.assign(task, actor="w", role="reviewer", mode="ro", scope=["d"])["id"]
        foreign = store.assign(elsewhere, actor="w", role="builder", mode="rw", scope=["e"])["id"]
        live = store.assign(task, actor="w", role="builder", mode="rw", scope=["f"])["id"]
        for item in (mine, before, during, after, reader, foreign):
            store.finish(item, result="passed", summary="ok")
        stamps = {mine: ("2026-01-01T00:00:10.000000Z", "2026-01-01T00:00:20.000000Z"),
                  before: ("2026-01-01T00:00:01.000000Z", "2026-01-01T00:00:10.000000Z"),  # Ends exactly at start.
                  during: ("2026-01-01T00:00:05.000000Z", "2026-01-01T00:00:11.000000Z"),
                  after: ("2026-01-01T00:00:20.000000Z", "2026-01-01T00:00:30.000000Z"),  # Starts exactly at end.
                  reader: ("2026-01-01T00:00:12.000000Z", "2026-01-01T00:00:13.000000Z"),
                  foreign: ("2026-01-01T00:00:12.000000Z", "2026-01-01T00:00:13.000000Z")}
        for item, (start, end) in stamps.items():
            store.connection.execute("UPDATE assignments SET started_at = ?, ended_at = ? WHERE id = ?", (start, end, item))
        store.connection.execute("UPDATE assignments SET started_at = ? WHERE id = ?",
                                 ("2026-01-01T00:00:15.000000Z", live))
        assert store.overlapping_writers(mine) == [(during, ["b"]), (live, ["f"])]
        assert store.overlapping_writers(mine, until="2026-01-01T00:00:15.000000Z") == [(during, ["b"])]
        assert store.overlapping_writers(mine, until="2026-01-01T00:00:31.000000Z") == [
            (during, ["b"]), (after, ["c"]), (live, ["f"])]


def test_legacy_change_records_gain_empty_incomplete_reasons():
    assert ledger._changes('{"status": "clean"}') == {"status": "clean", "incomplete_reasons": []}
    assert ledger._changes('{"status": "clean", "incomplete_reasons": ["x"]}')["incomplete_reasons"] == ["x"]
    assert ledger._changes("[1]") == [1]


def test_record_changes_exact_contract(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):  # In-process like the rest.
        _record_changes_contract(tmp_path)


def test_overlapping_writers_exact_window_project_and_mode(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        _overlapping_writers_contract(tmp_path)


def _minimal_records_contract(tmp_path):
    store, task = _store(tmp_path)
    with closing(store):
        store.set_status(task, to="in_progress", actor=LEAD)
        first = store.assign(task, actor="w", role="reviewer", mode="ro", scope=[])["id"]
        store.finish(first, result="passed", summary="ok")
        # Optional fields may be absent: counts, paths and omitted default to empty/zero.
        store.record_changes(first, {"status": "unobserved"})
        assert store.events(task)[-1]["summary"] == f"{first}: changes unobserved; 0 outside scope, 0 unattributed"
        second = store.assign(task, actor="w", role="builder", mode="rw", scope=["src"])["id"]
        store.finish(second, result="passed", summary="ok")
        store.record_changes(second, {"status": "changes", "paths": [
            {"path": "x", "change": "created", "attribution": "in_scope", "evidence": "presence"}]},
            [{"title": "T", "detail": "", "location": ""}])
        opened = [event for event in store.events(task) if event["type"] == "finding_opened"]
        assert [event["actor"] for event in opened] == ["hipson"]
        assert store.events(task)[-1]["summary"] == (
            f"{second}: changes changes; 0 outside scope, 0 unattributed; paths: in_scope created x; findings "
            f"{opened[0]['refs'][0]}")


def test_minimal_change_records_exact_contract(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        _minimal_records_contract(tmp_path)


def _environment_contract(tmp_path):
    store, task = _store(tmp_path)
    with closing(store):
        minimal = {"mode": "minimal", "names": ["HOME", "PATH"], "allowed": ["GH_TOKEN"], "missing": ["GH_TOKEN"],
                   "dropped": 4}
        first = store.assign(task, actor="w", role="explorer", mode="ro", environment=minimal)
        assert first["environment"] == minimal and store.activity(first["id"])["environment"] == minimal
        stored = store.connection.execute("SELECT environment FROM assignments WHERE id = ?", (first["id"],)).fetchone()[0]
        assert json.loads(stored) == minimal
        inherit = store.assign(task, actor="w", role="explorer", mode="ro", environment={"mode": "inherit", "count": 3})
        assert inherit["environment"] == {"mode": "inherit", "count": 3}
        plain = store.assign(task, actor="w", role="explorer", mode="ro")
        assert plain["environment"] is None
        assert store.connection.execute("SELECT environment FROM assignments WHERE id = ?",
                                        (plain["id"],)).fetchone()[0] is None
        for bad in ({"mode": "all"}, {"mode": None}, {}, ["minimal"], "minimal"):
            _error(lambda bad=bad: store.assign(task, actor="w", role="explorer", mode="ro", environment=bad),
                   "environment must be an object with mode inherit or minimal", "assignment.environment_invalid")
        # A refused record creates nothing.
        assert len(store.show(task)["assignments"]) == 3
        secret = "sk-ant-api03-" + "b" * 40
        redacted = store.assign(task, actor="w", role="explorer", mode="ro",
                                environment={"mode": "minimal", "names": [secret], "allowed": [], "missing": [],
                                             "dropped": 0})["environment"]
        assert secret not in json.dumps(redacted) and redacted["mode"] == "minimal" and redacted["dropped"] == 0
        assert secret not in store.connection.execute(
            "SELECT environment FROM assignments ORDER BY id DESC LIMIT 1").fetchone()[0]


def test_environment_records_exact_contract(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        _environment_contract(tmp_path)



def test_v9_schema_constants_are_exact():
    assert ledger.MIGRATION_V8_TO_V9 == ("ALTER TABLE assignments ADD COLUMN environment TEXT",
                                         "UPDATE schema_version SET version = 9 WHERE singleton = 1")


def test_v10_schema_constants_are_exact():
    assert ledger.MIGRATION_V9_TO_V10 == ("ALTER TABLE tasks ADD COLUMN needs TEXT NOT NULL DEFAULT '[]'",
                                          "ALTER TABLE assignments ADD COLUMN needs TEXT",
                                          "UPDATE schema_version SET version = 10 WHERE singleton = 1")
    assert ledger.MAX_NEEDS == 16 and "routed" in ledger.RESERVED_EVENT_TYPES


def test_validate_needs_exact_contract():
    assert ledger.validate_needs([]) == []
    assert ledger.validate_needs(["code_write", "web_research", "code_write"]) == ["code_write", "web_research"]
    assert ledger.validate_needs(["a" + "b" * 39]) == ["a" + "b" * 39]
    assert ledger.validate_needs(["a1_"]) == ["a1_"]
    assert ledger.validate_needs([f"t{index}" for index in range(16)]) == [f"t{index}" for index in range(16)]
    _error(lambda: ledger.validate_needs([f"t{index}" for index in range(17)]),
           "Capability tags must be a list of at most 16 names", "capability.tags_invalid")
    _error(lambda: ledger.validate_needs("code_write"), "Capability tags must be a list of at most 16 names",
           "capability.tags_invalid")
    for bad in ("Code", "1abc", "a" + "b" * 40, "a-b", "", "a b", 7):
        _error(lambda bad=bad: ledger.validate_needs([bad]),
               f"Capability tags are lower-case names such as code_write, not {bad!r}", "capability.tag_invalid")


def _needs_contract(tmp_path):
    store, task = _store(tmp_path)
    with closing(store):
        assert store.show(task)["needs"] == []
        tagged = store.open_task(project=tmp_path, title="T", objective="o", lead=LEAD,
                                 needs=["web_research", "code_write", "web_research"])
        assert tagged["needs"] == ["web_research", "code_write"]
        stored = store.connection.execute("SELECT needs FROM tasks WHERE id = ?", (tagged["id"],)).fetchone()[0]
        assert json.loads(stored) == ["web_research", "code_write"]
        inherited = store.assign(tagged["id"], actor="w", role="explorer", mode="ro")
        assert inherited["needs"] == ["web_research", "code_write"]
        own = store.assign(tagged["id"], actor="w", role="explorer", mode="ro", needs=["browser"])
        assert own["needs"] == ["browser"]
        none = store.assign(tagged["id"], actor="w", role="explorer", mode="ro", needs=[])
        assert none["needs"] == []
        untagged = store.assign(task, actor="w", role="explorer", mode="ro")
        assert untagged["needs"] == []
        _error(lambda: store.assign(task, actor="w", role="explorer", mode="ro", needs=["Bad"]),
               "Capability tags are lower-case names such as code_write, not 'Bad'", "capability.tag_invalid")
        _error(lambda: store.open_task(project=tmp_path, title="T", objective="o", needs=["Bad"]),
               "Capability tags are lower-case names such as code_write, not 'Bad'", "capability.tag_invalid")
        event = store.record_route(tagged["id"], actor=" hipson ", summary=" route builder/ro: x ",
                                   refs=["codex", "abc"])
        assert (event["type"], event["actor"], event["summary"], event["refs"], event["task"]) == (
            "routed", "hipson", "route builder/ro: x", ["codex", "abc"], tagged["id"])
        assert store.events(tagged["id"])[-1] == event
        assert store.record_route(tagged["id"], actor="hipson", summary="no refs")["refs"] == []
        secret = "sk-ant-api03-" + "c" * 40
        hidden = store.record_route(tagged["id"], actor="hipson", summary=f"route {secret}", refs=[secret])
        assert secret not in json.dumps(hidden) and hidden["summary"].startswith("route ")
        assert secret not in json.dumps(store.events(tagged["id"])[-1])
        refusal = _error(lambda: store.record_route("T-9999", actor="hipson", summary="x"),
                         "Task not found: T-9999", "task.not_found")
        db = shlex.quote(str(store.path))
        assert refusal.fix == [
            {"tool": "task.list", "arguments": {}, "cli": f"hipson task list --db {db} --json"},
            {"tool": "task.now", "arguments": {"detail": "compact"},
             "cli": f"hipson task now --detail compact --db {db} --json"},
        ]
        _error(lambda: store.record_route(tagged["id"], actor=" ", summary="x"), "actor must not be empty",
               "input.text_required")
        _error(lambda: store.record_route(tagged["id"], actor="hipson", summary=" "), "summary must not be empty",
               "input.text_required")


def test_capability_tags_and_routed_events_exact_contract(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        _needs_contract(tmp_path)


def test_case_aware_claims_and_projects_exact_contract():
    for platform, folds in (("win32", True), ("darwin", True), ("linux", False), ("freebsd", False)):
        with patch.object(ledger.sys, "platform", platform):
            assert ledger._claims_overlap("SRC/a", "src") is folds
            assert ledger._claims_overlap("src", "SRC/a") is folds
            assert ledger._claims_overlap("src/a", "src") is True and ledger._claims_overlap("docs", "src") is False
            assert ledger._claims_overlap(".", "anything") is True
            assert ledger._same_project("/w/Repo", "/w/repo") is folds
            assert ledger._same_project("/w/repo", "/w/repo") is True
            assert ledger._same_project("/w/repo", "/w/other") is False
            windows = platform == "win32"
            assert ledger._claims_overlap("src.", "src") is windows and ledger._claims_overlap("src /a", "src") is windows
            assert ledger._claim_key(".") == "." and ledger._claim_key("a./b .") == ("a/b" if windows else
                                                                                     "a./b ." if not folds else "a./b .")
            assert ledger._claims_overlap("src.x", "src") is False


def _checkpoint_events_contract(tmp_path):
    store, task = _store(tmp_path)
    with closing(store):
        other_task = store.open_task(project=tmp_path / "Other", title="O", objective="o", lead=LEAD)["id"]
        held = store.assign(task, actor="w", role="builder", mode="rw", scope=["src"])["id"]
        # Another project's claims never conflict, whatever the case rules.
        store.assign(other_task, actor="w", role="builder", mode="rw", scope=["src"])
        with patch.object(ledger.sys, "platform", "darwin"):
            same = store.open_task(project=str(tmp_path).upper(), title="S", objective="o", lead=LEAD)["id"]
            refusal = _error(lambda: store.assign(same, actor="w", role="builder", mode="rw", scope=["SRC/x"]),
                   f"Claim 'SRC/x' conflicts with 'src' held by assignment {held}", "claim.conflict",
                   ledger.ClaimConflictError)
        assert refusal.fix == [
            _fix("task.show", {"task": task, "section": "assignments"}, f" --task {task} --section assignments", store.path),
            _fix("task.reap", {"task": task, "dry_run": True, "approved": True}, f" --task {task} --dry-run", store.path),
        ]
        event = store.record_checkpoint(held, type="checkpointed", summary=" A: checkpoint of src ")
        assert (event["type"], event["actor"], event["summary"], event["refs"], event["task"]) == (
            "checkpointed", "hipson", "A: checkpoint of src", [held], task)
        assert store.record_checkpoint(held, type="restored", summary="restored")["type"] == "restored"
        secret = "sk-ant-api03-" + "d" * 40
        assert secret not in json.dumps(store.record_checkpoint(held, type="restored", summary=f"x {secret}"))
        _error(lambda: store.record_checkpoint(held, type="observed", summary="x"),
               "Checkpoint events are checkpointed or restored", "checkpoint.event_invalid")
        _error(lambda: store.record_checkpoint(held, type="checkpointed", summary=" "), "summary must not be empty",
               "input.text_required")
        _error(lambda: store.record_checkpoint("A-9999", type="restored", summary="x"), "Assignment not found: A-9999",
               "assignment.not_found")
        assert {"checkpointed", "restored"} <= ledger.RESERVED_EVENT_TYPES


def test_checkpoint_events_and_case_aware_assign_exact_contract(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        _checkpoint_events_contract(tmp_path)


def test_v11_schema_constants_are_exact():
    assert ledger.REVIEW_POLICIES == ("none", "required")
    assert ledger.MIGRATION_V10_TO_V11 == (
        "ALTER TABLE tasks ADD COLUMN review_policy TEXT NOT NULL DEFAULT 'none'"
        " CHECK (review_policy IN ('none', 'required'))",
        "UPDATE schema_version SET version = 11 WHERE singleton = 1")
    assert {"review_policy", "flow"} <= ledger.RESERVED_EVENT_TYPES


@patch.object(task_verification, "project_state", return_value=dict(STATE))
def test_v12_usage_record_and_migration_exact_contract(_project_state, tmp_path):
    assert ledger.SCHEMA_VERSION == 12
    assert ledger.MIGRATION_V11_TO_V12 == (
        "ALTER TABLE assignments ADD COLUMN usage TEXT",
        "UPDATE schema_version SET version = 12 WHERE singleton = 1",
    )
    assert ledger.MIGRATIONS[-1] == ledger.MIGRATION_V11_TO_V12
    store, task = _store(tmp_path)
    db = store.path
    start, end = "2026-11-22T12:00:00Z", "2026-11-22T12:00:03.125Z"
    reported = {"source": "agy_json", "reason": None, "tokens": {
        "input_tokens": 16, "output_tokens": 4, "cached_tokens": 8, "cache_write_tokens": None,
        "reasoning_tokens": None, "total_tokens": 20,
    }}
    assert ledger._assignment_usage(reported, end, start) == {**reported, "elapsed_seconds": 0.0}
    with closing(store):
        for report, expected in (
            (reported, {**reported, "elapsed_seconds": 3.125}),
            (None, {"source": None, "tokens": None, "reason": "not_an_agent_runtime", "elapsed_seconds": 3.125}),
        ):
            with patch.object(ledger, "_timestamp", return_value=start):
                assigned = store.assign(task, actor="w", role="explorer", mode="ro")
            assert assigned["usage"] is None
            with patch.object(ledger, "_timestamp", return_value=end):
                kwargs = {} if report is None else {"usage": report}
                finished = store.finish(assigned["id"], result="passed", summary="finished", **kwargs)
            assert finished["usage"] == expected
            assert finished["started_at"] == start and finished["ended_at"] == end
            stored = store.connection.execute("SELECT usage FROM assignments WHERE id = ?", (assigned["id"],))
            assert json.loads(stored.fetchone()[0]) == expected

        store.connection.execute("ALTER TABLE assignments DROP COLUMN usage")
        store.connection.execute("UPDATE schema_version SET version = 11")
    with closing(ledger.Ledger(db, read_only=True)) as old:
        assert old.activity(assigned["id"])["usage"] is None
    with closing(ledger.Ledger(db)) as migrated:
        assert migrated.connection.execute("SELECT version FROM schema_version").fetchone()[0] == 12
        assert migrated.activity(assigned["id"])["usage"] is None
    with closing(ledger.Ledger(db)) as reopened:
        assert reopened.connection.execute("SELECT version FROM schema_version").fetchone()[0] == 12


def _old_ledger(path):
    with closing(ledger.Ledger(path)) as store:
        store.open_task(project=path.parent, title="kept", objective="survives the backup")
        store.connection.execute("ALTER TABLE assignments DROP COLUMN usage")
        store.connection.execute("UPDATE schema_version SET version = 11")


def _names(directory):
    return sorted(item.name for item in directory.iterdir() if item.name.startswith(("ledger", ".ledger"))
                  and item.name not in ("ledger.sqlite-wal", "ledger.sqlite-shm"))


def test_migration_backup_appears_only_complete_and_never_replaces_one(tmp_path):
    import os
    import sqlite3
    import stat
    from datetime import UTC, datetime

    fixed = datetime(2026, 11, 22, 21, 0, 0, 7, tzinfo=UTC)
    stamp = "20261122T210000000007Z"

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is UTC
            return fixed

    def check_case(case, directory):
        real_connect = sqlite3.connect
        db = directory / "ledger.sqlite"
        _old_ledger(db)
        working = f".ledger.sqlite.v11-backup-{stamp}-{os.getpid()}.partial"
        earlier = [f"ledger.sqlite.v11-backup-{stamp}"] if case in ("taken", "no_links_taken") else []
        for name in earlier:
            (directory / name).write_bytes(b"earlier")
        seen = []

        class Watch(sqlite3.Connection):
            def backup(self, target, *args, **kwargs):
                seen.append(_names(directory))  # What exists while the copy runs.
                if case == "killed":
                    raise KeyboardInterrupt
                super().backup(target, *args, **kwargs)
                if case == "migrated_meanwhile":
                    target.execute("UPDATE schema_version SET version = 12")
                    target.commit()

        def connect(*args, **kwargs):
            return real_connect(*args, factory=Watch, **kwargs) if str(args[0]) == str(db) else real_connect(*args, **kwargs)

        links = patch.object(ledger.os, "link", side_effect=PermissionError("no hard links")) \
            if case.startswith("no_links") else patch.object(ledger.os, "link", ledger.os.link)
        with patch.object(ledger, "datetime", Clock), patch.object(ledger.sqlite3, "connect", side_effect=connect), links:
            if case == "killed":
                with pytest.raises(KeyboardInterrupt):
                    ledger.Ledger(db)
            else:
                ledger.Ledger(db).close()
        assert seen[0] == sorted(["ledger.sqlite", working, *earlier]), case  # No backup name before the copy ends.
        names = _names(directory)
        assert not any(name.endswith(".partial") or ".partial-" in name for name in names), (case, names)
        backups = [name for name in names if "-backup-" in name and name not in earlier]
        if case in ("killed", "migrated_meanwhile"):
            assert backups == [], case
            return
        expected = f"ledger.sqlite.v11-backup-{stamp}" + ("-1" if earlier else "")
        assert backups == [expected], (case, names)
        if earlier:
            assert (directory / earlier[0]).read_bytes() == b"earlier"
        if os.name == "posix":
            assert stat.S_IMODE((directory / expected).stat().st_mode) == 0o600
        with closing(real_connect(directory / expected)) as copy:
            assert copy.execute("SELECT version FROM schema_version").fetchone()[0] == 11
            assert [row[0] for row in copy.execute("SELECT title FROM tasks")] == ["kept"]

    for case in ["plain", "taken", "no_links", "no_links_taken", "killed", "migrated_meanwhile"]:
        directory = tmp_path / case
        directory.mkdir()
        check_case(case, directory)


def test_worker_results_findings_and_notes_persist_without_env_secrets_or_cut_key_blocks(tmp_path):
    import os
    import sqlite3

    cut = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAsecretbody"
    with patch.dict(os.environ, {"SVC_TOKEN": "zq9plainvalue77"}):
        store, task = _store(tmp_path)
        db = store.path
        with closing(store):
            result = ledger.validate_task_result({
                "status": "completed", "summary": "env zq9plainvalue77 and " + cut, "files_changed": [{"path": "a zq9plainvalue77", "change": "modified"}],
                "commands": [{"command": "echo zq9plainvalue77", "exit_code": 0}], "risks": ["key " + cut],
                "findings": [{"severity": "minor", "title": "t zq9plainvalue77", "detail": cut, "location": "x"}]})
            assert result["summary"] == "env [REDACTED] and [REDACTED]"
            assert result["files_changed"] == [{"path": "a [REDACTED]", "change": "modified"}] and result["risks"] == ["key [REDACTED]"]
            assert result["commands"][0]["command"] == "echo [REDACTED]"
            assert result["findings"][0]["title"] == "t [REDACTED]" and result["findings"][0]["detail"] == "[REDACTED]"
            assignment = store.assign(task, actor="w", role="reviewer", mode="ro")["id"]
            store.finish(assignment, result="passed", summary="done", worker_result=result)
            finding = store.add_finding(task, actor="w", severity="minor", title="title zq9plainvalue77",
                                        detail="detail " + cut, location="loc zq9plainvalue77")
            assert (finding["title"], finding["detail"], finding["location"]) == (
                "title [REDACTED]", "detail [REDACTED]", "loc [REDACTED]")
            updated = store.update_finding(finding["id"], actor="lead", status="wontfix",
                                           resolution_note="note zq9plainvalue77 " + cut)
            assert updated["resolution_note"] == "note [REDACTED] [REDACTED]"
            store.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        raw = db.read_bytes()
        assert b"zq9plainvalue77" not in raw and b"MIIEowIBAAKCAQEAsecretbody" not in raw
        with closing(sqlite3.connect(db)) as check:
            assert check.execute("SELECT count(*) FROM findings").fetchone()[0] == 2  # The result's own and the added one.


def _reviewer(id, actor="rev", *, mode="ro", status="passed", result="completed", role="reviewer"):
    return {"id": id, "actor": actor, "role": role, "mode": mode, "status": status,
            "result": None if result is None else {"status": result}}


REVIEW_TAIL = ("; run an independent read-only reviewer after the latest builder and lead verification "
               "(hipson task flow, or hipson task run --role reviewer --mode ro)")


def test_review_state_exact_contract():
    builder = _reviewer("A-0001", "Codex", mode="rw", role="builder")
    off = ledger.review_state("none", "lead", [builder], {}, 0, 0)
    assert off == {"required": False, "passing": True, "accepted": None, "current": None, "reasons": []}
    assert ledger.review_state("required", "lead", [builder], {"A-0001": 1}, 2, 3) == {
        "required": True, "passing": False, "accepted": None, "current": None,
        "reasons": ["review required: no reviewer assignment" + REVIEW_TAIL]}
    good = ledger.review_state("required", "lead", [builder, _reviewer("A-0002")], {"A-0001": 1, "A-0002": 4}, 2, 3)
    assert good == {"required": True, "passing": True, "accepted": "A-0002", "current": "A-0002", "reasons": []}
    cases = [
        ({"A-0002": 2}, _reviewer("A-0002"), "it started before the latest builder finished", None),
        ({"A-0002": 3}, _reviewer("A-0002"), "it started before the accepted lead verification, so it did not "
                                             "review the verified tree", None),
        ({"A-0002": 4}, _reviewer("A-0002", mode="rw"), "it is not read-only", "A-0002"),
        ({"A-0002": 4}, _reviewer("A-0002", "codex"), "actor codex is the lead or a builder of this task", "A-0002"),
        ({"A-0002": 4}, _reviewer("A-0002", "LEAD"), "actor LEAD is the lead or a builder of this task", "A-0002"),
        ({"A-0002": 4}, _reviewer("A-0002", status="failed"), "it is failed", "A-0002"),
        ({"A-0002": 4}, _reviewer("A-0002", status="active"), "it is active", "A-0002"),
        ({"A-0002": 4}, _reviewer("A-0002", result=None),
         "it has no completed structured result (task run --result-file)", "A-0002"),
        ({"A-0002": 4}, _reviewer("A-0002", result="partial"),
         "it has no completed structured result (task run --result-file)", "A-0002"),
        ({}, _reviewer("A-0002"), "it started before the latest builder finished", None),
    ]
    for seqs, review, problem, current in cases:
        state = ledger.review_state("required", "lead", [builder, review], {"A-0001": 1, **seqs}, 2, 3)
        assert state == {"required": True, "passing": False, "accepted": None, "current": current,
                         "reasons": [f"review required: latest review A-0002 does not qualify: {problem}" + REVIEW_TAIL]}
    # A later review that ran and did not complete blocks; an interrupted (cancelled) or still-active one does not
    # outvote the accepted review (active work blocks done elsewhere).
    for status, result, blocks in (("failed", "completed", True), ("passed", "partial", True), ("passed", None, True),
                                   ("cancelled", None, False), ("active", None, False)):
        later = ledger.review_state("required", "lead", [builder, _reviewer("A-0002"),
                                                         _reviewer("A-0003", status=status, result=result)],
                                    {"A-0001": 1, "A-0002": 4, "A-0003": 5}, 2, 3)
        assert later["current"] == "A-0003" and later["passing"] is not blocks, (status, result)
        if blocks:
            assert later["accepted"] is None and later["reasons"][0].startswith(
                "review required: later review A-0003 did not complete (") and later["reasons"][0].endswith(
                "after accepted review A-0002; run the reviewer again")
        else:
            assert later["accepted"] == "A-0002"
    # Exactly at the boundary is not after it.
    edge = ledger.review_state("required", "lead", [builder, _reviewer("A-0002")], {"A-0002": 3}, 3, 0)
    assert not edge["passing"] and edge["current"] is None


def test_done_blockers_include_review_reasons_only_when_failing():
    view = {"id": "T-0001", "lead": "lead", "assignments": [], "findings": [], "claims": [],
            "review": {"required": True, "passing": False, "accepted": None, "current": None, "reasons": ["r1"]}}
    assert ledger.done_blockers(view) == ["r1"]
    view["review"] = {"required": True, "passing": True, "accepted": "A-0002", "current": None, "reasons": []}
    assert ledger.done_blockers(view) == []
    del view["review"]
    assert ledger.done_blockers(view) == []



def test_review_state_boundaries_ordering_and_full_refusals():
    builder = _reviewer("A-0001", "codex", mode="rw", role="builder")
    # A reviewer without a recorded start is never after anything, even when nothing else happened yet.
    unseen = ledger.review_state("required", "lead", [builder, _reviewer("A-0002")], {}, 0, 0)
    assert not unseen["passing"] and unseen["current"] is None
    # Order follows the recorded start, not list position; a missing start sorts first.
    ordered = ledger.review_state("required", "lead", [builder, _reviewer("A-0003", status="failed"),
                                                       _reviewer("A-0002", status="failed")],
                                  {"A-0003": 6, "A-0002": 5}, 2, 3)
    assert ordered["reasons"][0].startswith("review required: latest review A-0003 does not qualify")
    first = ledger.review_state("required", "lead", [builder, _reviewer("A-0002", status="failed"),
                                                     _reviewer("A-0004", status="failed")], {"A-0002": 1}, 0, 0)
    assert first["reasons"][0].startswith("review required: latest review A-0002 does not qualify")
    # A later passed and completed review that does not qualify for another reason does not block.
    later = ledger.review_state("required", "lead", [builder, _reviewer("A-0002"), _reviewer("A-0003", "codex")],
                                {"A-0001": 1, "A-0002": 4, "A-0003": 5}, 2, 3)
    assert later == {"required": True, "passing": True, "accepted": "A-0002", "current": "A-0003", "reasons": []}
    failed = ledger.review_state("required", "lead", [builder, _reviewer("A-0002"), _reviewer("A-0003", status="failed")],
                                 {"A-0001": 1, "A-0002": 4, "A-0003": 5}, 2, 3)
    assert failed == {"required": True, "passing": False, "accepted": None, "current": "A-0003", "reasons": [
        "review required: later review A-0003 did not complete (it is failed) after accepted review A-0002; "
        "run the reviewer again"]}


def test_review_seqs_exact_contract(tmp_path):
    with (patch.object(task_verification, "project_state", return_value=dict(STATE)),
          closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store):
        task = store.open_task(project=tmp_path, title="Seqs", objective="Event positions", lead=LEAD)["id"]
        store.set_status(task, to="in_progress", actor=LEAD)
        build = store.assign(task, actor="codex", role="builder", mode="rw", scope=["src"])["id"]
        store.finish(build, result="passed", summary="built")
        assert store._review_seqs(task, [], 9) == ({build: _seq(store, task, "assigned", build)}, 9, 0, {}, None)
        store._record_verification(_record(id="V-" + "a" * 32, task=task))
        store._record_verification(_record(id="V-" + "b" * 32, task=task))
        with store._transaction():  # Another event type with the same refs must not count.
            store._append_event(task, LEAD, "note", "refs only", [build, "V-" + "c" * 32])
        review = store.assign(task, actor="rev", role="reviewer", mode="ro", scope=[])["id"]
        store.record_review_tree(review, phase="start", revision="r1", fingerprint="f1", source="snapshot")
        store.record_review_tree(review, phase="end", revision="r2", fingerprint=None)
        store.record_review_tree(review, phase="end", revision="r3", fingerprint="f3")  # The first record counts.
        legacy = store.assign(task, actor="old", role="reviewer", mode="ro", scope=[])["id"]
        with store._transaction():  # An event written before sources were recorded reads as a live-tree review.
            store._append_event(task, "hipson", "reviewed", f"{legacy}: start revision=r4; fingerprint=f4", [legacy])
            store._append_event(task, "hipson", "reviewed", f"{legacy}: end revision=r4; fingerprint=f4; source=x",
                                [legacy])
        assigned = {build: _seq(store, task, "assigned", build), review: _seq(store, task, "assigned", review),
                    legacy: _seq(store, task, "assigned", legacy)}
        trees = {review: {"start": ("r1", "f1", "snapshot"), "end": ("r2", "none", "live")},
                 legacy: {"start": ("r4", "f4", "live")}}  # An unknown source matches nothing.
        tree = (STATE["revision"], STATE["fingerprint"])
        first = _seq(store, task, "lead_verified", "V-" + "a" * 32)
        assert store._review_seqs(task, ["V-" + "a" * 32], 7) == (assigned, 7, first, trees, tree)
        assert store._review_seqs(task, ["V-" + "b" * 32, "V-" + "a" * 32], 7) == (
            assigned, 7, _seq(store, task, "lead_verified", "V-" + "b" * 32), trees, tree)
        assert store._review_seqs(task, ["V-" + "d" * 32], 7) == (assigned, 7, 0, trees, None)
        assert 0 < first < assigned[review]
        _error(lambda: store.record_review_tree(build, phase="start", revision="r", fingerprint="f"),
               f"Assignment {build} is not an active reviewer", "review.assignment_not_active")
        _error(lambda: store.record_review_tree(review, phase="middle", revision="r", fingerprint="f"),
               "Review tree phase is start or end", "review.tree_phase_invalid")


def test_review_state_requires_the_verified_tree():
    builder = _reviewer("A-0001", "codex", mode="rw", role="builder")
    seqs = {"A-0001": 1, "A-0002": 4}
    tree = ("r", "f", "snapshot")
    live = ("it read the live working tree, not an immutable snapshot of the verified candidate (run reviewers "
            "through hipson task run without --live-tree, or hipson task flow)")
    def state(trees, verified=("r", "f")):
        return ledger.review_state("required", "lead", [builder, _reviewer("A-0002")], seqs, 2, 3, trees, verified)
    assert state({"A-0002": {"start": tree, "end": tree}})["accepted"] == "A-0002"
    assert state({"A-0002": {"start": tree, "end": tree}}, None)["accepted"] == "A-0002"
    for trees, problem in (
            ({}, "it has no recorded reviewed tree (run reviewers through hipson task run)"),
            ({"A-0002": {"start": tree}}, "it has no recorded reviewed tree (run reviewers through hipson task run)"),
            ({"A-0002": {"end": tree}}, "it has no recorded reviewed tree (run reviewers through hipson task run)"),
            ({"A-0002": {"start": ("r", "f", "live"), "end": tree}}, live),
            ({"A-0002": {"start": tree, "end": ("r", "f", "live")}}, live),
            ({"A-0002": {"start": ("r", "f"), "end": ("r", "f")}}, live),  # No source: never a snapshot.
            ({"A-0002": {"start": ("r", "f", "live"), "end": ("r", "f", "live")}}, live),
            ({"A-0002": {"start": tree + ("x",), "end": tree + ("x",)}}, live),
            ({"A-0002": {"start": tree, "end": ("r", "g", "snapshot")}}, "the reviewed snapshot changed during the review"),
            ({"A-0002": {"start": tree, "end": ("q", "f", "snapshot")}}, "the reviewed snapshot changed during the review"),
            ({"A-0002": {"start": ("r", "g", "snapshot"), "end": ("r", "g", "snapshot")}},
             "it reviewed a different tree than the accepted lead verification"),
            ({"A-0002": {"start": ("q", "f", "snapshot"), "end": ("q", "f", "snapshot")}},
             "it reviewed a different tree than the accepted lead verification")):
        assert state(trees)["reasons"] == [f"review required: latest review A-0002 does not qualify: {problem}"
                                           + REVIEW_TAIL], trees


def test_a_cancelled_review_does_not_hide_an_earlier_failed_one():
    builder = _reviewer("A-0001", "codex", mode="rw", role="builder")
    reviews = [_reviewer("A-0002"), _reviewer("A-0003", status="failed"), _reviewer("A-0004", status="cancelled")]
    state = ledger.review_state("required", "lead", [builder, *reviews],
                                {"A-0001": 1, "A-0002": 4, "A-0003": 5, "A-0004": 6}, 2, 3)
    assert state == {"required": True, "passing": False, "accepted": None, "current": "A-0004", "reasons": [
        "review required: later review A-0003 did not complete (it is failed) after accepted review A-0002; "
        "run the reviewer again"]}


def _seq(store, task, kind, ref):
    return next(event["seq"] for event in store.events(task) if event["type"] == kind and event["refs"][0] == ref)



def _gate_task(store, tmp_path, policy="required"):
    task = store.open_task(project=tmp_path, title="Gate", objective="Review gate", lead=LEAD, review_policy=policy)["id"]
    store.set_status(task, to="in_progress", actor=LEAD)
    build = store.assign(task, actor="codex", role="builder", mode="rw", scope=["src"])["id"]
    store.finish(build, result="passed", summary="built")
    store.ack(build)
    store._record_verification(_record(id="V-" + task[2:].rjust(32, "e"), task=task,
                                       builder_finished_seq=store._builder_finished_seq(task)))
    return task


def _review(store, task, actor="rev", source="snapshot"):
    # The completed result is written directly: result parsing has its own contracts outside this profile.
    item = store.assign(task, actor=actor, role="reviewer", mode="ro", scope=[])["id"]
    for phase in ("start", "end"):  # What task run records around a reviewer: the verified STATE tree.
        store.record_review_tree(item, phase=phase, revision=STATE["revision"], fingerprint=STATE["fingerprint"],
                                 source=source)
    store.finish(item, result="passed", summary="reviewed")
    with store._transaction():
        store.connection.execute("UPDATE assignments SET result = ? WHERE id = ?", (json.dumps({
            "status": "completed", "summary": "ok", "files_changed": [], "commands": [], "findings": [],
            "risks": []}), item))
    store.ack(item)
    return item


def test_review_policy_ledger_exact_contract(tmp_path):
    with (patch.object(task_verification, "project_state", return_value=dict(STATE)),
          closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store):
        task = _gate_task(store, tmp_path)
        policy = [event for event in store.events(task) if event["type"] == "review_policy"]
        assert [(event["actor"], event["summary"], event["refs"]) for event in policy] == [
            (LEAD, "independent read-only review required before done", [])]
        plain = _gate_task(store, tmp_path, policy="none")
        assert not [event for event in store.events(plain) if event["type"] == "review_policy"]
        assert store.show(plain)["review_policy"] == "none" and store.show(task)["review_policy"] == "required"
        store.set_status(task, to="review", actor=LEAD)
        reason = ("review required: no reviewer assignment; run an independent read-only reviewer after the latest "
                  "builder and lead verification (hipson task flow, or hipson task run --role reviewer --mode ro)")
        refusal = _error(lambda: store.set_status(task, to="done", actor=LEAD), f"Cannot mark {task} done: {reason}",
                         "close.review_required", ledger.GateRefusedError)
        _assert_gate_refusal(refusal, ["close.review_required"], fix=[_fix(
            "task.run", {"task": task, "role": "reviewer", "mode": "ro"}, f" --task {task} --role reviewer --mode ro",
            store.path, " --actor REVIEWER --result-file RESULT_PATH -- COMMAND")])
        # Reviews by the lead's label do not count; done_blockers keeps every other blocker too.
        _review(store, task, actor=LEAD.upper())
        active = store.assign(task, actor="other", role="explorer", mode="ro", scope=[])["id"]
        blockers = ledger.done_blockers(store.show(task))
        assert blockers[0].startswith(f"active assignment {active}") and blockers[-1].startswith(
            "review required: latest review A-0003 does not qualify: actor LEAD is the lead or a builder of this task")
        store.finish(active, result="passed", summary="x")
        store.ack(active)
        live = _review(store, task, source="live")  # Equal live fingerprints do not prove what was read.
        assert store.show(task)["review"]["reasons"][0].startswith(
            f"review required: latest review {live} does not qualify: it read the live working tree")
        good = _review(store, task)
        assert store.show(task)["review"] == {"required": True, "passing": True, "accepted": good, "current": good,
                                              "reasons": []}
        assert store.set_status(task, to="done", actor=LEAD)["status"] == "done"
        forced = _gate_task(store, tmp_path)
        store.set_status(forced, to="review", actor=LEAD)
        store.set_status(forced, to="done", actor=LEAD, force=True, reason="owner accepted without review")
        override = [event for event in store.events(forced) if event["type"] == "done_override"]
        assert override and override[0]["summary"] == f"Reason: owner accepted without review; blockers: {reason}"



def test_a_failed_review_before_the_accepted_one_does_not_block():
    builder = _reviewer("A-0001", "codex", mode="rw", role="builder")
    state = ledger.review_state("required", "lead", [builder, _reviewer("A-0002", status="failed"), _reviewer("A-0003")],
                                {"A-0001": 1, "A-0002": 4, "A-0003": 5}, 2, 3)
    assert state == {"required": True, "passing": True, "accepted": "A-0003", "current": "A-0003", "reasons": []}


def test_record_review_tree_exact_contract(tmp_path):
    with (patch.object(task_verification, "project_state", return_value=dict(STATE)),
          closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store):
        task = store.open_task(project=tmp_path, title="Trees", objective="Reviewed trees", lead=LEAD)["id"]
        store.set_status(task, to="in_progress", actor=LEAD)
        builder = store.assign(task, actor="codex", role="builder", mode="rw", scope=["src"])["id"]
        review = store.assign(task, actor="rev", role="reviewer", mode="ro", scope=[])["id"]
        for phase in ("middle", "START", ""):
            with pytest.raises(ledger.LedgerError) as error:
                store.record_review_tree(review, phase=phase, revision="r", fingerprint="f")
            assert str(error.value) == "Review tree phase is start or end"
            assert error.value.code == "review.tree_phase_invalid"
        with pytest.raises(ledger.LedgerError) as error:  # An active builder is not a reviewer.
            store.record_review_tree(builder, phase="start", revision="r", fingerprint="f")
        assert str(error.value) == f"Assignment {builder} is not an active reviewer"
        assert error.value.code == "review.assignment_not_active"
        for source in ("Snapshot", "", "other"):
            with pytest.raises(ledger.LedgerError) as error:
                store.record_review_tree(review, phase="start", revision="r", fingerprint="f", source=source)
            assert str(error.value) == "Review tree source is live or snapshot"
            assert error.value.code == "review.tree_source_invalid"
        event = store.record_review_tree(review, phase="start", revision="", fingerprint=None)
        assert (event["actor"], event["type"], event["summary"], event["refs"]) == (
            "hipson", "reviewed", f"{review}: start revision=none; fingerprint=none; source=live", [review])
        event = store.record_review_tree(review, phase="end", revision="r", fingerprint="f", source="snapshot")
        assert event["summary"] == f"{review}: end revision=r; fingerprint=f; source=snapshot"
        assert store._review_seqs(task, [], 0)[3] == {review: {"start": ("none", "none", "live"),
                                                               "end": ("r", "f", "snapshot")}}
        with store._transaction():  # A lead_verified record whose summary lacks the pattern binds no tree.
            store._append_event(task, LEAD, "lead_verified", "V-x: malformed", ["V-" + "9" * 32])
        assert store._review_seqs(task, ["V-" + "9" * 32], 0)[4] == ("", "")
        store.finish(review, result="passed", summary="done")
        with pytest.raises(ledger.LedgerError) as error:
            store.record_review_tree(review, phase="end", revision="r", fingerprint="f")
        assert str(error.value) == f"Assignment {review} is not an active reviewer"
        assert error.value.code == "review.assignment_not_active"



def test_close_task_expected_checks_exact_contract(tmp_path):
    with (patch.object(task_verification, "project_state", return_value=dict(STATE)),
          closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store):
        task = store.open_task(project=tmp_path, title="Close", objective="Expected checks", lead=LEAD,
                               checks=[CHECK])["id"]
        store.set_status(task, to="in_progress", actor=LEAD)
        store._record_verification(_record(id="V-" + "c" * 32, task=task, builder_finished_seq=0))
        with pytest.raises(ledger.CloseRefusedError) as error:
            store.close_task(task, to="review", actor=LEAD, expected_checks=[["pytest"]])
        assert error.value.reasons == ["declared checks differ from the ones this close expects: pytest -q"]
        _assert_gate_refusal(error.value, ["close.checks_mismatch"])
        assert store.show(task)["status"] == "in_progress"
        assert store.close_task(task, to="review", actor=LEAD, expected_checks=[("pytest", "-q")])["status"] == "review"


def test_gate_refused_error_constructor(tmp_path):
    message = "Cannot mark T-0001 done"
    fix = [_activity_fix("A-0001", tmp_path / "ledger.sqlite")]
    refusal = ledger.GateRefusedError(message, code="close.refused",
                                     reason_codes=("close.results_unacknowledged", "close.findings_open"), fix=fix)
    assert str(refusal) == message
    _assert_gate_refusal(refusal, ["close.results_unacknowledged", "close.findings_open"], fix=fix)

    refusal = ledger.GateRefusedError(message, code="close.refused")
    assert (str(refusal), refusal.code, refusal.reason_codes, refusal.fix) == (message, "close.refused", [], [])
    refusal = ledger.GateRefusedError(message)
    assert (str(refusal), refusal.code, refusal.reason_codes, refusal.fix) == (message, None, [], [])


def test_close_refused_error_constructor(tmp_path):
    reasons = ["unacknowledged result A-0001", "open major finding F-0001: fix it"]
    message = "Cannot close T-0001: unacknowledged result A-0001; open major finding F-0001: fix it"
    fix = [_activity_fix("A-0001", tmp_path / "ledger.sqlite")]
    refusal = ledger.CloseRefusedError("T-0001", reasons, code="close.refused",
                                      reason_codes=("close.results_unacknowledged", "close.findings_open"), fix=fix)
    assert str(refusal) == message and refusal.reasons == reasons
    _assert_gate_refusal(refusal, ["close.results_unacknowledged", "close.findings_open"], fix=fix)

    refusal = ledger.CloseRefusedError("T-0001", reasons, code="close.refused")
    assert str(refusal) == message and refusal.reasons == reasons
    _assert_gate_refusal(refusal, ["close.refused", "close.refused"])

    refusal = ledger.CloseRefusedError("T-0001", reasons)
    assert str(refusal) == message and refusal.reasons == reasons
    assert (refusal.code, refusal.reason_codes, refusal.fix) == (None, [], [])

    refusal = ledger.CloseRefusedError("T-0001", [], code="close.refused")
    assert (refusal.code, refusal.reason_codes, refusal.fix) == ("close.refused", [], [])


def test_gate_reason_codes_classify_raw_reasons_in_order():
    cases = [
        ("missing lead verification; run hipson task verify", "verify.lead_missing"),
        ("missing: never verified", "close.checks_missing"),
        ("failing verification: exit 2", "close.checks_failed"),
        ("stale: HEAD moved; before=a after=b", "verify.head_moved"),
        ("stale: working tree changed since verification: x.py", "verify.tree_changed"),
        ("stale: verification predates latest finished builder assignment", "close.checks_stale"),
        ("unavailable working-tree fingerprint: Project directory is unavailable", "project.unavailable"),
        ("unavailable working-tree fingerprint: HEAD moved while fingerprinting", "verify.head_moved"),
        ("unavailable working-tree fingerprint: Project changed while fingerprinting", "verify.tree_changed"),
        ("unavailable working-tree fingerprint: Git filter configuration is unavailable", "verify.git_filters_unavailable"),
        ("unavailable working-tree fingerprint: Git repository is present", "verify.git_state_unavailable"),
        ("unavailable working-tree fingerprint: Git HEAD is unavailable", "verify.git_head_unavailable"),
        ("unavailable working-tree fingerprint: Git content inventory is unavailable", "verify.git_inventory_unavailable"),
        ("unavailable working-tree fingerprint: Git submodules require separate verification", "verify.git_submodules"),
        ("unavailable working-tree fingerprint: Invalid repository path in content inventory", "verify.inventory_path_invalid"),
        ("unavailable working-tree fingerprint: Directory symlink prevents contained project fingerprinting", "verify.directory_symlink"),
        ("unavailable working-tree fingerprint: Special file prevents project fingerprinting", "verify.special_file"),
        ("unavailable working-tree fingerprint: Project content exceeds the snapshot budget", "verify.snapshot_budget"),
        ("unavailable working-tree fingerprint: A symlink points outside the project", "verify.external_symlink"),
        ("unavailable working-tree fingerprint: Git normalized content hash is unavailable", "verify.git_hash_unavailable"),
        ("unavailable working-tree fingerprint: Project /x is not a Git repository and is too large to fingerprint (over 20000 paths or 20 s)", "verify.walk_limit"),
        ("unavailable working-tree fingerprint: unknown", "verify.fingerprint_unavailable"),
        ("verification was not recorded by the task lead in a lead context", "verify.lead_required"),
        ("partial content coverage (metadata fallback): big.bin", "verify.coverage_partial"),
        ("verification process cleanup was not confirmed", "verify.cleanup_unconfirmed"),
        ("declared checks differ from the ones this close expects: pytest -q", "close.checks_mismatch"),
        ("active assignment A-0001 (rw)", "close.assignment_active"),
        ("claim A-0001:src", "close.claims_held"),
        ("held claims: hipson task reap --task T-0001", "close.claims_held"),
        ("unacknowledged result A-0002", "close.results_unacknowledged"),
        ("open blocking finding F-0001: fix it", "close.findings_open"),
        ("open major finding F-0002: fix it", "close.findings_open"),
        ("review required: no reviewer assignment", "close.review_required"),
        ("unknown reason mentioning missing lead verification", "close.refused"),
    ]
    reasons, codes = map(list, zip(*cases, strict=True))
    assert ledger._gate_reason_codes({"checks": []}, reasons) == codes
    assert ledger._gate_reason_codes({"checks": [{"reasons": []}]}, reasons) == codes

    verification = {"checks": [{"reasons": reasons[:2]}, {"reasons": reasons[2:4]}]}
    displayed = [
        *(f"check `stale: HEAD moved`: {reason}" for reason in reasons[:2]),
        *(f"check `review required`: {reason}" for reason in reasons[2:4]),
        *reasons[4:],
    ]
    assert ledger._gate_reason_codes(verification, displayed) == codes
    assert ledger._gate_reason_codes({"checks": []}, []) == []


def test_gate_refusals_keep_distinct_verification_codes(tmp_path):
    with (patch.object(task_verification, "project_state", return_value=dict(STATE)) as project_state,
          closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store):
        task = store.open_task(project=tmp_path, title="Candidate", objective="Verify", lead=LEAD, checks=[CHECK])["id"]
        store.set_status(task, to="in_progress", actor=LEAD)
        with pytest.raises(ledger.CloseRefusedError) as missing:
            store.close_task(task, to="review", actor=LEAD)
        _assert_gate_refusal(missing.value, ["close.checks_missing"], fix=[_verify_fix(task, store.path)])

        _verified(store, task)
        project_state.return_value = {**STATE, "revision": "c" * 40}
        with pytest.raises(ledger.GateRefusedError) as moved:
            store.set_status(task, to="review", actor=LEAD)
        _assert_gate_refusal(moved.value, ["verify.head_moved"], fix=[_verify_fix(task, store.path)])

        project_state.return_value = {**STATE, "files": [{"path": "new.py", "state": "file:hash"}]}
        with pytest.raises(ledger.CloseRefusedError) as changed:
            store.close_task(task, to="review", actor=LEAD)
        _assert_gate_refusal(changed.value, ["verify.tree_changed"], fix=[_verify_fix(task, store.path)])
        assert store.show(task)["status"] == "in_progress"


def test_close_refusal_pairs_codes_with_reasons_and_suggests_the_unread_activity(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        store, task = _store(tmp_path)
        with closing(store):
            assignment = store.assign(task, actor="w", role="reviewer", mode="ro")["id"]
            store.finish(assignment, result="passed", summary="unread")
            major = store.add_finding(task, actor="r", severity="major", title="Open major")["id"]
            with pytest.raises(ledger.CloseRefusedError) as error:
                store.close_task(task, actor=LEAD)
            refusal = error.value
            assert [reason.split()[0] for reason in refusal.reasons] == ["missing", "unacknowledged", "open"]
            _assert_gate_refusal(refusal, ["verify.lead_missing", "close.results_unacknowledged", "close.findings_open"],
                                 fix=[_verify_fix(task, store.path, explicit=True), *_ack_fixes(assignment, store.path),
                                      *_finding_fixes(task, major, store.path)])


def test_ack_records_exact_actor_note_and_lookup_fixes(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        store, task = _store(tmp_path)
        with closing(store):
            task_and_lead = {"actor": LEAD, "task": task}
            active = store.assign(task, actor="w", role="explorer", mode="ro")["id"]
            missing = _exact(lambda: store.ack("A-9999"), "Assignment not found: A-9999", "assignment.not_found")
            assert missing.fix == [_fix("task.now", {"detail": "compact", "all": True},
                                        " --detail compact --all", store.path)]
            busy = _exact(lambda: store.ack(active), f"Cannot acknowledge active assignment {active}", "assignment.active")
            assert busy.fix == []
            store.finish(active, result="passed", summary="reviewed")
            _exact(lambda: store.ack(active, actor=" "), "actor must not be empty", "input.text_required")
            default = store.ack(active)
            assert default["acknowledged_at"] is not None and default["ack_note"] == ""
            assert default["ack_state"] == "acknowledged"
            event = store.events(task)[-1]
            assert {key: event[key] for key in task_and_lead} == task_and_lead
            assert (event["type"], event["summary"], event["refs"]) == (
                "acknowledged", f"{active}: result acknowledged", [active])
            assert store.ack(active, actor="other", note="again") == default
            assert store.events(task)[-1] == event

            other = store.assign(task, actor="w", role="explorer", mode="ro")["id"]
            store.finish(other, result="failed", summary="reviewed")
            timestamp = "2026-11-22T12:01:10Z"
            with patch.object(ledger, "_timestamp", return_value=timestamp):
                noted = store.ack(other, actor=" delegate ", note="Read fully")
            assert noted == store.activity(other)
            assert (noted["acknowledged_at"], noted["ack_note"], noted["ack_state"]) == (
                timestamp, "Read fully", "acknowledged")
            event = store.events(task)[-1]
            assert (event["task"], event["actor"], event["type"], event["summary"], event["refs"]) == (
                task, "delegate", "acknowledged", f"{other}: Read fully", [other])


def test_close_refusal_can_classify_only_its_preflight_blockers(tmp_path):
    with (patch.object(task_verification, "project_state", return_value=dict(STATE)),
          closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store):
        task = store.open_task(project=tmp_path, title="Candidate", objective="Verify", lead=LEAD, checks=[CHECK])["id"]
        assignment = store.assign(task, actor="w", role="explorer", mode="ro")["id"]
        store.finish(assignment, result="passed", summary="unread")
        reasons = [f"unacknowledged result {assignment}"]
        error = ledger.close_refusal(store.show(task), reasons, store.path, include_verification=False)
        assert error.reasons == reasons and str(error) == f"Cannot close {task}: {reasons[0]}"
        _assert_gate_refusal(error, ["close.results_unacknowledged"], fix=_ack_fixes(assignment, store.path))


def test_assignment_delivery_retains_exact_details_and_historical_ack_state(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        store, task = _store(tmp_path)
        started = "2026-11-22T12:00:00Z"
        ended = "2026-11-22T12:00:20Z"
        environment = {"mode": "minimal", "names": ["PATH"], "allowed": ["PATH"], "missing": [], "dropped": 7}
        report = {"status": "completed", "summary": "Complete", "files_changed": [], "commands": [],
                  "findings": [], "risks": []}
        with closing(store), patch.object(ledger, "_timestamp", return_value=started) as clock:
            assigned = store.assign(task, actor=" worker ", role="reviewer", mode="ro", owner_pid=4321,
                                    owner_boot_id="boot", owner_host="host", owner_pid_namespace="pid:[101]",
                                    environment=environment, lease_seconds=600, needs=["review"])
            item = assigned["id"]
            expected = {"id": "A-0001", "task": task, "actor": "worker", "role": "reviewer", "mode": "ro", "scope": [],
                        "status": "active", "started_at": started, "ended_at": None, "owner_pid": 4321,
                        "owner_boot_id": "boot", "owner_host": "host", "owner_pid_namespace": "pid:[101]",
                        "lease_until": "2026-11-22T12:10:00Z", "result": None, "activity": None,
                        "failure_journal": None, "acknowledged_at": None, "ack_note": "", "changes": None,
                        "environment": environment, "needs": ["review"], "ack_state": "not_required", "usage": None}
            assert assigned == expected
            later = store.assign(task, actor="worker", role="builder", mode="rw", scope=["src"])["id"]
            clock.return_value = ended
            result = store.finish(item, result="passed", summary="Reviewed", refs=["result.json"])
            expected.update(status="passed", ended_at=ended, ack_state="unacknowledged",
                            usage={"source": None, "tokens": None, "reason": "not_an_agent_runtime", "elapsed_seconds": 20.0})
            assert result == expected
            store.connection.execute("UPDATE assignments SET result = ? WHERE id = ?", (json.dumps(report), item))
            expected["result"] = report
            assert store.activity(item) == expected
            event = store.events(task)[-1]
            assert (event["actor"], event["type"], event["summary"], event["refs"]) == (
                "worker", "finished", "passed: Reviewed", [item, "result.json"])
            store.set_status(task, to="cancelled", actor=LEAD)
            store.finish(later, result="failed", summary="Finished after closing")
            assert store.show(task)["claims"] == []
            assert store.activity(later)["ack_state"] == "unacknowledged"
            expected["ack_state"] = "not_required"
            assert store.activity(item) == expected

            legacy_row = store.connection.execute(
                "SELECT id, task, actor, role, mode, scope, status, started_at, ended_at FROM assignments WHERE id = ?",
                (item,),
            ).fetchone()
            assert store._assignment_dict(legacy_row) == {
                **expected, "owner_pid": None, "owner_boot_id": None, "owner_host": None,
                "owner_pid_namespace": None, "lease_until": None, "result": None, "environment": None, "needs": None,
                "usage": None,
            }
