from pathlib import Path

from hipson.approvals import ApprovalPolicy
from hipson.sandbox import check_read_path, check_write_path, is_allowlisted_read_only_command
from hipson.tools import ToolContext, build_default_registry


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


def test_exec_allowlist_is_read_only_and_narrow():
    assert is_allowlisted_read_only_command(["git", "status", "--short"]) is True
    assert is_allowlisted_read_only_command(["git", "diff"]) is True
    assert is_allowlisted_read_only_command(["git", "commit", "-m", "x"]) is False
    assert is_allowlisted_read_only_command(["rm", "-rf", "."]) is False
