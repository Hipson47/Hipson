"""Opt-in worker environment allowlist: selection, policy, ledger records (names only) and end-to-end runs."""

from __future__ import annotations

import json
import os
import shlex
import sqlite3
import subprocess
import sys
from argparse import Namespace
from contextlib import closing
from pathlib import Path

import pytest

from hipson import ledger, policy, worker_env
from tests.test_hipson_helpers import REPO_ROOT
from tests.test_ledger import assert_strict_schema
from tests.test_task_verification import _project

SECRET_VALUE = "dummy-worker-credential-7f1c"


def _source() -> dict[str, str]:
    return {"PATH": "/usr/bin", "HOME": "/home/me", "LC_ALL": "C.UTF-8", "HIPSON_LEDGER": "/tmp/l.sqlite",
            "HTTPS_PROXY": "http://proxy:3128", "https_proxy": "http://proxy:3128", "SSL_CERT_FILE": "/etc/ca.pem",
            "DUMMY_SECRET": SECRET_VALUE, "OPENAI_API_KEY": SECRET_VALUE, "AWS_SECRET_ACCESS_KEY": SECRET_VALUE}


def test_minimal_passes_only_the_baseline_hipson_and_allowed_names():
    env, record = worker_env.select(_source(), "minimal", platform="linux")
    assert set(env) == {"PATH", "HOME", "LC_ALL", "HIPSON_LEDGER", "HTTPS_PROXY", "https_proxy", "SSL_CERT_FILE"}
    assert record == {"mode": "minimal", "names": sorted(env), "allowed": [], "missing": [], "dropped": 3}
    assert SECRET_VALUE not in json.dumps(record) and "/usr/bin" not in json.dumps(record)
    allowed, record = worker_env.select(_source(), "minimal", ["OPENAI_API_KEY", "GH_TOKEN"], platform="linux")
    assert allowed["OPENAI_API_KEY"] == SECRET_VALUE and "DUMMY_SECRET" not in allowed
    assert record["allowed"] == ["OPENAI_API_KEY", "GH_TOKEN"] and record["missing"] == ["GH_TOKEN"]
    assert record["dropped"] == 2


def test_inherit_passes_everything_and_records_only_a_count():
    env, record = worker_env.select(_source(), "inherit", platform="linux")
    assert env == _source() and record == {"mode": "inherit", "count": len(_source())}
    with pytest.raises(ValueError, match="inherit, minimal"):
        worker_env.select(_source(), "none")


def test_names_match_case_insensitively_only_on_windows():
    source = {"Path": "C:\\Windows", "SystemRoot": "C:\\Windows", "hipson_task": "T-1", "openai_api_key": "x"}
    windows, record = worker_env.select(source, "minimal", ["OPENAI_API_KEY"], platform="win32")
    assert set(windows) == set(source) and record["missing"] == []
    posix, _ = worker_env.select(source, "minimal", platform="linux")
    assert set(posix) == {"hipson_task"}  # POSIX names are case-sensitive; HIPSON_* is always passed.


def test_allowed_names_are_validated_and_never_select_another_repository():
    assert worker_env.validate_names(["OPENAI_API_KEY", "PROGRAMFILES(X86)", "OPENAI_API_KEY"]) == [
        "OPENAI_API_KEY", "PROGRAMFILES(X86)"]
    for bad in ("", "A=B", "1ABC", "WITH SPACE", 7):
        with pytest.raises(ValueError, match="variable names"):
            worker_env.validate_names([bad])
    for git in ("GIT_DIR", "git_work_tree", "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0"):
        with pytest.raises(ValueError, match="another repository"):
            worker_env.validate_names([git])
    with pytest.raises(ValueError, match="at most 64"):
        worker_env.validate_names([f"NAME_{index}" for index in range(65)])


def _payload(valid: bool = True, **policy: object) -> dict[str, object]:
    return {"valid": valid, "issues": [] if valid else ["broken"], "policy": policy}


