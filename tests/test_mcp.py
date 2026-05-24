from pathlib import Path

from hipson.approvals import ApprovalPolicy
from hipson.gateway import MCPBridge
from hipson.tools import ToolRegistry, ToolResult, ToolSpec


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


def test_mcp_bridge_does_not_run_approval_required_read_tool_even_when_approved(tmp_path: Path):
    called = False

    def handler(_input_data: dict[str, object], _context) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(ok=True, output={"ok": True}, summary="read ran")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="read.approval_required",
            description="Read tool that is not safe-listed.",
            input_schema={"required": {}, "optional": {}},
            output_contract={"ok": "bool"},
            risk_level="read",
            approval_required=True,
            handler=handler,
        )
    )
    bridge = MCPBridge(registry=registry, approval_policy=ApprovalPolicy())

    result = bridge.call_tool("read.approval_required", {}, cwd=tmp_path, approved=True)

    assert called is False
    assert result["ok"] is False
    assert result["status"] == "approval_required"


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


def test_mcp_bridge_applies_memory_dir_path_policy(tmp_path: Path):
    bridge = MCPBridge()

    result = bridge.call_tool("memory.search", {"query": "x", "memory_dir": str(Path.home())}, cwd=tmp_path)

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert "Broad home/profile paths" in str(result["error"])


def test_mcp_bridge_bounds_provider_visible_tool_output(tmp_path: Path):
    secret = "sk-test-secret1234567890"
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="demo.large",
            description="Large MCP output.",
            input_schema={"required": {}, "optional": {}},
            output_contract={"markdown": "str"},
            risk_level="read",
            approval_required=False,
            handler=lambda _input_data, _context: ToolResult(
                ok=True,
                output={"markdown": f"{secret}\n" + ("x" * 8_000)},
                summary="large mcp output",
            ),
        )
    )
    bridge = MCPBridge(registry=registry, approval_policy=ApprovalPolicy())

    result = bridge.call_tool("demo.large", {}, cwd=tmp_path)

    rendered = str(result["output"])
    assert result["ok"] is True
    assert secret not in rendered
    assert len(rendered) < 1_400
    assert "truncated" in rendered


def test_mcp_bridge_rejects_unknown_tool():
    result = MCPBridge().call_tool("missing.tool", {}, cwd=Path.cwd())

    assert result["ok"] is False
    assert result["status"] == "rejected"
    assert "Unknown tool" in str(result["error"])
