"""Validated, database-free tenant filter rules for Microsoft 365 policies.

Rules use the same tree shape as RMM targeting: ``{operator: and|or,
children: [...]}`` groups and ``{field, operator, value, key?}`` conditions.
Text comparisons are case insensitive. List fields (tags, licences) match a
positive operator when any item matches; a negative operator requires that no
item matches. Missing data never satisfies a comparison, except ``is_not_set``.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any

MAX_DEPTH = 6
MAX_NODES = 100
MAX_VALUE_LENGTH = 512
MAX_LIST_VALUES = 100
MAX_KEY_LENGTH = 100

_PRESENCE = ("is_set", "is_not_set")
_OPERATORS = (
    "equals", "not_equals", "in", "not_in", "contains", "not_contains", "starts_with",
) + _PRESENCE
_NEGATED = {"not_equals": "equals", "not_in": "in", "not_contains": "contains"}

_FIELD_ROWS = (
    ("m365.license_sku", "Licence SKU", "Microsoft 365", "list"),
    ("m365.license_name", "Licence name", "Microsoft 365", "list"),
    ("company.name", "Company name", "Company", "text"),
    ("company.tags", "Company tag", "Company", "list"),
    ("company.variable", "Company variable", "Company", "text"),
)
_FIELDS = {
    key: {"key": key, "label": label, "category": category, "type": kind,
          "operators": list(_OPERATORS), **({"keyed": True} if key == "company.variable" else {})}
    for key, label, category, kind in _FIELD_ROWS
}
_KEY_PREFIXES = ("company.variables.", "company.variable.", "variables.")
_MISSING = object()


def field_catalogue() -> list[dict[str, Any]]:
    """Return the fields a policy filter can use, for the rule builder."""
    return copy.deepcopy(list(_FIELDS.values()))


def _text(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("values must be text")
    text = str(value).strip()
    if len(text) > MAX_VALUE_LENGTH:
        raise ValueError(f"values may contain at most {MAX_VALUE_LENGTH} characters")
    return text


def _values(value: Any) -> list[str]:
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, list):
        raise ValueError("a list of values is required")
    result = [_text(item) for item in items]
    result = [item for item in result if item]
    if not result:
        raise ValueError("at least one value is required")
    if len(result) > MAX_LIST_VALUES:
        raise ValueError(f"lists may contain at most {MAX_LIST_VALUES} values")
    return result


def _normalize_key(key: str) -> str:
    """Accept a pasted placeholder such as ``{{ company.variables.NAME }}``."""
    key = key.strip()
    if key.startswith("{{") and key.endswith("}}"):
        key = key[2:-2].strip()
    for prefix in _KEY_PREFIXES:
        if key.casefold().startswith(prefix) and len(key) > len(prefix):
            return key[len(prefix):].strip()
    return key


def validate_rules(rules: Any) -> dict[str, Any] | None:
    """Normalize a complete rule tree, raising a descriptive ``ValueError``.

    ``None`` means the policy has no filter and targets every tenant.
    """
    if rules is None:
        return None
    count = 0

    def visit(node: Any, depth: int, path: str) -> dict[str, Any]:
        nonlocal count
        count += 1
        try:
            if count > MAX_NODES:
                raise ValueError(f"rules may contain at most {MAX_NODES} groups and conditions")
            if depth > MAX_DEPTH:
                raise ValueError(f"rules may be nested at most {MAX_DEPTH} levels deep")
            if not isinstance(node, dict):
                raise ValueError("each group or condition must be an object")
            operator = node.get("operator")
            if not isinstance(operator, str) or len(operator) > 32:
                raise ValueError("an operator is required")
            operator = operator.strip().lower()
            if "children" in node or operator in ("and", "or"):
                if set(node) - {"operator", "children"}:
                    raise ValueError("groups may only contain operator and children")
                if operator not in ("and", "or"):
                    raise ValueError("group operator must be and or or")
                children = node.get("children")
                if not isinstance(children, list) or not children:
                    raise ValueError("groups must contain at least one condition or group")
                return {"operator": operator, "children": [
                    visit(child, depth + 1, f"{path}.children[{index}]")
                    for index, child in enumerate(children)
                ]}
            if set(node) - {"field", "operator", "value", "key"}:
                raise ValueError("conditions contain unsupported properties")
            field = node.get("field")
            if not isinstance(field, str) or field.strip() not in _FIELDS:
                raise ValueError("choose a supported filter field")
            spec = _FIELDS[field.strip()]
            if operator not in spec["operators"]:
                raise ValueError(f"operator {operator!r} is not supported for {spec['label']}")
            result: dict[str, Any] = {"field": spec["key"], "operator": operator}
            if spec.get("keyed"):
                key = node.get("key")
                if not isinstance(key, str) or not _normalize_key(key):
                    raise ValueError("choose a company variable")
                key = _normalize_key(key)
                if len(key) > MAX_KEY_LENGTH:
                    raise ValueError(f"variable names may contain at most {MAX_KEY_LENGTH} characters")
                result["key"] = key
            elif "key" in node:
                raise ValueError("only company variables accept a key")
            if operator in _PRESENCE:
                return result
            if "value" not in node or node["value"] is None:
                raise ValueError("a comparison value is required")
            if operator in ("in", "not_in"):
                result["value"] = _values(node["value"])
            else:
                value = _text(node["value"])
                if not value:
                    raise ValueError("a comparison value is required")
                result["value"] = value
            return result
        except ValueError as exc:
            raise ValueError(f"Rule {path}: {exc}") from None

    if not isinstance(rules, dict) or "children" not in rules:
        raise ValueError("Rule root: filter rules must start with an and/or group")
    return visit(rules, 1, "root")


def _resolve(condition: Mapping[str, Any], context: Mapping[str, Any]) -> Any:
    field = condition["field"]
    if field == "company.variable":
        variables = context.get("company_variables")
        if not isinstance(variables, Mapping):
            return _MISSING
        key = condition["key"]
        if key in variables:
            return variables[key]
        folded = key.casefold()
        for name, value in variables.items():
            if isinstance(name, str) and name.casefold() == folded:
                return value
        return _MISSING
    sources = {
        "m365.license_sku": "license_skus",
        "m365.license_name": "license_names",
        "company.tags": "company_tags",
        "company.name": "company_name",
    }
    value = context.get(sources[field], _MISSING)
    if _FIELDS[field]["type"] == "list" and value is not _MISSING and not isinstance(value, list):
        return _MISSING
    return value


def _unset(value: Any) -> bool:
    return (
        value is _MISSING
        or value is None
        or (isinstance(value, str) and not value.strip())
        or (isinstance(value, list) and not any(str(item).strip() for item in value))
    )


def _compare(actual: Any, expected: Any, operator: str) -> bool:
    text = str(actual).strip().casefold()
    if operator == "equals":
        return text == expected.casefold()
    if operator == "in":
        return text in {item.casefold() for item in expected}
    if operator == "contains":
        return expected.casefold() in text
    if operator == "starts_with":
        return text.startswith(expected.casefold())
    return False


def _matches_validated(tree: dict[str, Any], context: Mapping[str, Any]) -> bool:
    def evaluate(node: dict[str, Any]) -> bool:
        if "children" in node:
            results = (evaluate(child) for child in node["children"])
            return all(results) if node["operator"] == "and" else any(results)
        operator = node["operator"]
        actual = _resolve(node, context)
        if operator == "is_not_set":
            return _unset(actual)
        if operator == "is_set":
            return not _unset(actual)
        if actual is _MISSING or actual is None:
            return False
        values = actual if isinstance(actual, list) else [actual]
        positive = _NEGATED.get(operator, operator)
        hits = [_compare(value, node["value"], positive) for value in values]
        return not any(hits) if operator in _NEGATED else any(hits)

    return evaluate(tree)


def compile_rules(rules: Any) -> Callable[[Mapping[str, Any]], bool]:
    """Validate once and return a matcher reusable across tenant contexts.

    A policy without rules matches every tenant. Invalid rules raise
    ``ValueError`` here; a malformed context fails closed.
    """
    tree = validate_rules(rules)
    if tree is None:
        return lambda context: isinstance(context, Mapping)

    def matcher(context: Mapping[str, Any]) -> bool:
        if not isinstance(context, Mapping):
            return False
        try:
            return _matches_validated(tree, context)
        except (TypeError, ValueError, RecursionError):
            return False

    return matcher
