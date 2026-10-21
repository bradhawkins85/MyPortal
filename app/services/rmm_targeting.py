"""Validated, database-free targeting rules for native RMM checks.

Rules are non-empty trees of ``{operator: and|or, children: [...]}`` groups and
``{field, operator, value, key?}`` conditions. Text comparisons are case
insensitive. Collection positives match any item; negatives require no matching
item, including known-empty collections. Unknown inventory never satisfies a
negative predicate. Only explicit ``is_not_set`` matches absent, null or blank
values; it also matches known-empty collections.

Keyed custom fields and company variables support scalar or multi-value data.
Their equality compares booleans and numeric values when possible, otherwise
case-insensitive text. Numeric, dotted-version and CIDR operators convert values
explicitly and fail closed on incompatible inventory. Versions use numeric
components (an optional leading v is accepted), padding missing components with
zero. Validation limits the complete tree before evaluation, including OR arms.
"""

from __future__ import annotations

import copy
import ipaddress
import math
import re
from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_DEPTH = 8
MAX_NODES = 250
MAX_VALUE_LENGTH = 2048
MAX_LIST_VALUES = 100
MAX_KEY_LENGTH = 256

_PRESENCE = ("is_set", "is_not_set")
_EQUALITY = ("equals", "not_equals", "in", "not_in")
_TEXT = _EQUALITY + ("contains", "not_contains", "starts_with", "ends_with") + _PRESENCE
_NUMBER = _EQUALITY + ("gt", "gte", "lt", "lte", "between") + _PRESENCE
_BOOLEAN = ("equals", "not_equals") + _PRESENCE
_VERSION = _TEXT + ("version_gte", "version_lte")
_IP = _EQUALITY + ("in_subnet", "not_in_subnet") + _PRESENCE
_DYNAMIC = tuple(dict.fromkeys(_TEXT + _NUMBER + _VERSION + _IP))
_OPERATORS = {
    "text": _TEXT,
    "number": _NUMBER,
    "boolean": _BOOLEAN,
    "version": _VERSION,
    "ip": _IP,
    "list": _TEXT,
    "dynamic": _DYNAMIC,
}

