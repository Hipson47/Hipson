"""Platform regression controls shared by pytest and the dependency-free runner."""

import ctypes
import json
import os
import subprocess
import sys
from contextlib import closing
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from hipson import ledger, project
from hipson.home import detect_hipson_home
from tests.support import hipson_cli


def test_windows_pid_probe_only_queries_and_closes_handles():
    for opened, error, query_ok, exit_code, expected in (
        (None, 87, True, 259, False),  # PID not found.
        (None, 5, True, 259, True),  # Access denied must preserve claims.
        (None, 999, True, 259, True),  # Unknown failures also preserve claims.
        (0x123456789, 0, True, 259, True),  # Exercise pointer-sized handles.
        (0x123456789, 0, True, 0, False),
        (0x123456789, 0, False, 0, True),
    ):
        def query(handle, pointer, code=exit_code, success=query_ok):
            ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD)).contents.value = code
            return success

        kernel = SimpleNamespace(OpenProcess=Mock(return_value=opened),
                                 GetExitCodeProcess=Mock(side_effect=query), CloseHandle=Mock())
        with (
            patch.object(ledger.sys, "platform", "win32"),
            patch.object(ctypes, "WinDLL", return_value=kernel, create=True) as loader,
            patch.object(ctypes, "get_last_error", return_value=error, create=True),
            patch.object(ledger.os, "kill", side_effect=AssertionError("must never signal")),
            patch.object(ledger.os, "killpg", side_effect=AssertionError("must never signal"), create=True),
        ):
            assert ledger._pid_alive(42) is expected
        loader.assert_called_once_with("kernel32", use_last_error=True)
        kernel.OpenProcess.assert_called_once_with(0x1000, False, 42)
        assert kernel.OpenProcess.restype is wintypes.HANDLE
        if opened:
            kernel.CloseHandle.assert_called_once_with(opened)
        else:
            kernel.CloseHandle.assert_not_called()
            kernel.GetExitCodeProcess.assert_not_called()
    with patch.object(ctypes, "WinDLL", side_effect=AssertionError("invalid PID must not wrap"), create=True):
        for pid in (0, -1, 2**32, 2**63 - 1):
            assert not ledger._windows_pid_alive(pid)


def test_real_liveness_probe_preserves_running_process_and_detects_exit():
    # Runs the real ctypes branch on Windows CI; never use a signal as a liveness check.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        for _ in range(5):
            assert ledger._pid_alive(child.pid)
            assert child.poll() is None, "Liveness probe terminated its target"
        child.terminate()  # Explicit teardown, after every probe confirmed the process survived.
        child.wait(timeout=10)
        assert not ledger._pid_alive(child.pid)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)


def test_boot_identity_is_platform_specific_and_bounded():
    with patch.object(ledger.sys, "platform", "darwin"):
        for result, expected in (
            (SimpleNamespace(returncode=0, stdout="boot-session-uuid\n"), "boot-session-uuid"),
            (SimpleNamespace(returncode=0, stdout="\n"), None),
            (SimpleNamespace(returncode=1, stdout="unusable"), None),
        ):
            with patch.object(ledger.subprocess, "run", return_value=result) as run:
                assert ledger._boot_id() == expected
            run.assert_called_once_with(["sysctl", "-n", "kern.bootsessionuuid"], capture_output=True,
                                        text=True, encoding="ascii", timeout=2, check=False)
        for failure in (OSError("missing sysctl"), subprocess.TimeoutExpired("sysctl", 2), UnicodeError("bad output")):
            with patch.object(ledger.subprocess, "run", side_effect=failure):
                assert ledger._boot_id() is None
    with patch.object(ledger.sys, "platform", "linux"), patch.object(Path, "read_text", return_value="linux-boot\n"):
        assert ledger._boot_id() == "linux-boot"
    with patch.object(ledger.sys, "platform", "linux"), patch.object(Path, "read_text", side_effect=OSError):
        assert ledger._boot_id() is None
    with patch.object(ledger.sys, "platform", "win32"), \
            patch.object(ledger.subprocess, "run", side_effect=AssertionError("no subprocess on Windows")), \
            patch.object(ledger.os, "readlink", side_effect=AssertionError("PID namespaces are Linux-only")):
        assert ledger._boot_id() is None
        assert ledger._pid_namespace() is None


def test_native_paths_config_and_sqlite_uri_round_trip(tmp_path):
    with patch.object(Path, "home", return_value=tmp_path):
        assert detect_hipson_home({})[0] == tmp_path / ".config" / "hipson"
    root = tmp_path / "project with spaces # ü"
    root.mkdir()
    assert project.rel(root / "src" / "parser.py", root) == "src/parser.py"
    db = root / "ledger # ü.sqlite"
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=str(root), title="Zażółć 日本語", objective="UTF-8")
    with closing(ledger.Ledger(db, create=False, read_only=True)) as store:
        assert store.show(task["id"])["title"] == task["title"]


def test_installed_cli_and_mcp_smoke(tmp_path):
    cli = hipson_cli()
    assert cli, "requires installed hipson CLI (HIPSON_BIN, PATH, or checkout .venv)"
    root = tmp_path / "project with spaces ü"
    root.mkdir()
    env = {**os.environ, "HIPSON_LEDGER": str(root / "ledger.sqlite"), "PYTHONIOENCODING": "ascii"}

    def call(*args):
        result = subprocess.run([cli, *args], cwd=root, env=env, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr.decode("utf-8")
        return json.loads(result.stdout.decode("utf-8"))

    title = "CLI smoke Zażółć 日本語"
    task = call("task", "open", "--project", str(root), "--title", title, "--objective", "Smoke test", "--json")
    assignment = call("task", "assign", "--task", task["id"], "--actor", "smoke", "--role", "builder",
                      "--mode", "rw", "--scope", "src", "--lease-seconds", "300", "--json")
    assert assignment["status"] == "active" and assignment["lease_until"]
    assert call("task", "board", "--json")[0]["id"] == task["id"]
    requests = [
        {"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                                       "clientInfo": {"name": "smoke", "version": "1"}}},
        {"method": "notifications/initialized"},
        {"id": 2, "method": "tools/list"},
        {"id": 3, "method": "tools/call", "params": {"name": "task.show", "arguments": {"task": task["id"]}}},
    ]
    # Accept CRLF requests, emit UTF-8 LF JSON without a BOM even with a legacy console encoding.
    wire = "".join(json.dumps({"jsonrpc": "2.0", **request}, ensure_ascii=False) + "\r\n" for request in requests)
    result = subprocess.run([cli, "mcp", "serve", "--project", str(root), "--stdio"], cwd=root, env=env,
                            input=wire.encode("utf-8"), capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    assert not result.stdout.startswith(b"\xef\xbb\xbf") and b"\r" not in result.stdout
    replies = [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]
    assert [reply["id"] for reply in replies] == [1, 2, 3]
    assert replies[0]["result"]["serverInfo"]["name"] == "hipson"
    assert "task.board" in {tool["name"] for tool in replies[1]["result"]["tools"]}
    assert json.loads(replies[2]["result"]["content"][0]["text"])["title"] == title
    call("task", "finish", "--assignment", assignment["id"], "--result", "passed", "--summary", "Smoke complete", "--json")
