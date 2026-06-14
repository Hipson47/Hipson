"""AI Review Control Kit orchestration."""

from __future__ import annotations

import json
import shlex
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from hipson import (
    agent_contract,
    agents,
    contracts,
    evals,
    evidence,
    output_policy,
    packet_preflight,
    quality,
    run_control,
    workflow,
)
from hipson import verification as verification_mod
from hipson.project import resolve_project

VERIFY_PROFILES = ("quick", "full", "release")
RERUN_STEPS = (
    "contract",
    "preflight",
    "verify",
    "quality",
    "quality_eval",
    "evidence",
    "audit",
    "summary",
    "handoff",
    "manifest",
)


def run_review_kit(
    *,
    task: str = "review current diff",
    project_path: str | Path = ".",
    run_root: str | Path = "runs",
    include_diff: bool = True,
    diff_lines: int = 120,
    inspect: str | None = None,
    allowed_edit: str | None = None,
    acceptance: str | None = None,
    verification: str | None = None,
    skills: str | None = None,
    verify_profile: str = "quick",
    verify_limit: int | None = None,
    timeout: int = verification_mod.DEFAULT_TIMEOUT,
    ai_profile: str | None = None,
    run_sidecar: bool = False,
    sidecar_config: str | None = None,
    sidecar_env: str | None = None,
    decision: str = "pending",
    allow_unsafe_output: bool = False,
    workflow_name: str = "review_kit",
) -> dict[str, Any]:
    """Run the local-first AI Review Control Kit workflow."""

    project = resolve_project(str(project_path))
    _validate_verify_profile(verify_profile)
    if run_sidecar and not ai_profile:
        raise SystemExit("--run-sidecar requires --ai-profile so the agent/model choice is explicit")

    work_id = contracts.new_id("work")
    root = output_policy.resolve_output_path(
        run_root,
        cwd=project,
        allow_unsafe=allow_unsafe_output,
        description="review kit run root",
    )
    run_dir = root / work_id
    run_dir.mkdir(parents=True, exist_ok=False)

    paths = _run_paths(run_dir)
    contract = agent_contract.build_agent_contract(project)
    _write_json(paths["contract"], contract)

    plan = workflow.build_work_plan(
        task=task,
        project_path=str(project),
        include_diff=include_diff,
        diff_lines=diff_lines,
        write_packet=True,
        packet_output=str(paths["packet"]),
        inspect=inspect,
        allowed_edit=allowed_edit,
        acceptance=acceptance,
        verification=verification,
        skills=skills,
        ai_profile=ai_profile,
        allow_unsafe_output=allow_unsafe_output,
        work_id=work_id,
    )
    plan_payload = cast(dict[str, Any], plan)
    _sync_run_preflight_plan(plan_payload, packet_path=paths["packet"], preflight_path=paths["preflight"])
    workflow.write_work_plan(plan, str(paths["work"]), cwd=project, allow_unsafe_output=allow_unsafe_output)

    preflight = packet_preflight.preflight_packet(paths["packet"])
    packet_preflight.write_preflight(
        preflight,
        paths["preflight"],
        cwd=project,
        allow_unsafe_output=allow_unsafe_output,
    )

    sidecar_status = _sidecar_status(plan_payload, run_sidecar=run_sidecar)
    sidecar_path: Path | None = None
    if run_sidecar:
        sidecar_path = _run_sidecar(
            plan=plan_payload,
            packet_path=paths["packet"],
            output=paths["sidecar"],
            config=sidecar_config,
            env=sidecar_env,
        )
        sidecar_status["status"] = "ran"
        sidecar_status["output"] = str(sidecar_path)

    commands = _verification_commands_for_profile(
        plan_payload,
        verify_profile=verify_profile,
        verify_limit=verify_limit,
    )
    verification_result = verification_mod.run_verification(work_plan=plan_payload, commands=commands, timeout=timeout)
    verification_mod.write_verification_artifact(
        verification_result,
        paths["verification"],
        cwd=project,
        allow_unsafe_output=allow_unsafe_output,
    )

    quality_report = quality.build_quality_report(
        work_path=paths["work"],
        verification_path=paths["verification"],
        sidecar_path=sidecar_path,
        decision=decision,
    )
    quality.write_report(quality_report, paths["quality"], cwd=project, allow_unsafe_output=allow_unsafe_output)

    quality_eval: dict[str, Any] | None = None
    if sidecar_path is not None:
        quality_eval = evals.run_quality_eval(
            project_path=project,
            packet_path=paths["packet"],
            sidecar_path=sidecar_path,
            verification_path=paths["verification"],
        )
        evals.write_eval(quality_eval, paths["quality_eval"], cwd=project, allow_unsafe_output=allow_unsafe_output)

    record = evidence.build_evidence_record(
        work_plan=plan_payload,
        verification=verification_result,
        quality_report=quality_report,
        quality_eval=quality_eval,
        sidecar_report=str(sidecar_path) if sidecar_path else "",
        ledger_root=run_dir,
        human_decision=decision,
    )
    ledger = evidence.append_record(run_dir, record)
    audit = evidence.audit_bundle(work_path=str(paths["work"]), ledger_root=run_dir)
    _write_json(paths["audit"], audit)

    summary = render_summary(
        plan=plan_payload,
        verification=verification_result,
        quality_report=quality_report,
        quality_eval=quality_eval,
        evidence_record=record,
        audit=audit,
        sidecar_status=sidecar_status,
        verify_profile=verify_profile,
    )
    paths["summary"].write_text(summary, encoding="utf-8")
    handoff = run_control.build_handoff(run_dir)
    handoff_paths = run_control.write_handoff(run_dir, handoff)
    manifest = run_control.build_manifest(
        run_dir,
        workflow=workflow_name,
        mode="run",
        verify_profile=verify_profile,
        status="passed" if quality_report.get("ok") else "blocked",
        sidecar=sidecar_status,
        gates=cast(dict[str, Any], audit.get("latest_gates", {})),
    )
    manifest_path = run_control.write_manifest(run_dir, manifest)

    return {
        "schema_version": contracts.SCHEMA_VERSION,
        "artifact_kind": "hipson.review_kit_run",
        "work_id": work_id,
        "run_dir": str(run_dir),
        "mode": "run",
        "verify_profile": verify_profile,
        "status": "passed" if quality_report.get("ok") else "blocked",
        "sidecar": sidecar_status,
        "gates": audit.get("latest_gates", {}),
        "artifacts": {
            "contract": str(paths["contract"]),
            "work": str(paths["work"]),
            "packet": str(paths["packet"]),
            "preflight": str(paths["preflight"]),
            "verification": str(paths["verification"]),
            "quality": str(paths["quality"]),
            "quality_eval": str(paths["quality_eval"]) if quality_eval else "",
            "evidence": str(ledger),
            "audit": str(paths["audit"]),
            "summary": str(paths["summary"]),
            "handoff": handoff_paths["markdown"],
            "handoff_json": handoff_paths["json"],
            "manifest": str(manifest_path),
        },
    }