_FIELD_ROWS = (
    ("asset.id", "Asset ID", "Asset", "number"),
    ("asset.name", "Asset name", "Asset", "text"),
    ("asset.catalogue_type", "Asset catalogue type", "Asset", "text"),
    ("asset.machine_type", "Machine type", "Asset", "text"),
    ("asset.type", "Reported asset type", "Asset", "text"),
    ("asset.status", "Asset status", "Asset", "text"),
    ("asset.location", "Location", "Asset", "text"),
    ("asset.owner", "Owner", "Asset", "text"),
    ("asset.support_contact", "Support contact", "Asset", "text"),
    ("asset.criticality", "Criticality", "Asset", "text"),
    ("asset.review_status", "Review status", "Asset", "text"),
    ("asset.manufacturer", "Manufacturer", "Hardware", "text"),
    ("asset.model", "Hardware model", "Hardware", "text"),
    ("asset.serial_number", "Serial number", "Hardware", "text"),
    ("asset.cpu_name", "CPU model", "Hardware", "text"),
    ("asset.cpu_cores", "CPU physical cores", "Hardware", "number"),
    ("asset.cpu_logical_processors", "CPU logical processors", "Hardware", "number"),
    ("asset.bios_version", "BIOS version", "Hardware", "text"),
    ("asset.ram_gb", "Memory (GB)", "Hardware", "number"),
    ("asset.disk_gb", "Disk capacity (GB)", "Hardware", "number"),
    ("asset.disk_free_gb", "System disk free space (GB)", "Hardware", "number"),
    ("asset.hdd_size", "Reported disk description", "Hardware", "text"),
    ("asset.form_factor", "Form factor", "Hardware", "text"),
    ("asset.performance_score", "Performance score", "Hardware", "number"),
    ("asset.approx_age", "Approximate age (years)", "Hardware", "number"),
    ("asset.warranty_status", "Warranty status", "Hardware", "text"),
    ("asset.ip_address", "Asset IP address", "Network", "ip"),
    ("asset.os_name", "Asset operating system", "Operating system", "text"),
    ("asset.os_version", "Asset OS version", "Operating system", "version"),
    ("asset.os_build", "OS build", "Operating system", "version"),
    ("asset.domain", "Computer domain", "Operating system", "text"),
    ("asset.uptime_days", "Uptime (days)", "Operating system", "number"),
    ("agent.id", "Agent ID", "Agent", "number"),
    ("agent.hostname", "Agent hostname", "Agent", "text"),
    ("agent.architecture", "Agent architecture", "Agent", "text"),
    ("agent.version", "Agent version", "Agent", "version"),
    ("agent.status", "Agent status", "Agent", "text"),
    ("agent.os", "Agent operating system", "Operating system", "text"),
    ("agent.os_version", "Agent OS version", "Operating system", "version"),
    ("agent.last_ip", "Agent connection IP address", "Network", "ip"),
    ("company.id", "Company ID", "Company", "number"),
    ("company.name", "Company name", "Company", "text"),
    ("company.vip", "Company VIP status", "Company", "boolean"),
    ("company.agent_count", "Company active agent count", "Company", "number"),
    ("company.archived", "Company archived", "Company", "boolean"),
    ("company.pending_approval", "Company pending approval", "Company", "boolean"),
    ("company.is_demo", "Demo company", "Company", "boolean"),
    ("software.name", "Installed software name", "Software", "list"),
    ("software.version", "Installed software version", "Software", "version"),
    ("software.publisher", "Installed software publisher", "Software", "text"),
    ("asset.tags", "Asset tag (including automatic tags)", "Tags", "list"),
    ("company.tags", "Company tag", "Tags", "list"),
    ("asset.custom", "Asset custom field", "Custom fields", "dynamic"),
    ("company.variable", "Company custom variable", "Custom fields", "dynamic"),
)
_FIELDS = {
    key: {"key": key, "label": label, "category": category, "type": kind,
          "operators": list(_OPERATORS[kind]),
          **({"keyed": True} if kind == "dynamic" else {})}
    for key, label, category, kind in _FIELD_ROWS
}
for _field in _FIELDS.values():
    if _field["type"] == "boolean":
        _field["options"] = [{"value": True, "label": "Yes"}, {"value": False, "label": "No"}]
for _key in ("software.version", "software.publisher"):
    _FIELDS[_key]["keyed"] = True

_KEYED_COLLECTION_FIELDS = {"software.version": "software_versions", "software.publisher": "software_publishers"}

_MISSING = object()
_VERSION_PATTERN = re.compile(r"v?(\d+(?:\.\d+)*)", re.IGNORECASE | re.ASCII)
_NEGATED = {"not_equals": "equals", "not_contains": "contains", "not_in": "in", "not_in_subnet": "in_subnet"}


def field_catalogue() -> list[dict[str, Any]]:
    """Return editable field metadata, never inventory or variable values."""
    return copy.deepcopy(list(_FIELDS.values()))


def _text(value: Any) -> str:
    if not isinstance(value, (str, int, float, bool)):
        raise ValueError("a scalar text value is required")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("a finite value is required")
    result = str(value).strip()
    if len(result) > MAX_VALUE_LENGTH:
        raise ValueError(f"values may contain at most {MAX_VALUE_LENGTH} characters")
    return result


def _number(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("a number is required")
    text = _text(value) if not isinstance(value, Decimal) else str(value)
    if len(text) > MAX_VALUE_LENGTH:
        raise ValueError("numeric value is too long")
    try:
        result = Decimal(text)
    except InvalidOperation:
        raise ValueError("a number is required") from None
    if not result.is_finite() or result.copy_abs() > Decimal("1e308"):
        raise ValueError("a finite number within the supported range is required")
    return result


def _json_number(value: Any) -> int | float:
    result = _number(value)
    if result == result.to_integral_value():
        return int(result)
    normal = float(result)
    if normal == 0 and result != 0:
        raise ValueError("a number within the supported precision is required")
    return normal


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int)):
        normal = str(value).strip().casefold()
        if normal in ("true", "yes", "on", "1"):
            return True
        if normal in ("false", "no", "off", "0"):
            return False
    raise ValueError("a boolean (true or false) is required")


