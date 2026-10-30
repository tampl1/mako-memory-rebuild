"""Minimal JSON-Schema subset validator (stdlib only).

Supports the constructs used by contracts/*.schema.json: type, required,
properties, additionalProperties=false, enum, items, minLength, pattern,
format=date-time. Raises ValidationError with a JSON path.
"""

from __future__ import annotations

import re
from datetime import datetime

_DT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")


class ValidationError(ValueError):
    pass


def _check_format(value, fmt, path):
    if fmt == "date-time" and value is not None:
        if not isinstance(value, str) or not _DT.match(value):
            raise ValidationError(f"{path}: expected UTC RFC3339 date-time, got {value!r}")
        datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate(instance, schema, path="$"):
    if not isinstance(schema, dict):
        raise ValidationError(f"{path}: invalid schema node")
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        ok = False
        for one in types:
            if one == "string" and isinstance(instance, str):
                ok = True
            elif one == "object" and isinstance(instance, dict):
                ok = True
            elif one == "array" and isinstance(instance, list):
                ok = True
            elif one == "boolean" and isinstance(instance, bool):
                ok = True
            elif one == "null" and instance is None:
                ok = True
            elif one == "number" and isinstance(instance, (int, float)) and not isinstance(instance, bool):
                ok = True
            elif one == "integer" and isinstance(instance, int) and not isinstance(instance, bool):
                ok = True
        if not ok:
            raise ValidationError(f"{path}: expected {t}, got {type(instance).__name__}")
    if "enum" in schema and instance not in schema["enum"]:
        raise ValidationError(f"{path}: {instance!r} not in {schema['enum']}")
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            raise ValidationError(f"{path}: shorter than minLength {schema['minLength']}")
        if "pattern" in schema and not re.match(schema["pattern"], instance):
            raise ValidationError(f"{path}: does not match pattern {schema['pattern']}")
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                raise ValidationError(f"{path}: missing required key {key!r}")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in props:
                    raise ValidationError(f"{path}: unexpected key {key!r}")
        for key, sub in props.items():
            if key in instance:
                validate(instance[key], sub, f"{path}.{key}")
        _check_format(instance, schema.get("format"), path)
    if isinstance(instance, list) and "items" in schema:
        for i, item in enumerate(instance):
            validate(item, schema["items"], f"{path}[{i}]")
    return True