def test_cli_options_override_policy_and_allowed_names_add_up():
    resolve = worker_env.resolve
    assert resolve(Namespace(env=None, env_allow=[]), _payload()) == ("inherit", [])
    assert resolve(Namespace(env=None, env_allow=[]), _payload(worker_env="minimal", worker_env_allow=["A"])) == (
        "minimal", ["A"])
    assert resolve(Namespace(env="minimal", env_allow=["B", "A"]), _payload(worker_env_allow=["A"])) == (
        "minimal", ["A", "B"])
    # An explicit --env inherit wins over the policy, which then contributes no names.
    assert resolve(Namespace(env="inherit", env_allow=[]), _payload(worker_env="minimal", worker_env_allow=["A"])) == (
        "inherit", [])
    with pytest.raises(ValueError, match="needs the minimal"):
        resolve(Namespace(env=None, env_allow=["A"]), _payload())
    # A policy that asks to narrow the environment must be valid: never fall back to inherit silently.
    with pytest.raises(ValueError, match="sets worker_env but is invalid"):
        resolve(Namespace(env=None, env_allow=[]), _payload(False, worker_env="minimal"))
    assert resolve(Namespace(env=None, env_allow=[]), _payload(False)) == ("inherit", [])
    # An explicit --env decides alone; the invalid policy then contributes no allowed names.
    assert resolve(Namespace(env="minimal", env_allow=["B"]),
                   _payload(False, worker_env="minimal", worker_env_allow=["A"])) == ("minimal", ["B"])
    assert resolve(Namespace(env="inherit", env_allow=[]), _payload(False, worker_env="minimal")) == ("inherit", [])
    # Verification and other callers without the options get the default.
    assert resolve(Namespace(), _payload()) == ("inherit", [])


def test_policy_validates_worker_env_and_its_allowed_names():
    base = {**policy.DEFAULT_POLICY}
    assert policy.validate_policy_payload({**base, "worker_env": "minimal", "worker_env_allow": ["GH_TOKEN"]}) == []
    assert "worker_env must be one of inherit, minimal" in policy.validate_policy_payload({**base, "worker_env": "all"})
    assert "worker_env_allow must be a list of variable names" in policy.validate_policy_payload(
        {**base, "worker_env_allow": "GH_TOKEN"})
    issues = policy.validate_policy_payload({**base, "worker_env_allow": ["GIT_DIR"]})
    assert any("entry 1 cannot be passed" in issue and "GIT_DIR" in issue for issue in issues)


def test_summary_lines():
    assert worker_env.summary(None) == "not recorded"
    assert worker_env.summary({"mode": "inherit", "count": 40}) == "inherit (40 variables)"
    assert worker_env.summary({"mode": "minimal", "names": ["PATH", "HOME"], "allowed": ["GH_TOKEN"],
                               "missing": ["GH_TOKEN"], "dropped": 9}) == (
        "minimal (2 passed, 9 withheld); allowed but unset: GH_TOKEN")


def test_ledger_records_the_environment_with_redacted_names_and_rejects_bad_records(tmp_path):
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store:
        task = store.open_task(project=tmp_path, title="Env", objective="Env")["id"]
        record = {"mode": "minimal", "names": ["PATH", "sk-ant-api03-" + "a" * 40], "allowed": [], "missing": [],
                  "dropped": 3}
        assignment = store.assign(task, actor="codex", role="builder", mode="ro", environment=record)
        assert assignment["environment"]["mode"] == "minimal"
        assert "sk-ant-api03-" + "a" * 40 not in json.dumps(store.show(task))
        assert store.assign(task, actor="codex", role="builder", mode="ro")["environment"] is None
        for bad in ({"mode": "all"}, ["minimal"], {}):
            with pytest.raises(ledger.LedgerError, match="environment must be an object"):
                store.assign(task, actor="codex", role="builder", mode="ro", environment=bad)  # type: ignore[arg-type]
        assert_strict_schema("task.schema.json", store.show(task))


