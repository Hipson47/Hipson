"""Negative controls for the artifact contract validator."""

import json

from tests.json_schema import check_schema, schema_errors
from tests.test_hipson_helpers import SCHEMA_ROOT, assert_schema_valid


def test_all_artifact_schema_keywords_are_supported():
    for path in sorted(SCHEMA_ROOT.glob("*.schema.json")):
        check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_task_result_schema_rejects_malformed_nested_payloads():
    valid = {
        "status": "completed", "summary": "Verified", "files_changed": [{"path": "a.py", "change": "Fixed"}],
        "commands": [{"command": "check", "exit_code": None}],
        "findings": [{"severity": "major", "title": "Fix this", "detail": "Evidence", "location": None}],
        "risks": ["Known limitation"],
    }
    assert_schema_valid("task-result.schema.json", valid)
    schema = json.loads((SCHEMA_ROOT / "task-result.schema.json").read_text(encoding="utf-8"))
    for field, value in (
        ("files_changed", [42]), ("files_changed", [{"path": "a.py"}]),
        ("files_changed", [{"path": "a.py", "change": "fixed", "extra": True}]),
        ("commands", [None]), ("commands", [{"command": "check", "exit_code": True}]),
        ("commands", [{"command": "check", "exit_code": "0"}]),
        ("findings", [{**valid["findings"][0], "severity": "critical"}]),
        ("findings", [{**valid["findings"][0], "title": " \n\t"}]),
        ("findings", [{**valid["findings"][0], "location": 2}]),
        ("risks", [1]), ("extra", "forbidden"), ("status", "done"),
    ):
        assert schema_errors(schema, {**valid, field: value}), (field, value)
    for field in valid:
        assert schema_errors(schema, {key: value for key, value in valid.items() if key != field}), field
    # The audit probe must produce every relevant nested error, not just the extra-key error.
    probe = {**valid, "files_changed": [42], "commands": [None],
             "findings": [{**valid["findings"][0], "severity": "invalid"}], "risks": [3], "extra": True}
    errors = schema_errors(schema, probe)
    for expected in ("$.files_changed[0]: type", "$.commands[0]: type", "$.findings[0].severity: enum",
                     "$.risks[0]: type", "$.extra: additional"):
        assert expected in errors, errors
    try:
        assert_schema_valid("task-result.schema.json", probe)
    except AssertionError as exc:
        assert "$.files_changed[0]: type" in str(exc), str(exc)
    else:
        raise AssertionError("The shared artifact assertion accepted the audit's malformed probe")


def test_schema_validator_checks_refs_unions_bounds_and_uniqueness():
    schema = {
        "$defs": {"entry": {"type": ["integer", "null"], "minimum": 1, "maximum": 3}},
        "type": "array", "uniqueItems": True, "items": {"$ref": "#/$defs/entry"},
    }
    assert not schema_errors(schema, [None, 1, 3])
    for payload, keyword in (([0], "minimum"), ([4], "maximum"), ([False], "type"),
                             (["1"], "type"), ([1, 1.0], "uniqueItems")):
        assert any(keyword in error for error in schema_errors(schema, payload)), payload
    # `$ref` siblings also apply under draft 2020-12.
    sibling = {"$defs": {"n": {"type": "integer"}}, "$ref": "#/$defs/n", "maximum": 2}
    assert schema_errors(sibling, 3) == ["$: maximum"]
    assert not schema_errors({"type": ["object", "null"], "required": ["x"]}, None)
    assert schema_errors({"enum": [1]}, True) == ["$: enum"]
    assert schema_errors({"const": True}, 1) == ["$: const"]
    objects = [{"a": 1, "b": 2}, {"b": 2, "a": 1}]
    assert schema_errors({"uniqueItems": True}, objects) == ["$: uniqueItems"]
    assert not schema_errors({"uniqueItems": True}, [True, 1])
    assert schema_errors({"additionalProperties": {"type": "string"}}, {"x": 1})
    assert schema_errors(False, "anything")


def test_schema_validator_checks_string_constraints_and_datetime():
    schema = {"type": "string", "minLength": 2, "pattern": "\\S"}
    assert not schema_errors(schema, "ok")
    assert schema_errors(schema, "") == ["$: minLength", "$: pattern"]
    assert schema_errors(schema, "  ") == ["$: pattern"]
    schema = {"type": ["string", "null"], "format": "date-time"}
    for valid in (None, "2026-10-25T12:34:56Z", "2026-10-25t12:34:56.123+02:00"):
        assert not schema_errors(schema, valid), valid
    for invalid in ("today", "2026-02-30T12:34:56Z", "2026-10-25T12:34:56", "2026-10-25 12:34:56Z"):
        assert schema_errors(schema, invalid) == ["$: format"], invalid
    assert schema_errors(schema, "2026-10-25T12:34:56+01:60") == ["$: format"]


def test_schema_validator_rejects_unknown_vocabulary_in_unused_branches():
    for schema in (
        {"properties": {"absent": {"minItems": 1}}}, {"$defs": {"unused": {"oneOf": []}}},
        {"items": {"unknown": True}}, {"format": "email"}, {"type": ["unknown", "null"]},
        {"$ref": "external.schema.json"},
    ):
        try:
            schema_errors(schema, None)
        except AssertionError as exc:
            assert "Unsupported" in str(exc), str(exc)
        else:
            raise AssertionError(f"Unsupported schema accepted: {schema}")
    root = {"$defs": {"unused": {"unknown": True}}}
    try:
        schema_errors({}, None, root)
    except AssertionError as exc:
        assert "Unsupported" in str(exc)
    else:
        raise AssertionError("Unknown keywords in the reference root must fail")