def _version(value: Any) -> tuple[int, ...]:
    match = _VERSION_PATTERN.fullmatch(_text(value))
    if not match:
        raise ValueError("a dotted numeric version is required")
    return tuple(int(part) for part in match.group(1).split("."))


def _address(value: Any) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        return ipaddress.ip_address(_text(value))
    except ValueError:
        raise ValueError("a valid IPv4 or IPv6 address is required") from None


def _network(value: Any) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    text = _text(value)
    if "/" not in text:
        raise ValueError("a valid IPv4 or IPv6 CIDR subnet is required")
    try:
        return ipaddress.ip_network(text, strict=False)
    except ValueError:
        raise ValueError("a valid IPv4 or IPv6 CIDR subnet is required") from None


def _dynamic_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return _json_number(value)
    text = _text(value)
    if not text:
        raise ValueError("a non-empty value is required; use is_not_set for missing values")
    if text.casefold() in ("true", "false", "yes", "no", "on", "off"):
        return _boolean(text)
    return text


def _scalar(value: Any, kind: str, operator: str) -> Any:
    if operator in ("gt", "gte", "lt", "lte", "between") or kind == "number":
        return _json_number(value)
    if operator in ("in_subnet", "not_in_subnet"):
        return str(_network(value))
    if operator in ("version_gte", "version_lte"):
        _version(value)
        return _text(value)
    if kind == "boolean":
        return _boolean(value)
    if kind == "ip":
        return str(_address(value))
    if kind == "dynamic" and operator in _EQUALITY:
        return _dynamic_value(value)
    if not isinstance(value, str):
        raise ValueError("a text value is required")
    text = _text(value)
    if not text:
        raise ValueError("a non-empty value is required; use is_not_set for missing values")
    return text


def _list(value: Any) -> list[Any]:
    if isinstance(value, str):
        if len(value) > MAX_VALUE_LENGTH:
            raise ValueError(f"values may contain at most {MAX_VALUE_LENGTH} characters")
        values = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        values = value
    else:
        raise ValueError("a list or comma-separated values is required")
    if not values or len(values) > MAX_LIST_VALUES:
        raise ValueError(f"provide between 1 and {MAX_LIST_VALUES} values")
    if any(value is None or (isinstance(value, str) and not value.strip()) for value in values):
        raise ValueError("list values must not be empty")
    return values


def validate_rules(rules: Any) -> dict[str, Any]:
    """Normalize a complete rule tree, raising a descriptive ``ValueError``.

    Numeric and boolean form strings are converted to JSON values, ``in`` and
    ``between`` accept arrays or comma-separated strings, and CIDRs normalize
    host bits. The returned object shares no mutable values with the input.
    """
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
            if not isinstance(operator, str):
                raise ValueError("an operator is required")
            if len(operator) > 32:
                raise ValueError("choose a supported operator")
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
                raise ValueError("choose a supported targeting field")
            field = field.strip()
            spec = _FIELDS[field]
            if operator not in spec["operators"]:
                raise ValueError(f"operator {operator!r} is not supported for {spec['label']}")
            result: dict[str, Any] = {"field": field, "operator": operator}
            if spec.get("keyed"):
                key = node.get("key")
                if not isinstance(key, str) or not key.strip():
                    raise ValueError("a custom field, variable or software name key is required")
                if len(key.strip()) > MAX_KEY_LENGTH:
                    raise ValueError(f"custom keys may contain at most {MAX_KEY_LENGTH} characters")
                result["key"] = _normalize_key(field, key)
            elif "key" in node:
                raise ValueError("only custom fields, company variables and software metadata accept a key")
            if operator in _PRESENCE:
                # Editors may retain the previous scalar value after selecting
                # a presence operator. It is immaterial and never persisted.
                if "value" in node and node["value"] is not None:
                    _text(node["value"])
                return result
            if "value" not in node or node["value"] is None:
                raise ValueError("a comparison value is required")
            value = node["value"]
            if operator in ("in", "not_in", "between"):
                result["value"] = [_scalar(item, spec["type"], operator) for item in _list(value)]
                if operator == "between":
                    if len(result["value"]) != 2:
                        raise ValueError("between requires exactly two numeric bounds")
                    if result["value"][0] > result["value"][1]:
                        raise ValueError("the lower bound must not exceed the upper bound")
            else:
                result["value"] = _scalar(value, spec["type"], operator)
            return result
        except ValueError as exc:
            if str(exc).startswith("Rule root"):
                raise
            raise ValueError(f"Rule {path}: {exc}") from None

    if not isinstance(rules, dict) or "children" not in rules:
        raise ValueError("Rule root: targeting rules must start with an and/or group")
    return visit(rules, 1, "root")


