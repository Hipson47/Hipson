import json
import os
import subprocess
import sys
from pathlib import Path

from hipson import agents as hipson_agents
from hipson import memory as hipson_memory
from hipson import project as hipson_project
from hipson.assets import packaged_asset, runtime_asset
from hipson.codex_install import END_MARKER, START_MARKER, detect_codex_home, install_codex, merge_managed_block
from hipson.home import detect_hipson_home
from hipson.redaction import REDACTION, is_sensitive_path, redact_sensitive_paths, redact_text
from hipson.skills import parse_frontmatter, validate_skill_file, validate_skills


def run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True)


def init_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.email", "test@example.com")
    run_git(repo, "config", "user.name", "Test User")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    run_git(repo, "add", "tracked.txt")
    run_git(repo, "commit", "-m", "base")
    return repo


def run_cli(cwd: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    merged_env["PYTHONPATH"] = str(Path.cwd() / "src")
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "hipson.cli", *args],
        cwd=cwd,
        env=merged_env,
        text=True,
        capture_output=True,
        check=False,
    )


def with_provider_env_defaults(root_env: Path, fallback_env: Path, fn) -> None:
    old_root = hipson_agents.DEFAULT_ROOT_ENV
    old_fallback = hipson_agents.DEFAULT_HIPSON_ENV
    old_key = os.environ.get("OPENROUTER_API_KEY")
    old_override = os.environ.get("HIPSON_AGENTS_ENV")
    try:
        hipson_agents.DEFAULT_ROOT_ENV = root_env
        hipson_agents.DEFAULT_HIPSON_ENV = fallback_env
        fn()
    finally:
        hipson_agents.DEFAULT_ROOT_ENV = old_root
        hipson_agents.DEFAULT_HIPSON_ENV = old_fallback
        if old_key is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = old_key
        if old_override is None:
            os.environ.pop("HIPSON_AGENTS_ENV", None)
        else:
            os.environ["HIPSON_AGENTS_ENV"] = old_override


def test_redact_secrets_masks_common_key_patterns():
    text = "\n".join(
        [
            "OPENROUTER_API_KEY=sk-test-secret1234567890",
            "password=hunter2",
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
            "normal=value",
        ]
    )

    redacted = hipson_agents.redact_secrets(text)

    assert "sk-test" not in redacted
    assert "hunter2" not in redacted
    assert "abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "normal=value" in redacted


def test_redact_secrets_masks_quoted_json_and_url_tokens():
    text = '\n'.join(
        [
            '{"api_key": "super-secret-value"}',
            "https://example.test/callback?token=abc123secret&ok=1",
        ]
    )

    redacted = redact_text(text)

    assert "super-secret-value" not in redacted
    assert "abc123secret" not in redacted
    assert REDACTION in redacted


def test_sensitive_paths_are_case_insensitive_and_redacted():
    assert is_sensitive_path(".SSH/id_rsa") is True
    assert is_sensitive_path("app/.env.production") is True

    redacted = redact_sensitive_paths(" M app/.env.production\n M src/app.py")

    assert ".env.production" not in redacted
    assert "[sensitive file skipped]" in redacted
    assert "src/app.py" in redacted


def test_redact_secrets_masks_private_key_blocks():
    text = "-----BEGIN PRIVATE KEY-----\nabc123\n-----END PRIVATE KEY-----"

    redacted = hipson_agents.redact_secrets(text)

    assert "abc123" not in redacted
    assert REDACTION in redacted


def test_should_embed_file_blocks_secret_and_generated_paths():
    blocked = [
        ".env",
        ".envrc",
        "app/.env.local",
        "certs/prod.pem",
        "data/local.sqlite",
        ".git/config",
        "node_modules/pkg/index.js",
        ".next/server/app.js",
        "package-lock.json",
        "public/photo.jpg",
    ]

    for path in blocked:
        assert hipson_project.should_embed_file(path) is False


def test_should_embed_file_allows_source_and_docs():
    allowed = [
        "scripts/hipson_project.py",
        "docs/model-routing.md",
        "components/ContactForm.tsx",
    ]

    for path in allowed:
        assert hipson_project.should_embed_file(path) is True