def resume_review_kit(
    *,
    run_path: str | Path,
    verify_profile: str = "quick",
    verify_limit: int | None = None,
    timeout: int = verification_mod.DEFAULT_TIMEOUT,
    run_sidecar: bool = False,
    sidecar_config: str | None = None,
    sidecar_env: str | None = None,
    decision: str = "pending",
    allow_unsafe_output: bool = False,
    rerun_steps: Sequence[str] = (),
    workflow_name: str = "review_kit",
) -> dict[str, Any]:
    """Resume a review kit run by filling missing artifacts only."""

    _validate_verify_profile(verify_profile)
    rerun = _validate_rerun_steps(rerun_steps)
    run_dir = Path(run_path).expanduser()
    if not run_dir.is_absolute():
        run_dir = Path.cwd() / run_dir
    run_dir = run_dir.resolve()
    if not run_dir.exists() or not run_dir.is_dir():
        raise SystemExit(f"Review kit run directory does not exist: {run_dir}")

    paths = _run_paths(run_dir)
    if not paths["work"].exists():
        raise SystemExit("Cannot resume review kit run without work.json; rerun `hipson kit review`.")
    if not paths["packet"].exists():
        raise SystemExit("Cannot resume review kit run without review-packet.md; rerun `hipson kit review`.")

    plan_payload = _load_json(paths["work"])
    project = resolve_project(str(plan_payload.get("project", ".")))
    output_policy.resolve_output_path(
        run_dir,
        cwd=project,
        allow_unsafe=allow_unsafe_output,
        description="review kit resume run",
    )

    created: list[str] = []
    updated: list[str] = []

    if "contract" in rerun or not paths["contract"].exists():
        contract_exists = paths["contract"].exists()
        _write_json(paths["contract"], agent_contract.build_agent_contract(project))
        (updated if contract_exists else created).append(paths["contract"].name)

    if "preflight" in rerun or not paths["preflight"].exists():
        preflight_exists = paths["preflight"].exists()
        preflight = packet_preflight.preflight_packet(paths["packet"])
        packet_preflight.write_preflight(
            preflight,
            paths["preflight"],
            cwd=project,
            allow_unsafe_output=allow_unsafe_output,
        )
        (updated if preflight_exists else created).append(paths["preflight"].name)

    sidecar_status = _sidecar_status(plan_payload, run_sidecar=run_sidecar)
    sidecar_path = paths["sidecar"] if paths["sidecar"].exists() else None
    if sidecar_path is not None:
        sidecar_status["status"] = "existing"
        sidecar_status["output"] = str(sidecar_path)
    elif run_sidecar:
        sidecar_path = _run_sidecar(
            plan=plan_payload,
            packet_path=paths["packet"],
            output=paths["sidecar"],
            config=sidecar_config,
            env=sidecar_env,
        )
        sidecar_status["status"] = "ran"
        sidecar_status["output"] = str(sidecar_path)
        created.append(paths["sidecar"].name)
    sidecar_created = paths["sidecar"].name in created

    verification_changed = False
    if paths["verification"].exists() and "verify" not in rerun:
        verification = _load_json(paths["verification"])
    else:
        verification_exists = paths["verification"].exists()
        commands = _verification_commands_for_profile(
            plan_payload,
            verify_profile=verify_profile,
            verify_limit=verify_limit,
        )
        verification = verification_mod.run_verification(work_plan=plan_payload, commands=commands, timeout=timeout)
        verification_mod.write_verification_artifact(
            verification,
            paths["verification"],
            cwd=project,
            allow_unsafe_output=allow_unsafe_output,
        )
        (updated if verification_exists else created).append(paths["verification"].name)
        verification_changed = True

    quality_exists = paths["quality"].exists()
    quality_changed = False
    if "quality" in rerun or not quality_exists or verification_changed or sidecar_created:
        quality_report = quality.build_quality_report(
            work_path=paths["work"],
            verification_path=paths["verification"],
            sidecar_path=sidecar_path,
            decision=decision,
        )
        quality.write_report(
            quality_report,
            paths["quality"],
            cwd=project,
            allow_unsafe_output=allow_unsafe_output,
        )
        (updated if quality_exists else created).append(paths["quality"].name)
        quality_changed = True
    else:
        quality_report = _load_json(paths["quality"])

    quality_eval_exists = paths["quality_eval"].exists()
    quality_eval_changed = False
    quality_eval: dict[str, Any] | None
    if sidecar_path is not None and (
        "quality_eval" in rerun or not quality_eval_exists or verification_changed or sidecar_created
    ):
        quality_eval = evals.run_quality_eval(
            project_path=project,
            packet_path=paths["packet"],
            sidecar_path=sidecar_path,
            verification_path=paths["verification"],
        )
        evals.write_eval(
            quality_eval,
            paths["quality_eval"],
            cwd=project,
            allow_unsafe_output=allow_unsafe_output,
        )
        (updated if quality_eval_exists else created).append(paths["quality_eval"].name)
        quality_eval_changed = True
    elif quality_eval_exists:
        quality_eval = _load_json(paths["quality_eval"])
    else:
        quality_eval = None

    records = [
        record
        for record in evidence.read_records(run_dir)
        if str(record.get("work", {}).get("work_id", "")) == str(plan_payload.get("work_id", ""))
    ]
    evidence_changed = False
    if "evidence" in rerun or not records or verification_changed or quality_changed or quality_eval_changed or sidecar_created:
        record = evidence.build_evidence_record(
            work_plan=plan_payload,
            verification=verification,
            quality_report=quality_report,
            quality_eval=quality_eval,
            sidecar_report=str(sidecar_path) if sidecar_path else "",
            ledger_root=run_dir,
            human_decision=decision,
        )
        ledger = evidence.append_record(run_dir, record)
        created.append(ledger.name if not records else f"{ledger.name}:append")
        evidence_changed = True
    else:
        record = records[-1]
        ledger = evidence.ledger_path(run_dir)

    audit_exists = paths["audit"].exists()
    if "audit" in rerun or not audit_exists or evidence_changed:
        audit = evidence.audit_bundle(work_path=str(paths["work"]), ledger_root=run_dir)
        _write_json(paths["audit"], audit)
        (updated if audit_exists else created).append(paths["audit"].name)
    else:
        audit = _load_json(paths["audit"])

    summary_exists = paths["summary"].exists()
    summary_changed = False
    if "summary" in rerun or not summary_exists or evidence_changed:
        summary = render_summary(
            plan=plan_payload,
            verification=verification,
            quality_report=quality_report,
            quality_eval=quality_eval,
            evidence_record=record,
            audit=audit,
            sidecar_status=sidecar_status,
            verify_profile=verify_profile,
        )
        paths["summary"].write_text(summary, encoding="utf-8")
        (updated if summary_exists else created).append(paths["summary"].name)
        summary_changed = True

    handoff_json_exists = paths["handoff_json"].exists()
    handoff_exists = paths["handoff"].exists()
    if "handoff" in rerun or not handoff_json_exists or not handoff_exists or summary_changed or evidence_changed:
        handoff = run_control.build_handoff(run_dir)
        handoff_paths = run_control.write_handoff(run_dir, handoff)
        if handoff_json_exists:
            updated.append(Path(handoff_paths["json"]).name)
        else:
            created.append(Path(handoff_paths["json"]).name)
        if handoff_exists:
            updated.append(Path(handoff_paths["markdown"]).name)
        else:
            created.append(Path(handoff_paths["markdown"]).name)
    else:
        handoff_paths = {"json": str(paths["handoff_json"]), "markdown": str(paths["handoff"])}

    manifest_exists = paths["manifest"].exists()
    if "manifest" in rerun or not manifest_exists or created or updated:
        manifest = run_control.build_manifest(
            run_dir,
            workflow=workflow_name,
            mode="resume",
            verify_profile=verify_profile,
            status="passed" if quality_report.get("ok") else "blocked",
            sidecar=sidecar_status,
            gates=cast(dict[str, Any], audit.get("latest_gates", {})),
            created_artifacts=created,
            updated_artifacts=updated,
            rerun_steps=sorted(rerun),
        )
        manifest_path = run_control.write_manifest(run_dir, manifest)
        (updated if manifest_exists else created).append(manifest_path.name)
    else:
        manifest_path = paths["manifest"]

    return {
        "schema_version": contracts.SCHEMA_VERSION,
        "artifact_kind": "hipson.review_kit_run",
        "work_id": str(plan_payload.get("work_id", "")),
        "run_dir": str(run_dir),
        "mode": "resume",
        "verify_profile": verify_profile,
        "status": "passed" if quality_report.get("ok") else "blocked",
        "created_artifacts": created,
        "updated_artifacts": updated,
        "rerun_steps": sorted(rerun),
        "sidecar": sidecar_status,
        "gates": audit.get("latest_gates", {}),
        "artifacts": {
            "contract": str(paths["contract"]),
            "work": str(paths["work"]),
            "packet": str(paths["packet"]),
            "preflight": str(paths["preflight"]),
            "verification": str(paths["verification"]),
            "quality": str(paths["quality"]),
            "quality_eval": str(paths["quality_eval"]) if quality_eval else "",
            "evidence": str(ledger),
            "audit": str(paths["audit"]),
            "summary": str(paths["summary"]),
            "handoff": handoff_paths["markdown"],
            "handoff_json": handoff_paths["json"],
            "manifest": str(manifest_path),
        },
    }


