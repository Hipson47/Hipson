"""Offline setup acceptance tests, shared by all CI operating systems and both runners."""

import io
import json
import os
import sqlite3
import subprocess
import sys
import tomllib
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from hipson import cli, init_setup, ledger
from tests.support import ROOT, hipson_cli

_FAKE_CLIENT = '''import json, os, sys
from pathlib import Path
name, *args = sys.argv[1:]
with Path(os.environ["INIT_CLIENT_LOG"]).open("a", encoding="utf-8") as out:
    out.write(json.dumps({"client": name, "args": args, "cwd": str(Path.cwd())}) + "\\n")
if name != "claude":
    raise SystemExit("Codex setup should edit config directly, never launch a worker")
project_config = Path.cwd() / ".mcp.json"
user_config = Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json"
if args[:2] in (["mcp", "get"], ["mcp", "list"]):
    # A real get/list can connect to stdio servers. Planning must never call it.
    raise SystemExit(os.environ.get("INIT_MISSING_RESPONSE", 'No MCP server named "hipson". Run `claude mcp add` to add one.'))
if args[:2] == ["mcp", "add"]:
    scope = args[args.index("--scope") + 1]
    path = project_config if scope == "project" else user_config
    config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    command, *server_args = args[args.index("--") + 1:]
    config.setdefault("mcpServers", {})["hipson"] = {"command": command, "args": server_args}
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.environ.get("INIT_ADD_PARTIAL"):
        path.write_bytes(b'{"partial":')
        raise SystemExit("add failed after writing")
    if os.environ.get("INIT_ADD_ERROR"):
        raise SystemExit("add failed")
    path.write_text(json.dumps(config), encoding="utf-8")
    raise SystemExit(0)
raise SystemExit("unexpected client arguments")
'''


class _Setup:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.project = root / "project with spaces-é"
        self.project.mkdir()
        self.home = root / "home"
        self.home.mkdir()
        self.bin = root / "bin"
        self.bin.mkdir()
        self.codex_home = root / "codex"
        self.codex_config = self.codex_home / "config.toml"
        self.claude_config = root / "claude-config" / ".claude.json"
        self.db = root / "hipson-home" / "ledger.sqlite"
        self.log = root / "client-log.jsonl"
        self.cli = hipson_cli()
        assert self.cli, "installed hipson is required on every CI OS"
        self.env = {
            **os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home),
            "CODEX_HOME": str(self.codex_home), "CODEX_USER_HOME": "", "HIPSON_HOME": str(self.db.parent),
            "HIPSON_LEDGER": str(self.db), "XDG_CONFIG_HOME": str(root / "xdg"),
            "CLAUDE_CONFIG_DIR": str(self.claude_config.parent), "INIT_CLIENT_LOG": str(self.log),
            "INIT_MISSING_RESPONSE": "", "INIT_ADD_PARTIAL": "", "INIT_ADD_ERROR": "", "PYTHONDONTWRITEBYTECODE": "1",
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
        }
        source = self.bin / "client.py"
        source.write_text(_FAKE_CLIENT, encoding="utf-8")
        for name in ("claude", "codex"):
            if sys.platform == "win32":
                shim = self.bin / f"{name}.cmd"
                shim.write_text(f'@"{sys.executable}" "{source}" {name} %*\r\n', encoding="utf-8", newline="")
            else:
                shim = self.bin / name
                shim.write_text(f"#!{sys.executable}\nimport runpy, sys\nsys.argv.insert(1, {name!r})\nrunpy.run_path({str(source)!r}, run_name='__main__')\n", encoding="utf-8")
                shim.chmod(0o755)

    def run(self, *args: str, expected: int = 0):
        result = subprocess.run(
            [self.cli, "init", "--project", str(self.project), *args], cwd=self.root, env=self.env,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        assert result.returncode == expected, (result.stdout, result.stderr)
        return json.loads(result.stdout) if "--json" in args else result.stdout

    def calls(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def snapshots(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*")
                if path.is_file() and path != self.log}

    def foreign_configs(self):
        self.codex_home.mkdir()
        self.codex_config.write_bytes(b'# keep these bytes\r\nmodel = "existing"\r\n\r\n[mcp_servers.foreign]\r\ncommand = "keep"')
        self.claude_config.parent.mkdir()
        foreign = {"mcpServers": {"foreign": {"command": "keep"}}, "unrelated": [1, 2]}
        self.claude_config.write_text(json.dumps(foreign), encoding="utf-8")
        (self.project / ".mcp.json").write_text(json.dumps(foreign), encoding="utf-8")

    def registration(self, project: Path | None = None):
        return {"command": str(Path(self.cli).resolve()),
                "args": ["mcp", "serve", "--project", str(project or self.project), "--stdio"]}

    def register_claude(self, scope: str, entry: dict):
        path = self.project / ".mcp.json" if scope == "project" else self.claude_config
        config = json.loads(path.read_bytes()) if path.exists() else {}
        target = config.setdefault("projects", {}).setdefault(str(self.project), {}) if scope == "local" else config
        target.setdefault("mcpServers", {})["hipson"] = entry
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config), encoding="utf-8")