_KEY_PREFIXES = {
    "company.variable": ("company.variables.", "company.variable.", "variables."),
    "asset.custom": ("asset.custom.", "custom."),
}


def _normalize_key(field: str, key: str) -> str:
    """Accept a template placeholder such as ``{{ company.variables.NAME }}``.

    Editors commonly paste the script placeholder rather than the bare name;
    the wrapper and scope prefix are stripped so the rule addresses NAME.
    """
    key = key.strip()
    if key.startswith("{{") and key.endswith("}}"):
        key = key[2:-2].strip()
    for prefix in _KEY_PREFIXES.get(field, ()):
        if key.casefold().startswith(prefix) and len(key) > len(prefix):
            return key[len(prefix):].strip()
    return key


def _resolve(condition: dict[str, Any], context: Mapping[str, Any]) -> Any:
    field = condition["field"]
    collections = {"software.name": "software", "asset.tags": "asset_tags", "company.tags": "company_tags"}
    if field in collections:
        value = context.get(collections[field], _MISSING)
        # A string is not a complete software/tag inventory.
        return value if value is _MISSING or value is None or isinstance(value, list) else _MISSING
    if field in _KEYED_COLLECTION_FIELDS:
        values = context.get(_KEYED_COLLECTION_FIELDS[field], _MISSING)
        if not isinstance(values, Mapping):
            return _MISSING
        software_key = condition["key"].casefold()
        matching = [value for key, value in values.items() if isinstance(key, str) and key.casefold() == software_key]
        if not matching:
            return _MISSING
        result = []
        for value in matching:
            if not isinstance(value, list):
                return _MISSING
            result.extend(value)
        # No reported version/publisher is unavailable metadata rather than a
        # known empty installation inventory. Filter software.name for absence.
        return result if result else _MISSING
    keyed = {"asset.custom": "asset_custom", "company.variable": "company_variables"}
    if field in keyed:
        values = context.get(keyed[field], _MISSING)
        if not isinstance(values, Mapping):
            return _MISSING
        key = condition["key"]
        if key in values:
            return values[key]
        # Names match case insensitively, as script template placeholders do;
        # an exact match above always takes precedence.
        folded = key.casefold()
        for name, value in values.items():
            if isinstance(name, str) and name.casefold() == folded:
                return value
        return _MISSING
    scope, key = field.split(".", 1)
    values = context.get(scope, _MISSING)
    return values.get(key, _MISSING) if isinstance(values, Mapping) else _MISSING


def _unset(value: Any) -> bool:
    return value is _MISSING or value is None or (isinstance(value, str) and not value.strip()) or (isinstance(value, list) and not value)