def render_summary(
    *,
    plan: dict[str, Any],
    verification: dict[str, Any],
    quality_report: dict[str, Any],
    quality_eval: dict[str, Any] | None,
    evidence_record: dict[str, Any],
    audit: dict[str, Any],
    sidecar_status: dict[str, str],
    verify_profile: str = "quick",
) -> str:
    claims = evidence_record.get("claims", {})
    gates = audit.get("latest_gates", {})
    lines = [
        "# AI Review Control Kit Summary",
        "",
        "## What Was Reviewed",
        f"- Work ID: `{plan.get('work_id', '')}`",
        f"- Task: {plan.get('task', '')}",
        f"- Project: `{plan.get('project', '')}`",
        f"- Packet: `{plan.get('packet', {}).get('path', '')}`",
        "",
        "## What Changed",
        *_bullet_code(plan.get("changed_files", []), "No changed files were detected."),
        "",
        "## Commands Ran",
        f"- Verification profile: `{verify_profile}`",
        *_verification_lines(verification),
        f"- Sidecar: {sidecar_status.get('status', 'not_configured')}",
        "",
        "## Gates",
        *_gate_lines(gates),
        "",
        "## Safe To Claim",
        *_bullet_text(claims.get("safe", []), "No safety claims are supported yet."),
        "",
        "## Unknowns",
        *_bullet_text(evidence_record.get("unknowns", []), "No unknowns recorded."),
        "",
        "## Quality Eval",
        *_quality_eval_lines(quality_eval),
        "",
        "## Next Agent Step",
        f"- {_next_agent_step(gates, evidence_record)}",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _run_paths(run_dir: Path) -> dict[str, Path]:
    return {
        "contract": run_dir / "contract.json",
        "work": run_dir / "work.json",
        "packet": run_dir / "review-packet.md",
        "preflight": run_dir / "preflight.json",
        "verification": run_dir / "verify.json",
        "quality": run_dir / "quality.json",
        "quality_eval": run_dir / "quality-eval.json",
        "sidecar": run_dir / "sidecar.md",
        "audit": run_dir / "audit.json",
        "summary": run_dir / "summary.md",
        "manifest": run_dir / "manifest.json",
        "handoff": run_dir / "handoff.md",
        "handoff_json": run_dir / "handoff.json",
    }


def _sidecar_status(plan: dict[str, Any], *, run_sidecar: bool) -> dict[str, str]:
    ai_quality = plan.get("ai_quality", {})
    if not isinstance(ai_quality, dict) or not ai_quality.get("enabled"):
        return {"status": "not_configured", "dry_run_command": "", "run_command": ""}
    return {
        "status": "run_requested" if run_sidecar else "dry_run_prepared",
        "dry_run_command": str(ai_quality.get("dry_run_command", "")),
        "run_command": str(ai_quality.get("run_command", "")),
    }


def _run_sidecar(
    *,
    plan: dict[str, Any],
    packet_path: Path,
    output: Path,
    config: str | None,
    env: str | None,
) -> Path:
    ai_quality = plan.get("ai_quality", {})
    if not isinstance(ai_quality, dict) or not ai_quality.get("enabled"):
        raise SystemExit("--run-sidecar requires an enabled AI profile")
    agent_name = str(ai_quality.get("agent", ""))
    model = str(ai_quality.get("model", ""))
    if not agent_name:
        raise SystemExit("AI profile did not resolve a sidecar agent")

    agents.load_provider_envs(env)
    config_path = Path(config).expanduser().resolve() if config else agents.DEFAULT_CONFIG
    config_payload = agents.load_json(config_path)
    agent = dict(agents.agent_config(config_payload, agent_name))
    if model:
        agent["model"] = agents.normalize_model_override(model) or model
    provider = agents.provider_config(config_payload, agent)
    packet = agents.read_packet(str(packet_path), agents.DEFAULT_MAX_PACKET_CHARS)
    response = agents.openrouter_chat(provider, agent, packet)
    content = agents.extract_content(response)
    return agents.write_report(agent_name, str(agent["model"]), str(packet_path), content, str(output))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _sync_run_preflight_plan(plan: dict[str, Any], *, packet_path: Path, preflight_path: Path) -> None:
    plan["packet_preflight"] = {
        "command": shlex.join(["hipson", "packet", "preflight", str(packet_path), "-o", str(preflight_path), "--json"]),
        "output": str(preflight_path),
        "required_before_sidecar": True,
        "reason": "Local packet safety gate before any provider-backed sidecar call.",
    }


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"JSON artifact must be an object: {path}")
    return data


