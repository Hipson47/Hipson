"""Worker environment selection: inherit the lead's environment or pass only an allowlist (opt-in)."""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Mapping
from typing import Any

MODES = ("inherit", "minimal")
DEFAULT_MODE = "inherit"
MAX_ALLOW = 64

# What a worker CLI needs to run, find its file-based login and reach the network: login identity, locale,
# terminal, temporary directories, proxies, CA bundles, Windows/WSL essentials and agent CLI config homes.
# Credentials passed as variables (OPENAI_API_KEY, ANTHROPIC_API_KEY, GH_TOKEN, SSH_AUTH_SOCK, ...) are left
# out on purpose: each needs an explicit --env-allow.
BASELINE = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "PWD", "LANG", "LANGUAGE", "TZ", "TERM", "COLORTERM",
    "TMP", "TEMP", "TMPDIR", "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "VIRTUAL_ENV",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy", "all_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "GIT_SSL_CAINFO",
    "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
    "CODEX_HOME", "CLAUDE_CONFIG_DIR",
    "WSL_DISTRO_NAME", "WSL_INTEROP", "WSLENV",
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE", "USERNAME", "USERDOMAIN",
    "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "ALLUSERSPROFILE", "PUBLIC",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
    "COMMONPROGRAMW6432", "COMPUTERNAME", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
})
BASELINE_PREFIXES = ("LC_",)
ALWAYS_PREFIXES = ("HIPSON_",)
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_().]{0,127}")


def validate_names(names: Iterable[object], *, source: str = "--env-allow") -> list[str]:
    """Allowed names must be plain variable names; repository-selecting git variables can never be allowed."""
    from hipson.task_runner import git_redirecting_variable

    items = list(names)
    if len(items) > MAX_ALLOW:
        raise ValueError(f"{source} accepts at most {MAX_ALLOW} names")
    checked: list[str] = []
    for position, name in enumerate(items, 1):
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            # Never echo the rejected text: a mistaken "$TOKEN" would put the credential itself in the message.
            raise ValueError(f"{source} expects variable names such as OPENAI_API_KEY; entry {position} is not one")
        if git_redirecting_variable(name):
            raise ValueError(f"{source} entry {position} cannot be passed: git variables that select another "
                             "repository or inject configuration (GIT_DIR, GIT_CONFIG_*, ...) are never allowed")
        if name not in checked:
            checked.append(name)
    return checked


def _fold(name: str, platform: str) -> str:
    # Windows variable names are case-insensitive; POSIX names are not (http_proxy and HTTP_PROXY differ).
    return name.upper() if platform == "win32" else name


def select(source: Mapping[str, str], mode: str, extra: Iterable[str] = (), *,
           platform: str = sys.platform) -> tuple[dict[str, str], dict[str, Any]]:
    """Return the worker environment and its ledger record, which holds names and counts, never values."""
    if mode not in MODES:
        raise ValueError(f"worker environment must be one of {', '.join(MODES)}")
    names = list(extra)
    if mode == "inherit":
        return dict(source), {"mode": "inherit", "count": len(source)}
    permitted = {_fold(item, platform) for item in (*BASELINE, *names)}

    def passes(key: str) -> bool:
        # Prefixes are matched in upper case: HIPSON_* and LC_* are conventionally upper-case everywhere.
        return key.upper().startswith((*ALWAYS_PREFIXES, *BASELINE_PREFIXES)) or _fold(key, platform) in permitted

    chosen = {key: value for key, value in source.items() if passes(key)}
    present = {_fold(key, platform) for key in chosen}
    return chosen, {
        "mode": "minimal",
        "names": sorted(chosen),
        "allowed": names,
        "missing": [name for name in names if _fold(name, platform) not in present],
        "dropped": len(source) - len(chosen),
    }


POLICY_KEYS = ("worker_env", "worker_env_allow")


def _normal(key: object) -> str:
    # Separators of any kind (spaces, tabs, dashes, quotes, underscores, none at all) do not matter: "worker env",
    # "Worker-Env" and "workerenv" all read as worker_env.
    compact = re.sub(r"[\W_]+", "", str(key).lower())
    return {"workerenv": "worker_env", "workerenvallow": "worker_env_allow"}.get(compact, compact)


def misspelled_keys(policy: Mapping[str, Any]) -> list[str]:
    """Keys that look like worker_env settings but are not spelled exactly: never ignore them silently."""
    return sorted(str(key) for key in policy if _normal(key) in POLICY_KEYS and key not in POLICY_KEYS)


def resolve(args: object, policy_payload: Mapping[str, Any]) -> tuple[str, list[str]]:
    """CLI options win over project policy; allowed names add up. A policy asking for minimal must be valid."""
    raw = policy_payload.get("policy")
    policy: dict[str, Any] = raw if isinstance(raw, dict) else {}
    valid = bool(policy_payload.get("valid"))
    if not valid and any(_normal(key) in POLICY_KEYS for key in policy) and getattr(args, "env", None) is None:
        issues = "; ".join(str(issue) for issue in policy_payload.get("issues", []))
        # Never fall back to the full environment when the policy that asked to narrow it cannot be used; an
        # explicit --env decides on its own (an invalid policy then contributes no allowed names).
        raise ValueError(f"project policy sets worker_env but is invalid: {issues}; fix it or pass --env explicitly")
    usable = policy if valid else {}
    mode = getattr(args, "env", None) or usable.get("worker_env") or DEFAULT_MODE
    requested = validate_names(getattr(args, "env_allow", None) or [])
    if mode != "minimal":
        if requested:
            raise ValueError("--env-allow needs the minimal worker environment (--env minimal)")
        return mode, []  # An explicit --env inherit overrides the policy, including its allowed names.
    return mode, validate_names([*usable.get("worker_env_allow", []), *requested], source="worker_env_allow")


def summary(record: Mapping[str, Any] | None) -> str:
    """One line for task show and lead context."""
    if not record:
        return "not recorded"
    if record.get("mode") == "inherit":
        return f"inherit ({record.get('count', 0)} variables)"
    text = f"minimal ({len(record.get('names', []))} passed, {record.get('dropped', 0)} withheld)"
    if record.get("missing"):
        text += "; allowed but unset: " + ", ".join(record["missing"])
    return text
