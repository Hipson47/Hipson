"""The board must not fingerprint the whole task history, and a walk outside Git must stay bounded."""

from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from hipson import task_verification
from hipson.ledger import Ledger


def _open(store: Ledger, project: Path, title: str) -> str:
    project.mkdir(parents=True, exist_ok=True)
    return store.open_task(project=project, title=title, objective="Objective")["id"]


def test_board_fingerprints_only_tasks_it_can_show(tmp_path: Path):
    real = task_verification.project_state
    fingerprinted: list[str] = []

    def counting(project, db):
        fingerprinted.append(Path(project).name)
        return real(project, db)

    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        finished = _open(store, tmp_path / "finished", "Finished")
        cancelled = _open(store, tmp_path / "cancelled", "Cancelled")
        active = _open(store, tmp_path / "active", "Active")
        store.set_status(finished, to="in_progress", actor="claude")
        store.set_status(cancelled, to="cancelled", actor="claude")
        store.set_status(finished, to="cancelled", actor="claude")
        with patch.object(task_verification, "project_state", side_effect=counting):
            board = store.board()
    assert [item["id"] for item in board] == [active]
    assert fingerprinted == ["active"], "terminal tasks without live assignments must not be fingerprinted"


def test_board_still_shows_a_terminal_task_with_a_live_assignment(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path / "one", "One")
        assigned = store.assign(task, actor="codex", role="builder", mode="rw", scope=["."])
        store.set_status(task, to="cancelled", actor="claude")
        board = store.board()
        assert [item["id"] for item in board] == [task]
        assert [item["id"] for item in board[0]["assignments"]] == [assigned["id"]]


def test_board_still_shows_a_terminal_task_with_an_unacknowledged_result(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path / "one", "One")
        assigned = store.assign(task, actor="codex", role="explorer", mode="ro", scope=["."])
        store.set_status(task, to="cancelled", actor="claude")
        # A result delivered after the closure is unacknowledged (one delivered before it is historical).
        store.finish(assigned["id"], result="passed", summary="Read done")
        assert [item["id"] for item in store.board()] == [task]
        store.ack(assigned["id"])
        assert store.board() == []


def test_board_fingerprints_a_shared_project_once(tmp_path: Path):
    real = task_verification.project_state
    calls: list[str] = []

    def counting(project, db):
        calls.append(str(project))
        return real(project, db)

    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        first = _open(store, tmp_path / "shared", "First")
        second = _open(store, tmp_path / "shared", "Second")
        with patch.object(task_verification, "project_state", side_effect=counting):
            board = store.board()
    assert [item["id"] for item in board] == [first, second]
    assert len(calls) == 1
    assert board[0]["verification"] == board[1]["verification"]


def test_walk_outside_git_fails_closed_past_the_path_limit(tmp_path: Path):
    project = tmp_path / "media"
    for index in range(3):
        folder = project / f"part{index}"
        folder.mkdir(parents=True)
        for name in range(4):
            (folder / f"clip{name}.bin").write_bytes(b"x")
    with patch.object(task_verification, "WALK_PATH_LIMIT", 5):
        state = task_verification.project_state(str(project), tmp_path / "ledger.sqlite")
    assert state["fingerprint"] is None
    assert "too large to fingerprint" in state["error"]
    small = task_verification.project_state(str(project), tmp_path / "ledger.sqlite")
    assert small["error"] is None and small["fingerprint"]


def test_walk_outside_git_fails_closed_past_the_time_limit(tmp_path: Path):
    project = tmp_path / "slow"
    (project / "a").mkdir(parents=True)
    (project / "a" / "file.txt").write_text("x")
    with patch.object(task_verification, "WALK_SECONDS", -1.0):
        state = task_verification.project_state(str(project), tmp_path / "ledger.sqlite")
    assert "too large to fingerprint" in state["error"]