def test_init_dry_run_exact_plan_without_setup_writes(tmp_path):
    setup = _Setup(tmp_path)
    setup.foreign_configs()
    before = setup.snapshots()
    report = setup.run("--dry-run", "--yes", "--json")
    assert report["ok"] and report["mode"] == "dry-run"
    assert report["verification"]["status"] == "planned"
    assert report["checks"]["ledger"] == str(setup.db)
    assert setup.snapshots() == before
    assert setup.calls() == []
    actions = {action["kind"]: action for action in report["actions"]}
    assert all(action["status"] == "planned" for action in actions.values())
    assert "exclusive" in actions["codex"]["backup_before_edit"]
    claude = actions["claude"]
    assert claude["cwd"] == str(setup.project)
    assert claude["command"][1:10] == ["mcp", "add", "--transport", "stdio", "--scope", "project", "hipson", "--", report["checks"]["hipson_executable"]]
    assert claude["command"][10:] == ["mcp", "serve", "--project", str(setup.project), "--stdio"]
    appended = tomllib.loads(actions["codex"]["append"])["mcp_servers"]["hipson"]
    assert appended["command"] == report["checks"]["hipson_executable"]
    assert appended["args"] == claude["command"][10:]
    assert appended["env"] == {"HIPSON_LEDGER": str(setup.db)}
    text = setup.run("--client", "codex", "--dry-run")
    assert "Append exactly" in text and actions["codex"]["append"].replace("\r\n", "\n") in text
    assert str(setup.codex_config) in text and "backup-" in text


def test_init_noninteractive_refuses_without_yes_and_preserves_everything(tmp_path):
    setup = _Setup(tmp_path)
    setup.foreign_configs()
    before = setup.snapshots()
    report = setup.run("--json", expected=1)
    assert not report["ok"] and "--yes" in report["errors"][0]
    assert setup.snapshots() == before
    assert all(action["status"] == "planned" for action in report["actions"])
    assert setup.calls() == []


