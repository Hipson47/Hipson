from pathlib import Path

from hipson.learning import LearningError, propose_from_session
from hipson.session import open_session_store


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