def test_parse_repos_yaml_minimal_registry(tmp_path: Path):
    registry = tmp_path / "repos.yaml"
    registry.write_text(
        """
repos:
  - name: Example
    path: /tmp/example
    type: app
    progress: docs/hipson-progress.md
    risk_paths:
      - auth
""",
        encoding="utf-8",
    )

    repos = hipson_project.parse_repos_yaml(registry)

    assert repos == [
        {
            "name": "Example",
            "path": "/tmp/example",
            "type": "app",
            "progress": "docs/hipson-progress.md",
            "risk_paths": ["auth"],
        }
    ]


def test_parse_repos_yaml_reads_registry_metadata(tmp_path: Path):
    registry = tmp_path / "repos.yaml"
    registry.write_text(
        """
repos:
  - name: Example
    path: /tmp/example
    type: app
    owners:
      - Alice
      - Bob
    tags:
      - frontend
    verification:
      - npm run test
      - npm run build
    risk_paths:
      - auth
      - migrations
""",
        encoding="utf-8",
    )

    repos = hipson_project.parse_repos_yaml(registry)

    assert repos[0]["owners"] == ["Alice", "Bob"]
    assert repos[0]["tags"] == ["frontend"]
    assert repos[0]["verification"] == ["npm run test", "npm run build"]
    assert repos[0]["risk_paths"] == ["auth", "migrations"]


def test_sensitive_packet_paths_are_blocked():
    blocked = [
        Path("/tmp/.env"),
        Path("/tmp/project/.env.local"),
        Path("/home/user/.ssh/id_rsa"),
        Path("/home/user/.config/hipson/agents.env"),
    ]

    for path in blocked:
        assert hipson_agents.is_sensitive_packet_path(path) is True


def test_normal_packet_paths_are_allowed():
    allowed = [
        Path("/tmp/review-packet.md"),
        Path("/workspace/project/runs/review.md"),
    ]

    for path in allowed:
        assert hipson_agents.is_sensitive_packet_path(path) is False


def test_provider_env_prefers_exported_environment(tmp_path: Path):
    root_env = tmp_path / ".env"
    fallback_env = tmp_path / "agents.env"
    root_env.write_text("OPENROUTER_API_KEY=sk-root-secret1234567890\n", encoding="utf-8")
    fallback_env.write_text("OPENROUTER_API_KEY=sk-fallback-secret1234567890\n", encoding="utf-8")

    def run() -> None:
        os.environ.pop("HIPSON_AGENTS_ENV", None)
        os.environ["OPENROUTER_API_KEY"] = "sk-exported-secret1234567890"
        hipson_agents.load_provider_envs()
        assert os.environ["OPENROUTER_API_KEY"] == "sk-exported-secret1234567890"

    with_provider_env_defaults(root_env, fallback_env, run)


def test_provider_env_loads_root_before_user_fallback(tmp_path: Path):
    root_env = tmp_path / ".env"
    fallback_env = tmp_path / "agents.env"
    root_env.write_text("OPENROUTER_API_KEY=sk-root-secret1234567890\n", encoding="utf-8")
    fallback_env.write_text("OPENROUTER_API_KEY=sk-fallback-secret1234567890\n", encoding="utf-8")

    def run() -> None:
        os.environ.pop("HIPSON_AGENTS_ENV", None)
        os.environ.pop("OPENROUTER_API_KEY", None)
        loaded = hipson_agents.load_provider_envs()
        assert loaded == [root_env.resolve(), fallback_env.resolve()]
        assert os.environ["OPENROUTER_API_KEY"] == "sk-root-secret1234567890"

    with_provider_env_defaults(root_env, fallback_env, run)


def test_empty_root_env_does_not_block_user_fallback(tmp_path: Path):
    root_env = tmp_path / ".env"
    fallback_env = tmp_path / "agents.env"
    root_env.write_text("OPENROUTER_API_KEY=\n", encoding="utf-8")
    fallback_env.write_text("OPENROUTER_API_KEY=sk-fallback-secret1234567890\n", encoding="utf-8")

    def run() -> None:
        os.environ.pop("HIPSON_AGENTS_ENV", None)
        os.environ.pop("OPENROUTER_API_KEY", None)
        hipson_agents.load_provider_envs()
        assert os.environ["OPENROUTER_API_KEY"] == "sk-fallback-secret1234567890"

    with_provider_env_defaults(root_env, fallback_env, run)


