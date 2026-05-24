from pathlib import Path

from hipson.approvals import ApprovalPolicy
from hipson.sandbox import check_read_path, check_write_path, is_allowlisted_read_only_command
from hipson.tools import PathPolicy, ToolContext, ToolResult, ToolSpec, build_default_registry


def test_approval_policy_matrix_and_metadata(tmp_path: Path):
    policy = ApprovalPolicy()
    context = ToolContext(cwd=tmp_path, repo_root=None, session_id="test")

    read = policy.evaluate("read", {"path": "."}, context)
    write_generated = policy.evaluate("write", {"output": "runs/packet.md"}, context)
    write_source = policy.evaluate("write", {"output": "src/change.py"}, context)
    external = policy.evaluate("external", {}, context)
    external_dry = policy.evaluate("external", {}, context, dry_run=True)
    external_fake = policy.evaluate("external", {}, context, fake_provider=True)
    exec_read_only = policy.evaluate("exec", {"cmd": ["git", "status", "--short"]}, context)
    exec_needs_approval = policy.evaluate("exec", {"cmd": ["python", "script.py"]}, context)
    dangerous = policy.evaluate("dangerous", {}, context)

    assert read.allowed is True
    assert write_generated.allowed is True
    assert write_source.requires_approval is True
    assert external.requires_approval is True
    assert external_dry.allowed is True
    assert external_fake.allowed is True
    assert exec_read_only.allowed is True
    assert exec_needs_approval.requires_approval is True
    assert dangerous.blocked is True
    assert dangerous.to_metadata() == {
        "allowed": False,
        "requires_approval": False,
        "blocked": True,
        "risk_level": "dangerous",
        "reason": "Dangerous actions are blocked by default",
    }


def test_sandbox_refuses_sensitive_paths_traversal_and_broad_profiles(tmp_path: Path):
    read_ok = check_read_path(".", tmp_path)
    sensitive = check_read_path(".env", tmp_path)
    traversal = check_read_path("../outside", tmp_path)
    broad_home = check_read_path(str(Path.home()), tmp_path)
    windows_profile = check_read_path("/mnt/c/Users/marci", tmp_path)
    generated_write = check_write_path("runs/packet.md", tmp_path)
    source_write = check_write_path("src/app.py", tmp_path)

    assert read_ok.allowed is True
    assert sensitive.allowed is False
    assert traversal.allowed is False
    assert broad_home.allowed is False
    assert windows_profile.allowed is False
    assert generated_write.allowed is True
    assert source_write.allowed is False