def test_init_yes_registers_both_preserves_foreign_config_and_is_idempotent(tmp_path):
    setup = _Setup(tmp_path)
    setup.foreign_configs()
    codex_before = setup.codex_config.read_bytes()
    project_before = (setup.project / ".mcp.json").read_bytes()
    report = setup.run("--yes", "--json")
    assert report["ok"] and report["verification"]["status"] == "passed"
    assert report["verification"]["tools_count"] > 10
    assert all(action["status"] == "applied" for action in report["actions"])
    actions = {action["kind"]: action for action in report["actions"]}
    assert setup.codex_config.read_bytes() == codex_before + actions["codex"]["append"].encode("utf-8")
    assert Path(actions["codex"]["backup"]).read_bytes() == codex_before
    assert Path(actions["claude"]["backup"]).read_bytes() == project_before
    project_config = json.loads((setup.project / ".mcp.json").read_bytes())
    assert project_config["mcpServers"]["foreign"] == {"command": "keep"}
    assert project_config["unrelated"] == [1, 2]
    assert json.loads(setup.claude_config.read_bytes())["mcpServers"] == {"foreign": {"command": "keep"}}
    with closing(ledger.Ledger(setup.db, read_only=True)) as store:
        assert store.list_tasks() == []
    before = setup.snapshots()
    second = setup.run("--json")  # No consent is needed when there are no edits.
    assert second["ok"] and second["verification"]["status"] == "passed"
    assert all(action["status"] == "skipped" and action["detail"] == "already configured" for action in second["actions"])
    assert setup.snapshots() == before
    calls = setup.calls()
    assert len([call for call in calls if call["args"][:2] == ["mcp", "add"]]) == 1
    assert all(call["client"] == "claude" and call["cwd"] == str(setup.project) for call in calls)
    steps = report["next_steps"]
    assert len(steps) == 6 and "approve the project MCP server" in steps[0] and str(setup.project) in steps[1]
    assert "--check" in steps[2] and "--start" in steps[2]
    assert "task run" in steps[3] and steps[4] == "hipson task now" and "task close" in steps[5]
    assert len(second["next_steps"]) == 5  # Nothing new to approve once registered.


def test_init_claude_user_scope_backs_up_before_cli_edit(tmp_path):
    setup = _Setup(tmp_path)
    setup.foreign_configs()
    before = setup.claude_config.read_bytes()
    codex_before = setup.codex_config.read_bytes()
    report = setup.run("--client", "claude", "--scope", "user", "--yes", "--json")
    action = report["actions"][1]
    assert action["kind"] == "claude" and action["path"] == str(setup.claude_config)
    assert Path(action["backup"]).read_bytes() == before
    assert setup.codex_config.read_bytes() == codex_before
    assert "hipson" not in json.loads((setup.project / ".mcp.json").read_bytes())["mcpServers"]
    assert json.loads(setup.claude_config.read_bytes())["mcpServers"]["foreign"] == {"command": "keep"}


def test_init_new_codex_config_and_client_none_do_not_launch_clients(tmp_path):
    setup = _Setup(tmp_path)
    setup.run("--client", "none", "--dry-run", "--json")
    assert not setup.db.exists() and not setup.log.exists()
    report = setup.run("--client", "none", "--yes", "--json")
    assert report["ok"] and not setup.codex_home.exists() and not setup.log.exists()
    assert [action["kind"] for action in report["actions"]] == ["ledger"]
    report = setup.run("--client", "codex", "--yes", "--json")
    assert report["ok"] and not setup.log.exists()
    config = tomllib.loads(setup.codex_config.read_text(encoding="utf-8"))
    assert config["mcp_servers"]["hipson"]["args"][-2:] == [str(setup.project), "--stdio"]
    assert not list(setup.codex_home.glob("*.backup-*"))


def test_init_tty_consent_only_accepts_yes_and_json_stdout_stays_clean(tmp_path):
    for answer, expected in (("y\n", 0), ("yes\n", 0), ("n\n", 1), ("\n", 1), ("", 1), ("sure\n", 1)):
        setup = _Setup(tmp_path / str(len(list(tmp_path.iterdir()))))
        terminal = io.StringIO(answer)
        terminal.isatty = lambda: True
        stdout, stderr = io.StringIO(), io.StringIO()
        with (patch.dict(os.environ, setup.env, clear=True), patch.object(sys, "stdin", terminal),
              redirect_stdout(stdout), redirect_stderr(stderr)):
            code = cli.main(["init", "--project", str(setup.project), "--client", "codex", "--json"])
        assert code == expected
        report = json.loads(stdout.getvalue())
        assert report["ok"] == (expected == 0)
        assert "Apply this setup?" in stderr.getvalue() and "[mcp_servers.hipson]" in stderr.getvalue()
        assert setup.codex_config.exists() == (expected == 0)
        assert setup.db.exists() == (expected == 0)