def test_hipson_agents_env_overrides_default_env_files(tmp_path: Path):
    explicit_env = tmp_path / "explicit.env"
    root_env = tmp_path / ".env"
    fallback_env = tmp_path / "agents.env"
    explicit_env.write_text("OPENROUTER_API_KEY=sk-explicit-secret1234567890\n", encoding="utf-8")
    root_env.write_text("OPENROUTER_API_KEY=sk-root-secret1234567890\n", encoding="utf-8")
    fallback_env.write_text("OPENROUTER_API_KEY=sk-fallback-secret1234567890\n", encoding="utf-8")

    def run() -> None:
        os.environ["HIPSON_AGENTS_ENV"] = str(explicit_env)
        os.environ.pop("OPENROUTER_API_KEY", None)
        loaded = hipson_agents.load_provider_envs()
        assert loaded == [explicit_env.resolve()]
        assert os.environ["OPENROUTER_API_KEY"] == "sk-explicit-secret1234567890"

    with_provider_env_defaults(root_env, fallback_env, run)


def test_discover_python_commands_prefers_local_runner(tmp_path: Path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "run_tests.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()

    assert hipson_project.discover_python_commands(tmp_path) == ["python3 scripts/run_tests.py"]


def test_scan_detects_unstaged_change(tmp_path: Path):
    repo = init_git_repo(tmp_path)
    (repo / "tracked.txt").write_text("base\nunstaged\n", encoding="utf-8")

    scan = hipson_project.build_scan(repo, include_diff=True, diff_lines=1)

    assert "tracked.txt" in scan
    assert "## Unstaged Diff" in scan
    assert "+unstaged" in scan
    assert "## Staged Diff" in scan


def test_scan_detects_staged_change(tmp_path: Path):
    repo = init_git_repo(tmp_path)
    (repo / "tracked.txt").write_text("base\nstaged\n", encoding="utf-8")
    run_git(repo, "add", "tracked.txt")

    scan = hipson_project.build_scan(repo, include_diff=True, diff_lines=1)

    assert "tracked.txt" in scan
    assert "## Staged Diff" in scan
    assert "+staged" in scan


def test_scan_detects_mixed_staged_and_unstaged_change(tmp_path: Path):
    repo = init_git_repo(tmp_path)
    (repo / "tracked.txt").write_text("base\nstaged\n", encoding="utf-8")
    run_git(repo, "add", "tracked.txt")
    (repo / "tracked.txt").write_text("base\nstaged\nunstaged\n", encoding="utf-8")

    scan = hipson_project.build_scan(repo, include_diff=True, diff_lines=1)

    assert "+staged" in scan
    assert "+unstaged" in scan
    assert hipson_project.changed_files(repo, hipson_project.git_root(repo)).count("tracked.txt") == 1


def test_scan_detects_untracked_file(tmp_path: Path):
    repo = init_git_repo(tmp_path)
    (repo / "new.txt").write_text("hello\n", encoding="utf-8")

    scan = hipson_project.build_scan(repo, include_diff=True, diff_lines=1)

    assert "new.txt" in scan
    assert "### Untracked file: `new.txt`" in scan
    assert "hello" in scan


def test_scan_redacts_tracked_secret_diff(tmp_path: Path):
    repo = init_git_repo(tmp_path)
    (repo / "tracked.txt").write_text("base\nOPENAI_API_KEY=sk-test-secret1234567890\n", encoding="utf-8")

    scan = hipson_project.build_scan(repo, include_diff=True, diff_lines=1)

    assert "sk-test-secret1234567890" not in scan
    assert REDACTION in scan


def test_scan_summarizes_sensitive_untracked_file(tmp_path: Path):
    repo = init_git_repo(tmp_path)
    (repo / ".env").write_text("OPENROUTER_API_KEY=sk-test-secret1234567890\n", encoding="utf-8")

    scan = hipson_project.build_scan(repo, include_diff=True, diff_lines=1)

    assert "sk-test-secret1234567890" not in scan
    assert "[sensitive file skipped]" in scan


def test_scan_redacts_sensitive_file_names_from_status_and_diff(tmp_path: Path):
    repo = init_git_repo(tmp_path)
    (repo / ".env.production").write_text("OPENAI_API_KEY=sk-test-secret1234567890\n", encoding="utf-8")

    scan = hipson_project.build_scan(repo, include_diff=True, diff_lines=1)

    assert ".env.production" not in scan
    assert "sk-test-secret1234567890" not in scan
    assert "[sensitive file skipped]" in scan


def test_scan_clean_repo_has_no_changed_files(tmp_path: Path):
    repo = init_git_repo(tmp_path)

    scan = hipson_project.build_scan(repo, include_diff=True, diff_lines=1)

    assert "## Changed Files\n- none" in scan
    assert "## Unstaged Diff Stat\n```text\nnone" in scan
    assert "## Staged Diff Stat\n```text\nnone" in scan


def test_multi_scan_renders_staged_and_unstaged_labels(tmp_path: Path):
    repo = init_git_repo(tmp_path)
    (repo / "tracked.txt").write_text("base\nstaged\n", encoding="utf-8")
    run_git(repo, "add", "tracked.txt")
    (repo / "tracked.txt").write_text("base\nstaged\nunstaged\n", encoding="utf-8")
    registry = tmp_path / "repos.yaml"
    registry.write_text(f"repos:\n  - name: Sample\n    path: {repo}\n", encoding="utf-8")
    records = []
    for item in hipson_project.parse_repos_yaml(registry):
        records.append(hipson_project.build_scan_record(item, include_diff=True))

    rendered = hipson_project.render_multi_scan(records)

    assert "### Unstaged Diff Stat" in rendered
    assert "### Staged Diff Stat" in rendered
    assert "### Unstaged Diff" in rendered
    assert "### Staged Diff" in rendered
    assert "+staged" in rendered
    assert "+unstaged" in rendered


def test_skill_validator_accepts_repo_skills():
    results = validate_skills(Path.cwd())

    assert results
    assert all(result.ok for result in results), results


def test_skill_validator_rejects_missing_frontmatter(tmp_path: Path):
    skill = tmp_path / "SKILL.md"
    skill.write_text("# Bad Skill\n", encoding="utf-8")

    result = validate_skill_file(skill)

    assert not result.ok
    assert any("frontmatter" in error for error in result.errors)


def test_skill_frontmatter_accepts_multiline_description():
    frontmatter, errors = parse_frontmatter(
        """---
name: sample-skill
description:
  First sentence for the skill.
  Second sentence with more details.
license: MIT
---

# Sample
"""
    )

    assert errors == []
    assert frontmatter["description"] == "First sentence for the skill. Second sentence with more details."


def test_packaged_assets_are_available_outside_repo_cwd(tmp_path: Path):
    assert packaged_asset("codex-workflow-kit/global/AGENTS.md").exists()
    assert packaged_asset("codex-workflow-kit/skills/hipson-workflow/SKILL.md").exists()
    result = run_cli(
        tmp_path,
        "install",
        "codex",
        "--dry-run",
        env={"HOME": str(tmp_path / "home"), "CODEX_HOME": str(tmp_path / "codex")},
    )

    assert result.returncode == 0, result.stderr
    assert str(tmp_path / "codex") in result.stdout
    assert not (tmp_path / "codex").exists()


def test_runtime_asset_finds_default_agent_config():
    assert runtime_asset("config/agents.json").exists()


def test_agent_router_uses_metadata():
    config = {
        "agents": {
            "ui": {
                "expertise": ["ui", "accessibility"],
                "use_when": ["premium ui screenshot"],
                "avoid_when": ["backend"],
                "context_budget": 1000,
                "can_handle_sensitive_context": False,
            },
            "architecture": {
                "expertise": ["architecture", "security"],
                "use_when": ["high risk architecture"],
                "avoid_when": [],
                "context_budget": 1000,
                "can_handle_sensitive_context": False,
            },
        }
    }

    routed = hipson_agents.route_agents(config, task="premium ui screenshot review", risk="ui", context_chars=100)

    assert routed[0][0] == "ui"
    assert hipson_agents.route_agents(config, task="premium ui", sensitive=True) == []


def test_llm_router_summary_redacts_sensitive_metadata():
    summary = hipson_agents.route_summary(
        task_type="review",
        risk="security",
        task="review OPENROUTER_API_KEY=sk-test-secret1234567890",
        files=["src/auth.py", ".env.production"],
        chars=4200,
        skills=["hipson-backend", "token=abc123secret"],
        sensitive=False,
    )

    text = json.dumps(summary)
    assert "sk-test-secret1234567890" not in text
    assert ".env.production" not in text
    assert "abc123secret" not in text
    assert summary["files"] == ["src/auth.py", "[sensitive file skipped]"]
    assert summary["chars"] == 4200


def test_llm_router_normalizes_model_choice():
    config = {
        "providers": {
            "openrouter": {
                "base_url": "https://openrouter.ai/api/v1",
                "api_key_env": "OPENROUTER_API_KEY",
            }
        },
        "router": {
            "provider": "openrouter",
            "model": "cheap-router",
            "temperature": 0,
            "max_tokens": 120,
        },
        "agents": {
            "reviewer_cheap": {
                "expertise": ["review", "security"],
                "use_when": ["security review"],
                "avoid_when": [],
                "context_budget": 10000,
                "can_handle_sensitive_context": False,
            }
        },
    }
    seen_payload = {}

    def fake_provider_chat(provider, payload, *, timeout=90):
        seen_payload.update(payload)
        return {"choices": [{"message": {"content": '{"agent":"reviewer_cheap","confidence":0.82,"reason":"security fit"}'}}]}

    old_provider_chat = hipson_agents.provider_chat
    try:
        hipson_agents.provider_chat = fake_provider_chat
        summary = hipson_agents.route_summary(
            task_type="review",
            risk="security",
            task="security review",
            files=["src/auth.py"],
            chars=4200,
            skills=["hipson-backend"],
            sensitive=False,
        )

        choice = hipson_agents.route_with_llm(config, summary)
    finally:
        hipson_agents.provider_chat = old_provider_chat

    assert choice == {
        "agent": "reviewer_cheap",
        "confidence": 0.82,
        "reason": "security fit",
        "source": "llm",
        "model": "cheap-router",
    }
    payload_text = json.dumps(seen_payload)
    assert "<packet>" not in payload_text
    assert "src/auth.py" in payload_text


def test_llm_router_dry_run_cli_sends_redacted_summary_only(tmp_path: Path):
    result = run_cli(
        tmp_path,
        "sidecar",
        "route",
        "--task",
        "review OPENAI_API_KEY=sk-test-secret1234567890",
        "--risk",
        "security",
        "--task-type",
        "review",
        "--file",
        ".env.production",
        "--skills",
        "hipson-backend",
        "--context-chars",
        "4200",
        "--llm",
        "--llm-dry-run",
    )

    assert result.returncode == 0, result.stderr
    assert "sk-test-secret1234567890" not in result.stdout
    assert ".env.production" not in result.stdout
    assert "[sensitive file skipped]" in result.stdout
    assert "candidates" in result.stdout


def test_memory_add_redacts_and_searches(tmp_path: Path):
    note = hipson_memory.add_note(
        root=tmp_path,
        scope="repo",
        repo="Hipson",
        kind="decision",
        summary="Use router memory with OPENAI_API_KEY=sk-test-secret1234567890",
        tags=["router", "memory"],
        sources=["docs/agent-provider-model.md"],
    )

    assert note.id
    stored = (tmp_path / "notes.jsonl").read_text(encoding="utf-8")
    assert "sk-test-secret1234567890" not in stored
    assert REDACTION in stored
    results = hipson_memory.search_notes(root=tmp_path, query="router", repo="Hipson")
    assert results
    assert results[0].note.kind == "decision"


def test_memory_refuses_sensitive_source_path(tmp_path: Path):
    try:
        hipson_memory.add_note(
            root=tmp_path,
            scope="repo",
            repo="Hipson",
            kind="decision",
            summary="Do not store secrets",
            sources=[".env"],
        )
    except SystemExit as exc:
        assert "sensitive source path" in str(exc)
    else:
        raise AssertionError("Expected sensitive memory source to be refused")


def test_memory_redacts_metadata_fields(tmp_path: Path):
    note = hipson_memory.add_note(
        root=tmp_path,
        scope="OPENAI_API_KEY=sk-test-secret1234567890",
        repo="https://example.test/repo?token=abc123secret",
        kind="decision",
        summary="ok",
        tags=["password=hunter2"],
    )

    stored = (tmp_path / "notes.jsonl").read_text(encoding="utf-8")

    assert "sk-test-secret1234567890" not in stored
    assert "abc123secret" not in stored
    assert "hunter2" not in stored
    assert REDACTION in stored
    assert note.scope != "OPENAI_API_KEY=sk-test-secret1234567890"


def test_codex_home_prefers_codex_home(tmp_path: Path):
    codex_home, warnings = detect_codex_home({"CODEX_HOME": str(tmp_path / "codex")})

    assert codex_home == (tmp_path / "codex").resolve()
    assert warnings == []


def test_codex_user_home_legacy_fallback_warns(tmp_path: Path):
    codex_home, warnings = detect_codex_home({"CODEX_USER_HOME": str(tmp_path)})

    assert codex_home == (tmp_path / ".codex").resolve()
    assert warnings


def test_hipson_home_uses_hipson_home_then_xdg(tmp_path: Path):
    home, warnings = detect_hipson_home({"HIPSON_HOME": str(tmp_path / "hipson")})
    assert home == (tmp_path / "hipson").resolve()
    assert warnings == []

    home, warnings = detect_hipson_home({"XDG_CONFIG_HOME": str(tmp_path / "xdg")})
    assert home == (tmp_path / "xdg" / "hipson").resolve()
    assert warnings == []


def test_merge_managed_block_preserves_user_content():
    existing = "User rules\n"
    block = f"{START_MARKER}\nHipson rules\n{END_MARKER}\n"

    merged = merge_managed_block(existing, block)

    assert "User rules" in merged
    assert "Hipson rules" in merged


def test_merge_managed_block_replaces_existing_block():
    existing = f"Before\n{START_MARKER}\nold\n{END_MARKER}\nAfter\n"
    block = f"{START_MARKER}\nnew\n{END_MARKER}\n"

    merged = merge_managed_block(existing, block)

    assert "Before" in merged
    assert "After" in merged
    assert "new" in merged
    assert "old" not in merged


def test_merge_managed_block_rejects_multiple_blocks():
    existing = (
        f"{START_MARKER}\none\n{END_MARKER}\n"
        f"{START_MARKER}\ntwo\n{END_MARKER}\n"
    )
    block = f"{START_MARKER}\nnew\n{END_MARKER}\n"

    try:
        merge_managed_block(existing, block)
    except ValueError as exc:
        assert "multiple" in str(exc)
    else:
        raise AssertionError("Expected multiple marker blocks to be rejected")


def test_install_codex_dry_run_does_not_write(tmp_path: Path):
    plan = install_codex(dry_run=True, codex_home=tmp_path / ".codex")

    assert plan.codex_home == (tmp_path / ".codex")
    assert not plan.codex_home.exists()


def test_install_codex_apply_preserves_existing_agents_content(tmp_path: Path):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    agents = codex_home / "AGENTS.md"
    agents.write_text("User custom rules\n", encoding="utf-8")

    install_codex(dry_run=False, codex_home=codex_home)

    text = agents.read_text(encoding="utf-8")
    assert "User custom rules" in text
    assert START_MARKER in text
    assert END_MARKER in text
    assert (codex_home / "skills" / "hipson-workflow" / "SKILL.md").exists()
    assert list(codex_home.glob("AGENTS.md.backup-*"))


def test_install_codex_apply_replaces_existing_marker_block(tmp_path: Path):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    agents = codex_home / "AGENTS.md"
    agents.write_text(f"Before\n{START_MARKER}\nold\n{END_MARKER}\nAfter\n", encoding="utf-8")

    install_codex(dry_run=False, codex_home=codex_home)

    text = agents.read_text(encoding="utf-8")
    assert "Before" in text
    assert "After" in text
    assert "\nold\n" not in text
    assert text.count(START_MARKER) == 1


def test_packet_generation_redacts_before_persistence(tmp_path: Path):
    repo = init_git_repo(tmp_path)
    (repo / "tracked.txt").write_text("base\nOPENROUTER_API_KEY=sk-test-secret1234567890\n", encoding="utf-8")
    output = tmp_path / "packet.md"

    result = run_cli(
        tmp_path,
        "packet",
        "review",
        str(repo),
        "--title",
        "Secret review",
        "--include-diff",
        "-o",
        str(output),
    )

    assert result.returncode == 0, result.stderr
    text = output.read_text(encoding="utf-8")
    assert "sk-test-secret1234567890" not in text
    assert REDACTION in text


def test_packet_generation_uses_compiled_sections(tmp_path: Path):
    repo = init_git_repo(tmp_path)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "run_tests.py").write_text("print('ok')\n", encoding="utf-8")
    output = tmp_path / "packet.md"

    result = run_cli(
        tmp_path,
        "packet",
        "review",
        str(repo),
        "--title",
        "Compiled review",
        "--skills",
        "skill_system-prompt-architect,skill_agentic-rag-orchestration",
        "-o",
        str(output),
    )

    assert result.returncode == 0, result.stderr
    text = output.read_text(encoding="utf-8")
    assert "## Selected skills/reference material" in text
    assert "`skill_system-prompt-architect`" in text
    assert "## Evidence bundle" in text
    assert "## Output format" in text
    assert "- `Do not edit files.`" not in text
    assert "Inspect reported or discovered command: `python3 scripts/run_tests.py`" in text


