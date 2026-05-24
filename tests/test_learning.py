import contextlib
import io
import json
from pathlib import Path

from hipson import cli
from hipson import memory as hipson_memory
from hipson.learning import LearningError, propose_from_session
from hipson.session import open_session_store


def run_cli(*args: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = cli.main(list(args))
    except SystemExit as exc:
        rc = int(exc.code or 0) if isinstance(exc.code, int) else 1
    return rc, stdout.getvalue(), stderr.getvalue()


def test_learning_proposes_redacted_memory_candidate_without_persisting(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    try:
        session_id = store.create_session(cwd=str(tmp_path), repo_root=str(tmp_path), title="Learning")
        store.add_message(session_id, "user", "Remember OPENROUTER_API_KEY=sk-test-secret1234567890")
        store.add_message(session_id, "assistant", "Use packet-first runtime hardening for next PR.")

        proposals = propose_from_session(store, session_id)
        memory_rows = store.connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    finally:
        store.close()

    memory = next(proposal for proposal in proposals if proposal.kind == "memory")
    rendered = str(memory.to_dict())
    assert memory.approval_required is True
    assert memory.approval_status == "proposed"
    assert "packet-first runtime hardening" in memory.summary
    assert "sk-test-secret1234567890" not in rendered
    assert memory.source_refs[0] == f"session:{session_id}"
    assert memory_rows == 0


def test_learning_proposes_skill_reference_from_session_messages(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    try:
        session_id = store.create_session(cwd=str(tmp_path), title="Skills")
        store.add_message(session_id, "user", "Please use the hipson-workflow skill for this repo review.")

        proposals = propose_from_session(store, session_id)
    finally:
        store.close()

    skill = next(proposal for proposal in proposals if proposal.kind == "skill_reference")
    assert skill.payload["skill"] == "hipson-workflow"
    assert skill.payload["usage"] == "reference_data_only"
    assert skill.approval_required is True
    assert skill.source_refs and str(skill.source_refs[0]).startswith("message:")


def test_learning_proposes_skill_reference_from_skill_view_tool_call(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    try:
        session_id = store.create_session(cwd=str(tmp_path), title="Skills")
        store.add_message(session_id, "user", "Show the matching reference.")
        store.add_tool_call(
            session_id,
            tool_name="skill.view",
            input_data={"name": "hipson-workflow"},
            output_data={"name": "hipson-workflow", "content": "bounded"},
            risk_level="read",
        )

        proposals = propose_from_session(store, session_id)
    finally:
        store.close()

    skill = next(proposal for proposal in proposals if proposal.kind == "skill_reference")
    assert skill.payload["skill"] == "hipson-workflow"
    assert skill.source_refs and str(skill.source_refs[0]).startswith("tool_call:")


def test_learning_missing_session_is_explicit(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    try:
        try:
            propose_from_session(store, "missing")
        except LearningError as exc:
            assert "Session does not exist" in str(exc)
        else:
            raise AssertionError("Expected missing session error")
    finally:
        store.close()


def test_learning_empty_session_has_no_candidates(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    try:
        session_id = store.create_session(cwd=str(tmp_path), title="Empty")
        proposals = propose_from_session(store, session_id)
    finally:
        store.close()

    assert proposals == []


def test_learning_proposal_ids_are_deterministic_for_apply_workflow(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    try:
        session_id = store.create_session(cwd=str(tmp_path), repo_root=str(tmp_path), title="Stable")
        store.add_message(session_id, "assistant", "Remember bounded runtime observability.")
        first = [proposal.to_dict() for proposal in propose_from_session(store, session_id)]
        second = [proposal.to_dict() for proposal in propose_from_session(store, session_id)]
    finally:
        store.close()

    assert first == second
    assert str(first[0]["id"]).startswith("learn_")


def test_learning_cli_propose_is_redacted_and_does_not_persist_memory(tmp_path: Path):
    secret = "sk-test-secret1234567890"
    db_path = tmp_path / "runtime.sqlite"
    memory_dir = tmp_path / "memory"
    store = open_session_store(db_path)
    try:
        session_id = store.create_session(cwd=str(tmp_path), repo_root=str(tmp_path), title="Learning CLI")
        store.add_message(session_id, "assistant", f"Persist only approved learning notes {secret}")
    finally:
        store.close()

    rc, stdout, stderr = run_cli("learn", "propose", "--session-id", session_id, "--session-db", str(db_path), "--json")
    assert rc == 0
    assert stderr == ""
    assert secret not in stdout
    payload = json.loads(stdout)
    memory_proposal = next(proposal for proposal in payload["proposals"] if proposal["kind"] == "memory")
    assert memory_proposal["approval_status"] == "proposed"
    assert memory_proposal["approval_required"] is True
    assert not (memory_dir / "notes.jsonl").exists()


def test_learning_cli_apply_memory_explicitly_writes_redacted_note_with_provenance(tmp_path: Path):
    secret = "sk-test-secret1234567890"
    db_path = tmp_path / "runtime.sqlite"
    memory_dir = tmp_path / "memory"
    store = open_session_store(db_path)
    try:
        session_id = store.create_session(cwd=str(tmp_path), repo_root=str(tmp_path), title="Learning Apply")
        store.add_message(session_id, "assistant", f"Use explicit apply-memory for runtime learning {secret}")
        proposal_id = next(
            proposal.id for proposal in propose_from_session(store, session_id) if proposal.kind == "memory"
        )
    finally:
        store.close()

    rc, stdout, stderr = run_cli(
        "learn",
        "apply-memory",
        "--session-id",
        session_id,
        "--proposal-id",
        proposal_id,
        "--memory-dir",
        str(memory_dir),
        "--session-db",
        str(db_path),
        "--json",
    )
    assert rc == 0
    assert stderr == ""
    assert secret not in stdout
    result = json.loads(stdout)
    assert result["status"] == "applied"
    assert result["proposal_id"] == proposal_id
    assert result["source_refs"][0] == f"session:{session_id}"

    notes = hipson_memory.load_notes(memory_dir)
    source_rows = hipson_memory.read_jsonl(memory_dir / "sources.jsonl")
    assert len(notes) == 1
    assert notes[0].kind == "handoff"
    assert secret not in notes[0].summary
    assert any(row["path"] == f"session:{session_id}" for row in source_rows)
    assert any(str(row["path"]).startswith("message:") for row in source_rows)


def test_learning_cli_apply_memory_refuses_non_memory_proposal(tmp_path: Path):
    db_path = tmp_path / "runtime.sqlite"
    store = open_session_store(db_path)
    try:
        session_id = store.create_session(cwd=str(tmp_path), title="Skill Draft")
        store.add_message(session_id, "assistant", "Use the hipson-workflow skill next time.")
        proposal_id = next(
            proposal.id for proposal in propose_from_session(store, session_id) if proposal.kind == "skill_reference"
        )
    finally:
        store.close()

    rc, stdout, stderr = run_cli(
        "learn",
        "apply-memory",
        "--session-id",
        session_id,
        "--proposal-id",
        proposal_id,
        "--memory-dir",
        str(tmp_path / "memory"),
        "--session-db",
        str(db_path),
    )
    assert rc == 1
    assert stdout == ""
    assert "only memory proposals can be applied" in stderr