def test_init_migration_dry_run_is_read_only_then_apply_backs_up(tmp_path):
    setup = _Setup(tmp_path)
    setup.db.parent.mkdir()
    with closing(sqlite3.connect(setup.db)) as connection:
        connection.executescript((ROOT / "tests/fixtures/task-ledger-v1.sql").read_text(encoding="utf-8"))
    before = setup.snapshots()
    report = setup.run("--client", "none", "--dry-run", "--json")
    assert report["checks"]["ledger_schema"] == 1
    assert setup.snapshots() == before
    report = setup.run("--client", "none", "--yes", "--json")
    assert report["ok"] and report["actions"][0]["status"] == "applied"
    with closing(sqlite3.connect(report["actions"][0]["backup"])) as backup:
        assert backup.execute("SELECT version FROM schema_version").fetchone()[0] == 1
    with closing(ledger.Ledger(setup.db, read_only=True)) as store:
        assert store.connection.execute("SELECT version FROM schema_version").fetchone()[0] == ledger.SCHEMA_VERSION


def test_init_live_wal_dry_run_does_not_change_ledger_or_sidecars(tmp_path):
    setup = _Setup(tmp_path)
    with closing(ledger.Ledger(setup.db)) as store:
        store.open_task(project=setup.project, title="Keep", objective="Preserve live WAL")
        before = setup.snapshots()
        report = setup.run("--client", "none", "--dry-run", "--json")
        assert report["checks"]["ledger_schema"] == ledger.SCHEMA_VERSION
        assert setup.snapshots() == before


def test_init_existing_hipson_quoted_toml_is_never_overwritten(tmp_path):
    setup = _Setup(tmp_path)
    setup.codex_home.mkdir()
    setup.codex_config.write_bytes(b'[mcp_servers."hipson"]\r\ncommand = "foreign-command"\r\nenabled = false\r\n')
    before = setup.codex_config.read_bytes()
    report = setup.run("--client", "codex", "--yes", "--json", expected=1)
    assert not report["ok"] and report["actions"][1]["status"] == "conflict"
    assert "foreign-command" in report["errors"][0] and str(setup.project) in report["errors"][0]
    assert setup.codex_config.read_bytes() == before
    assert not setup.db.exists()
    assert not list(setup.codex_home.glob("*.backup-*"))


def test_init_conflict_reports_preserve_literal_windows_paths(tmp_path):
    command = r"C:\Program Files\Hipson\hipson.exe"
    project = r"C:\Users\O'Neil\project with spaces-é"
    entry = {"command": command, "args": ["mcp", "serve", "--project", project, "--stdio"]}
    for client in ("claude", "codex"):
        setup = _Setup(tmp_path / client)
        if client == "claude":
            setup.register_claude("project", entry)
        else:
            setup.codex_home.mkdir()
            setup.codex_config.write_text(
                f'[mcp_servers.hipson]\ncommand = {json.dumps(command)}\nargs = {json.dumps(entry["args"])}\n',
                encoding="utf-8",
            )
        before = setup.snapshots()
        report = setup.run("--client", client, "--yes", "--json", expected=1)
        text = setup.run("--client", client, "--dry-run", expected=1)
        for message in (report["errors"][0], report["actions"][1]["detail"], text):
            assert command in message and project in message and str(setup.project) in message
        assert setup.snapshots() == before


def test_init_identical_claude_registrations_match_scope_and_resolved_paths(tmp_path):
    for scope in ("project", "user"):
        setup = _Setup(tmp_path / scope)
        entry = setup.registration()
        entry["type"] = "stdio"
        entry["args"][3] = str(setup.project / ".." / setup.project.name / ".")
        setup.register_claude(scope, entry)
        before = setup.snapshots()
        report = setup.run("--client", "claude", "--scope", scope, "--dry-run", "--json")
        assert report["ok"] and report["actions"][1]["status"] == "skipped"
        assert report["actions"][1]["detail"] == "already configured"
        assert setup.snapshots() == before