def test_v8_ledgers_open_read_only_and_migrate_to_an_empty_environment_column(tmp_path):
    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=tmp_path, title="Old", objective="Old ledger")["id"]
        store.assign(task, actor="codex", role="builder", mode="ro")
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("ALTER TABLE assignments DROP COLUMN environment")
        connection.execute("UPDATE schema_version SET version = 8")
        connection.commit()
    with closing(ledger.Ledger(db, read_only=True)) as store:
        assert store.show(task)["assignments"][0]["environment"] is None
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 8
    with closing(ledger.Ledger(db)) as store:
        assert store.connection.execute("SELECT version FROM schema_version").fetchone()[0] == ledger.SCHEMA_VERSION == 9
        assert "environment" in [row[1] for row in store.connection.execute("PRAGMA table_info(assignments)")]


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if key not in ("HIPSON_TASK", "HIPSON_ASSIGNMENT", "HIPSON_ROLE")}
    env.update(HIPSON_LEDGER=str(tmp_path / "ledger.sqlite"), PYTHONPATH=str(REPO_ROOT / "src"),
               DUMMY_SECRET=SECRET_VALUE, EXTRA_ALLOWED="visible", **extra)
    return env


def _hipson(project: Path, tmp_path: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-P", "-m", "hipson.cli", *args], cwd=project, env=_env(tmp_path, **extra),
                          capture_output=True, text=True, timeout=120)


def _probe(report: Path) -> list[str]:
    # The worker reports which variables it can see, by name only.
    code = (f"import json, os; json.dump({{k: k in os.environ for k in "
            f"['DUMMY_SECRET', 'EXTRA_ALLOWED', 'PATH', 'HIPSON_LEDGER', 'HIPSON_TASK', 'HIPSON_ASSIGNMENT', "
            f"'HIPSON_PROGRESS_FILE']}}, open({str(report)!r}, 'w'))")
    return [sys.executable, "-c", code]


def _open_task(tmp_path: Path, project: Path) -> str:
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store:
        task = store.open_task(project=project, title="Env", objective="Env")["id"]
        store.set_status(task, to="in_progress", actor="claude")
    return task


def _run(tmp_path: Path, project: Path, task: str, *options: str) -> dict[str, bool]:
    report = tmp_path / "seen.json"
    report.unlink(missing_ok=True)
    run = _hipson(project, tmp_path, "task", "run", "--task", task, "--actor", "codex", "--role", "explorer",
                  *options, "--", *_probe(report))
    assert run.returncode == 0, run.stderr
    return json.loads(report.read_text())


def test_task_run_minimal_hides_secrets_inherit_shows_them_and_the_ledger_has_names_only(tmp_path):
    project = _project(tmp_path)
    task = _open_task(tmp_path, project)
    inherited = _run(tmp_path, project, task)
    assert inherited["DUMMY_SECRET"] and inherited["EXTRA_ALLOWED"]
    minimal = _run(tmp_path, project, task, "--env", "minimal")
    assert not minimal["DUMMY_SECRET"] and not minimal["EXTRA_ALLOWED"]
    assert minimal["PATH"] and all(minimal[name] for name in minimal if name.startswith("HIPSON_"))
    allowed = _run(tmp_path, project, task, "--env", "minimal", "--env-allow", "EXTRA_ALLOWED")
    assert allowed["EXTRA_ALLOWED"] and not allowed["DUMMY_SECRET"]
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite", read_only=True)) as store:
        view = store.show(task)
    first, second, third = (item["environment"] for item in view["assignments"])
    assert first["mode"] == "inherit" and first["count"] > 0
    assert second["mode"] == "minimal" and "PATH" in second["names"] and "DUMMY_SECRET" not in second["names"]
    assert third["allowed"] == ["EXTRA_ALLOWED"] and "EXTRA_ALLOWED" in third["names"]
    assert SECRET_VALUE.encode() not in (tmp_path / "ledger.sqlite").read_bytes()
    shown = _hipson(project, tmp_path, "task", "show", "--task", task)
    assert shown.stdout.count("    Env minimal (") == 2 and SECRET_VALUE not in shown.stdout
    now = _hipson(project, tmp_path, "task", "now", "--all")
    assert " env minimal (" in now.stdout


