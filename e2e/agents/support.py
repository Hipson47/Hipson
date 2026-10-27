"""Shared scenario assertions, using observed ledger data and parser behavior."""

import json
import subprocess
import sys


def parser_works(ctx, *, trimmed=False):
    source = ctx.state["files"].get("src/parser/pairs.py", "")
    # Execute fixture code in a bounded child, never import agent-edited code into the harness.
    check = ("import json,sys; ns={}; exec(sys.stdin.read(), ns); "
             "print(json.dumps([ns['parse_pair']('a:b'), ns['parse_pair'](' a : b ')]))")
    try:
        result = subprocess.run([sys.executable, "-I", "-c", check], input=source, capture_output=True,
                                text=True, timeout=2, check=True, cwd=ctx.workdir, env={})
        values = json.loads(result.stdout)
        return values[0] == ["a", "b"] and (not trimmed or values[1] == ["a", "b"])
    except (ValueError, OSError, subprocess.SubprocessError):
        return False


def event_seq(ctx, kind, ref):
    events = ctx.events(kind, ref)
    return events[0]["seq"] if events else -1


def passed_worker(item, role, mode):
    return (item["role"] == role and item["mode"] == mode and item["status"] == "passed"
            and item["owner_pid"] is not None and item["result"] is not None
            and item["result"]["status"] == "completed")


def assignment_observations(ctx, assignment):
    """Observe delivery preservation across snapshots, independent of read commands."""
    states = [ctx.initial, *[state for entry in ctx.audit for state in (entry['before'], entry['after'])], ctx.state]
    return [item for state in states for task in state['tasks'] for item in task['assignments']
            if item['id'] == assignment]


def covers_parser(item):
    return any(path in ('.', 'src', 'src/parser', 'src/parser/pairs.py') for path in item['scope'])