def test_init_claude_other_project_user_registration_plans_safe_project_override(tmp_path):
    setup = _Setup(tmp_path)
    setup.foreign_configs()
    other = setup.root / "other-project"
    setup.register_claude("user", setup.registration(other))
    before = setup.snapshots()
    report = setup.run("--client", "claude", "--dry-run", "--json")
    action = report["actions"][1]
    assert report["ok"] and action["status"] == "planned"
    assert "conflict" in action["detail"] and "project-scope override" in action["detail"]
    assert str(other) in action["detail"] and str(setup.project) in action["detail"]
    assert "user scope" in action["detail"] and "project scope" in action["detail"]
    assert action["command"][6:8] == ["project", "hipson"]
    assert setup.snapshots() == before
    setup.run("--client", "claude", "--json", expected=1)
    assert setup.snapshots() == before  # An override still requires consent.
    user_before = setup.claude_config.read_bytes()
    project_before = (setup.project / ".mcp.json").read_bytes()
    report = setup.run("--client", "claude", "--yes", "--json")
    assert report["ok"] and report["actions"][1]["status"] == "applied"
    assert setup.claude_config.read_bytes() == user_before
    assert Path(report["actions"][1]["backup"]).read_bytes() == project_before
    assert json.loads((setup.project / ".mcp.json").read_bytes())["mcpServers"]["hipson"] == setup.registration()
    before = setup.snapshots()
    second = setup.run("--client", "claude", "--json")
    assert second["ok"] and all(action["status"] == "skipped" for action in second["actions"])
    assert setup.snapshots() == before


def test_init_claude_target_or_higher_scope_conflicts_never_apply(tmp_path):
    for existing_scope, requested_scope in (("project", "project"), ("user", "user"), ("local", "project"), ("project", "user")):
        setup = _Setup(tmp_path / f"{existing_scope}-{requested_scope}")
        other = setup.root / "other-project"
        setup.register_claude(existing_scope, setup.registration(other))
        before = setup.snapshots()
        for options in (("--dry-run",), ("--yes",), ()):
            report = setup.run("--client", "claude", "--scope", requested_scope, *options, "--json", expected=1)
            assert not report["ok"] and report["actions"][1]["status"] == "conflict"
            assert str(other) in report["errors"][0] and str(setup.project) in report["errors"][0]
            assert f"{existing_scope} scope" in report["errors"][0]
            assert "never replaces" in report["errors"][0]
            assert setup.snapshots() == before
        assert setup.calls() == []


def test_init_codex_compares_the_complete_command_and_args_before_skipping(tmp_path):
    for variant in ("identical", "other-project", "extra-arg", "command", "disabled"):
        setup = _Setup(tmp_path / variant)
        entry = setup.registration()
        if variant == "other-project":
            entry["args"][3] = str(setup.root / "other-project")
        elif variant == "extra-arg":
            entry["args"].append("--unexpected")
        elif variant == "command":
            entry["command"] = "different-hipson"
        else:
            entry["args"] = ["mcp", "serve", "--project=.", "--stdio"]
        setup.codex_home.mkdir()
        original = (f'# keep bytes\r\n[mcp_servers."hipson"]\r\ncommand = {json.dumps(entry["command"])}\r\n'
                    f'args = {json.dumps(entry["args"])}\r\nenabled = {"false" if variant == "disabled" else "true"}\r\n'
                    f'env = {{ HIPSON_LEDGER = {json.dumps(str(setup.db))} }}\r\n').encode()
        setup.codex_config.write_bytes(original)
        before = setup.snapshots()
        for options in (("--dry-run",), ("--yes",)):
            report = setup.run("--client", "codex", *options, "--json", expected=0 if variant == "identical" else 1)
            action = report["actions"][1]
            assert action["status"] == ("skipped" if variant == "identical" else "conflict")
            if variant != "identical":
                assert "conflict" in report["errors"][0] and str(setup.project) in report["errors"][0]
                assert setup.snapshots() == before
            if variant == "other-project":
                assert str(setup.root / "other-project") in report["errors"][0]
            assert setup.codex_config.read_bytes() == original
        assert not list(setup.codex_home.glob("*.backup-*")) and not setup.log.exists()