def test_project_policy_selects_minimal_and_an_invalid_policy_never_falls_back(tmp_path):
    project = _project(tmp_path)
    (project / ".hipson").mkdir()
    policy_file = project / ".hipson" / "policy.json"
    policy_file.write_text(json.dumps({"worker_env": "minimal", "worker_env_allow": ["EXTRA_ALLOWED"]}))
    task = _open_task(tmp_path, project)
    seen = _run(tmp_path, project, task)
    assert not seen["DUMMY_SECRET"] and seen["EXTRA_ALLOWED"]
    assert _run(tmp_path, project, task, "--env", "inherit")["DUMMY_SECRET"]
    policy_file.write_text(json.dumps({"worker_env": "minimal", "local_only": False}))
    refused = _hipson(project, tmp_path, "task", "run", "--task", task, "--actor", "codex", "--role", "explorer",
                      "--", sys.executable, "-c", "pass")
    assert refused.returncode == 64 and "sets worker_env but is invalid" in refused.stderr
    policy_file.unlink()
    bad_allow = _hipson(project, tmp_path, "task", "run", "--task", task, "--actor", "codex", "--role", "explorer",
                        "--env", "minimal", "--env-allow", "GIT_DIR", "--", sys.executable, "-c", "pass")
    assert bad_allow.returncode == 64 and "entry 1 cannot be passed" in bad_allow.stderr
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite", read_only=True)) as store:
        assert len(store.show(task)["assignments"]) == 2  # Refused runs never assign.


def test_an_unreadable_policy_that_may_set_worker_env_never_falls_back_to_inherit(tmp_path):
    project = _project(tmp_path)
    (project / ".hipson").mkdir()
    policy_file = project / ".hipson" / "policy.json"
    policy_file.write_text('{"worker_env": "minimal",}')  # A trailing comma: not JSON.
    task = _open_task(tmp_path, project)
    refused = _hipson(project, tmp_path, "task", "run", "--task", task, "--actor", "codex", "--role", "explorer",
                      "--", sys.executable, "-c", "pass")
    assert refused.returncode == 64 and "cannot be read (JSONDecodeError) and may set worker_env" in refused.stderr
    assert not _run(tmp_path, project, task, "--env", "minimal")["DUMMY_SECRET"]
    assert _run(tmp_path, project, task, "--env", "inherit")["DUMMY_SECRET"]
    # A \u escape can spell the key, so a broken policy holding one is refused too.
    policy_file.write_text('{"worker\\u005fenv": "minimal",}')
    escaped = _hipson(project, tmp_path, "task", "run", "--task", task, "--actor", "codex", "--role", "explorer",
                      "--", sys.executable, "-c", "pass")
    assert escaped.returncode == 64 and "may set worker_env" in escaped.stderr
    # A broken policy that never mentions worker_env keeps the 1.6 behaviour: the run inherits, also when a
    # policy.yaml it shadows does mention worker_env.
    policy_file.write_text('{"observe_changes": true,}')
    (project / ".hipson" / "policy.yaml").write_text("worker_env: minimal\n")
    assert _run(tmp_path, project, task)["DUMMY_SECRET"]


def _refused(tmp_path: Path, project: Path, task: str) -> str:
    refused = _hipson(project, tmp_path, "task", "run", "--task", task, "--actor", "codex", "--role", "explorer",
                      "--", sys.executable, "-c", "pass")
    assert refused.returncode == 64, refused.stderr
    return refused.stderr


def test_worker_env_is_read_only_from_policy_json_and_yaml_that_may_set_it_refuses(tmp_path):
    project = _project(tmp_path)
    (project / ".hipson").mkdir()
    yaml_file = project / ".hipson" / "policy.yaml"
    task = _open_task(tmp_path, project)
    for text in ("worker_env: minimal\n", '"worker_env": minimal\n', '"worker\\u005fenv": minimal\n',
                 "\ufeffworker_env: minimal\n", '{"worker_env": "minimal"}\n', "Worker-Env: minimal\n",
                 "worker_env: minimal\nagent_integration:\n  \"worker_env\": inherit\n"):
        yaml_file.write_text(text, encoding="utf-8")
        assert "read only from .hipson/policy.json" in _refused(tmp_path, project, task), text
    # An explicit --env decides, and YAML never configures the worker environment.
    yaml_file.write_text("worker_env: minimal\nworker_env_allow:\n  - EXTRA_ALLOWED\n")
    seen = _run(tmp_path, project, task, "--env", "minimal")
    assert not seen["DUMMY_SECRET"] and not seen["EXTRA_ALLOWED"]
    yaml_file.write_text("observe_changes: true\n")  # A YAML policy that never mentions it: unchanged behaviour.
    assert _run(tmp_path, project, task)["DUMMY_SECRET"]


