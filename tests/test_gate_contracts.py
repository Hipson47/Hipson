"""Exact contracts of the review/done gates: verification_state, close_task and status transitions.

These run in-process with a fixed project state, so they are fast enough for mutation testing of the ledger core.
"""

import hashlib
import itertools
import json
from contextlib import closing
from unittest.mock import patch

from hipson import ledger, task_verification
from hipson.task_verification import argv_identity, verification_state
from tests.test_ledger import _error

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
            store.add_finding(task, actor="r", severity="major", title="Open major")
            _verified(store, task)
            assert store.close_task(task, to="review", actor=LEAD)["status"] == "review"
            _error(lambda: store.close_task(task, actor=LEAD), "unacknowledged result", ledger.CloseRefusedError)
            try:
                store.close_task(task, actor=LEAD)
            except ledger.CloseRefusedError as exc:
                assert str(exc).startswith(f"Cannot close {task}: ") and len(exc.reasons) == 2
            else:
                raise AssertionError("close with open findings accepted")
            _error(lambda: store.close_task(task, to="review", actor=LEAD), "close works from open, in_progress or review")
            _error(lambda: store.close_task(task, to="cancelled", actor=LEAD), "Close target must be review or done")
            _error(lambda: store.close_task(task, actor=" "), "actor must not be empty")


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
            _error(lambda: store.set_status(task, to="done", actor=LEAD), f"open blocking finding {blocking}: Stop")
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
            _error(lambda: store.set_status(task, to="done", actor=LEAD),
                   f"Invalid task transition for {task}: open -> done (allowed: cancelled, in_progress); "
                   f"hipson task close --task {task} verifies and closes in one step")
            _error(lambda: store.set_status(task, to="review", actor=LEAD),
                   f"Invalid task transition for {task}: open -> review (allowed: cancelled, in_progress)")
            store.set_status(task, to="cancelled", actor=LEAD, summary="dropped")
            assert store.events(task)[-1]["summary"] == "open -> cancelled: dropped"
            _error(lambda: store.set_status(task, to="open", actor=LEAD), "(allowed: none (terminal))")


def test_close_task_refuses_unverified_work_with_every_reason_and_closes_from_review(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        store, task = _store(tmp_path)
        with closing(store):
            store.set_status(task, to="in_progress", actor=LEAD)
            active = store.assign(task, actor="w", role="builder", mode="ro")["id"]
            try:
                store.close_task(task, actor=LEAD)
            except ledger.CloseRefusedError as exc:
                assert exc.reasons == ["missing lead verification; run hipson task verify",
                                       f"active assignment {active}: wait for it, or hipson task reap --task {task}"]
            else:
                raise AssertionError("unverified close accepted")
            try:
                store.close_task(task, to="review", actor=LEAD)
            except ledger.CloseRefusedError as exc:
                assert exc.reasons[-1].startswith(f"active assignment {active}")
            else:
                raise AssertionError("close to review with an active assignment accepted")
            store.finish(active, result="passed", summary="ok")
            store.ack(active, note="read")
            _verified(store, task)
            assert store.close_task(task, to="review", actor=LEAD)["status"] == "review"
            try:
                store.close_task(task, to="review", actor=LEAD)
            except ledger.CloseRefusedError as exc:
                assert exc.reasons == [f"{task} is review; close works from open, in_progress or review (use --to done)"]
            else:
                raise AssertionError("close review -> review accepted")
            assert store.close_task(task, actor=LEAD)["status"] == "done"  # From review, one transition only.
            assert [event["summary"].split(";")[0] for event in store.events(task) if event["type"] == "status"][-2:] == [
                "in_progress -> review", "review -> done"]
            try:
                store.close_task(task, actor=LEAD)
            except ledger.CloseRefusedError as exc:
                assert exc.reasons == [f"{task} is done; close works from open, in_progress or review"]
            else:
                raise AssertionError("close of a done task accepted")


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
           "Acceptance checks must be a list of at most 16 argv lists")
    _error(lambda: ledger.validate_checks("pytest"), "Acceptance checks must be a list of at most 16 argv lists")
    assert ledger.validate_checks([["c"] * 64]) == [["c"] * 64]
    for bad in ([["c"] * 65], [[]], ["pytest -q"], [None]):
        _error(lambda bad=bad: ledger.validate_checks(bad),
               "Each acceptance check must be a nonempty argv list of at most 64 items")
    assert ledger.validate_checks([["c", "x" * 4096]]) == [["c", "x" * 4096]]
    for bad in ("x" * 4097, "a\x1fb", "a\x7fb", 3):
        _error(lambda bad=bad: ledger.validate_checks([["c", bad]]),
               "Acceptance check arguments must be strings of at most 4096 characters without control characters")
    assert ledger.validate_checks([["c", "a b", "\x80"]]) == [["c", "a b", "\x80"]]  # Space and 0x80 are fine.
    _error(lambda: ledger.validate_checks([[" ", "pytest"]]), "An acceptance check needs a command name")
    assert ledger.validate_checks([["a", " "]]) == [["a", " "]]
    _error(lambda: ledger.validate_checks([["c", "token=ghp_" + "a" * 36]]),
           "Acceptance checks must not contain secret-shaped text; pass secrets via the environment")
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
                   f"Replacing declared acceptance checks of {task} requires --reason")
            _error(lambda: store.set_checks(task, checks=[], actor=LEAD, reason="  "), "requires --reason")
            store.set_checks(task, checks=[], actor=LEAD, reason=" manual ")
            event = store.events(task)[-1]
            assert event["type"] == "checks_set" and event["actor"] == LEAD
            assert event["summary"] == "acceptance checks: none declared; previously: pytest -q; reason: manual"
            store.set_checks(task, checks=[CHECK], actor=LEAD)  # Declaring on an empty list needs no reason.
            _error(lambda: store.set_checks(task, checks=[CHECK], actor=" "), "actor must not be empty")
            store.set_status(task, to="cancelled", actor=LEAD)
            _error(lambda: store.set_checks(task, checks=[], actor=LEAD, reason="x"),
                   f"Cannot change acceptance checks of cancelled task {task}")


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
            _error(lambda: store.set_status(task, to="done", actor=LEAD),
                   f"Cannot mark {task} done: active assignment {live} (rw); claim {live}:src; "
                   f"open major finding {major}: M; unacknowledged result {unread}")
            _error(lambda: store.set_status(task, to="done", actor=LEAD, force=True), "Override reason must not be empty")
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
            _error(lambda: store.set_status(task, to="review", actor=LEAD),
                   f"Cannot mark {task} review: missing lead verification; run hipson task verify; next: hipson task "
                   f"close --task {task} (runs declared checks), or hipson task verify --task {task} -- COMMAND "
                   "(MCP: task.close / task.verify)")
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
            _error(lambda: store.set_status(task, to="review", actor=LEAD), f"Cannot mark {task} review: stale: HEAD moved")
            assert store.show(task)["status"] == "in_progress"
            state.side_effect = itertools.chain([dict(STATE)], itertools.repeat(moved))
            view = store.set_status(task, to="review", actor=LEAD, force=True, reason="moved during close")
            assert view["status"] == "review"
            override, status = store.events(task)[-2:]
            assert override["type"] == "review_override"
            assert override["summary"].startswith("Reason: moved during close; final verification blockers: stale: HEAD moved")
            assert "final lead verification OVERRIDDEN: stale: HEAD moved" in status["summary"]
            assert "V-" + "9" * 32 not in status["refs"]


