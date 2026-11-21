"""Disposable multi-project ledger for the agent UX measurements."""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from hipson import ledger

LEAD = "lead"
CHECK = [sys.executable, "-I", "-c", "from pathlib import Path; assert Path('fixture.txt').is_file()"]


@dataclass(frozen=True)
class AgentUxFixture:
    db: Path
    heavy_task: str


def _git(project: Path, *args: str) -> None:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    subprocess.run(["git", "-C", str(project), *args], env=env, check=True, capture_output=True, timeout=20)


def _commit(project: Path, subject: str) -> None:
    _git(project, "add", ".")
    _git(project, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "-qm", subject)


def _project(project: Path, *, inventory_files: int = 0) -> Path:
    project.mkdir()
    _git(project, "init", "-q", "--template=")
    _git(project, "config", "core.autocrlf", "false")
    (project / "fixture.txt").write_bytes(b"initial revision\n")
    if inventory_files:
        sources = project / "src" / "inventory"
        sources.mkdir(parents=True)
        for index in range(inventory_files):
            (sources / f"entry-{index:04d}.txt").write_bytes(b"fixture\n")
    _commit(project, "Initial fixture")
    return project


def _verify(db: Path, task: str) -> None:
    # Workers can build this synthetic ledger without their real assignment leaking into the check.
    env = {key: value for key, value in os.environ.items() if key not in {
        "HIPSON_ROLE", "HIPSON_TASK", "HIPSON_ASSIGNMENT",
    }}
    env["PYTHONPATH"] = str(Path(ledger.__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, "-m", "hipson.cli", "task", "verify", "--db", str(db), "--task", task, "--timeout", "20"],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Fixture verification failed for {task}: {result.stdout}{result.stderr}")


def _open(store: ledger.Ledger, project: Path, title: str, *, review: bool = False) -> str:
    return store.open_task(
        project=project, title=title, objective="Track work on the generated project files", lead=LEAD,
        acceptance=["The fixture marker is present"], checks=[CHECK], review_policy="required" if review else "none",
    )["id"]


def build(root: Path) -> AgentUxFixture:
    """Populate a fresh directory owned by the caller, usually a temporary directory."""
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Roughly 300 kB of JSON metadata across the files, with only a few kB of file contents.
    heavy = _project(root / "inventory", inventory_files=1050)
    tools = _project(root / "tools")
    docs = _project(root / "docs")
    db = root / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        heavy_review = _open(store, heavy, "Review the inventory update", review=True)
        stale = _open(store, heavy, "Recheck the previous inventory revision")
        store.set_status(stale, to="in_progress", actor=LEAD)
        _verify(db, stale)
        (heavy / "fixture.txt").write_bytes(b"next revision\n")
        _commit(heavy, "Update the fixture marker")
        for index in range(1, 11):
            _open(store, heavy, f"Inventory follow-up {index}")

        docs_review = _open(store, docs, "Review the documentation update", review=True)
        for task in (heavy_review, docs_review):
            store.set_status(task, to="in_progress", actor=LEAD)
            _verify(db, task)
            store.set_status(task, to="review", actor=LEAD)

        active = _open(store, tools, "Keep the fixture marker assigned")
        store.assign(active, actor="builder", role="builder", mode="rw", scope=["fixture.txt"])
        delivery = _open(store, tools, "Acknowledge the fixture delivery")
        assignment = store.assign(delivery, actor="builder", role="builder", mode="rw", scope=["notes.txt"])
        summary = "Prepared fixture notes for review"
        store.finish(assignment["id"], result="passed", summary=summary, worker_result={
            "status": "completed", "summary": summary, "files_changed": [], "commands": [], "findings": [], "risks": [],
        })
        for project, severity in ((tools, "blocking"), (docs, "major")):
            task = _open(store, project, f"Resolve the {severity} review finding")
            store.add_finding(task, actor="reviewer", severity=severity, title="Document the marker format",
                              location="fixture.txt:1")

        # Ledger has no clock injection API. Patch its timestamp source so normal writes
        # create consistent task and event timestamps without modifying the SQLite schema.
        old_stamp = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        with patch.object(ledger, "_timestamp", return_value=old_stamp):
            for project, status in ((tools, "open"), (docs, "in_progress"), (docs, "open")):
                task = _open(store, project, f"Dormant {project.name} task ({status})")
                if status == "in_progress":
                    store.set_status(task, to=status, actor=LEAD)

        _open(store, tools, "Plan the next tool update")
        _open(store, docs, "Plan the next documentation update")
    return AgentUxFixture(db, heavy_review)