def test_doubtful_json_policies_refuse_and_near_miss_keys_are_invalid(tmp_path):
    project = _project(tmp_path)
    (project / ".hipson").mkdir()
    json_file = project / ".hipson" / "policy.json"
    task = _open_task(tmp_path, project)
    for key in ("Worker_Env", "worker-env", " worker_env"):
        json_file.write_text(json.dumps({key: "minimal"}))
        assert "spell it exactly worker_env" in _refused(tmp_path, project, task), key
    for text in ('{"Worker_Env": "minimal",}', '{"worker env": "minimal",}'):
        json_file.write_text(text)
        assert "may set worker_env" in _refused(tmp_path, project, task), text
    json_file.write_text('\ufeff{"worker_env": "minimal"}', encoding="utf-8")  # json rejects a BOM.
    assert "may set worker_env" in _refused(tmp_path, project, task)
    json_file.write_text('{"worker\\u005fenv": "minimal"}')  # Valid JSON: the exact parser decodes the key.
    assert not _run(tmp_path, project, task)["DUMMY_SECRET"]
    # A nested key is not the setting; the root decides.
    json_file.write_text(json.dumps({"worker_env": "minimal", "agent_integration": {"worker_env": "inherit"}}))
    assert not _run(tmp_path, project, task)["DUMMY_SECRET"]
    assert _run(tmp_path, project, task, "--env", "inherit")["DUMMY_SECRET"]


def test_a_rejected_allowed_name_is_never_echoed(tmp_path):
    project = _project(tmp_path)
    task = _open_task(tmp_path, project)
    cookie = "sessionid=opaque-cookie-value-91a"
    refused = _hipson(project, tmp_path, "task", "run", "--task", task, "--actor", "codex", "--role", "explorer",
                      "--env", "minimal", "--env-allow", cookie, "--", sys.executable, "-c", "pass")
    assert refused.returncode == 64 and "entry 1 is not one" in refused.stderr
    assert "opaque-cookie-value-91a" not in refused.stderr + refused.stdout
    git_like = "GIT_CONFIG_opaque_credential_91a"
    refused = _hipson(project, tmp_path, "task", "run", "--task", task, "--actor", "codex", "--role", "explorer",
                      "--env", "minimal", "--env-allow", git_like, "--", sys.executable, "-c", "pass")
    assert refused.returncode == 64 and "entry 1 cannot be passed" in refused.stderr
    assert "opaque_credential_91a" not in refused.stderr + refused.stdout


def test_lead_verification_keeps_the_inherited_environment(tmp_path):
    project = _project(tmp_path)
    (project / ".hipson").mkdir()
    (project / ".hipson" / "policy.json").write_text(json.dumps({"worker_env": "minimal"}))
    task = _open_task(tmp_path, project)
    check = [sys.executable, "-c", "import os; assert os.environ['DUMMY_SECRET']"]
    verified = _hipson(project, tmp_path, "task", "verify", "--task", task, "--", *check)
    assert verified.returncode == 0, verified.stderr


def test_task_do_forwards_the_environment_options(tmp_path):
    project = _project(tmp_path)
    report = tmp_path / "seen.json"
    done = _hipson(project, tmp_path, "task", "do", "--title", "Env", "--check",
                   shlex.join([sys.executable, "-c", "pass"]), "--actor", "codex", "--scope", "source.py",
                   "--env", "minimal", "--env-allow", "EXTRA_ALLOWED", "--", *_probe(report))
    assert done.returncode == 0, done.stderr
    seen = json.loads(report.read_text())
    assert seen["EXTRA_ALLOWED"] and not seen["DUMMY_SECRET"]