def test_init_claude_matching_project_still_rejects_changed_command_or_args(tmp_path):
    for variant in ("command", "args", "transport"):
        setup = _Setup(tmp_path / variant)
        entry = setup.registration()
        if variant == "command":
            entry["command"] = "different-hipson"
        elif variant == "args":
            entry["args"].append("--unexpected")
        else:
            entry["type"] = "http"
        setup.register_claude("project", entry)
        before = setup.snapshots()
        report = setup.run("--client", "claude", "--yes", "--json", expected=1)
        assert report["actions"][1]["status"] == "conflict" and setup.snapshots() == before


def test_init_current_claude_missing_server_wording_cannot_block_fresh_setup(tmp_path):
    for index, message in enumerate(('No MCP server named "hipson". Run `claude mcp add` to add one.',
                                     'No MCP server found with name: hipson')):
        setup = _Setup(tmp_path / str(index))
        setup.env["INIT_MISSING_RESPONSE"] = message
        report = setup.run("--client", "claude", "--yes", "--json")
        assert report["ok"] and report["actions"][1]["status"] == "applied"
        assert [call["args"][:2] for call in setup.calls()] == [["mcp", "add"]]


def test_init_changed_claude_discovery_source_refuses_stale_project_override(tmp_path):
    setup = _Setup(tmp_path)
    setup.register_claude("user", setup.registration(setup.root / "other-project"))
    with patch.dict(os.environ, setup.env, clear=True):
        plan = init_setup.build_plan(project_path=str(setup.project), client="claude", scope="project", dry_run=False)
        assert plan.report["ok"]
        setup.register_claude("local", setup.registration(setup.root / "higher-priority-project"))
        before = setup.snapshots()
        init_setup.apply_plan(plan)
    assert not plan.report["ok"] and "changed since planning" in plan.report["errors"][0]
    assert setup.snapshots() == before and not (setup.project / ".mcp.json").exists()


def test_init_invalid_config_and_discovery_failures_never_apply(tmp_path):
    for invalid in (b"bad = [", b"\xff", b"mcp_servers = 'wrong'", b"mcp_servers = {}"):
        setup = _Setup(tmp_path / str(len(list(tmp_path.iterdir()))))
        setup.codex_home.mkdir()
        setup.codex_config.write_bytes(invalid)
        before = setup.snapshots()
        report = setup.run("--client", "codex", "--yes", "--json", expected=1)
        assert not report["ok"] and "TOML" in report["errors"][0]
        assert setup.snapshots() == before
    setup = _Setup(tmp_path / "failed-discovery")
    setup.claude_config.parent.mkdir()
    setup.claude_config.write_bytes(b'{"broken":')
    before = setup.snapshots()
    report = setup.run("--yes", "--json", expected=1)
    assert "invalid JSON" in report["errors"][0] and setup.snapshots() == before
    assert setup.calls() == []


def test_init_registration_failure_retains_backup_and_reports_partial_actions(tmp_path):
    setup = _Setup(tmp_path)
    setup.foreign_configs()
    setup.env["INIT_ADD_ERROR"] = "1"
    before = (setup.project / ".mcp.json").read_bytes()
    report = setup.run("--yes", "--json", expected=1)
    assert not report["ok"] and "registration failed" in report["errors"][0]
    assert report["actions"][0]["status"] == "applied"
    assert report["actions"][1]["status"] == "failed"
    assert Path(report["actions"][1]["backup"]).read_bytes() == before
    assert (setup.project / ".mcp.json").read_bytes() == before
    assert report["actions"][2]["status"] == "planned"