def _validate_verify_profile(profile: str) -> None:
    if profile not in VERIFY_PROFILES:
        values = ", ".join(VERIFY_PROFILES)
        raise SystemExit(f"--verify-profile must be one of: {values}")


def _validate_rerun_steps(steps: Sequence[str]) -> set[str]:
    values = {step for step in steps if step}
    invalid = sorted(values - set(RERUN_STEPS))
    if invalid:
        raise SystemExit(f"--rerun-step must be one of: {', '.join(RERUN_STEPS)}")
    return values


def _verification_commands_for_profile(
    work_plan: dict[str, Any],
    *,
    verify_profile: str,
    verify_limit: int | None,
) -> list[str]:
    _validate_verify_profile(verify_profile)
    if verify_limit is not None:
        return verification_mod.verification_commands(work_plan, limit=verify_limit)
    limit = 1 if verify_profile == "quick" else None
    return verification_mod.verification_commands(work_plan, limit=limit)


def _verification_lines(verification: dict[str, Any]) -> list[str]:
    results = verification.get("results", [])
    if not isinstance(results, list) or not results:
        return ["- No verification commands ran."]
    return [
        f"- `{item.get('command', '')}`: {item.get('status', '')} ({item.get('exit_code', '')})"
        for item in results
        if isinstance(item, dict)
    ]