def test_sandbox_blocks_symlink_escapes_sensitive_names_and_precise_write_roots(tmp_path: Path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    outside_secret = outside / "secret.txt"
    outside_secret.write_text("outside", encoding="utf-8")
    (workspace / "runs").mkdir()
    (workspace / "runs" / "linked-secret").symlink_to(outside_secret)

    symlink_escape = check_read_path("runs/linked-secret", workspace)
    assert symlink_escape.allowed is False
    assert "active workspace" in symlink_escape.reason

    for sensitive_path in [
        ".env",
        ".env.local",
        ".ssh/id_rsa",
        ".aws/credentials",
        ".config/hipson/agents.env",
        ".gnupg/private.key",
        "certs/client.pem",
        "state/runtime.sqlite",
        "state/local.db",
    ]:
        decision = check_read_path(sensitive_path, workspace)
        assert decision.allowed is False, sensitive_path
        assert "Sensitive paths" in decision.reason

    for allowed_write in ["runs/report.md", "scans/latest.md", "docs/plan.md", "memory/notes.jsonl"]:
        decision = check_write_path(allowed_write, workspace)
        assert decision.allowed is True, allowed_write

    for blocked_write in ["src/app.py", "README.md", "tmp/report.md"]:
        decision = check_write_path(blocked_write, workspace)
        assert decision.allowed is False, blocked_write
        assert "runs/, scans/, docs/, or memory/" in decision.reason


def test_approval_policy_can_evaluate_registered_tool_specs(tmp_path: Path):
    registry = build_default_registry()
    context = ToolContext(cwd=tmp_path, repo_root=None, session_id="test")
    policy = ApprovalPolicy()

    scan = policy.evaluate_tool(registry.get("repo.scan"), {"path": "."}, context)
    packet = policy.evaluate_tool(
        registry.get("packet.review.create"),
        {"project": ".", "title": "Review", "output": "runs/review.md"},
        context,
    )

    assert scan.allowed is True
    assert packet.allowed is True


def test_approval_policy_checks_declared_path_fields(tmp_path: Path):
    registry = build_default_registry()
    context = ToolContext(cwd=tmp_path, repo_root=None, session_id="test")
    policy = ApprovalPolicy()

    memory_home = policy.evaluate_tool(
        registry.get("memory.search"),
        {"query": "x", "memory_dir": str(Path.home())},
        context,
    )
    memory_traversal = policy.evaluate_tool(
        registry.get("memory.search"),
        {"query": "x", "memory_dir": "../memory"},
        context,
    )
    packet_outside = policy.evaluate_tool(
        registry.get("packet.review.create"),
        {"project": ".", "title": "Review", "output": "src/review.md"},
        context,
    )
    skill_home = policy.evaluate_tool(
        registry.get("skill.view"),
        {"root": str(Path.home()), "name": "x"},
        context,
    )

    assert memory_home.blocked is True
    assert "Broad home/profile paths" in memory_home.reason
    assert memory_traversal.blocked is True
    assert "Path traversal" in memory_traversal.reason
    assert packet_outside.blocked is True
    assert "Write path must be under runs/" in packet_outside.reason
    assert skill_home.blocked is True
    assert "Broad home/profile paths" in skill_home.reason


def test_exec_allowlist_is_read_only_and_narrow():
    assert is_allowlisted_read_only_command(["git", "status", "--short"]) is True
    assert is_allowlisted_read_only_command(["git", "diff"]) is True
    assert is_allowlisted_read_only_command(["git", "commit", "-m", "x"]) is False
    assert is_allowlisted_read_only_command(["rm", "-rf", "."]) is False


def test_approval_policy_fault_injection_fails_closed_for_overrides_and_unsafe_paths(tmp_path: Path):
    policy = ApprovalPolicy()
    context = ToolContext(cwd=tmp_path, repo_root=None, session_id="test")

    dangerous = policy.evaluate("dangerous", {}, context, approved=True, fake_provider=True, dry_run=True)
    external = policy.evaluate("external", {}, context)
    write_source_approved = policy.evaluate("write", {"output": "src/app.py"}, context, approved=True)
    write_traversal_approved = policy.evaluate("write", {"output": "../runs/report.md"}, context, approved=True)
    read_sensitive_approved = policy.evaluate("read", {"path": ".ssh/id_rsa"}, context, approved=True)

    assert dangerous.blocked is True
    assert external.requires_approval is True
    assert write_source_approved.allowed is False
    assert write_source_approved.requires_approval is True
    assert write_traversal_approved.blocked is True
    assert read_sensitive_approved.blocked is True


def test_approval_policy_rejects_declared_path_field_with_wrong_type_before_risk(tmp_path: Path):
    spec = ToolSpec(
        name="demo.path_type",
        description="Path field must be a string.",
        input_schema={"required": {"path": "object"}, "optional": {}},
        output_contract={"ok": "bool"},
        risk_level="read",
        approval_required=False,
        handler=lambda _input_data, _context: ToolResult(ok=True, output={"ok": True}, summary="ok"),
        path_policies=(PathPolicy("path", "read_workspace"),),
    )
    context = ToolContext(cwd=tmp_path, repo_root=None, session_id="test")

    decision = ApprovalPolicy().evaluate_tool(spec, {"path": ["not", "a", "path"]}, context)

    assert decision.allowed is False
    assert decision.blocked is True
    assert "path value must be a string" in decision.reason