def test_init_changed_config_refuses_stale_plan_and_backup_failure_prevents_edit(tmp_path):
    setup = _Setup(tmp_path)
    setup.foreign_configs()
    with patch.dict(os.environ, setup.env, clear=True):
        plan = init_setup.build_plan(project_path=str(setup.project), client="codex", scope="user", dry_run=False)
        setup.codex_config.write_bytes(b'# changed after plan\n')
        init_setup.apply_plan(plan)
        assert not plan.report["ok"] and "changed since planning" in plan.report["errors"][0]
        assert setup.codex_config.read_bytes() == b'# changed after plan\n'
        plan = init_setup.build_plan(project_path=str(setup.project), client="codex", scope="user", dry_run=False)
        with patch.object(init_setup, "backup_file", side_effect=PermissionError("backup denied")):
            init_setup.apply_plan(plan)
        assert not plan.report["ok"] and "backup denied" in plan.report["errors"][0]
        assert setup.codex_config.read_bytes() == b'# changed after plan\n'


def test_init_windows_cmd_resolution_and_safe_argument_transport(tmp_path):
    with (patch.object(init_setup.sys, "platform", "win32"),
          patch.object(init_setup.shutil, "which", side_effect=[None, "C:/tools/claude.cmd"]) as which):
        assert init_setup._client_executable("claude") == "C:/tools/claude.cmd"
    assert [call.args for call in which.call_args_list] == [("claude",), ("claude.cmd",)]
    argv = ["C:/tools/claude.cmd", "mcp", "add", "--", "C:/repo %PATH% ! &/hipson.exe"]
    with (patch.object(init_setup.sys, "platform", "win32"),
          patch.object(init_setup, "run_contained", return_value=subprocess.CompletedProcess(argv, 0, "", "")) as run):
        init_setup._run(argv, tmp_path)
    assert "/d /v:off /s /c" in run.call_args.args[0]
    assert run.call_args.kwargs["env"]["HIPSON_INIT_ARG_4"] == argv[4]
    assert argv[4] not in run.call_args.args[0]  # Only references, never interpolated shell input.
    # On Windows CI the main acceptance tests execute real .cmd shims as well.


def test_init_client_receives_literal_shell_metacharacters_in_project_path(tmp_path):
    setup = _Setup(tmp_path)
    renamed = setup.root / "project & %PATH% ! ^ (literal)"
    setup.project.rename(renamed)
    setup.project = renamed
    report = setup.run("--client", "claude", "--yes", "--json")
    assert report["ok"]
    config = json.loads((renamed / ".mcp.json").read_bytes())
    assert config["mcpServers"]["hipson"]["args"][-2:] == [str(renamed), "--stdio"]
    assert all(call["cwd"] == str(renamed) for call in setup.calls())


def test_init_prerequisite_failures_and_missing_clients_are_read_only(tmp_path):
    setup = _Setup(tmp_path)
    before = setup.snapshots()
    with patch.dict(os.environ, setup.env, clear=True):
        for selected in ("auto", "claude"):
            with patch.object(init_setup, "_client_executable", return_value=None):
                plan = init_setup.build_plan(project_path=str(setup.project), client=selected, scope="project", dry_run=False)
            assert not plan.report["ok"] and not setup.db.exists()
        which = init_setup.shutil.which
        with patch.object(init_setup.shutil, "which", side_effect=lambda name: None if name == "git" else which(name)):
            plan = init_setup.build_plan(project_path=str(setup.project), client="none", scope="project", dry_run=False)
        assert not plan.report["ok"] and "Git" in plan.report["errors"][0]
        with (patch.object(init_setup.sys, "platform", "unsupported"),
              patch.object(init_setup, "executable", side_effect=ValueError("Cannot locate the installed hipson executable")) as executable):
            plan = init_setup.build_plan(project_path=str(setup.project), client="none", scope="project", dry_run=False)
        assert not plan.report["ok"] and "Unsupported platform" in plan.report["errors"][0]
        executable.assert_not_called()
        plan = init_setup.build_plan(project_path=str(setup.root / "missing"), client="none", scope="project", dry_run=False)
        assert not plan.report["ok"] and "does not exist" in plan.report["errors"][0]
    assert setup.snapshots() == before