def _dynamic_equals(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return _boolean(actual) == _boolean(expected)
    try:
        return _number(actual) == _number(expected)
    except ValueError:
        return _text(actual).casefold() == _text(expected).casefold()


def _compare(actual: Any, expected: Any, kind: str, operator: str) -> bool:
    if operator in ("gt", "gte", "lt", "lte", "between"):
        number = _number(actual)
        if operator == "between":
            return _number(expected[0]) <= number <= _number(expected[1])
        other = _number(expected)
        return {"gt": number > other, "gte": number >= other, "lt": number < other, "lte": number <= other}[operator]
    if operator in ("version_gte", "version_lte"):
        current, target = _version(actual), _version(expected)
        length = max(len(current), len(target))
        current += (0,) * (length - len(current))
        target += (0,) * (length - len(target))
        return current >= target if operator == "version_gte" else current <= target
    if operator == "in_subnet":
        address, network = _address(actual), _network(expected)
        return address.version == network.version and address in network
    if operator == "in":
        return any(_compare(actual, item, kind, "equals") for item in expected)
    if kind == "number":
        current, target = _number(actual), _number(expected)
    elif kind == "boolean":
        current, target = _boolean(actual), _boolean(expected)
    elif kind == "ip":
        current, target = _address(actual), _address(expected)
    elif kind == "dynamic" and operator == "equals":
        return _dynamic_equals(actual, expected)
    else:
        current, target = _text(actual).casefold(), _text(expected).casefold()
    if operator == "equals":
        return current == target
    if operator == "contains":
        return target in current
    if operator == "starts_with":
        return current.startswith(target)
    if operator == "ends_with":
        return current.endswith(target)
    raise ValueError("unsupported comparison")


def _valid_present(value: Any, kind: str, keyed_collection: bool = False) -> bool:
    if isinstance(value, list) and kind not in ("list", "ip", "dynamic") and not keyed_collection:
        raise ValueError("this inventory field must be a scalar")
    values = value if isinstance(value, list) else [value]
    if not values:
        return False
    for item in values:
        if _unset(item):
            return False
        if kind == "number":
            _number(item)
        elif kind == "boolean":
            _boolean(item)
        elif kind == "ip":
            _address(item)
        elif kind in ("text", "version", "list") and not isinstance(item, str):
            raise ValueError("this inventory field must contain text")
        else:
            _text(item)
    return True


def _matches_validated(tree: dict[str, Any], context: Any) -> bool:
    """Evaluate an internally owned tree that has already passed validation."""
    try:
        if not isinstance(context, Mapping):
            return False

        def evaluate(node: dict[str, Any]) -> bool:
            if "children" in node:
                results = (evaluate(child) for child in node["children"])
                return all(results) if node["operator"] == "and" else any(results)
            actual = _resolve(node, context)
            operator = node["operator"]
            if operator in _NEGATED and node["field"] in (context.get("incomplete_fields") or []):
                return False
            kind = _FIELDS[node["field"]]["type"]
            if operator == "is_not_set":
                return _unset(actual)
            if isinstance(actual, list) and not actual and kind in ("list", "ip", "dynamic"):
                return operator in _NEGATED
            if _unset(actual):
                return False
            try:
                if not _valid_present(actual, kind, node["field"] in _KEYED_COLLECTION_FIELDS):
                    return False
                if operator == "is_set":
                    return True
                values = actual if isinstance(actual, list) else [actual]
                positive = _NEGATED.get(operator, operator)
                # Evaluate every item before negating: malformed inventory may
                # never turn a failed positive comparison into a negative hit.
                hits = [_compare(value, node["value"], kind, positive) for value in values]
                return not any(hits) if operator in _NEGATED else any(hits)
            except (ValueError, TypeError, OverflowError):
                return False

        return evaluate(tree)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return False


def compile_rules(rules: Any) -> Callable[[Any], bool]:
    """Validate once and return a matcher reusable across inventory contexts.

    Invalid rules raise ``ValueError`` during compilation. The normalized tree
    is an independent copy retained privately by the returned closure, so later
    edits to a form or database row cannot alter the compiled selector. Context
    data is still checked on every evaluation and malformed inventory fails
    closed. Fleet selection should compile each check once, not once per agent.
    """
    tree = validate_rules(rules)
    return lambda context: _matches_validated(tree, context)


def matches_rules(rules: Any, context: Any) -> bool:
    """Evaluate rules against scoped inventory, failing closed on bad rules.

    Context contains asset/agent/company mappings, software/asset_tags/
    company_tags lists (None means unavailable), and asset_custom/
    company_variables mappings keyed by definition ID or name. Optional
    software_versions/software_publishers map package names to value lists;
    package-name lookup is case insensitive. No lookup,
    template expansion, script execution or secret resolution takes place.
    """
    try:
        return compile_rules(rules)(context)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return False
