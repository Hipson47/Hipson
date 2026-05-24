from pathlib import Path

from hipson.gateway import MCPBridge


def test_mcp_bridge_lists_only_safe_read_tools_by_default():
    bridge = MCPBridge()
    tools = {tool["name"]: tool for tool in bridge.list_tools()}

    assert "repo.changed_files" in tools
    assert "memory.search" in tools
    assert "skill.list" in tools
    assert "packet.review.create" not in tools
    assert all(tool["risk_level"] == "read" for tool in tools.values())


def test_mcp_bridge_can_call_safe_read_tool(tmp_path: Path):
    bridge = MCPBridge()

    result = bridge.call_tool("repo.changed_files", {"path": "."}, cwd=tmp_path)

    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["output"] == {"changed_files": [], "untracked_files": []}


def test_mcp_bridge_does_not_run_approval_gated_write_tool(tmp_path: Path):
    bridge = MCPBridge()

    result = bridge.call_tool(
        "packet.review.create",
        {
            "project": ".",
            "title": "Review",
            "output": "runs/review.md",
        },
        cwd=tmp_path,
        approved=True,
    )

    assert result["ok"] is False
    assert result["status"] == "approval_required"
    assert not (tmp_path / "runs" / "review.md").exists()


def test_mcp_bridge_blocks_sensitive_paths_and_redacts_errors(tmp_path: Path):
    bridge = MCPBridge()
    secret = tmp_path / ".env"
    secret.write_text("OPENROUTER_API_KEY=sk-test-secret1234567890", encoding="utf-8")

    result = bridge.call_tool("repo.changed_files", {"path": ".env"}, cwd=tmp_path)

    rendered = str(result)
    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert "sk-test-secret1234567890" not in rendered
    assert ".env" not in str(result["error"])


def test_mcp_bridge_rejects_unknown_tool():
    result = MCPBridge().call_tool("missing.tool", {}, cwd=Path.cwd())

    assert result["ok"] is False
    assert result["status"] == "rejected"
    assert "Unknown tool" in str(result["error"])
