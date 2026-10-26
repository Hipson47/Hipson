"""Security regression probes for init, run offline on every CI operating system."""

import json
import os
import signal
import stat
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from unittest.mock import patch

from hipson import codex_install, init_setup
from tests.test_init import _Setup


def _wait(path: Path, timeout: float = 8) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"Timed out waiting for {path}"
        time.sleep(0.02)


def _codex_entry(setup: _Setup, extra: str = "") -> bytes:
    entry = setup.registration()
    return (f'[mcp_servers.hipson]\ncommand = {json.dumps(entry["command"])}\n'
            f'args = {json.dumps(entry["args"])}\n{extra}').encode()


def test_init_planning_never_connects_even_to_conflicting_claude_servers(tmp_path):
    setup = _Setup(tmp_path)
    setup.env["INIT_MISSING_RESPONSE"] = "No MCP server found with name: hipson"
    setup.register_claude("local", {"command": "unsafe-stdio-server", "args": []})
    before = setup.snapshots()
    for args in (("--dry-run",), (), ("--yes",)):
        report = setup.run("--client", "claude", *args, "--json", expected=1)
        assert "conflict" in report["errors"][0]
        assert setup.calls() == []  # get/list can execute the unsafe command before consent.
        assert setup.snapshots() == before


def test_init_concurrent_processes_cannot_append_duplicate_codex_tables(tmp_path):
    setup = _Setup(tmp_path)
    setup.foreign_configs()
    # Keep the interleaving focused on config writes, with no ledger migration in between.
    setup.run("--client", "none", "--yes", "--json")
    script = setup.root / "concurrent.py"
    script.write_text('''import json, sys, time
from pathlib import Path
from hipson import init_setup
project, root, worker = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
def wait(path):
    end = time.monotonic() + 15
    while not path.exists():
        if time.monotonic() > end:
            raise RuntimeError("barrier timed out")
        time.sleep(.02)
plan = init_setup.build_plan(project_path=str(project), client="codex", scope="project", dry_run=False)
assert plan.report["ok"], plan.report
original = init_setup.backup_file
def backup(path):
    result = original(path)
    (root / ("backup-" + worker)).touch()
    wait(root / "release-backups")
    return result
init_setup.backup_file = backup
(root / ("ready-" + worker)).touch()
wait(root / ("start-" + worker))
init_setup.apply_plan(plan)
print(json.dumps(plan.report))
''', encoding="utf-8")
    children = []
    try:
        for worker in ("1", "2"):
            children.append(subprocess.Popen([sys.executable, str(script), str(setup.project), str(setup.root), worker],
                                            env=setup.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
            _wait(setup.root / f"ready-{worker}")
        (setup.root / "start-1").touch()
        _wait(setup.root / "backup-1")
        (setup.root / "start-2").touch()
        # Old init lets worker 2 pass validation while worker 1 is copying its backup.
        deadline = time.monotonic() + 0.6
        while not (setup.root / "backup-2").exists() and time.monotonic() < deadline:
            time.sleep(.02)
        (setup.root / "release-backups").touch()
        reports = []
        for child in children:
            stdout, stderr = child.communicate(timeout=20)
            assert child.returncode == 0, (stdout, stderr)
            reports.append(json.loads(stdout))
        parsed = tomllib.loads(setup.codex_config.read_text(encoding="utf-8"))
        assert parsed["mcp_servers"]["hipson"] == setup.registration()
        assert sum(report["ok"] for report in reports) == 1, reports
        assert any("changed since planning" in str(report["errors"]) for report in reports)
        assert len(list(setup.codex_home.glob("config.toml.backup-*"))) == 1
    finally:
        (setup.root / "release-backups").touch()
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)


def test_init_detects_same_bytes_replacement_and_edit_during_backup(tmp_path):
    for variant in ("replacement", "edit-during-backup"):
        setup = _Setup(tmp_path / variant)
        setup.foreign_configs()
        before = setup.codex_config.read_bytes()
        with patch.dict(os.environ, setup.env, clear=True):
            plan = init_setup.build_plan(project_path=str(setup.project), client="codex", scope="project", dry_run=False)
            backup = init_setup.backup_file
            def replace(path, before=before, variant=variant):
                staged = path.with_suffix(".replacement")
                staged.write_bytes(before if variant == "replacement" else b'# newer editor content\n')
                staged.replace(path)
            def edited_backup(path, backup=backup):
                result = backup(path)
                replace(path)
                return result
            if variant == "replacement":
                replace(setup.codex_config)
                init_setup.apply_plan(plan)
            else:
                with patch.object(init_setup, "backup_file", side_effect=edited_backup):
                    init_setup.apply_plan(plan)
        assert not plan.report["ok"] and "changed since planning" in str(plan.report["errors"])
        assert setup.codex_config.read_bytes() == (before if variant == "replacement" else b'# newer editor content\n')


def test_init_failed_partial_claude_registration_restores_the_failing_action(tmp_path):
    for existing in (False, True):
        setup = _Setup(tmp_path / str(existing))
        if existing:
            setup.foreign_configs()
        setup.env["INIT_ADD_PARTIAL"] = "1"
        setup.env["INIT_MISSING_RESPONSE"] = "No MCP server found with name: hipson"
        path = setup.project / ".mcp.json"
        before = path.read_bytes() if path.exists() else None
        report = setup.run("--client", "claude", "--yes", "--json", expected=1)
        assert not report["ok"] and report["actions"][1]["status"] == "failed"
        assert (path.read_bytes() if path.exists() else None) == before
        if existing:
            assert Path(report["actions"][1]["backup"]).read_bytes() == before
        setup.env["INIT_ADD_PARTIAL"] = ""
        assert setup.run("--client", "claude", "--yes", "--json")["ok"]


def test_init_codex_atomic_staging_and_postwrite_validation_recover(tmp_path):
    from hipson import init_config

    for fault in ("staging-write", "replace", "postwrite", "concurrent-edit"):
        setup = _Setup(tmp_path / fault)
        setup.foreign_configs()
        before = setup.codex_config.read_bytes()
        setup.codex_config.chmod(0o600)
        with patch.dict(os.environ, setup.env, clear=True):
            plan = init_setup.build_plan(project_path=str(setup.project), client="codex", scope="user", dry_run=False)
            original_write = init_setup.atomic_write
            original_fdopen = init_config.os.fdopen
            class PartialWriter:
                def __init__(self, handle):
                    self.handle = handle
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    self.handle.close()
                def write(self, content):
                    self.handle.write(content[:8])
                    self.handle.flush()
                    raise OSError("injected partial write")
            def stage_failure(path, content, expected, original_write=original_write, original_fdopen=original_fdopen):
                def partial(descriptor, mode):
                    return PartialWriter(original_fdopen(descriptor, mode))
                with patch.object(init_config.os, "fdopen", side_effect=partial):
                    original_write(path, content, expected)
            def corrupt(path, content, expected, original_write=original_write):
                return original_write(path, b'bad = [', expected)
            def edited(path, content, expected, original_write=original_write):
                written = original_write(path, content, expected)
                path.write_bytes(b'# unrelated newer edit\n')
                return written
            if fault == "staging-write":
                context = patch.object(init_setup, "atomic_write", side_effect=stage_failure)
            elif fault == "replace":
                context = patch.object(init_config.os, "replace", side_effect=OSError("injected replace failure"))
            elif fault == "postwrite":
                context = patch.object(init_setup, "atomic_write", side_effect=corrupt)
            else:
                context = patch.object(init_setup, "atomic_write", side_effect=edited)
            with context:
                init_setup.apply_plan(plan)
        assert not plan.report["ok"], plan.report
        assert setup.codex_config.read_bytes() == (b'# unrelated newer edit\n' if fault == "concurrent-edit" else before)
        assert Path(plan.report["actions"][1]["backup"]).read_bytes() == before
        assert tomllib.loads(before.decode())
        assert not list(setup.codex_home.glob(".*.tmp"))
        if os.name != "nt":
            assert stat.S_IMODE(setup.codex_config.stat().st_mode) == 0o600


def test_init_backup_is_private_before_copy_and_removed_after_partial_copy(tmp_path):
    source = tmp_path / "private.toml"
    source.write_bytes(b'credential = "test-secret"\n')
    source.chmod(0o600)
    observed = []
    def partial_copy(incoming, outgoing):
        # Inspect the actual destination before any secret bytes are written.
        observed.append(stat.S_IMODE(os.fstat(outgoing.fileno()).st_mode))
        outgoing.write(incoming.read(10))
        outgoing.flush()
        raise OSError("injected copy failure")
    previous_umask = os.umask(0o022)
    try:
        with patch.object(codex_install.shutil, "copyfileobj", side_effect=partial_copy):
            try:
                codex_install.backup_file(source)
            except OSError as exc:
                assert "injected copy" in str(exc)
            else:
                raise AssertionError("copy failure was ignored")
    finally:
        os.umask(previous_umask)
    assert not list(tmp_path.glob("*.backup-*")), "partial secret backup left behind"
    if os.name != "nt":
        assert observed == [0o600]
    backup = codex_install.backup_file(source)
    assert backup.read_bytes() == source.read_bytes()
    if os.name != "nt":
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_init_codex_project_layers_conflict_before_any_writes(tmp_path):
    for setting in ('enabled = false', 'command = "foreign"', 'disabled_tools = ["task.open"]',
                    'enabled_tools = ["scan"]', '[mcp_servers.hipson.env]\nHIPSON_LEDGER = "foreign.sqlite"'):
        setup = _Setup(tmp_path / str(len(list(tmp_path.iterdir()))))
        layer = setup.project / ".codex" / "config.toml"
        layer.parent.mkdir()
        layer.write_text('[mcp_servers.hipson]\n' + setting + '\n', encoding="utf-8")
        before = setup.snapshots()
        report = setup.run("--client", "codex", "--yes", "--json", expected=1)
        assert "project config" in report["errors"][0] and str(layer) in report["errors"][0]
        assert report["actions"][1]["status"] == "conflict"
        assert setup.snapshots() == before


def test_init_codex_snapshots_new_and_existing_project_layers(tmp_path):
    for existing in (False, True):
        setup = _Setup(tmp_path / str(existing))
        (setup.project / ".git").mkdir()
        nested = setup.project / "nested"
        nested.mkdir()
        layer = setup.project / ".codex" / "config.toml"
        if existing:
            layer.parent.mkdir()
            layer.write_bytes(b'# original\n')
        with patch.dict(os.environ, setup.env, clear=True):
            plan = init_setup.build_plan(project_path=str(nested), client="codex", scope="project", dry_run=False)
            layer.parent.mkdir(exist_ok=True)
            layer.write_bytes(b'[mcp_servers.hipson]\nenabled = false\n')
            init_setup.apply_plan(plan)
        assert not plan.report["ok"] and "changed since planning" in str(plan.report["errors"])
        assert not setup.db.exists() and not setup.codex_config.exists()


def test_init_rejects_changed_environment_and_unavailable_task_tools(tmp_path):
    variants = [
        '[mcp_servers.hipson.env]\nHIPSON_LEDGER = "elsewhere.sqlite"',
        '[mcp_servers.hipson.env]\nHIPSON_HOME = "elsewhere"',
        'disabled_tools = ["task.open", "task.board"]',
        'enabled_tools = ["scan"]', 'enabled_tools = []',
    ]
    for index, extra in enumerate(variants):
        setup = _Setup(tmp_path / str(index))
        setup.codex_home.mkdir()
        setup.codex_config.write_bytes(_codex_entry(setup, extra + "\n"))
        before = setup.snapshots()
        report = setup.run("--client", "codex", "--yes", "--json", expected=1)
        assert not report["ok"] and report["actions"][1]["status"] == "conflict"
        assert setup.snapshots() == before
    setup = _Setup(tmp_path / "claude-env")
    setup.register_claude("project", {**setup.registration(), "env": {"HIPSON_LEDGER": "elsewhere.sqlite"}})
    before = setup.snapshots()
    assert not setup.run("--client", "claude", "--yes", "--json", expected=1)["ok"]
    assert setup.snapshots() == before


def test_init_verifies_effective_registration_args_env_and_tool_filters(tmp_path):
    setup = _Setup(tmp_path)
    setup.codex_home.mkdir()
    entry = setup.registration()
    entry["args"] = ["mcp", "serve", "--project=.", "--stdio"]
    setup.codex_config.write_bytes((f'[mcp_servers.hipson]\ncommand = {json.dumps(entry["command"])}\n'
                                   f'args = {json.dumps(entry["args"])}\nenabled_tools = ["task.open", "task.board"]\n'
                                   'disabled_tools = ["scan"]\n'
                                   f'[mcp_servers.hipson.env]\nHIPSON_LEDGER = {json.dumps(str(setup.db))}\n').encode())
    with patch.dict(os.environ, setup.env, clear=True):
        plan = init_setup.build_plan(project_path=str(setup.project), client="codex", scope="user", dry_run=False)
        assert plan.report["ok"], plan.report
        verify = init_setup.verify_mcp
        calls = []
        def recording(*args, **kwargs):
            calls.append(kwargs)
            return verify(*args, **kwargs)
        with patch.object(init_setup, "verify_mcp", side_effect=recording):
            init_setup.apply_plan(plan)
    assert plan.report["ok"], plan.report
    assert calls[0]["command"] == [entry["command"], *entry["args"]]
    assert calls[0]["env"]["HIPSON_LEDGER"] == str(setup.db)
    assert calls[0]["enabled_tools"] == ["task.open", "task.board"] and calls[0]["disabled_tools"] == ["scan"]
    # Even a live Hipson server must fail verification when the client filters its task tools.
    assert verify([entry["command"], *entry["args"]], setup.project, env=setup.env,
                  disabled_tools=["task.board"])["status"] == "failed"


def _kill_descendant(pid: int) -> None:
    if sys.platform == "win32":
        from hipson.task_windows import Job
        job = Job()
        try:
            handle = job.kernel.OpenProcess(1, False, pid)
            if handle:
                # Attach is deliberately not used: the probe is already running.
                import ctypes
                terminate = job.kernel.TerminateProcess
                terminate.argtypes = [ctypes.c_void_p, ctypes.c_uint]
                terminate.restype = ctypes.c_int
                terminate(handle, 1)
                job.kernel.CloseHandle(handle)
        finally:
            job.close()
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_init_timeout_and_interrupt_stop_descendants_including_cmd_launchers(tmp_path):
    for interrupt in (False, True):
        root = tmp_path / str(interrupt)
        root.mkdir()
        pidfile, heartbeat = root / "descendant.pid", root / "heartbeat"
        source = root / "spawn.py"
        source.write_text('''import os, subprocess, sys, time
from pathlib import Path
if len(sys.argv) > 1:
    Path("descendant.pid").write_text(str(os.getpid()))
    while True:
        Path("heartbeat").write_text(str(time.time_ns()))
        time.sleep(.03)
subprocess.Popen([sys.executable, __file__, "child"])
time.sleep(60)
''', encoding="utf-8")
        argv = [sys.executable, str(source)]
        if sys.platform == "win32":
            launcher = root / "client.cmd"
            launcher.write_text(f'@"{sys.executable}" "{source}" %*\r\n', encoding="utf-8", newline="")
            argv = [str(launcher)]
        original_communicate = subprocess.Popen.communicate
        injected = False
        def interrupted(child, *args, heartbeat=heartbeat, original_communicate=original_communicate, **kwargs):
            nonlocal injected
            if not injected:
                injected = True
                _wait(heartbeat)
                raise KeyboardInterrupt
            return original_communicate(child, *args, **kwargs)
        try:
            with patch.object(init_setup, "TIMEOUT", 1.5):
                try:
                    if interrupt:
                        with patch.object(subprocess.Popen, "communicate", interrupted):
                            init_setup._run(argv, root)
                    else:
                        init_setup._run(argv, root)
                except (subprocess.TimeoutExpired, KeyboardInterrupt):
                    pass
                else:
                    raise AssertionError("contained process did not time out / interrupt")
            _wait(heartbeat)
            time.sleep(.15)  # Allow an in-flight last write to finish on Windows.
            before = heartbeat.read_bytes()
            time.sleep(.25)
            assert heartbeat.read_bytes() == before, "descendant survived init cleanup"
        finally:
            if pidfile.exists():
                _kill_descendant(int(pidfile.read_text()))
