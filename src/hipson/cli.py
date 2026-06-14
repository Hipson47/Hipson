"""Hipson CLI."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import cast

from hipson import (
    __version__,
    agent_contract,
    agents,
    evals,
    learning,
    memory,
    model_profiles,
    packet_preflight,
    provider_doctor,
    quality,
    verification,
    workflow,
)
from hipson import evidence as evidence_mod
from hipson import hermes as hermes_bridge
from hipson import project as project_mod
from hipson.approvals import ApprovalPolicy
from hipson.assets import runtime_asset
from hipson.codex_install import format_install_plan, install_codex
from hipson.home import detect_codex_home, detect_hipson_home
from hipson.output_policy import resolve_output_path
from hipson.paths import package_root
from hipson.project import (
    build_scan,
    build_scan_record,
    discover_commands,
    parse_repos_yaml,
    render_multi_scan,
    resolve_project_from_registry,
    write_output,
)
from hipson.providers import (
    DEFAULT_API_KEY_ENV,
    DEFAULT_BASE_URL,
    ChatProvider,
    FakeProvider,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderResponse,
    ProviderToolCall,
)
from hipson.providers import (
    DEFAULT_MODEL as DEFAULT_PROVIDER_MODEL,
)
from hipson.redaction import is_sensitive_path, redact_text
from hipson.router import format_text_route, route_task
from hipson.runtime import HipsonRuntime, RuntimeMode, default_session_db
from hipson.scheduler import Scheduler, parse_json_object
from hipson.session import SessionStore, open_session_store
from hipson.skills import SkillLookupError, format_validation_results, list_skill_metadata, validate_skills, view_skill
from hipson.tools import ToolContext, ToolRegistryError, ToolSpec, bounded_tool_output, build_default_registry

PACKAGE_ROOT = package_root()
MAX_CLI_FIELD_CHARS = 220
MAX_CLI_CONTENT_CHARS = 1200
MAX_CLI_JSON_CHARS = 4000


def _bounded_cli(value: object, *, limit: int = MAX_CLI_FIELD_CHARS) -> str:
    text = redact_text(str(value))
    if len(text) <= limit:
        return text
    marker = f"... [truncated to {limit} chars]"
    return text[: max(0, limit - len(marker))].rstrip() + marker


def _bounded_json(value: object, *, limit: int = MAX_CLI_JSON_CHARS) -> str:
    text = redact_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))
    if len(text) <= limit:
        return text
    marker = f"\n... [truncated to {limit} chars]"
    return text[: max(0, limit - len(marker))].rstrip() + marker


def _session_db_path(args: argparse.Namespace) -> Path:
    return Path(args.session_db).expanduser() if getattr(args, "session_db", None) else default_session_db()


def _open_existing_session_store(args: argparse.Namespace) -> tuple[Path, SessionStore | None]:
    db_path = _session_db_path(args)
    if not db_path.exists():
        return db_path, None
    return db_path, open_session_store(db_path)


def _positive_limit(value: int, *, default: int = 20, maximum: int = 100) -> int:
    if value <= 0:
        return default
    return min(value, maximum)


def command_doctor(args: argparse.Namespace) -> int:
    codex_home, codex_warnings = detect_codex_home()
    hipson_home, hipson_warnings = detect_hipson_home()
    skill_results = validate_skills(PACKAGE_ROOT)
    skill_failures = [result for result in skill_results if not result.ok]
    git_available = shutil.which("git") is not None
    cwd_git = project_mod.git_root(Path.cwd()) if git_available else None
    config_path = runtime_asset("config/agents.json")
    provider_env_paths = agents.provider_env_paths()
    existing_provider_env = next((path for path in provider_env_paths if path.exists()), None)
    commands = discover_commands(Path.cwd())
    warnings = codex_warnings + hipson_warnings
    assets = {
        "codex_agents": runtime_asset("codex-workflow-kit/global/AGENTS.md").exists(),
        "hipson_skill": runtime_asset("codex-workflow-kit/skills/hipson-workflow/SKILL.md").exists(),
        "agent_config": config_path.exists(),
        "templates": runtime_asset("templates/hipson-progress.md").exists(),
    }
    checks = {
        "python_version": sys.version.split()[0],
        "package_version": __version__,
        "git": "ok" if git_available else "missing",
        "cwd": str(Path.cwd()),
        "cwd_git_root": str(cwd_git) if cwd_git else "not a git repo or unavailable",
        "codex_home": str(codex_home),
        "hipson_home": str(hipson_home),
        "sidecar_env": f"found: {existing_provider_env}" if existing_provider_env else "missing optional",
        "config_readable": config_path.exists(),
        "assets": assets,
        "skills_checked": len(skill_results),
        "skills_failed": len(skill_failures),
        "commands": commands,
        "warnings": warnings,
    }
    failed = (not git_available) or (not config_path.exists()) or any(not ok for ok in assets.values()) or bool(skill_failures)
    if args.json:
        print(json.dumps({**checks, "ok": not failed}, indent=2))
        return 1 if failed else 0

    print(f"python: {checks['python_version']}")
    print(f"hipson: {checks['package_version']}")
    print(f"git: {checks['git']}")
    print(f"cwd: {checks['cwd']}")
    print(f"cwd_git_root: {checks['cwd_git_root']}")
    print(f"codex_home: {checks['codex_home']}")
    print(f"hipson_home: {checks['hipson_home']}")
    print(f"sidecar_env: {checks['sidecar_env']}")
    print(f"config_readable: {'ok' if checks['config_readable'] else 'missing'}")
    print("assets:")
    for name, ok in assets.items():
        print(f"- {name}: {'ok' if ok else 'missing'}")
    print(f"skills: {len(skill_results)} checked, {len(skill_failures)} failed")
    print("commands:")
    for command in commands or ["none discovered"]:
        print(f"- {command}")
    if warnings:
        print("warnings:")
        for warning in warnings:
            print(f"- {warning}")
    return 1 if failed else 0


def command_scan(args: argparse.Namespace) -> int:
    try:
        project = project_mod.resolve_project(args.path)
        write_output(build_scan(project, include_diff=args.include_diff, diff_lines=args.diff_lines), args.output)
        return 0
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc.code, int) else 1


def command_scan_many(args: argparse.Namespace) -> int:
    registry = Path(args.registry).expanduser().resolve()
    if not registry.exists():
        print(f"Registry file does not exist: {registry}", file=sys.stderr)
        return 1
    repos = parse_repos_yaml(registry)
    if not repos:
        print(f"No repos found in registry: {registry}", file=sys.stderr)
        return 1
    records = []
    for repo in repos:
        repo = dict(repo)
        repo["path"] = str(resolve_project_from_registry(str(repo["path"]), registry))
        records.append(build_scan_record(repo, include_diff=args.include_diff))
    write_output(render_multi_scan(records), args.output)
    if args.json_output:
        json_path = Path(args.json_output).expanduser().resolve()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {json_path}")
    return 0


def command_init(args: argparse.Namespace) -> int:
    try:
        project_mod.command_init(args)
        return 0
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int) else 1


def command_check_setup(args: argparse.Namespace) -> int:
    try:
        project_mod.command_check_setup(args)
        return 0
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int) else 1


def command_skill_validate(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    results = validate_skills(root)
    print(format_validation_results(results))
    return 1 if any(not result.ok for result in results) else 0


def command_skill_list(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    skills = list_skill_metadata(root, query=args.query or "")
    if args.json:
        print(json.dumps({"skills": skills}, indent=2))
        return 0
    if not skills:
        print("No skills found.")
        return 0
    for skill in skills:
        status = "ok" if skill["ok"] else "failed"
        print(f"{skill['name']}: {status} - {skill['description']}")
        print(f"  path: {skill['path']}")
    return 0


def command_skill_view(args: argparse.Namespace) -> int:
    try:
        skill = view_skill(
            Path(args.root).expanduser().resolve(),
            name=args.name,
            max_chars=args.max_chars,
        )
    except SkillLookupError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(skill, indent=2))
    else:
        print(skill["content"])
    return 0


def command_skill_use(args: argparse.Namespace) -> int:
    try:
        skill = view_skill(
            Path(args.root).expanduser().resolve(),
            name=args.name,
            max_chars=args.max_chars,
        )
    except SkillLookupError as exc:
        print(exc, file=sys.stderr)
        return 1
    payload = {
        "skill_index": [{"name": skill["name"], "description": skill["description"], "path": skill["path"]}],
        "skill_excerpt": {
            "name": skill["name"],
            "content": skill["content"],
            "truncated": skill["truncated"],
            "runtime_policy": "reference_data_only",
        },
    }
    print(json.dumps(payload, indent=2) if args.json else skill["content"])
    return 0


def command_install_codex(args: argparse.Namespace) -> int:
    if args.apply == args.dry_run:
        print("Choose exactly one of --dry-run or --apply.", file=sys.stderr)
        return 2
    plan = install_codex(dry_run=args.dry_run)
    print(format_install_plan(plan, dry_run=args.dry_run))
    return 0


def command_route(args: argparse.Namespace) -> int:
    route = route_task(args.task)
    if args.json:
        print(json.dumps(route, indent=2))
    else:
        print(format_text_route(route))
    return 0


def command_contract_show(args: argparse.Namespace) -> int:
    try:
        contract = agent_contract.build_agent_contract(args.project)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc.code, int) else 1
    agent_contract.print_agent_contract(contract, json_output=args.json)
    return 0


def command_work(args: argparse.Namespace) -> int:
    try:
        plan = workflow.build_work_plan(
            task=args.task,
            project_path=args.project,
            include_diff=args.include_diff,
            diff_lines=args.diff_lines,
            write_packet=args.write_packet,
            packet_output=args.packet_output,
            inspect=args.inspect,
            allowed_edit=args.allowed_edit,
            acceptance=args.acceptance,
            verification=args.verification,
            skills=args.skills,
            ai_quality=args.ai_quality,
            ai_free=args.free_ai,
            ai_agent=args.ai_agent,
            ai_model=args.ai_model,
            ai_profile=args.ai_profile,
            allow_unsafe_output=args.allow_unsafe_output,
        )
    except (SystemExit, ValueError) as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc, SystemExit) and isinstance(exc.code, int) else 1
    if args.work_output:
        workflow.write_work_plan(
            plan,
            args.work_output,
            cwd=plan["project"],
            allow_unsafe_output=args.allow_unsafe_output,
        )
    workflow.print_work_plan(plan, json_output=args.json)
    return 0


def command_verify_run(args: argparse.Namespace) -> int:
    try:
        work_plan = verification.load_work_plan(args.work)
        commands = args.command if args.command else verification.verification_commands(work_plan, limit=args.limit)
        result = verification.run_verification(work_plan=work_plan, commands=commands, timeout=args.timeout)
        output = args.output or verification.default_verification_output(work_plan)
        written = verification.write_verification_artifact(
            result,
            output,
            cwd=str(work_plan.get("project", "")) or None,
            allow_unsafe_output=args.allow_unsafe_output,
        )
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc.code, int) else 1
    if args.json:
        print(json.dumps({**result, "output": str(written)}, indent=2, ensure_ascii=False))
    else:
        print(f"verification: {result['status']}")
        print(f"output: {written}")
        for item in result["results"]:
            print(f"- {item['command']}: {item['status']} ({item['exit_code']})")
    return 0 if result["status"] == "passed" else 1


def command_evidence_append(args: argparse.Namespace) -> int:
    try:
        work_plan = verification.load_work_plan(args.work)
        ledger_root = evidence_mod.evidence_dir(args.ledger_dir, project=work_plan.get("project"))
        verification_payload = evidence_mod.load_json_artifact(args.verification)
        quality_report_payload = evidence_mod.load_json_artifact(args.quality_report)
        quality_eval_payload = evidence_mod.load_json_artifact(args.quality_eval)
        record = evidence_mod.build_evidence_record(
            work_plan=work_plan,
            verification=verification_payload,
            quality_report=quality_report_payload,
            quality_eval=quality_eval_payload,
            sidecar_report=args.sidecar_report,
            ledger_root=ledger_root,
            human_decision=args.decision,
        )
        path = evidence_mod.append_record(ledger_root, record)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc.code, int) else 1
    if args.json:
        print(json.dumps({"ledger": str(path), "record": record}, indent=2, ensure_ascii=False))
    else:
        print(f"Appended evidence {record['event_id']} to {path}")
    return 0


def command_evidence_show(args: argparse.Namespace) -> int:
    if args.work:
        work_plan = verification.load_work_plan(args.work)
        root = evidence_mod.evidence_dir(args.ledger_dir, project=work_plan.get("project"))
    else:
        root = evidence_mod.evidence_dir(args.ledger_dir)
    records = evidence_mod.read_records(root)
    if args.work:
        work_id = str(work_plan.get("work_id", ""))
        records = [record for record in records if str(record.get("work", {}).get("work_id", "")) == work_id]
    if args.latest and records:
        records = [records[-1]]
    if args.json:
        print(json.dumps({"ledger": str(evidence_mod.ledger_path(root)), "records": records}, indent=2, ensure_ascii=False))
        return 0
    if not records:
        print("No evidence records found.")
        return 0
    for record in records:
        work = cast(dict[str, object], record.get("work", {}))
        verification_payload = cast(dict[str, object], record.get("verification", {}))
        print(
            f"{record.get('event_id', '')}: work={work.get('work_id', '')} "
            f"verification={verification_payload.get('status', 'unknown')}"
        )
    return 0


def command_evidence_export(args: argparse.Namespace) -> int:
    output_cwd: str | None = None
    if args.work:
        work_plan = verification.load_work_plan(args.work)
        root = evidence_mod.evidence_dir(args.ledger_dir, project=work_plan.get("project"))
        work_id = str(work_plan.get("work_id", ""))
        output_cwd = str(work_plan.get("project", "")) or None
    else:
        root = evidence_mod.evidence_dir(args.ledger_dir)
    records = evidence_mod.read_records(root)
    if args.work:
        records = [record for record in records if str(record.get("work", {}).get("work_id", "")) == work_id]
    payload = {"schema_version": "1.0", "records": records}
    if args.output:
        output = resolve_output_path(
            args.output,
            cwd=output_cwd,
            allow_unsafe=args.allow_unsafe_output,
            description="evidence export output",
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {output}")
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def command_audit_show(args: argparse.Namespace) -> int:
    try:
        work_plan = verification.load_work_plan(args.work)
        root = evidence_mod.evidence_dir(args.ledger_dir, project=work_plan.get("project"))
        bundle = evidence_mod.audit_bundle(work_path=args.work, ledger_root=root)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc.code, int) else 1
    if args.json:
        print(json.dumps(bundle, indent=2, ensure_ascii=False))
        return 0
    work = cast(dict[str, object], bundle["work"])
    print(f"work: {work.get('work_id', '')}")
    print(f"latest_status: {bundle['latest_status']}")
    print(f"latest_quality_gate: {bundle['latest_quality_gate']}")
    print(f"latest_eval_ok: {bundle['latest_eval_ok']}")
    print(f"latest_release_claim_gate: {bundle['latest_release_claim_gate']}")
    print("unknowns:")
    for item in cast(list[object], bundle["unknowns"]):
        print(f"- {item}")
    return 0


def command_audit_export(args: argparse.Namespace) -> int:
    try:
        work_plan = verification.load_work_plan(args.work)
        root = evidence_mod.evidence_dir(args.ledger_dir, project=work_plan.get("project"))
        bundle = evidence_mod.audit_bundle(work_path=args.work, ledger_root=root)
        output = resolve_output_path(
            args.output,
            cwd=str(work_plan.get("project", "")) or None,
            allow_unsafe=args.allow_unsafe_output,
            description="audit export output",
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc.code, int) else 1
    print(f"Wrote {output}")
    return 0


def command_provider_doctor(args: argparse.Namespace) -> int:
    try:
        payload = provider_doctor.doctor_payload(config_path=args.config, env_path=args.env)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc.code, int) else 1
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 1
    print(f"ok: {str(payload['ok']).lower()}")
    print(f"config: {payload['config']}")
    print(f"sent_repo_data: {str(payload['sent_repo_data']).lower()}")
    print("providers:")
    for name, status in cast(dict[str, dict[str, object]], payload["providers"]).items():
        key_status = "present" if status.get("api_key_present") else "missing optional"
        print(f"- {name}: url={'ok' if status.get('url_ok') else 'failed'} key={key_status}")
    print("recommendations:")
    for recommendation in cast(list[str], payload["recommendations"]):
        print(f"- {recommendation}")
    return 0 if payload["ok"] else 1


def command_model_profile_list(args: argparse.Namespace) -> int:
    payload = model_profiles.load_profiles(args.config)
    profiles = model_profiles.profiles(payload)
    if args.json:
        print(json.dumps({"schema_version": payload.get("schema_version", "1.0"), "profiles": profiles}, indent=2))
        return 0
    for name, profile in sorted(profiles.items()):
        print(f"{name}: agent={profile.get('agent')} model={profile.get('model')} tier={profile.get('quality_tier')}")
    return 0


def command_model_profile_show(args: argparse.Namespace) -> int:
    try:
        profile = model_profiles.get_profile(args.name, args.config)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc.code, int) else 1
    print(json.dumps(profile, indent=2) if args.json else model_profiles.render_profile(profile))
    return 0


def command_model_profile_recommend(args: argparse.Namespace) -> int:
    try:
        profile = model_profiles.recommend_profile(task=args.task, risk=args.risk, path=args.config)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc.code, int) else 1
    print(json.dumps(profile, indent=2) if args.json else model_profiles.render_profile(profile))
    return 0


def command_packet_preflight(args: argparse.Namespace) -> int:
    payload = packet_preflight.preflight_packet(args.path, max_chars=args.max_chars)
    if args.output:
        packet_preflight.write_preflight(payload, args.output, allow_unsafe_output=args.allow_unsafe_output)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"ok: {str(payload['ok']).lower()}")
        print(f"path: {payload['path']}")
        print(f"sha256: {payload['sha256'] or 'none'}")
        for error in cast(list[str], payload["errors"]):
            print(f"error: {error}")
        for warning in cast(list[str], payload["warnings"]):
            print(f"warning: {warning}")
    return 0 if payload["ok"] else 1


def command_quality_report(args: argparse.Namespace) -> int:
    try:
        report = quality.build_quality_report(
            work_path=args.work,
            verification_path=args.verify,
            sidecar_path=args.sidecar,
            decision=args.decision,
        )
        if args.output:
            quality.write_report(
                report,
                args.output,
                cwd=str(report.get("project", "")) or None,
                allow_unsafe_output=args.allow_unsafe_output,
            )
    except (SystemExit, OSError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else quality.render_report(report))
    return 0 if report.get("ok") else 1


def command_quality_eval(args: argparse.Namespace) -> int:
    try:
        result = evals.run_quality_eval(
            project_path=args.project,
            packet_path=args.packet,
            sidecar_path=args.sidecar,
            verification_path=args.verify,
            allow_work_artifact_packet=args.allow_work_artifact_packet,
        )
        if args.output:
            evals.write_eval(
                result,
                args.output,
                cwd=args.project,
                allow_unsafe_output=args.allow_unsafe_output,
            )
    except (SystemExit, OSError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else evals.render_eval(result))
    return 0 if result.get("ok") else 1


def command_hermes_doctor(args: argparse.Namespace) -> int:
    payload = hermes_bridge.doctor_payload()
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(f"ok: {str(payload['ok']).lower()}")
    print(f"hermes_cli: {payload['hermes_cli'] or 'missing'}")
    print(f"hermes_version: {payload['hermes_version'] or 'unknown'}")
    paths = cast(dict[str, object], payload["paths"])
    print(f"hermes_home: {paths['hermes_home']}")
    print(f"hermes_env: {paths['hermes_env']}")
    print(f"hermes_config: {paths['hermes_config']}")
    print(f"hipson_bus: {paths['bus_events']}")
    print(f"hermes_skill: {'ok' if payload['hermes_skill_installed'] else 'missing'} - {paths['skill_target']}")
    print(f"telegram_token_configured: {str(payload['telegram_token_configured']).lower()}")
    print(f"telegram_allowed_users_configured: {str(payload['telegram_allowed_users_configured']).lower()}")
    print("recommendations:")
    for recommendation in cast(list[str], payload["recommendations"]):
        print(f"- {recommendation}")
    return 0


def command_hermes_install_skill(args: argparse.Namespace) -> int:
    result = hermes_bridge.install_hermes_skill(force=args.force)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    action = "Already installed" if result["status"] == "exists" else "Installed"
    print(f"{action}: {result['target']}")
    print(f"source: {result['source']}")
    return 0


def command_hermes_intake(args: argparse.Namespace) -> int:
    try:
        event = hermes_bridge.build_intake_event(
            task=args.task,
            project=args.project,
            channel=args.channel,
            actor=args.actor,
        )
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc.code, int) else 1
    paths = hermes_bridge.hermes_paths()
    event["bus_event_path"] = paths["bus_events"]
    if not args.no_write:
        hermes_bridge.append_bus_event(event)
    rendered = hermes_bridge.render_intake(event)
    if args.output:
        write_output(rendered, args.output)
    if args.json:
        print(json.dumps(event, indent=2, ensure_ascii=False))
    elif not args.output:
        print(rendered)
    return 0


def command_hermes_events_list(args: argparse.Namespace) -> int:
    events = hermes_bridge.read_bus_events(limit=args.limit)
    if args.json:
        print(json.dumps({"events": events}, indent=2, ensure_ascii=False))
        return 0
    if not events:
        print("No Hermes bus events found.")
        return 0
    for event in events:
        route = cast(dict[str, object], event.get("route", {}))
        project = cast(dict[str, object], event.get("project", {}))
        print(
            f"{event.get('event_id', '')}: {event.get('status', '')} "
            f"mode={route.get('mode', '')} risk={route.get('risk', '')} project={project.get('path', '')}"
        )
    return 0


def command_hermes_events_show(args: argparse.Namespace) -> int:
    event = hermes_bridge.find_bus_event(args.event_id)
    if event is None:
        print(f"Hermes bus event not found: {args.event_id}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(event, indent=2, ensure_ascii=False))
    else:
        print(hermes_bridge.render_intake(event))
    return 0


def command_chat(args: argparse.Namespace) -> int:
    if args.fake and args.provider:
        print("--fake cannot be combined with --provider.", file=sys.stderr)
        return 2
    if args.fake_response and not args.fake:
        print("--fake-response requires --fake or --offline.", file=sys.stderr)
        return 2
    if args.fake_tool_call and not args.fake:
        print("--fake-tool-call requires --fake or --offline.", file=sys.stderr)
        return 2
    if args.fake_tool_input and not args.fake_tool_call:
        print("--fake-tool-input requires --fake-tool-call.", file=sys.stderr)
        return 2
    query = args.query
    if query is None and not sys.stdin.isatty():
        query = sys.stdin.read().strip()
    if query is None:
        query = input("hipson> ").strip()
    if not query:
        print("A chat request is required.", file=sys.stderr)
        return 2

    provider: ChatProvider | None
    if args.fake:
        fake_response = args.fake_response or "Fake provider response"
        provider = FakeProvider.with_text(fake_response)
        runtime_mode = RuntimeMode.FAKE
        model = "fake"
        if args.fake_tool_call:
            try:
                fake_tool_input = parse_json_object(args.fake_tool_input or "{}")
                _validate_fake_demo_tool(args.fake_tool_call)
            except json.JSONDecodeError as exc:
                print(exc, file=sys.stderr)
                return 2
            except (ValueError, ToolRegistryError) as exc:
                print(exc, file=sys.stderr)
                return 1
            provider = FakeProvider(
                responses=[
                    ProviderResponse(
                        text="Fake/offline tool call requested",
                        tool_calls=[
                            ProviderToolCall(
                                id="cli-fake-call-1",
                                name=args.fake_tool_call,
                                input=fake_tool_input,
                            )
                        ],
                        raw_metadata={"provider": "fake"},
                    ),
                    ProviderResponse(text=fake_response, raw_metadata={"provider": "fake"}),
                ]
            )
    elif args.provider:
        try:
            provider = _build_chat_provider(args)
        except ProviderError as exc:
            print(f"Chat provider is not configured: {exc}", file=sys.stderr)
            return 1
        runtime_mode = RuntimeMode.REAL
        model = args.model or DEFAULT_PROVIDER_MODEL
    else:
        provider = None
        runtime_mode = RuntimeMode.LOCAL
        model = "local-router"

    session_db = Path(args.session_db).expanduser() if args.session_db else default_session_db()
    store = open_session_store(session_db)
    try:
        runtime = HipsonRuntime(
            store=store,
            provider=provider,
            runtime_mode=runtime_mode,
            model=model,
        )
        if runtime_mode == RuntimeMode.LOCAL:
            result = runtime.run_local(query, cwd=Path.cwd(), session_id=args.session_id)
        else:
            result = runtime.run(query, cwd=Path.cwd(), session_id=args.session_id)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        store.close()

    if runtime_mode == RuntimeMode.LOCAL:
        prefix = "Local/router mode"
    elif args.fake:
        prefix = "Fake/offline mode"
    else:
        prefix = f"Provider mode ({args.provider})"
    print(f"{prefix}: {result.answer}")
    if result.tool_calls:
        print("Tool calls:")
        for record in result.tool_calls:
            print(f"- {record.name}: {record.status} - {_bounded_cli(record.summary, limit=MAX_CLI_CONTENT_CHARS)}")
    return 0 if result.status == "completed" else 1


def _build_chat_provider(args: argparse.Namespace) -> OpenAICompatibleProvider:
    if args.provider != "openai-compatible":
        raise ProviderError("Unsupported chat provider", str(args.provider))
    return OpenAICompatibleProvider.from_env(
        base_url=args.provider_url or DEFAULT_BASE_URL,
        api_key_env=args.api_key_env,
        timeout=float(args.provider_timeout),
        allow_local_http=bool(args.allow_local_provider_http),
    )


def _validate_fake_demo_tool(tool_name: str) -> ToolSpec:
    registry = build_default_registry()
    spec = registry.get(tool_name)
    if spec.risk_level != "read" or spec.approval_required:
        raise ToolRegistryError("Fake tool-call demo is limited to read-risk tools that do not require approval")
    return spec


def command_packet_review(args: argparse.Namespace) -> int:
    try:
        project_mod.command_review_packet(args)
        return 0
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int) else 1


def command_packet_exec(args: argparse.Namespace) -> int:
    try:
        project_mod.command_executor_packet(args)
        return 0
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int) else 1


def command_sidecar_list(args: argparse.Namespace) -> int:
    try:
        agents.command_list(args)
        return 0
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc.code, int) else 1


def command_sidecar_run(args: argparse.Namespace) -> int:
    try:
        agents.command_run(args)
        return 0
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc.code, int) else 1


def command_memory(args: argparse.Namespace) -> int:
    try:
        args.memory_func(args)
        return 0
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc.code, int) else 1


def command_session_list(args: argparse.Namespace) -> int:
    db_path, store = _open_existing_session_store(args)
    if store is None:
        if args.json:
            print(json.dumps({"session_db": str(db_path), "sessions": []}, indent=2))
        else:
            print("No sessions found.")
        return 0
    try:
        sessions = [_session_summary(store, session) for session in store.list_sessions(limit=_positive_limit(args.limit))]
    finally:
        store.close()
    if args.json:
        print(json.dumps({"session_db": str(db_path), "sessions": sessions}, indent=2))
        return 0
    if not sessions:
        print("No sessions found.")
        return 0
    for session in sessions:
        print(
            f"{session['id']} updated={session['updated_at']} "
            f"messages={session['message_count']} tools={session['tool_call_count']} "
            f"title={session['title']} cwd={session['cwd']}"
        )
    return 0


def command_session_show(args: argparse.Namespace) -> int:
    db_path, store = _open_existing_session_store(args)
    if store is None:
        print(f"Session DB does not exist: {db_path}", file=sys.stderr)
        return 1
    try:
        session = store.get_session(args.session_id)
        if session is None:
            print(f"Session does not exist: {args.session_id}", file=sys.stderr)
            return 1
        messages = store.list_messages(args.session_id, limit=_positive_limit(args.message_limit, maximum=200))
        tool_calls = store.list_tool_calls(args.session_id)[: _positive_limit(args.tool_limit, maximum=200)]
        approvals = store.list_approval_records(session_id=args.session_id)[: _positive_limit(args.tool_limit, maximum=200)]
        payload = {
            "session": _session_summary(store, session),
            "messages": [_message_summary(message) for message in messages],
            "tool_calls": [_tool_call_summary(tool_call) for tool_call in tool_calls],
            "approval_records": [_approval_summary(approval) for approval in approvals],
        }
    finally:
        store.close()
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    session_payload = cast(dict[str, object], payload["session"])
    print(f"session: {session_payload['id']}")
    print(f"title: {session_payload['title']}")
    print(f"cwd: {session_payload['cwd']}")
    print(f"created_at: {session_payload['created_at']}")
    print(f"updated_at: {session_payload['updated_at']}")
    print("messages:")
    for message in cast(list[dict[str, object]], payload["messages"]):
        print(f"- {message['role']} {message['id']} {message['created_at']}")
        print(f"  {_bounded_cli(message['content'], limit=MAX_CLI_CONTENT_CHARS)}")
    print("tool_calls:")
    for tool_call in cast(list[dict[str, object]], payload["tool_calls"]):
        print(
            f"- {tool_call['status']} {tool_call['tool_name']} "
            f"risk={tool_call['risk_level']} approval={tool_call['approval_status']}"
        )
        if tool_call["error"]:
            print(f"  error: {_bounded_cli(tool_call['error'], limit=MAX_CLI_CONTENT_CHARS)}")
        print(f"  output: {_bounded_json(tool_call['output'])}")
    print("approval_records:")
    for approval in cast(list[dict[str, object]], payload["approval_records"]):
        print(
            f"- {approval['decision']} {approval['tool_name']} "
            f"risk={approval['risk_level']} source={approval['source']}"
        )
        if approval["reason"]:
            print(f"  reason: {_bounded_cli(approval['reason'], limit=MAX_CLI_CONTENT_CHARS)}")
        if approval["expires_at"]:
            print(f"  expires_at: {approval['expires_at']}")
    return 0


def command_session_search(args: argparse.Namespace) -> int:
    db_path, store = _open_existing_session_store(args)
    if store is None:
        if args.json:
            print(json.dumps({"session_db": str(db_path), "results": []}, indent=2))
        else:
            print("No session search results.")
        return 0
    try:
        search_backend = store.search_backend()
        results = [_search_summary(result) for result in store.search_messages(args.query, limit=_positive_limit(args.limit))]
    finally:
        store.close()
    if args.json:
        print(
            json.dumps(
                {"session_db": str(db_path), "search_backend": search_backend, "results": results},
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    if not results:
        print("No session search results.")
        return 0
    for result in results:
        print(
            f"{result['session_id']} message={result['message_id']} "
            f"role={result['role']} created_at={result['created_at']}"
        )
        print(f"  {result['snippet']}")
    return 0


def command_tool_list(args: argparse.Namespace) -> int:
    registry = build_default_registry()
    tools = [_tool_spec_payload(spec) for spec in registry.list()]
    if args.json:
        print(json.dumps({"tools": tools}, indent=2, ensure_ascii=False))
        return 0
    for tool in tools:
        print(
            f"{tool['name']} risk={tool['risk_level']} "
            f"approval_required={tool['approval_required']} - {tool['description']}"
        )
    return 0


def command_tool_show(args: argparse.Namespace) -> int:
    registry = build_default_registry()
    try:
        spec = registry.get(args.name)
    except ToolRegistryError as exc:
        print(exc, file=sys.stderr)
        return 1
    payload = _tool_spec_payload(spec)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(f"name: {payload['name']}")
    print(f"description: {payload['description']}")
    print(f"risk_level: {payload['risk_level']}")
    print(f"approval_required: {payload['approval_required']}")
    print("input_schema:")
    print(_bounded_json(payload["input_schema"]))
    print("output_contract:")
    print(_bounded_json(payload["output_contract"]))
    print("path_policies:")
    print(_bounded_json(payload["path_policies"]))
    return 0


def command_tool_run(args: argparse.Namespace) -> int:
    registry = build_default_registry()
    cwd = Path(args.cwd).expanduser().resolve() if args.cwd else Path.cwd().resolve()
    session_id = ""
    store: SessionStore | None = None
    try:
        try:
            input_data = parse_json_object(args.input)
        except (json.JSONDecodeError, ValueError) as exc:
            payload = _tool_run_payload(
                args.name,
                status="rejected",
                risk_level="dangerous",
                approval_status="invalid_input",
                summary="Tool input was rejected",
                error=str(exc),
            )
            _print_tool_run_payload(payload, json_output=args.json)
            return 2

        try:
            spec = registry.validate_input(args.name, input_data)
        except ToolRegistryError as exc:
            payload = _tool_run_payload(
                args.name,
                status="rejected",
                risk_level="dangerous",
                approval_status="invalid_input",
                summary="Tool input was rejected",
                error=str(exc),
            )
            _print_tool_run_payload(payload, json_output=args.json)
            return 1

        if spec.risk_level != "read" or spec.approval_required:
            payload = _tool_run_payload(
                spec.name,
                status="rejected",
                risk_level=spec.risk_level,
                approval_status="requires_approval" if spec.approval_required else "blocked",
                summary="Tool run rejected",
                error="Manual tool run is limited to read-risk tools that do not require approval",
            )
            _print_tool_run_payload(payload, json_output=args.json)
            return 1

        if args.session_db or args.session_id:
            store = open_session_store(_session_db_path(args))
            try:
                session_id = _tool_run_session_id(store, args.session_id, cwd)
            except ToolRegistryError as exc:
                payload = _tool_run_payload(
                    spec.name,
                    status="rejected",
                    risk_level=spec.risk_level,
                    approval_status="invalid_session",
                    summary="Tool run rejected",
                    error=str(exc),
                )
                _print_tool_run_payload(payload, json_output=args.json)
                return 1

        context = ToolContext(cwd=cwd, repo_root=project_mod.git_root(cwd), session_id=session_id or "cli-tool-run")
        decision = ApprovalPolicy().evaluate_tool(spec, input_data, context)
        if not decision.allowed:
            payload = _tool_run_payload(
                spec.name,
                status="rejected",
                risk_level=spec.risk_level,
                approval_status="blocked" if decision.blocked else "requires_approval",
                summary="Tool run rejected",
                error=decision.reason,
                session_id=session_id,
            )
            _persist_cli_tool_run(store, session_id, spec.name, input_data, payload)
            _print_tool_run_payload(payload, json_output=args.json)
            return 1

        result = registry.run(spec.name, input_data, context)
        status = "completed" if result.ok else "failed"
        payload = _tool_run_payload(
            spec.name,
            status=status,
            risk_level=spec.risk_level,
            approval_status="approved",
            summary=result.summary,
            output=bounded_tool_output(result),
            error=result.error,
            artifacts=list(result.artifacts),
            session_id=session_id,
        )
        _persist_cli_tool_run(store, session_id, spec.name, input_data, payload)
        _print_tool_run_payload(payload, json_output=args.json)
        return 0 if result.ok else 1
    finally:
        if store is not None:
            store.close()


def command_learn_propose(args: argparse.Namespace) -> int:
    db_path, store = _open_existing_session_store(args)
    if store is None:
        print(f"Session DB does not exist: {db_path}", file=sys.stderr)
        return 1
    try:
        proposals = learning.propose_from_session(
            store,
            args.session_id,
            max_summary_chars=_positive_limit(args.max_summary_chars, default=500, maximum=2000),
        )
    except learning.LearningError as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        store.close()
    records = [proposal.to_dict() for proposal in proposals]
    if args.json:
        print(json.dumps({"session_id": args.session_id, "proposals": records}, indent=2, ensure_ascii=False))
        return 0
    if not records:
        print("No learning proposals found.")
        return 0
    print(f"Learning proposals for session {args.session_id}:")
    for proposal in records:
        print(
            f"- {proposal['id']} kind={proposal['kind']} "
            f"approval={proposal['approval_status']} confidence={proposal['confidence']}"
        )
        print(f"  {_bounded_cli(proposal['summary'], limit=MAX_CLI_CONTENT_CHARS)}")
    print("Use `hipson learn apply-memory --session-id ... --proposal-id ... --memory-dir ...` to apply a memory proposal.")
    return 0


def command_learn_apply_memory(args: argparse.Namespace) -> int:
    db_path, store = _open_existing_session_store(args)
    if store is None:
        print(f"Session DB does not exist: {db_path}", file=sys.stderr)
        return 1
    try:
        proposals = learning.propose_from_session(store, args.session_id)
    except learning.LearningError as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        store.close()

    proposal = next((candidate for candidate in proposals if candidate.id == args.proposal_id), None)
    if proposal is None:
        print(f"Learning proposal does not exist: {args.proposal_id}", file=sys.stderr)
        return 1
    if proposal.kind != "memory":
        print(f"Proposal {args.proposal_id} is {proposal.kind}; only memory proposals can be applied.", file=sys.stderr)
        return 1

    memory_root = Path(args.memory_dir).expanduser()
    if is_sensitive_path(memory_root):
        print(f"Refusing to write memory under sensitive path: {memory_root}", file=sys.stderr)
        return 1
    for source_ref in proposal.source_refs:
        if is_sensitive_path(source_ref) or source_ref != redact_text(source_ref):
            print(f"Refusing to store sensitive source reference: {source_ref}", file=sys.stderr)
            return 1

    payload = proposal.payload
    raw_tags = payload.get("tags", proposal.tags)
    tags = [str(tag) for tag in raw_tags] if isinstance(raw_tags, list) else proposal.tags
    raw_confidence = payload.get("confidence", proposal.confidence)
    confidence = float(raw_confidence) if isinstance(raw_confidence, (str, int, float)) else proposal.confidence
    try:
        note = memory.add_note(
            root=memory_root,
            scope=str(payload.get("scope", "session")),
            repo=str(payload.get("repo", "")),
            kind=str(payload.get("kind", "handoff")),
            summary=_bounded_cli(payload.get("summary", proposal.summary), limit=MAX_CLI_CONTENT_CHARS),
            tags=tags,
            sources=proposal.source_refs,
            confidence=confidence,
        )
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc.code, int) else 1

    result = {
        "status": "applied",
        "proposal_id": proposal.id,
        "note_id": note.id,
        "memory_dir": str(memory_root.resolve()),
        "source_refs": proposal.source_refs,
    }
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Applied memory proposal {proposal.id} as note {note.id}")
        print(f"memory_dir: {result['memory_dir']}")
        print(f"sources: {', '.join(proposal.source_refs)}")
    return 0


def _session_summary(store: SessionStore, session: dict[str, object]) -> dict[str, object]:
    counts = store.session_counts(str(session["id"]))
    return {
        "id": str(session["id"]),
        "title": _bounded_cli(session.get("title", "")),
        "cwd": _bounded_cli(session.get("cwd", "")),
        "repo_root": _bounded_cli(session.get("repo_root", "")),
        "status": str(session.get("status", "")),
        "created_at": str(session.get("created_at", "")),
        "updated_at": str(session.get("updated_at", "")),
        "message_count": counts["messages"],
        "tool_call_count": counts["tool_calls"],
    }


def _message_summary(message: dict[str, object]) -> dict[str, object]:
    return {
        "id": str(message.get("id", "")),
        "role": str(message.get("role", "")),
        "content": _bounded_cli(message.get("content", ""), limit=MAX_CLI_CONTENT_CHARS),
        "metadata": message.get("metadata", {}),
        "created_at": str(message.get("created_at", "")),
    }


def _tool_call_summary(tool_call: dict[str, object]) -> dict[str, object]:
    return {
        "id": str(tool_call.get("id", "")),
        "tool_name": _bounded_cli(tool_call.get("tool_name", "")),
        "input": tool_call.get("input", {}),
        "output": tool_call.get("output", {}),
        "risk_level": str(tool_call.get("risk_level", "")),
        "approval_status": str(tool_call.get("approval_status", "")),
        "status": str(tool_call.get("status", "")),
        "error": _bounded_cli(tool_call.get("error", ""), limit=MAX_CLI_CONTENT_CHARS),
        "started_at": str(tool_call.get("started_at", "")),
        "completed_at": str(tool_call.get("completed_at", "")),
    }


def _search_summary(row: dict[str, object]) -> dict[str, object]:
    return {
        "kind": str(row.get("kind", "message")),
        "record_id": str(row.get("record_id", row.get("message_id", ""))),
        "session_id": str(row.get("session_id", "")),
        "session_title": _bounded_cli(row.get("session_title", "")),
        "message_id": str(row.get("message_id", "")),
        "role": str(row.get("role", "")),
        "created_at": str(row.get("created_at", "")),
        "snippet": _bounded_cli(row.get("content", ""), limit=MAX_CLI_CONTENT_CHARS),
    }


def _approval_summary(approval: dict[str, object]) -> dict[str, object]:
    return {
        "id": str(approval.get("id", "")),
        "source": _bounded_cli(approval.get("source", "")),
        "tool_name": _bounded_cli(approval.get("tool_name", "")),
        "risk_level": str(approval.get("risk_level", "")),
        "decision": str(approval.get("decision", "")),
        "reason": _bounded_cli(approval.get("reason", ""), limit=MAX_CLI_CONTENT_CHARS),
        "approved_by": _bounded_cli(approval.get("approved_by", "")),
        "scope": str(approval.get("scope", "")),
        "metadata": approval.get("metadata", {}),
        "created_at": str(approval.get("created_at", "")),
        "expires_at": str(approval.get("expires_at", "") or ""),
    }


def _tool_spec_payload(spec: ToolSpec) -> dict[str, object]:
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.input_schema,
        "output_contract": spec.output_contract,
        "risk_level": spec.risk_level,
        "approval_required": spec.approval_required,
        "path_policies": [
            {
                "field": policy.field,
                "mode": policy.mode,
                "base_field": policy.base_field,
            }
            for policy in spec.path_policies
        ],
    }


def _tool_run_payload(
    tool_name: str,
    *,
    status: str,
    risk_level: str,
    approval_status: str,
    summary: str,
    output: dict[str, object] | None = None,
    error: str = "",
    artifacts: list[str] | None = None,
    session_id: str = "",
) -> dict[str, object]:
    return {
        "tool_name": _bounded_cli(tool_name),
        "status": status,
        "risk_level": risk_level,
        "approval_status": approval_status,
        "summary": _bounded_cli(summary, limit=MAX_CLI_CONTENT_CHARS),
        "output": output or {},
        "error": _bounded_cli(error, limit=MAX_CLI_CONTENT_CHARS),
        "artifacts": artifacts or [],
        "session_id": session_id,
    }


def _print_tool_run_payload(payload: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    print(
        f"{payload['tool_name']}: {payload['status']} "
        f"risk={payload['risk_level']} approval={payload['approval_status']}"
    )
    if payload["summary"]:
        print(f"summary: {payload['summary']}")
    if payload["error"]:
        print(f"error: {payload['error']}")
    print("output:")
    print(_bounded_json(payload["output"]))


def _tool_run_session_id(store: SessionStore, session_id: str | None, cwd: Path) -> str:
    if session_id:
        if store.get_session(session_id) is None:
            raise ToolRegistryError(f"Session does not exist: {session_id}")
        return session_id
    return store.create_session(cwd=str(cwd), repo_root=str(project_mod.git_root(cwd) or ""), title="Hipson tool run")


def _persist_cli_tool_run(
    store: SessionStore | None,
    session_id: str,
    tool_name: str,
    input_data: dict[str, object],
    payload: dict[str, object],
) -> None:
    if store is None or not session_id:
        return
    tool_call_id = store.add_tool_call(
        session_id,
        tool_name=tool_name,
        input_data=input_data,
        output_data=cast(dict[str, object], payload.get("output", {})),
        risk_level=str(payload["risk_level"]),
        approval_status=str(payload["approval_status"]),
        status=str(payload["status"]),
        error=str(payload["error"]),
    )
    store.add_approval_record(
        session_id=session_id,
        tool_call_id=tool_call_id,
        source="cli.tool.run",
        tool_name=tool_name,
        risk_level=str(payload["risk_level"]),
        decision=str(payload["approval_status"]),
        reason=str(payload["error"] or payload["summary"]),
        approved_by="policy" if payload["approval_status"] == "approved" else "",
        metadata={"tool_status": str(payload["status"])},
    )


def command_scheduler_create(args: argparse.Namespace) -> int:
    try:
        input_data = parse_json_object(args.input)
    except (json.JSONDecodeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    store = open_session_store(Path(args.session_db).expanduser() if args.session_db else default_session_db())
    try:
        scheduler = Scheduler.with_defaults(store)
        job_id = scheduler.create_tool_job(
            tool_name=args.tool,
            input_data=input_data,
            run_after=args.run_after,
            approved=args.approved,
        )
    finally:
        store.close()
    print(job_id)
    return 0


def command_scheduler_list(args: argparse.Namespace) -> int:
    store = open_session_store(Path(args.session_db).expanduser() if args.session_db else default_session_db())
    try:
        jobs = store.list_jobs(status=args.status, limit=args.limit)
    finally:
        store.close()
    if args.json:
        print(json.dumps({"jobs": jobs}, indent=2))
        return 0
    if not jobs:
        print("No scheduler jobs found.")
        return 0
    for job in jobs:
        print(f"{job['id']}: {job['status']} {job['kind']} run_after={job.get('run_after') or 'now'}")
    return 0


def command_scheduler_tick(args: argparse.Namespace) -> int:
    store = open_session_store(Path(args.session_db).expanduser() if args.session_db else default_session_db())
    try:
        scheduler = Scheduler.with_defaults(store)
        results = scheduler.tick(cwd=Path.cwd(), now=args.now, limit=args.limit)
    finally:
        store.close()
    if args.json:
        print(json.dumps({"results": [result.__dict__ for result in results]}, indent=2))
        return 0
    if not results:
        print("No due scheduler jobs.")
        return 0
    for result in results:
        detail = f": {result.error}" if result.error else ""
        print(f"{result.job_id}: {result.status} - {result.summary}{detail}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hipson", description="Hipson Codex-native local dev workflow tool")
    parser.add_argument("--version", action="version", version=f"hipson {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check local Hipson prerequisites")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable doctor output")
    doctor.set_defaults(func=command_doctor)

    scan = subparsers.add_parser("scan", help="Scan a repository")
    scan.add_argument("path")
    scan.add_argument("--include-diff", action="store_true")
    scan.add_argument("--diff-lines", type=int, default=3)
    scan.add_argument("-o", "--output", help="Write markdown to a file")
    scan.set_defaults(func=command_scan)

    scan_many = subparsers.add_parser("scan-many", help="Scan repos from a registry")
    scan_many.add_argument("registry", nargs="?", default="repos.yaml", help="Repo registry YAML")
    scan_many.add_argument("--include-diff", action="store_true", help="Include staged and unstaged diff bodies")
    scan_many.add_argument("-o", "--output", help="Write markdown to a file")
    scan_many.add_argument("--json", dest="json_output", help="Write JSON scan records to a file")
    scan_many.set_defaults(func=command_scan_many)

    route = subparsers.add_parser("route", help="Suggest a deterministic Hipson workflow for a task")
    route.add_argument("--task", required=True, help="Task description")
    route.add_argument("--json", action="store_true", help="Print machine-readable route output")
    route.set_defaults(func=command_route)

    contract = subparsers.add_parser("contract", help="Inspect the first-class Hipson agent contract")
    contract_sub = contract.add_subparsers(dest="contract_command", required=True)
    contract_show = contract_sub.add_parser("show", help="Show the local agent contract")
    contract_show.add_argument("--project", default=".", help="Target repository; defaults to current directory")
    contract_show.add_argument("--json", action="store_true", help="Print machine-readable agent contract")
    contract_show.set_defaults(func=command_contract_show)

    work = subparsers.add_parser(
        "work",
        help="Build a provider-free Codex work brief from route, scan, packet, verify, and handoff steps",
    )
    work.add_argument("--task", required=True, help="Task description")
    work.add_argument("--project", default=".", help="Target repository; defaults to current directory")
    work.add_argument("--include-diff", action="store_true", default=True, help="Include redacted diff context")
    work.add_argument("--no-diff", dest="include_diff", action="store_false", help="Do not include diff context")
    work.add_argument("--diff-lines", type=int, default=120, help="Diff line budget for the embedded scan")
    work.add_argument("--write-packet", action="store_true", help="Write the selected local review/executor packet")
    work.add_argument("--packet-output", help="Packet output path; defaults to runs/review-packet.md or runs/executor-packet.md")
    work.add_argument("--work-output", help="Write the machine-readable work plan JSON to this path")
    work.add_argument("--inspect", help="Comma-separated files to inspect for executor packets")
    work.add_argument("--allowed-edit", help="Comma-separated edit scope required for writing executor packets")
    work.add_argument("--acceptance", help="Observable acceptance criterion for executor packets")
    work.add_argument("--verification", help="Primary verification command for the work brief")
    work.add_argument("--skills", help="Comma-separated extra skills or references to include")
    work.add_argument("--ai-quality", action="store_true", help="Prepare an explicit advisory AI quality pass")
    work.add_argument("--free-ai", action="store_true", help="Use OpenRouter's free model router for the AI quality pass")
    work.add_argument("--ai-agent", help="Sidecar agent to use for the AI quality pass")
    work.add_argument("--ai-model", help="OpenRouter model slug to use for the AI quality pass")
    work.add_argument("--ai-profile", help="Curated model profile to use for the AI quality pass")
    work.add_argument(
        "--allow-unsafe-output",
        action="store_true",
        help="Explicitly allow generated artifact outputs outside runs/, scans/, docs/, or memory/",
    )
    work.add_argument("--json", action="store_true", help="Print machine-readable work plan")
    work.set_defaults(func=command_work)

    model = subparsers.add_parser("model", help="Inspect curated AI model profiles")
    model_sub = model.add_subparsers(dest="model_command", required=True)
    model_profile = model_sub.add_parser("profile", help="Curated model profile commands")
    model_profile.add_argument("--config", help="Model profile config JSON")
    model_profile_sub = model_profile.add_subparsers(dest="model_profile_command", required=True)
    model_profile_list = model_profile_sub.add_parser("list", help="List model profiles")
    model_profile_list.add_argument("--json", action="store_true", help="Print machine-readable profiles")
    model_profile_list.set_defaults(func=command_model_profile_list)
    model_profile_show = model_profile_sub.add_parser("show", help="Show one model profile")
    model_profile_show.add_argument("name")
    model_profile_show.add_argument("--json", action="store_true", help="Print machine-readable profile")
    model_profile_show.set_defaults(func=command_model_profile_show)
    model_profile_recommend = model_profile_sub.add_parser("recommend", help="Recommend a profile for a task")
    model_profile_recommend.add_argument("--task", required=True, help="Task description")
    model_profile_recommend.add_argument("--risk", default="normal", help="Risk hint")
    model_profile_recommend.add_argument("--json", action="store_true", help="Print machine-readable profile")
    model_profile_recommend.set_defaults(func=command_model_profile_recommend)

    verify = subparsers.add_parser("verify", help="Run verification commands from a Hipson work plan")
    verify_sub = verify.add_subparsers(dest="verify_command", required=True)
    verify_run = verify_sub.add_parser("run", help="Run local verification commands from a work JSON artifact")
    verify_run.add_argument("--work", required=True, help="Work plan JSON path from hipson work --work-output")
    verify_run.add_argument("--command", action="append", help="Override verification command; repeatable")
    verify_run.add_argument("--limit", type=int, help="Run only the first N work-plan verification commands")
    verify_run.add_argument("--timeout", type=int, default=verification.DEFAULT_TIMEOUT, help="Per-command timeout in seconds")
    verify_run.add_argument("-o", "--output", help="Write verification JSON artifact; defaults to runs/<work-id>-verification.json")
    verify_run.add_argument(
        "--allow-unsafe-output",
        action="store_true",
        help="Explicitly allow verification output outside generated artifact directories",
    )
    verify_run.add_argument("--json", action="store_true", help="Print machine-readable verification result")
    verify_run.set_defaults(func=command_verify_run)

    evidence = subparsers.add_parser("evidence", help="Manage local Hipson evidence ledger records")
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True)
    evidence_append = evidence_sub.add_parser("append", help="Append one evidence record for a work plan")
    evidence_append.add_argument("--work", required=True, help="Work plan JSON path")
    evidence_append.add_argument("--verification", help="Verification JSON artifact path")
    evidence_append.add_argument("--quality-report", help="Quality report JSON artifact path")
    evidence_append.add_argument("--quality-eval", help="Quality eval JSON artifact path")
    evidence_append.add_argument("--sidecar-report", help="Optional sidecar report path or id")
    evidence_append.add_argument("--decision", default="pending", help="Human decision, e.g. pending, accepted, blocked")
    evidence_append.add_argument("--ledger-dir", help="Ledger directory; defaults to <project>/runs")
    evidence_append.add_argument("--json", action="store_true", help="Print machine-readable appended record")
    evidence_append.set_defaults(func=command_evidence_append)
    evidence_show = evidence_sub.add_parser("show", help="Show evidence ledger records")
    evidence_show.add_argument("--work", help="Optional work plan JSON path to filter records")
    evidence_show.add_argument("--ledger-dir", help="Ledger directory; defaults to cwd/runs")
    evidence_show.add_argument("--latest", action="store_true", help="Show only the latest record")
    evidence_show.add_argument("--json", action="store_true", help="Print machine-readable records")
    evidence_show.set_defaults(func=command_evidence_show)
    evidence_export = evidence_sub.add_parser("export", help="Export evidence ledger records")
    evidence_export.add_argument("--work", help="Optional work plan JSON path to filter records")
    evidence_export.add_argument("--ledger-dir", help="Ledger directory; defaults to cwd/runs")
    evidence_export.add_argument("-o", "--output", help="Write export JSON to a file")
    evidence_export.add_argument(
        "--allow-unsafe-output",
        action="store_true",
        help="Explicitly allow evidence export output outside generated artifact directories",
    )
    evidence_export.set_defaults(func=command_evidence_export)

    audit = subparsers.add_parser("audit", help="Show or export an audit bundle for a work plan")
    audit_sub = audit.add_subparsers(dest="audit_command", required=True)
    audit_show = audit_sub.add_parser("show", help="Show an audit bundle")
    audit_show.add_argument("--work", required=True, help="Work plan JSON path")
    audit_show.add_argument("--ledger-dir", help="Ledger directory; defaults to <project>/runs")
    audit_show.add_argument("--json", action="store_true", help="Print machine-readable audit bundle")
    audit_show.set_defaults(func=command_audit_show)
    audit_export = audit_sub.add_parser("export", help="Export an audit bundle JSON file")
    audit_export.add_argument("--work", required=True, help="Work plan JSON path")
    audit_export.add_argument("--ledger-dir", help="Ledger directory; defaults to <project>/runs")
    audit_export.add_argument("-o", "--output", required=True, help="Audit bundle output path")
    audit_export.add_argument(
        "--allow-unsafe-output",
        action="store_true",
        help="Explicitly allow audit output outside generated artifact directories",
    )
    audit_export.set_defaults(func=command_audit_export)

    provider = subparsers.add_parser("provider", help="Inspect explicit provider readiness without sending repo data")
    provider_sub = provider.add_subparsers(dest="provider_command", required=True)
    provider_doctor_cmd = provider_sub.add_parser("doctor", help="Check provider and agent config readiness")
    provider_doctor_cmd.add_argument("--config", help="Agent config JSON; defaults to packaged config")
    provider_doctor_cmd.add_argument("--env", help="Provider env file")
    provider_doctor_cmd.add_argument("--json", action="store_true", help="Print machine-readable provider status")
    provider_doctor_cmd.set_defaults(func=command_provider_doctor)

    hermes = subparsers.add_parser("hermes", help="Bridge Hermes Agent with Hipson workflows")
    hermes_sub = hermes.add_subparsers(dest="hermes_command", required=True)
    hermes_doctor = hermes_sub.add_parser("doctor", help="Check Hermes/Hipson bridge readiness")
    hermes_doctor.add_argument("--json", action="store_true", help="Print machine-readable bridge status")
    hermes_doctor.set_defaults(func=command_hermes_doctor)
    hermes_install_skill = hermes_sub.add_parser("install-skill", help="Install the Hipson workflow skill into Hermes")
    hermes_install_skill.add_argument("--force", action="store_true", help="Overwrite an existing Hermes skill copy")
    hermes_install_skill.add_argument("--json", action="store_true", help="Print machine-readable install result")
    hermes_install_skill.set_defaults(func=command_hermes_install_skill)
    hermes_intake = hermes_sub.add_parser("intake", help="Route and record one Hermes-originated Hipson task")
    hermes_intake.add_argument("--task", required=True, help="Task description from Hermes or the user")
    hermes_intake.add_argument("--project", default=".", help="Target repository; defaults to current directory")
    hermes_intake.add_argument("--channel", default="cli", help="Origin channel, e.g. cli, telegram, discord")
    hermes_intake.add_argument("--actor", default="hermes", help="Origin actor or bot identity")
    hermes_intake.add_argument("--no-write", action="store_true", help="Do not append the event to the Hipson Hermes bus")
    hermes_intake.add_argument("-o", "--output", help="Write a Markdown intake packet to a file")
    hermes_intake.add_argument("--json", action="store_true", help="Print machine-readable intake event")
    hermes_intake.set_defaults(func=command_hermes_intake)
    hermes_events = hermes_sub.add_parser("events", help="Inspect Hermes bus events")
    hermes_events_sub = hermes_events.add_subparsers(dest="hermes_events_command", required=True)
    hermes_events_list = hermes_events_sub.add_parser("list", help="List recent Hermes bus events")
    hermes_events_list.add_argument("--limit", type=int, default=20)
    hermes_events_list.add_argument("--json", action="store_true", help="Print machine-readable events")
    hermes_events_list.set_defaults(func=command_hermes_events_list)
    hermes_events_show = hermes_events_sub.add_parser("show", help="Show one Hermes bus event")
    hermes_events_show.add_argument("event_id")
    hermes_events_show.add_argument("--json", action="store_true", help="Print machine-readable event")
    hermes_events_show.set_defaults(func=command_hermes_events_show)

    chat = subparsers.add_parser(
        "chat",
        help="Run local deterministic chat workflows; provider mode remains explicit",
    )
    chat.add_argument("-q", "--query", help="Run one non-interactive request")
    chat.add_argument("--session-db", help="SQLite session DB path; defaults to Hipson config runtime.sqlite")
    chat.add_argument("--session-id", help="Continue an existing runtime session")
    chat.add_argument(
        "--fake",
        "--offline",
        dest="fake",
        action="store_true",
        help="Use explicit fake/offline provider mode for tests and demos",
    )
    chat.add_argument("--fake-response", help="Deterministic fake provider response for --fake mode")
    chat.add_argument("--fake-tool-call", help="Explicit fake/offline demo tool call to execute through the runtime")
    chat.add_argument("--fake-tool-input", help="JSON object input for --fake-tool-call")
    chat.add_argument(
        "--provider",
        choices=["openai-compatible"],
        help="Use an explicit real provider adapter; omitted chat uses local deterministic router mode",
    )
    chat.add_argument("--provider-url", default=DEFAULT_BASE_URL, help="OpenAI-compatible provider base URL")
    chat.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV, help="Environment variable containing the provider API key")
    chat.add_argument("--model", default=DEFAULT_PROVIDER_MODEL, help="Provider model name")
    chat.add_argument("--provider-timeout", type=float, default=90.0, help="Provider request timeout in seconds")
    chat.add_argument(
        "--allow-local-provider-http",
        action="store_true",
        help="Allow http://localhost provider URLs for explicit local test endpoints only",
    )
    chat.set_defaults(func=command_chat)

    init = subparsers.add_parser("init", help="Create docs/hipson-progress.md in a project")
    init.add_argument("project", help="Project directory")
    init.add_argument("--force", action="store_true", help="Overwrite existing progress file")
    init.set_defaults(func=command_init)

    check = subparsers.add_parser("check-setup", help="Check local Hipson orchestrator setup")
    check.add_argument("--registry", default="repos.yaml", help="Repo registry YAML")
    check.add_argument("--require-global", action="store_true", help="Fail if global Codex kit is not installed")
    check.set_defaults(func=command_check_setup)

    skill = subparsers.add_parser("skill", help="Skill commands")
    skill_sub = skill.add_subparsers(dest="skill_command", required=True)
    skill_list = skill_sub.add_parser("list", help="List skill metadata")
    skill_list.add_argument("--root", default=str(PACKAGE_ROOT))
    skill_list.add_argument("--query", help="Filter skills by name, description, or path")
    skill_list.add_argument("--json", action="store_true", help="Print machine-readable skill metadata")
    skill_list.set_defaults(func=command_skill_list)
    skill_view = skill_sub.add_parser("view", help="View one bounded skill as reference data")
    skill_view.add_argument("name", help="Skill name")
    skill_view.add_argument("--root", default=str(PACKAGE_ROOT))
    skill_view.add_argument("--max-chars", type=int, default=4000)
    skill_view.add_argument("--json", action="store_true", help="Print machine-readable skill content")
    skill_view.set_defaults(func=command_skill_view)
    skill_use = skill_sub.add_parser("use", help="Prepare one bounded skill reference payload")
    skill_use.add_argument("name", help="Skill name")
    skill_use.add_argument("--root", default=str(PACKAGE_ROOT))
    skill_use.add_argument("--max-chars", type=int, default=4000)
    skill_use.add_argument("--json", action="store_true", help="Print machine-readable runtime reference payload")
    skill_use.set_defaults(func=command_skill_use)
    validate = skill_sub.add_parser("validate", help="Validate Codex SKILL.md files")
    validate.add_argument("--root", default=str(PACKAGE_ROOT))
    validate.set_defaults(func=command_skill_validate)

    install = subparsers.add_parser("install", help="Install integrations")
    install_sub = install.add_subparsers(dest="install_command", required=True)
    codex = install_sub.add_parser("codex", help="Install Hipson into Codex")
    mode = codex.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    codex.set_defaults(func=command_install_codex)

    packet = subparsers.add_parser("packet", help="Generate bounded agent packets")
    packet_sub = packet.add_subparsers(dest="packet_command", required=True)
    preflight = packet_sub.add_parser("preflight", help="Check a packet before sidecar/provider use")
    preflight.add_argument("path", help="Packet path")
    preflight.add_argument("--max-chars", type=int, default=packet_preflight.MAX_PACKET_CHARS)
    preflight.add_argument("-o", "--output", help="Write preflight JSON artifact")
    preflight.add_argument(
        "--allow-unsafe-output",
        action="store_true",
        help="Explicitly allow preflight output outside generated artifact directories",
    )
    preflight.add_argument("--json", action="store_true", help="Print machine-readable preflight result")
    preflight.set_defaults(func=command_packet_preflight)
    review = packet_sub.add_parser("review", help="Generate a read-only review packet")
    review.add_argument("project", help="Project directory")
    review.add_argument("--title", required=True, help="Review title")
    review.add_argument("--scope", default="current git delta", help="Review scope")
    review.add_argument("--include-diff", action="store_true", help="Embed staged and unstaged diff bodies")
    review.add_argument("--skills", help="Comma-separated skills or references to include in the packet contract")
    review.add_argument("-o", "--output", help="Write markdown to a file")
    review.set_defaults(func=command_packet_review)

    executor = packet_sub.add_parser("exec", help="Generate an implementation packet")
    executor.add_argument("project", help="Project directory")
    executor.add_argument("--title", required=True, help="Task title")
    executor.add_argument("--goal", required=True, help="Concrete implementation goal")
    executor.add_argument("--scope", default="next bounded task", help="Task scope")
    executor.add_argument("--inspect", help="Comma-separated files to inspect")
    executor.add_argument("--allowed-edit", help="Comma-separated files or directories allowed for edits")
    executor.add_argument("--acceptance", default="[fill in observable success condition]", help="Acceptance criterion")
    executor.add_argument("--verification", help="Exact verification command")
    executor.add_argument("--skills", help="Comma-separated skills or references to include in the packet contract")
    executor.add_argument("-o", "--output", help="Write markdown to a file")
    executor.set_defaults(func=command_packet_exec)

    sidecar = subparsers.add_parser("sidecar", help="Run optional API sidecar agents")
    sidecar.add_argument("--config", default=str(agents.DEFAULT_CONFIG), help="Agent config JSON")
    sidecar.add_argument("--env", help="Provider env file")
    sidecar_sub = sidecar.add_subparsers(dest="sidecar_command", required=True)
    sidecar_list = sidecar_sub.add_parser("list", help="List configured sidecar agents")
    sidecar_list.set_defaults(func=command_sidecar_list)
    sidecar_route = sidecar_sub.add_parser("route", help="Suggest sidecar agents for a task")
    sidecar_route.add_argument("--task", required=True, help="Task description")
    sidecar_route.add_argument("--risk", default="normal", help="Risk hint, e.g. normal, high, security, architecture, ui")
    sidecar_route.add_argument("--context-chars", type=int, default=0, help="Estimated packet size")
    sidecar_route.add_argument("--sensitive", action="store_true", help="Whether the packet contains sensitive context")
    sidecar_route.add_argument("--file", action="append", help="Relevant file path for LLM routing summary; repeatable")
    sidecar_route.add_argument("--skills", help="Comma-separated skills for LLM routing summary")
    sidecar_route.add_argument("--task-type", default="review", help="Task type for LLM routing summary")
    sidecar_route.add_argument("--llm", action="store_true", help="Use optional provider-backed router on redacted summary")
    sidecar_route.add_argument("--llm-dry-run", action="store_true", help="Print LLM router summary without calling provider")
    sidecar_route.add_argument("--limit", type=int, default=3)
    sidecar_route.set_defaults(func=agents.command_route)
    sidecar_run = sidecar_sub.add_parser("run", help="Run a sidecar agent on a packet")
    sidecar_run.add_argument("--agent", required=True, help="Agent name from config")
    sidecar_run.add_argument("--packet", required=True, help="Markdown packet path")
    sidecar_run.add_argument("--model", help="Override the configured OpenRouter model for this run")
    sidecar_run.add_argument("-o", "--output", help="Output report path")
    sidecar_run.add_argument("--dry-run", action="store_true", help="Print provider request without calling API")
    sidecar_run.add_argument("--max-packet-chars", type=int, default=agents.DEFAULT_MAX_PACKET_CHARS, help="Maximum packet characters sent to provider")
    sidecar_run.set_defaults(func=command_sidecar_run)

    quality_parser = subparsers.add_parser("quality", help="Correlate work, verification, and sidecar quality artifacts")
    quality_sub = quality_parser.add_subparsers(dest="quality_command", required=True)
    quality_report = quality_sub.add_parser("report", help="Build a local quality report")
    quality_report.add_argument("--work", required=True, help="Work plan JSON path")
    quality_report.add_argument("--verify", help="Verification JSON artifact path")
    quality_report.add_argument("--sidecar", help="Sidecar report path")
    quality_report.add_argument("--decision", default="pending", help="Human decision")
    quality_report.add_argument("-o", "--output", help="Write report JSON artifact")
    quality_report.add_argument(
        "--allow-unsafe-output",
        action="store_true",
        help="Explicitly allow quality report output outside generated artifact directories",
    )
    quality_report.add_argument("--json", action="store_true", help="Print machine-readable quality report")
    quality_report.set_defaults(func=command_quality_report)
    quality_eval = quality_sub.add_parser("eval", help="Evaluate a sidecar report against local repo evidence")
    quality_eval.add_argument("--project", default=".", help="Project directory")
    quality_eval.add_argument("--packet", help="Packet path used by the sidecar")
    quality_eval.add_argument("--sidecar", required=True, help="Sidecar report path")
    quality_eval.add_argument("--verify", help="Verification JSON artifact path")
    quality_eval.add_argument("-o", "--output", help="Write eval JSON artifact")
    quality_eval.add_argument(
        "--allow-work-artifact-packet",
        action="store_true",
        help="Explicitly allow a work-plan JSON artifact to be evaluated as packet text",
    )
    quality_eval.add_argument(
        "--allow-unsafe-output",
        action="store_true",
        help="Explicitly allow quality eval output outside generated artifact directories",
    )
    quality_eval.add_argument("--json", action="store_true", help="Print machine-readable eval result")
    quality_eval.set_defaults(func=command_quality_eval)

    memory_parser = subparsers.add_parser("memory", help="Manage local Hipson memory")
    memory_parser.add_argument("--memory-dir", help="Memory directory; defaults to repo-local memory/")
    memory_sub = memory_parser.add_subparsers(dest="memory_command", required=True)
    memory_add = memory_sub.add_parser("add", help="Add a durable memory note")
    memory_add.add_argument("--scope", default="global", help="Memory scope")
    memory_add.add_argument("--repo", default="", help="Repository path or name")
    memory_add.add_argument("--kind", default="note", help="Memory kind, e.g. decision, risk, handoff")
    memory_add.add_argument("--summary", required=True, help="Memory summary")
    memory_add.add_argument("--tags", help="Comma-separated tags")
    memory_add.add_argument("--source", action="append", help="Source path or reference; repeatable")
    memory_add.add_argument("--confidence", type=float, default=1.0)
    memory_add.set_defaults(func=command_memory, memory_func=memory.command_add)
    memory_search = memory_sub.add_parser("search", help="Search memory notes")
    memory_search.add_argument("query")
    memory_search.add_argument("--repo")
    memory_search.add_argument("--scope")
    memory_search.add_argument("--limit", type=int, default=5)
    memory_search.set_defaults(func=command_memory, memory_func=memory.command_search)
    memory_list = memory_sub.add_parser("list", help="List recent memory notes")
    memory_list.add_argument("--repo")
    memory_list.add_argument("--scope")
    memory_list.add_argument("--limit", type=int, default=20)
    memory_list.set_defaults(func=command_memory, memory_func=memory.command_list)

    session_parser = subparsers.add_parser("session", help="Inspect local runtime sessions")
    session_parser.add_argument("--session-db", help="SQLite session DB path; defaults to Hipson config runtime.sqlite")
    session_sub = session_parser.add_subparsers(dest="session_command", required=True)
    session_list = session_sub.add_parser("list", help="List local runtime sessions")
    session_list.add_argument(
        "--session-db",
        default=argparse.SUPPRESS,
        help="SQLite session DB path; defaults to Hipson config runtime.sqlite",
    )
    session_list.add_argument("--limit", type=int, default=20)
    session_list.add_argument("--json", action="store_true", help="Print machine-readable sessions")
    session_list.set_defaults(func=command_session_list)
    session_show = session_sub.add_parser("show", help="Show one redacted runtime session")
    session_show.add_argument("session_id")
    session_show.add_argument(
        "--session-db",
        default=argparse.SUPPRESS,
        help="SQLite session DB path; defaults to Hipson config runtime.sqlite",
    )
    session_show.add_argument("--message-limit", type=int, default=50)
    session_show.add_argument("--tool-limit", type=int, default=50)
    session_show.add_argument("--json", action="store_true", help="Print machine-readable session details")
    session_show.set_defaults(func=command_session_show)
    session_search = session_sub.add_parser("search", help="Search redacted runtime session messages")
    session_search.add_argument("query")
    session_search.add_argument(
        "--session-db",
        default=argparse.SUPPRESS,
        help="SQLite session DB path; defaults to Hipson config runtime.sqlite",
    )
    session_search.add_argument("--limit", type=int, default=20)
    session_search.add_argument("--json", action="store_true", help="Print machine-readable search results")
    session_search.set_defaults(func=command_session_search)

    tool_parser = subparsers.add_parser("tool", help="Inspect runtime tool registry metadata")
    tool_sub = tool_parser.add_subparsers(dest="tool_command", required=True)
    tool_list = tool_sub.add_parser("list", help="List registered runtime tools")
    tool_list.add_argument("--json", action="store_true", help="Print machine-readable tools")
    tool_list.set_defaults(func=command_tool_list)
    tool_show = tool_sub.add_parser("show", help="Show one registered runtime tool")
    tool_show.add_argument("name")
    tool_show.add_argument("--json", action="store_true", help="Print machine-readable tool metadata")
    tool_show.set_defaults(func=command_tool_show)
    tool_run = tool_sub.add_parser("run", help="Run one safe read-only runtime tool through policy checks")
    tool_run.add_argument("name")
    tool_run.add_argument("input", help="Tool input JSON object")
    tool_run.add_argument("--cwd", help="Workspace for path policy checks; defaults to current directory")
    tool_run.add_argument("--session-db", help="Persist an auditable tool call in this SQLite session DB")
    tool_run.add_argument("--session-id", help="Persist into an existing session; defaults to a new session when --session-db is used")
    tool_run.add_argument("--json", action="store_true", help="Print machine-readable tool result")
    tool_run.set_defaults(func=command_tool_run)

    learn_parser = subparsers.add_parser("learn", help="Propose and explicitly apply approval-gated runtime learning")
    learn_parser.add_argument("--session-db", help="SQLite session DB path; defaults to Hipson config runtime.sqlite")
    learn_sub = learn_parser.add_subparsers(dest="learn_command", required=True)
    learn_propose = learn_sub.add_parser("propose", help="Propose memory/skill candidates from a session")
    learn_propose.add_argument("--session-id", required=True)
    learn_propose.add_argument(
        "--session-db",
        default=argparse.SUPPRESS,
        help="SQLite session DB path; defaults to Hipson config runtime.sqlite",
    )
    learn_propose.add_argument("--max-summary-chars", type=int, default=500)
    learn_propose.add_argument("--json", action="store_true", help="Print machine-readable proposals")
    learn_propose.set_defaults(func=command_learn_propose)
    learn_apply = learn_sub.add_parser("apply-memory", help="Explicitly persist one approved memory proposal")
    learn_apply.add_argument("--session-id", required=True)
    learn_apply.add_argument("--proposal-id", required=True)
    learn_apply.add_argument("--memory-dir", required=True)
    learn_apply.add_argument(
        "--session-db",
        default=argparse.SUPPRESS,
        help="SQLite session DB path; defaults to Hipson config runtime.sqlite",
    )
    learn_apply.add_argument("--json", action="store_true", help="Print machine-readable apply result")
    learn_apply.set_defaults(func=command_learn_apply_memory)

    scheduler_parser = subparsers.add_parser("scheduler", help="Manage opt-in local scheduler jobs")
    scheduler_parser.add_argument("--session-db", help="SQLite session DB path; defaults to Hipson config runtime.sqlite")
    scheduler_sub = scheduler_parser.add_subparsers(dest="scheduler_command", required=True)
    scheduler_create = scheduler_sub.add_parser("create", help="Create a due tool job")
    scheduler_create.add_argument("--tool", required=True, help="Registered tool name")
    scheduler_create.add_argument("--input", required=True, help="Tool input JSON object")
    scheduler_create.add_argument("--run-after", help="UTC timestamp; defaults to due now")
    scheduler_create.add_argument("--approved", action="store_true", help="Mark non-read job as explicitly approved")
    scheduler_create.set_defaults(func=command_scheduler_create)
    scheduler_list = scheduler_sub.add_parser("list", help="List scheduler jobs")
    scheduler_list.add_argument("--status", help="Filter by status")
    scheduler_list.add_argument("--limit", type=int, default=20)
    scheduler_list.add_argument("--json", action="store_true", help="Print machine-readable jobs")
    scheduler_list.set_defaults(func=command_scheduler_list)
    scheduler_tick = scheduler_sub.add_parser("tick", help="Run due scheduler jobs once")
    scheduler_tick.add_argument("--now", help="Override current UTC timestamp for tests")
    scheduler_tick.add_argument("--limit", type=int, default=20)
    scheduler_tick.add_argument("--json", action="store_true", help="Print machine-readable tick results")
    scheduler_tick.set_defaults(func=command_scheduler_tick)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
