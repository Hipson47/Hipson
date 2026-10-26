"""Validate the JSON Schema keywords used in schemas/, without third-party dependencies.

This is deliberately bounded to the repository's vocabulary, not a general JSON
Schema implementation. Unknown keywords, types, formats and external references
fail even in unused branches so a new contract cannot silently weaken validation.
"""

import math
import re
from datetime import datetime

KEYWORDS = frozenset({
    "$schema", "title", "description", "$defs", "$ref", "type", "const", "enum",
    "properties", "additionalProperties", "required", "items", "uniqueItems",
    "pattern", "minLength", "maxLength", "maxItems", "minimum", "maximum", "format",
})
TYPES = frozenset({"object", "array", "string", "boolean", "integer", "number", "null"})


def _resolve(ref, root):
    assert ref.startswith("#/"), f"Unsupported schema reference: {ref}"
    target = root
    for part in ref[2:].split("/"):
        target = target[part.replace("~1", "/").replace("~0", "~")]
    return target


def check_schema(schema, root=None):
    """Check the whole vocabulary, including definitions and absent properties."""
    if root is None:
        root = schema
    if isinstance(schema, bool):
        return
    assert isinstance(schema, dict), f"Expected schema object or boolean: {schema!r}"
    assert not (unknown := schema.keys() - KEYWORDS), f"Unsupported schema keywords: {sorted(unknown)}"
    if "$schema" in schema:
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        assert names and all(name in TYPES for name in names), f"Unsupported schema types: {names}"
    if "format" in schema:
        assert schema["format"] == "date-time", f"Unsupported schema format: {schema['format']}"
    if "pattern" in schema:
        re.compile(schema["pattern"])
    if "$ref" in schema:
        _resolve(schema["$ref"], root)
    for key in ("properties", "$defs"):
        for child in schema.get(key, {}).values():
            check_schema(child, root)
    for key in ("items", "additionalProperties"):
        if key in schema:
            check_schema(schema[key], root)


def _is_type(name, value):
    if name == "integer":
        return type(value) is int or (type(value) is float and math.isfinite(value) and value.is_integer())
    if name == "number":
        return type(value) is int or (type(value) is float and math.isfinite(value))
    return isinstance(value, {"object": dict, "array": list, "string": str,
                              "boolean": bool, "null": type(None)}[name])


def _equal(left, right):
    # JSON numbers compare by value, but true and 1 are different JSON values.
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_equal(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right, strict=True))
    return left == right


def _date_time(value):
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])", value) is None:
        return False
    try:
        datetime.fromisoformat(value.upper())
    except ValueError:
        return False
    return True


def schema_errors(schema, payload, root=None, path="$"):
    if root is None:
        root = schema
    check_schema(root)
    if schema is not root:
        check_schema(schema, root)
    return _errors(schema, payload, root, path)


def _errors(schema, payload, root, path):
    if isinstance(schema, bool):
        return [] if schema else [f"{path}: false schema"]
    errors = []
    if "$ref" in schema:
        errors += _errors(_resolve(schema["$ref"], root), payload, root, path)
    if "const" in schema and not _equal(payload, schema["const"]):
        errors.append(f"{path}: const")
    if "enum" in schema and not any(_equal(payload, item) for item in schema["enum"]):
        errors.append(f"{path}: enum")
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(name, payload) for name in names):
            return [*errors, f"{path}: type"]
    if isinstance(payload, str):
        if len(payload) < schema.get("minLength", 0):
            errors.append(f"{path}: minLength")
        if 'maxLength' in schema and len(payload) > schema['maxLength']:
            errors.append(f'{path}: maxLength')
        if "pattern" in schema and re.search(schema["pattern"], payload) is None:
            errors.append(f"{path}: pattern")
        if schema.get("format") == "date-time" and not _date_time(payload):
            errors.append(f"{path}: format")
    if type(payload) in (int, float):
        if "minimum" in schema and payload < schema["minimum"]:
            errors.append(f"{path}: minimum")
        if "maximum" in schema and payload > schema["maximum"]:
            errors.append(f"{path}: maximum")
    if isinstance(payload, list):
        if 'maxItems' in schema and len(payload) > schema['maxItems']:
            errors.append(f'{path}: maxItems')
        if schema.get("uniqueItems") and any(
            _equal(item, other) for index, item in enumerate(payload) for other in payload[:index]
        ):
            errors.append(f"{path}: uniqueItems")
        for index, item in enumerate(payload):
            errors += _errors(schema.get("items", True), item, root, f"{path}[{index}]")
    if isinstance(payload, dict):
        properties = schema.get("properties", {})
        errors += [f"{path}.{key}: required" for key in schema.get("required", []) if key not in payload]
        for key, value in payload.items():
            if key in properties:
                errors += _errors(properties[key], value, root, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{key}: additional")
            else:
                errors += _errors(schema.get("additionalProperties", True), value, root, f"{path}.{key}")
    return errors
