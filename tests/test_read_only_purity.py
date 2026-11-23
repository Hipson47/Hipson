"""Every read-only CLI command and MCP read tool leaves a v12 ledger with live WAL frames untouched.

Measured by hand during the 1.8.1 campaign on copies of a real 37 MB ledger (byte-identical before and after);
this keeps it as a regression test, with an open writer whose committed frames are still only in the -wal.
"""

import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from hipson import ledger
from tests.test_task_verification import _project


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _snapshot(db: Path) -> dict[str, object]:
    return {"db": _digest(db), "wal": _digest(Path(f"{db}-wal")),
            "listing": sorted(path.name for path in db.parent.iterdir())}


def _cli(db: Path, *argv: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "hipson.cli", *argv[:2], "--db", str(db), *argv[2:]], cwd=cwd,
                          capture_output=True, text=True, timeout=120)


def test_reads_never_touch_a_ledger_whose_writer_still_holds_wal_frames(tmp_path):
    project = _project(tmp_path)
    db = tmp_path / "ledger" / "ledger.sqlite"
    db.parent.mkdir()
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=project, title="pure", objective="reads stay reads", checks=[["true"]])["id"]
        store.set_status(task, actor="lead", to="in_progress")
        assignment = store.assign(task, actor="w", role="reviewer", mode="ro")["id"]
        store.finish(assignment, result="passed", summary="reviewed")
        store.add_finding(task, actor="w", severity="minor", title="note", detail="d", location="x")
    # A writer that keeps its connection open with committed frames only in the WAL, as a running lead would.
    writer = sqlite3.connect(db, isolation_level=None)
    try:
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE tasks SET objective = objective || ' (live)' WHERE id = ?", (task,))
        writer.execute("COMMIT")
        assert Path(f"{db}-wal").stat().st_size > 0
        before = _snapshot(db)
        reads = [
            ("task", "board", "--json"), ("task", "board", "--detail", "compact", "--json"),
            ("task", "now", "--json"), ("task", "now", "--detail", "compact"),
            ("task", "list", "--json"),
            ("task", "show", "--task", task, "--json"), ("task", "show", "--task", task, "--detail", "compact"),
            *[("task", "show", "--task", task, "--section", section, "--json")
              for section in ("verification", "assignments", "findings", "events")],
            ("task", "events", "--task", task, "--json"), ("task", "finding", "list", "--json"),
            ("task", "activity", "--assignment", assignment, "--json"),
            ("task", "cleanup", "--project", str(project), "--json"),
        ]
        for argv in reads:
            proc = _cli(db, *argv, cwd=project)
            assert proc.returncode in (0, 1), (argv, proc.returncode, proc.stderr[-400:])  # cleanup refuses: 1.
            assert _snapshot(db) == before, argv
        resume = subprocess.run([sys.executable, "-m", "hipson.cli", "lead", "resume", "--db", str(db),
                                 "--detail", "compact", "--json"], cwd=project, capture_output=True, text=True,
                                timeout=120)
        # Resume reads without creating sidecars, so with frames only in the WAL it refuses instead of guessing.
        assert resume.returncode == 0 or '"code": "ledger.wal_pending"' in resume.stderr, resume.stderr[-400:]
        assert _snapshot(db) == before
        calls = [("task.board", {"detail": "compact"}), ("task.now", {"detail": "compact"}),
                 ("lead.resume", {"detail": "compact"}), ("task.show", {"task": task}),
                 ("task.show", {"task": task, "section": "events"}), ("task.cleanup", {"project": str(project)})]
        lines = [json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})]
        lines += [json.dumps({"jsonrpc": "2.0", "id": index + 1, "method": "tools/call",
                              "params": {"name": name, "arguments": arguments}})
                  for index, (name, arguments) in enumerate(calls)]
        mcp = subprocess.run([sys.executable, "-m", "hipson.cli", "mcp", "serve", "--stdio", "--project", str(project)],
                             input="\n".join(lines) + "\n", cwd=project, capture_output=True, text=True, timeout=120,
                             env={**__import__("os").environ, "HIPSON_LEDGER": str(db)})
        replies = [json.loads(line) for line in mcp.stdout.splitlines() if line.startswith("{")]
        assert len(replies) == len(calls) + 1, mcp.stderr[-400:]
        assert all("result" in reply for reply in replies), replies
        assert _snapshot(db) == before
        # The writer's frames are still readable and still only in the WAL.
        assert writer.execute("SELECT objective FROM tasks WHERE id = ?", (task,)).fetchone()[0].endswith("(live)")
    finally:
        writer.close()