def test_packaged_assets_stay_in_sync():
    pairs = [
        ("ORCHESTRATOR.md", "src/hipson/assets/ORCHESTRATOR.md"),
        ("config/agents.json", "src/hipson/assets/config/agents.json"),
        ("templates/agent-review-packet.md", "src/hipson/assets/templates/agent-review-packet.md"),
        ("templates/agent-executor-packet.md", "src/hipson/assets/templates/agent-executor-packet.md"),
    ]

    for source, asset in pairs:
        assert Path(source).read_text(encoding="utf-8") == Path(asset).read_text(encoding="utf-8")


def test_sidecar_dry_run_redacts_packet_before_send_path(tmp_path: Path):
    packet = tmp_path / "packet.md"
    packet.write_text("token=sk-test-secret1234567890\n", encoding="utf-8")

    result = run_cli(
        tmp_path,
        "sidecar",
        "run",
        "--agent",
        "reviewer_cheap",
        "--packet",
        str(packet),
        "--dry-run",
        env={"HIPSON_HOME": str(tmp_path / "hipson")},
    )

    assert result.returncode == 0, result.stderr
    assert "sk-test-secret1234567890" not in result.stdout
    assert "redacted packet omitted" in result.stdout


def test_sidecar_run_without_key_fails_gracefully(tmp_path: Path):
    packet = tmp_path / "packet.md"
    packet.write_text("review me\n", encoding="utf-8")

    result = run_cli(
        tmp_path,
        "sidecar",
        "run",
        "--agent",
        "reviewer_cheap",
        "--packet",
        str(packet),
        env={"HIPSON_HOME": str(tmp_path / "hipson"), "OPENROUTER_API_KEY": ""},
    )

    assert result.returncode != 0
    assert "Missing OPENROUTER_API_KEY" in result.stderr


def test_cli_subprocess_smoke_commands(tmp_path: Path):
    repo = init_git_repo(tmp_path)
    registry = tmp_path / "repos.yaml"
    registry.write_text(f"repos:\n  - name: Sample\n    path: {repo}\n", encoding="utf-8")
    env = {
        "HOME": str(tmp_path / "home"),
        "CODEX_HOME": str(tmp_path / "codex"),
        "HIPSON_HOME": str(tmp_path / "hipson"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
    }
    commands = [
        ("--help",),
        ("doctor",),
        ("init", "--help"),
        ("check-setup", "--help"),
        ("scan", str(repo)),
        ("scan-many", str(registry)),
        ("skill", "validate"),
        ("install", "codex", "--dry-run"),
        ("packet", "review", "--help"),
        ("packet", "exec", "--help"),
        ("memory", "--memory-dir", str(tmp_path / "memory"), "list"),
        ("sidecar", "list"),
        ("sidecar", "route", "--task", "architecture security review"),
    ]

    for command in commands:
        result = run_cli(tmp_path, *command, env=env)
        assert result.returncode == 0, f"{command}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