def test_init_same_second_backups_are_unique_and_invalid_plans_cannot_apply(tmp_path):
    setup = _Setup(tmp_path)
    setup.foreign_configs()
    original = setup.codex_config.read_bytes()
    with patch.dict(os.environ, setup.env, clear=True), patch("hipson.codex_install.time.strftime", return_value="fixed-stamp"):
        backups = []
        for _ in range(2):
            setup.codex_config.write_bytes(original)
            plan = init_setup.build_plan(project_path=str(setup.project), client="codex", scope="project", dry_run=False)
            init_setup.apply_plan(plan)
            assert plan.report["ok"]
            backups.append(Path(plan.report["actions"][1]["backup"]))
        assert backups[0] != backups[1]
        assert all(path.read_bytes() == original for path in backups)
        for dry_run, project in ((True, setup.project), (False, setup.root / "missing")):
            before = setup.snapshots()
            plan = init_setup.build_plan(project_path=str(project), client="none", scope="project", dry_run=dry_run)
            init_setup.apply_plan(plan)
            assert setup.snapshots() == before


def test_init_mcp_verification_rejects_failed_malformed_or_wrong_servers(tmp_path):
    for result in (
        subprocess.CompletedProcess([], 1, "", "failed"),
        subprocess.CompletedProcess([], 0, "garbage", ""),
        subprocess.CompletedProcess([], 0, '{"id":1,"result":{}}\n{"id":2,"result":{"tools":[]}}', ""),
    ):
        with patch.object(init_setup, "_run", return_value=result):
            assert init_setup.verify_mcp(["hipson"], tmp_path)["status"] == "failed"
    with patch.object(init_setup, "_run", side_effect=subprocess.TimeoutExpired("hipson", 15)):
        assert init_setup.verify_mcp(["hipson"], tmp_path)["status"] == "failed"


def test_init_auto_leaves_codex_registered_for_another_project_and_sets_up_claude(tmp_path):
    setup = _Setup(tmp_path)
    entry = setup.registration(setup.root / "other-project")
    setup.codex_home.mkdir()
    original = (f'[mcp_servers.hipson]\ncommand = {json.dumps(entry["command"])}\nargs = {json.dumps(entry["args"])}\n'
                f'env = {{ HIPSON_LEDGER = {json.dumps(str(setup.db))} }}\n').encode()
    setup.codex_config.write_bytes(original)
    preview = setup.run("--dry-run", "--json")
    codex = next(action for action in preview["actions"] if action["kind"] == "codex")
    assert preview["ok"] and codex["status"] == "skipped" and "left unchanged in auto mode" in codex["detail"]
    assert any("--client claude" in warning for warning in preview["checks"]["warnings"])
    report = setup.run("--yes", "--json")
    claude = [item["status"] for item in report["actions"] if item["kind"] == "claude"]
    assert report["ok"] and claude and claude[0] != "conflict", report
    assert setup.codex_config.read_bytes() == original
    explicit = setup.run("--client", "codex", "--dry-run", "--json", expected=1)
    assert explicit["actions"][1]["status"] == "conflict"


def test_init_warns_when_the_project_repository_has_no_commit(tmp_path):
    setup = _Setup(tmp_path)
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    subprocess.run(["git", "init", "-q", str(setup.project)], check=True, env=env, timeout=10)
    report = setup.run("--client", "none", "--dry-run", "--json")
    assert any("no commits yet" in warning for warning in report["checks"]["warnings"])
    subprocess.run(["git", "-C", str(setup.project), "-c", "user.name=T", "-c", "user.email=t@example.com",
                    "commit", "-q", "--allow-empty", "-m", "Initial"], check=True, env=env, timeout=10)
    report = setup.run("--client", "none", "--dry-run", "--json")
    assert not any("no commits yet" in warning for warning in report["checks"].get("warnings", []))
