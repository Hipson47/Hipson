import sys
from pathlib import Path
from unittest import SkipTest

from hipson.approvals import ApprovalPolicy
from hipson.sandbox import check_read_path, check_skill_root_path, check_write_path, is_allowlisted_read_only_command
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
    windows_profile = check_read_path("/mnt/c/Users/example", tmp_path)
    generated_write = check_write_path("runs/packet.md", tmp_path)
    source_write = check_write_path("src/app.py", tmp_path)

    assert read_ok.allowed is True
    assert sensitive.allowed is False
    assert traversal.allowed is False
    assert broad_home.allowed is False
    assert windows_profile.allowed is False
    assert generated_write.allowed is True
    assert source_write.allowed is False


def test_sandbox_blocks_symlink_escapes(tmp_path: Path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    outside_secret = outside / "secret.txt"
    outside_secret.write_text("outside", encoding="utf-8")
    (workspace / "runs").mkdir()
    try:
        (workspace / "runs" / "linked-secret").symlink_to(outside_secret)
    except OSError as exc:
        if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
            raise SkipTest("Windows symlinks require Developer Mode or SeCreateSymbolicLinkPrivilege") from exc
        raise

    symlink_escape = check_read_path("runs/linked-secret", workspace)
    assert symlink_escape.allowed is False
    assert "active workspace" in symlink_escape.reason


def test_sandbox_blocks_sensitive_names_and_precise_write_roots(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
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


def test_approval_policy_legacy_path_keys_all_get_sandbox_checks(tmp_path: Path):
    policy = ApprovalPolicy()
    context = ToolContext(cwd=tmp_path, repo_root=None, session_id="test")

    for key in ("path", "project", "packet", "source"):
        decision = policy.evaluate("read", {key: "../outside"}, context)

        assert decision.blocked is True, key
        assert decision.allowed is False, key
        assert "Path traversal is not allowed" in decision.reason


def test_sandbox_skill_root_allows_relative_workspace_and_packaged_assets(tmp_path: Path):
    workspace_skill_root = tmp_path / "skills"
    workspace_skill_root.mkdir()

    relative = check_skill_root_path("skills", tmp_path)
    packaged = check_skill_root_path(Path(__file__).resolve().parents[1] / "skills", tmp_path)
    outside = check_skill_root_path(tmp_path.parent, tmp_path)

    assert relative.allowed is True
    assert relative.path == workspace_skill_root.resolve()
    assert packaged.allowed is True
    assert outside.allowed is False
    assert "active workspace or packaged Hipson assets" in outside.reason


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


def test_audit_h05_git_arguments_reject_writes_and_helpers():
    denied = [
        ["branch", "-D", "x"], ["branch", "newname"], ["branch", "-m", "old", "new"],
        ["branch", "--delete", "x"], ["branch", "--edit-description"], ["branch", "-f", "x"],
        ["diff", "--output=file"], ["diff", "--output", "file"], ["show", "--output=file"],
        ["log", "--output=file"], ["show", "-ofile"], ["show", "-o", "file"],
        ["diff", "--ext-diff"], ["show", "--textconv"], ["log", "--ext-diff"],
        ["-c", "diff.external=command", "diff"], ["-cdiff.external=command", "diff"],
        ["branch", "--list", "--delete", "x"], ["diff", "--out=file"], ["show", "--textc"],
        ["branch", "-"], ["branch", "--all=yes"], ["branch", "--list=x"],
        ["branch", "--format"], ["branch", "--sort", "--delete"], ["branch", "--unknown"],
        ["branch", "--show-current", "newname"], ["-cfoo=bar", "status"],
        ["branch", "--format", "value", "--delete", "x"], ["branch", "-d"],
        ["branch", "-X"], ["branch", "-avX"], ["show", "--output=file=name"],
    ]
    for arguments in denied:
        assert not is_allowlisted_read_only_command(["git", *arguments]), arguments
    for arguments in (["status", "--short"], ["diff", "--stat"], ["log", "-1"], ["show", "HEAD:README.md"],
                      ["branch"], ["branch", "-arv"], ["branch", "-avv"], ["branch", "--list", "feature/*"],
                      ["branch", "--show-current"], ["diff", "--", "--output=file"]):
        assert is_allowlisted_read_only_command(["git", *arguments]), arguments
    for option in ("--all", "--remotes", "--verbose", "--no-color", "--no-column", "--ignore-case",
                   "--contains", "--no-contains", "--merged", "--no-merged", "--color", "--column"):
        assert is_allowlisted_read_only_command(["git", "branch", option]), option
    for option in ("--format", "--sort", "--points-at"):
        assert is_allowlisted_read_only_command(["git", "branch", option + "=HEAD"]), option
        assert is_allowlisted_read_only_command(["git", "branch", option, "HEAD"]), option
    for arguments in ([], ["git"], ["other", "status"]):
        assert not is_allowlisted_read_only_command(arguments)
    assert is_allowlisted_read_only_command(["ls", "-l"])
    assert is_allowlisted_read_only_command(["pwd"])


def test_sandbox_rejects_broad_wsl_profiles_even_when_selected_as_workspace():
    # Choosing an over-broad workspace must not turn an entire Windows profile into readable input.
    for raw in ("/mnt/c/Users", "/mnt/c/Users/audit-user", "/mnt/c/uSeRs/audit-user"):
        profile = Path(raw).resolve()
        assert not check_read_path(profile, profile).allowed, raw
    project = Path("/mnt/c/Users/audit-user/project").resolve()
    assert check_read_path(project / "README.md", project).allowed