def _gate_lines(gates: object) -> list[str]:
    if not isinstance(gates, dict) or not gates:
        return ["- No gates recorded."]
    return [f"- `{key}`: `{value}`" for key, value in sorted(gates.items())]


def _quality_eval_lines(quality_eval: dict[str, Any] | None) -> list[str]:
    if not quality_eval:
        return ["- Not run; no sidecar report was attached."]
    return [
        f"- OK: `{str(quality_eval.get('ok')).lower()}`",
        f"- Score: `{quality_eval.get('score', 0)}`",
        f"- Issues: `{len(quality_eval.get('issues', [])) if isinstance(quality_eval.get('issues'), list) else 0}`",
    ]


def _next_agent_step(gates: object, evidence_record: dict[str, Any]) -> str:
    gate_payload = gates if isinstance(gates, dict) else {}
    if gate_payload.get("release_claim_gate") == "passed":
        return "Review audit.json and summary.md, then make the final human release or handoff decision."
    unknowns = evidence_record.get("unknowns", [])
    if isinstance(unknowns, list) and unknowns:
        return f"Resolve the first unknown: {unknowns[0]}"
    return "Resolve blocked gates, rerun local verification, then append new evidence."


def _bullet_code(items: object, empty: str) -> list[str]:
    values = items if isinstance(items, list) else []
    if not values:
        return [f"- {empty}"]
    return [f"- `{item}`" for item in values]


def _bullet_text(items: object, empty: str) -> list[str]:
    values = items if isinstance(items, list) else []
    if not values:
        return [f"- {empty}"]
    return [f"- {item}" for item in values]