def _exact(fn, message, error=ledger.LedgerError):
    try:
        fn()
    except error as exc:
        assert str(exc) == message, str(exc)
    else:
        raise AssertionError(f"Expected {error.__name__}: {message}")


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
            _exact(lambda: store.set_status(task, to="done", actor=LEAD),
                   f"Cannot mark {task} done: stale: HEAD moved; before={'a' * 40} after={'c' * 40}")
            assert store.show(task)["status"] == "review"
            state.side_effect = None
            state.return_value = dict(STATE)
            store.set_status(task, to="done", actor=LEAD)
            _exact(lambda: store.set_checks(task, checks=[], actor=LEAD, reason="x"),
                   f"Cannot change acceptance checks of done task {task}")
            _exact(lambda: store.close_task(task, to="cancelled", actor=LEAD), "Close target must be review or done")
            _exact(lambda: store.close_task(task, actor=LEAD),
                   f"Cannot close {task}: {task} is done; close works from open, in_progress or review",
                   ledger.CloseRefusedError)
            _exact(lambda: ledger.validate_checks([[" "]]), "An acceptance check needs a command name")
            _exact(lambda: ledger.validate_checks([["c", "token=ghp_" + "a" * 36]]),
                   "Acceptance checks must not contain secret-shaped text; pass secrets via the environment")


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
                   "Change observation must be an object with a known status")
        _exact(lambda: store.record_changes(assignment, _changes()), f"Assignment {assignment} is still active")
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
               f"Assignment {assignment} already has a change observation")
        other = store.assign(task, actor="w", role="reviewer", mode="ro", scope=[])["id"]
        store.finish(other, result="passed", summary="ok")
        store.record_changes(other, _changes("incomplete"))
        assert store.events(task)[-1]["summary"] == (
            f"{other}: changes incomplete; 0 outside scope, 0 unattributed; incomplete, not a clean result")
        third = store.assign(task, actor="w", role="reviewer", mode="ro", scope=[])["id"]
        store.finish(third, result="passed", summary="ok")
        store.record_changes(third, _changes("clean"))
        assert store.events(task)[-1]["summary"] == f"{third}: changes clean; 0 outside scope, 0 unattributed"
        assert store.events(task)[-1]["refs"] == [third]


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
                   "environment must be an object with mode inherit or minimal")
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
    assert ledger.SCHEMA_VERSION == 10
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
           "Capability tags must be a list of at most 16 names")
    _error(lambda: ledger.validate_needs("code_write"), "Capability tags must be a list of at most 16 names")
    for bad in ("Code", "1abc", "a" + "b" * 40, "a-b", "", "a b", 7):
        _error(lambda bad=bad: ledger.validate_needs([bad]),
               f"Capability tags are lower-case names such as code_write, not {bad!r}")


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
               "Capability tags are lower-case names such as code_write, not 'Bad'")
        _error(lambda: store.open_task(project=tmp_path, title="T", objective="o", needs=["Bad"]),
               "Capability tags are lower-case names such as code_write, not 'Bad'")
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
        _error(lambda: store.record_route("T-9999", actor="hipson", summary="x"), "Task not found: T-9999")
        _error(lambda: store.record_route(tagged["id"], actor=" ", summary="x"), "actor must not be empty")
        _error(lambda: store.record_route(tagged["id"], actor="hipson", summary=" "), "summary must not be empty")


def test_capability_tags_and_routed_events_exact_contract(tmp_path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        _needs_contract(tmp_path)
