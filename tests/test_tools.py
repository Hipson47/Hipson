import contextlib
import io
import json
from pathlib import Path

from hipson import cli
from hipson import memory as hipson_memory
from hipson.tools import (
    PathPolicy,
    ToolContext,
    ToolRegistry,
    ToolRegistryError,
    ToolResult,
    ToolSpec,
    bounded_tool_output,
    build_default_registry,
)


def run_cli(*args: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = cli.main(list(args))
    except SystemExit as exc:
        rc = int(exc.code or 0) if isinstance(exc.code, int) else 1
    return rc, stdout.getvalue(), stderr.getvalue()


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


def test_tool_specs_require_path_policy_for_path_like_inputs():
    try:
        ToolSpec(
            name="demo.path",
            description="Missing path policy.",
            input_schema={"required": {"path": "str"}, "optional": {}},
            output_contract={"ok": "bool"},
            risk_level="read",
            approval_required=False,
            handler=lambda _input_data, _context: ToolResult(ok=True, output={"ok": True}, summary="ok"),
        )
    except ToolRegistryError as exc:
        assert "path-like input needs path policy" in str(exc)
    else:
        raise AssertionError("Expected missing path policy to fail")


def test_tool_registry_enforces_output_contracts_and_handler_boundaries(tmp_path: Path):
    registry = ToolRegistry()
    context = ToolContext(cwd=tmp_path, repo_root=None, session_id="test")
    registry.register(
        ToolSpec(
            name="demo.valid",
            description="Valid output.",
            input_schema={"required": {}, "optional": {}},
            output_contract={"value": "str", "count": "int"},
            risk_level="read",
            approval_required=False,
            handler=lambda _input_data, _context: ToolResult(
                ok=True,
                output={"value": "ok", "count": 1},
                summary="ok",
            ),
        )
    )
    registry.register(
        ToolSpec(
            name="demo.missing",
            description="Missing required output.",
            input_schema={"required": {}, "optional": {}},
            output_contract={"value": "str"},
            risk_level="read",
            approval_required=False,
            handler=lambda _input_data, _context: ToolResult(ok=True, output={}, summary="bad"),
        )
    )
    registry.register(
        ToolSpec(
            name="demo.wrong",
            description="Wrong output type.",
            input_schema={"required": {}, "optional": {}},
            output_contract={"value": "str"},
            risk_level="read",
            approval_required=False,
            handler=lambda _input_data, _context: ToolResult(ok=True, output={"value": 123}, summary="bad"),
        )
    )
    registry.register(
        ToolSpec(
            name="demo.raises",
            description="Handler raises.",
            input_schema={"required": {}, "optional": {}},
            output_contract={"value": "str"},
            risk_level="read",
            approval_required=False,
            handler=lambda _input_data, _context: (_ for _ in ()).throw(OSError("OPENROUTER_API_KEY=sk-test-secret1234567890")),
        )
    )

    assert registry.run("demo.valid", {}, context).ok is True
    missing = registry.run("demo.missing", {}, context)
    wrong = registry.run("demo.wrong", {}, context)
    raised = registry.run("demo.raises", {}, context)

    assert missing.ok is False
    assert "missing required key" in missing.error
    assert wrong.ok is False
    assert "must be str" in wrong.error
    assert raised.ok is False
    assert "OSError" in raised.error
    assert "sk-test-secret1234567890" not in raised.error


def test_tool_registry_fault_injection_rejects_bool_as_int_and_json_decode_errors(tmp_path: Path):
    registry = ToolRegistry()
    context = ToolContext(cwd=tmp_path, repo_root=None, session_id="test")
    registry.register(
        ToolSpec(
            name="demo.count",
            description="Requires a real integer and returns a real integer.",
            input_schema={"required": {"count": "int"}, "optional": {}},
            output_contract={"count": "int"},
            risk_level="read",
            approval_required=False,
            handler=lambda input_data, _context: ToolResult(
                ok=True,
                output={"count": input_data["count"]},
                summary="counted",
            ),
        )
    )
    registry.register(
        ToolSpec(
            name="demo.bool_output",
            description="Returns bool where int is required.",
            input_schema={"required": {}, "optional": {}},
            output_contract={"count": "int"},
            risk_level="read",
            approval_required=False,
            handler=lambda _input_data, _context: ToolResult(ok=True, output={"count": True}, summary="bad"),
        )
    )
    registry.register(
        ToolSpec(
            name="demo.json_error",
            description="Raises a JSON decode error.",
            input_schema={"required": {}, "optional": {}},
            output_contract={"value": "str"},
            risk_level="read",
            approval_required=False,
            handler=lambda _input_data, _context: json.loads("{"),
        )
    )

    try:
        registry.run("demo.count", {"count": True}, context)
    except ToolRegistryError as exc:
        assert "count must be int" in str(exc)
    else:
        raise AssertionError("Expected bool input to be rejected for int schema")

    bad_output = registry.run("demo.bool_output", {}, context)
    json_error = registry.run("demo.json_error", {}, context)

    assert bad_output.ok is False
    assert "must be int" in bad_output.error
    assert json_error.ok is False
    assert "JSONDecodeError" in json_error.error


def test_tool_registry_type_contracts_cover_unions_null_lists_and_unsupported_types(tmp_path: Path):
    registry = ToolRegistry()
    context = ToolContext(cwd=tmp_path, repo_root=None, session_id="test")
    registry.register(
        ToolSpec(
            name="demo.types",
            description="Exercise composite dependency-free type contracts.",
            input_schema={
                "required": {"items": "list[dict]", "selector": "str|int"},
                "optional": {"maybe": "str|null"},
            },
            output_contract={
                "required": {"items": "list[dict]", "selector": "str|int"},
                "optional": {"maybe": "str|null"},
            },
            risk_level="read",
            approval_required=False,
            handler=lambda input_data, _context: ToolResult(ok=True, output=dict(input_data), summary="typed"),
        )
    )
    registry.register(
        ToolSpec(
            name="demo.unsupported_output",
            description="Returns a value with an unsupported declared type.",
            input_schema={"required": {}, "optional": {}},
            output_contract={"value": "tuple"},
            risk_level="read",
            approval_required=False,
            handler=lambda _input_data, _context: ToolResult(ok=True, output={"value": ("x",)}, summary="bad"),
        )
    )

    valid = registry.run("demo.types", {"items": [{"a": 1}], "selector": 1, "maybe": None}, context)
    unsupported = registry.run("demo.unsupported_output", {}, context)

    assert valid.ok is True
    assert valid.output["maybe"] is None
    for bad_input in [
        {"items": [{"a": 1}], "selector": None},
        {"items": ["not a dict"], "selector": "name"},
    ]:
        try:
            registry.run("demo.types", bad_input, context)
        except ToolRegistryError as exc:
            assert "demo.types input" in str(exc)
        else:
            raise AssertionError(f"Expected bad composite input to fail: {bad_input}")

    assert unsupported.ok is False
    assert "Unsupported schema type: tuple" in unsupported.error


def test_bounded_tool_output_redacts_and_summarizes_nested_large_values():
    secret = "sk-test-secret1234567890"
    output = {
        "markdown": f"token={secret}\n" + ("x" * 8_000),
        "items": [{"value": index, "secret": secret} for index in range(50)],
        "metadata": {f"key_{index}": f"{secret}-{index}" for index in range(40)},
    }
    result = ToolResult(ok=True, output=output, summary="large nested output", artifacts=tuple(str(i) for i in range(50)))

    bounded = bounded_tool_output(result)
    rendered = str(bounded)

    assert secret not in rendered
    assert "[REDACTED]" in rendered
    assert "truncated" in rendered
    assert len(rendered) < 4_500


def test_tool_cli_lists_and_shows_registry_metadata():
    rc, stdout, stderr = run_cli("tool", "list")
    assert rc == 0
    assert stderr == ""
    assert "repo.scan risk=read" in stdout
    assert "packet.review.create risk=write" in stdout

    rc, stdout, stderr = run_cli("tool", "show", "memory.search", "--json")
    assert rc == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["name"] == "memory.search"
    assert payload["risk_level"] == "read"
    assert payload["approval_required"] is False
    assert payload["input_schema"]["required"]["query"] == "str"
    assert payload["path_policies"] == [
        {"field": "memory_dir", "mode": "read_memory_store", "base_field": ""}
    ]


def test_tool_cli_unknown_tool_fails_cleanly():
    rc, stdout, stderr = run_cli("tool", "show", "missing.tool")
    assert rc == 1
    assert stdout == ""
    assert "Unknown tool: missing.tool" in stderr


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
    assert specs["repo.scan"].path_policies == (PathPolicy("path", "read_workspace"),)
    assert specs["memory.search"].path_policies == (PathPolicy("memory_dir", "read_memory_store"),)
    assert specs["packet.review.create"].risk_level == "write"
    assert PathPolicy("output", "write_generated") in specs["packet.review.create"].path_policies
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
