from pathlib import Path

from hipson import memory as hipson_memory
from hipson.tools import ToolContext, ToolRegistry, ToolRegistryError, ToolResult, ToolSpec, build_default_registry


def test_tool_registry_rejects_duplicates_unknown_tools_and_bad_input(tmp_path: Path):
    registry = ToolRegistry()
    spec = ToolSpec(
        name="demo.echo",
        description="Echo a required value.",
        input_schema={"required": {"value": "str"}, "optional": {}},
        output_contract={"value": "str"},
        risk_level="read",
        approval_required=False,
        handler=lambda input_data, _context: ToolResult(
            ok=True,
            output={"value": input_data["value"]},
            summary="echoed",
        ),
    )
    context = ToolContext(cwd=tmp_path, repo_root=None, session_id="test")

    registry.register(spec)
    assert registry.run("demo.echo", {"value": "ok"}, context).output == {"value": "ok"}

    for action in [
        lambda: registry.register(spec),
        lambda: registry.run("missing.tool", {}, context),
        lambda: registry.run("demo.echo", {}, context),
        lambda: registry.run("demo.echo", {"value": 1}, context),
        lambda: registry.run("demo.echo", {"value": "ok", "extra": "no"}, context),
    ]:
        try:
            action()
        except ToolRegistryError:
            pass
        else:
            raise AssertionError("Expected registry validation failure")


def test_default_tool_registry_exposes_initial_mvp_tools():
    registry = build_default_registry()
    specs = {spec.name: spec for spec in registry.list()}

    assert set(specs) == {
        "memory.search",
        "packet.review.create",
        "repo.changed_files",
        "repo.scan",
        "skill.list",
        "skill.view",
    }
    assert specs["repo.scan"].risk_level == "read"
    assert specs["packet.review.create"].risk_level == "write"
    assert specs["packet.review.create"].approval_required is False
    assert specs["skill.list"].risk_level == "read"
    assert specs["skill.view"].approval_required is False


def test_repo_tools_return_structured_redacted_outputs(tmp_path: Path):
    registry = build_default_registry()
    context = ToolContext(cwd=tmp_path, repo_root=None, session_id="test")

    scan = registry.run("repo.scan", {"path": ".", "include_diff": False, "diff_lines": 3}, context)
    changed = registry.run("repo.changed_files", {"path": "."}, context)

    assert scan.ok is True
    assert "# Hipson Delta Scan" in str(scan.output["markdown"])
    assert scan.output["changed_files"] == []
    assert isinstance(scan.output["commands"], list)
    assert changed.output == {"changed_files": [], "untracked_files": []}


def test_memory_search_tool_uses_local_memory_dir(tmp_path: Path):
    hipson_memory.add_note(
        root=tmp_path / "memory",
        scope="repo",
        repo="Hipson",
        kind="decision",
        summary="Keep runtime provider-free by default.",
        tags=["runtime"],
    )
    registry = build_default_registry()
    context = ToolContext(cwd=tmp_path, repo_root=None, session_id="test")

    result = registry.run("memory.search", {"query": "provider-free", "repo": "Hipson", "limit": 5}, context)

    assert result.ok is True
    assert result.output["results"]
    first = result.output["results"][0]
    assert first["kind"] == "decision"
    assert first["summary"] == "Keep runtime provider-free by default."


def test_review_packet_tool_writes_only_allowed_workspace_paths(tmp_path: Path):
    registry = build_default_registry()
    context = ToolContext(cwd=tmp_path, repo_root=None, session_id="test")

    result = registry.run(
        "packet.review.create",
        {
            "project": ".",
            "title": "Review runtime plan",
            "scope": "current git delta",
            "include_diff": False,
            "output": "runs/review-packet.md",
        },
        context,
    )
    blocked = registry.run(
        "packet.review.create",
        {
            "project": ".",
            "title": "Review runtime plan",
            "output": "../outside.md",
        },
        context,
    )

    assert result.ok is True
    packet_path = Path(str(result.output["path"]))
    assert packet_path == tmp_path / "runs" / "review-packet.md"
    assert packet_path.exists()
    assert "Agent Review Packet" in packet_path.read_text(encoding="utf-8")
    assert blocked.ok is False
    assert not (tmp_path.parent / "outside.md").exists()
