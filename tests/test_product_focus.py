"""1.7 product focus: experimental surfaces are hidden but callable, and agent guidance leads with the ledger."""

from __future__ import annotations

import argparse
import json
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest

from hipson import agent_bootstrap, agent_contract, cli, ledger, mcp_server, task_context, task_verification
from tests.test_gate_contracts import STATE
from tests.test_hipson_helpers import init_git_repo, run_cli_inprocess

DEPRECATED = set(agent_contract.DEPRECATED_COMMANDS)


def test_deprecated_commands_are_absent_from_top_level_help_but_still_parse():
    parser = cli.build_parser()
    text = parser.format_help()
    usage = parser.format_usage()
    for name in DEPRECATED:
        assert f"{name}," not in usage and f",{name}" not in usage, name
        assert not any(line.split()[:1] == [name] for line in text.splitlines()), name
        assert parser.parse_args([name, *_minimal_args(name)]).command == name
    for name in ("task", "lead", "init", "kit", "work"):
        assert f"{{{name}," in usage or f",{name}," in usage, name
    assert DEPRECATED == {"autopilot", "release", "scheduler", "chat", "learn", "hermes"}


def _minimal_args(name: str) -> list[str]:
    return {"autopilot": ["resume", "--run", "r"], "release": ["claim", "--run", "r", "--claim", "c"],
            "scheduler": ["list"], "chat": [], "learn": ["propose", "--session-id", "s"],
            "hermes": ["doctor"]}[name]


def test_deprecated_command_warns_on_stderr_and_keeps_json_stdout(tmp_path: Path):
    rc, stdout, stderr = run_cli_inprocess("scheduler", "--session-db", str(tmp_path / "s.sqlite"), "list", "--json")
    assert rc == 0
    assert json.loads(stdout)["jobs"] == []
    assert stderr.strip() == cli._deprecation_notice("scheduler")
    assert "removed in 2.0" in stderr and "docs/DEPRECATIONS.md" in stderr


def test_deprecation_marker_survives_subcommands_that_reuse_the_command_dest():
    parser = cli.build_parser()
    # `verify run --command` overwrites args.command with a list; the marker must not depend on it.
    supported = parser.parse_args(["verify", "run", "--work", "w.json", "--command", "chat"])
    assert supported.command == ["chat"] and not hasattr(supported, "deprecated_command")
    assert parser.parse_args(["autopilot", "resume", "--run", "r"]).deprecated_command == "autopilot"


def test_deprecated_subcommand_help_explains_the_replacement():
    subparsers = next(action for action in cli.build_parser()._actions if isinstance(action, argparse._SubParsersAction))
    help_text = " ".join(subparsers.choices["autopilot"].format_help().split())
    assert "deprecated and hidden since 1.7" in help_text and "hipson task do" in help_text


def test_supported_commands_do_not_warn(tmp_path: Path):
    rc, _stdout, stderr = run_cli_inprocess("contract", "show", "--project", str(init_git_repo(tmp_path)), "--json")
    assert rc == 0 and stderr == ""


def test_release_claim_is_gone_from_mcp():
    assert "release.claim" not in mcp_server.MCP_TOOL_NAMES
    assert "release.claim" not in mcp_server.MCP_GATED_TOOLS and "release.claim" not in mcp_server.MCP_INPUT_SCHEMAS
    with pytest.raises(SystemExit, match="Unknown tool: release.claim"):
        mcp_server._tool_payload("release.claim", {"run": "runs/x", "claim": "c", "approved": True}, project=Path("."))


def test_agent_contract_leads_with_the_ledger_and_lists_no_deprecated_surface(tmp_path: Path):
    contract = agent_contract.build_agent_contract(init_git_repo(tmp_path))
    assert contract["supported_workflows"][0]["name"] == "lead_task_ledger"
    assert contract["supported_workflows"][0]["steps"][-1] == "task.close"
    names = [workflow["name"] for workflow in contract["supported_workflows"]]
    assert not any("autopilot" in name or "release" in name for name in names)
    surfaces = " ".join(" ".join(values) for values in contract["available_command_surfaces"].values())
    for name in DEPRECATED:
        assert not any(command.split()[0] == name for values in contract["available_command_surfaces"].values()
                       for command in values), (name, surfaces)
    assert contract["deprecated_command_surfaces"] == dict(sorted(agent_contract.DEPRECATED_COMMANDS.items()))
    assert "gated_release_claim" not in contract["adapter_capabilities"]["mcp_stdio"]["capabilities"]
    assert contract["adapter_capabilities"]["mcp_stdio"]["primary_interface"] is True
    rendered = agent_contract.render_agent_contract(contract)
    assert "lead resume -> task open --check --start -> task run -> task now/ack -> task close" in rendered
    assert "release claims" not in rendered


def test_agent_bootstrap_starts_with_lead_resume(tmp_path: Path):
    payload = agent_bootstrap.build_bootstrap(target="mcp", project_path=init_git_repo(tmp_path))
    assert payload["recommended_first_command"] == "hipson lead resume --json"
    assert payload["recommended_first_tool"] == "lead.resume"
    assert payload["policy"]["default_workflow"] == "lead_task_ledger"
    assert payload["fallback_commands"][0] == "hipson task now"


def test_brief_shows_a_readable_gate_summary_instead_of_raw_json(tmp_path: Path):
    with patch.object(task_verification, "project_state", return_value=dict(STATE)):
        store = ledger.Ledger(tmp_path / "ledger.sqlite")
        with closing(store):
            task = store.open_task(project=tmp_path, title="Brief", objective="Readable gate", lead="lead",
                                   checks=[["pytest", "-q"], ["ruff", "check", "a b"]])["id"]
            view = task_context.brief(store, task, role="builder")
    text = task_context.render(view)
    section = text.split("## Lead verification\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert section.splitlines() == [
        "Gate: not passing (missing)",
        "- pytest -q: missing: never verified; run hipson task verify (no command runs all checks)",
        "- ruff check 'a b': missing: never verified; run hipson task verify (no command runs all checks)",
        f"Working tree: revision {'a' * 12}, clean",
    ]
    assert '"freshness"' not in text and "untrusted_fields" not in section
    assert view["verification"].startswith("{") and "freshness" in view["verification"]  # JSON field kept.
